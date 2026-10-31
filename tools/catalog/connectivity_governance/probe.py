"""Orquestación de la sonda DEV/QA (F2.1c) — versionada y testeable offline.

El script `_handoffs/connectivity_devqa_probe.py` solo lee la configuración, valida allowlist/confirmación y
captura el secreto; TODA la ejecución bajo presupuesto vive acá para que el cableado quede cubierto por tests:

- UNA política de reintentos (`RetryPolicy` con sleep presupuestado) inyectada a la vez al ORQUESTADOR
  (`extract_inventory`: list_spaces/get_connection) y a la FUENTE (`list_connections`). Cada espera cobra un
  reintento global y no puede exceder el tiempo restante.
- Medición de llamadas FÍSICAS en la fuente (`CliConnectionSource(budget=…)`): preflight incluido, cada intento
  cuenta una vez (sin `BudgetedSource` encima → sin doble conteo).
- Duración global acotada ANTES de cada llamada/espera (timeout = tiempo restante) y verificada al final como
  respaldo. Cualquier límite agotado ⇒ ABORTED explícito: el inventario NO se expone (nunca COMPLETE/VERIFIED).
- Huella de metadatos del perfil REAL del CLI antes/después: si cambió, la ejecución se marca como NO válida.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .extraction import RetryPolicy, extract_inventory
from .safety import Budget, BudgetError, profile_fingerprint

EXIT_OK = 0
EXIT_PREFLIGHT_INSUFFICIENT = 2
EXIT_INVENTORY_FAILED = 3
EXIT_ABORTED = 4
EXIT_REAL_PROFILE_MODIFIED = 5


@dataclass
class ProbeOutcome:
    result: str                            # OK | PREFLIGHT_INSUFFICIENT | ABORTED
    exit_code: int
    preflight: Optional[dict] = None
    inventory: Optional[dict] = None       # SOLO si no hubo aborto
    abort_code: Optional[str] = None
    abort_message: Optional[str] = None
    real_profile_modified: bool = False
    budget: dict = field(default_factory=dict)


def _budget_stats(budget: Budget) -> dict:
    return {"requests_used": budget.requests_used, "max_requests": budget.max_requests,
            "retries_used": budget.retries_used, "max_total_retries": budget.max_total_retries,
            "elapsed_s": round(budget.elapsed(), 3), "max_duration_s": budget.max_duration_s}


def run_probe(source, budget: Budget, *, client: str, tenant_id: str, tenant_host: str,
              expected_spaces: list, space_environment_map: Optional[dict] = None, max_attempts: int = 4,
              sleep: Callable[[float], None] = time.sleep,
              real_profile: Optional[Path] = None) -> ProbeOutcome:
    """Preflight (una vez) → extracción read-only, todo bajo `budget`. No persiste nada."""
    if getattr(source, "budget", None) is not budget:
        raise ValueError("la fuente debe medir sus llamadas físicas contra el MISMO presupuesto (source.budget)")
    retry = RetryPolicy(max_attempts=max_attempts, sleep=budget.retry_sleep(sleep))
    source.retry = retry                   # list_connections (reintento propio de la fuente)
    before = profile_fingerprint(real_profile) if real_profile is not None else None

    outcome: ProbeOutcome
    budget.start()
    try:
        report = source.preflight_report()
        if not report.authorized_for_extraction():
            outcome = ProbeOutcome("PREFLIGHT_INSUFFICIENT", EXIT_PREFLIGHT_INSUFFICIENT, preflight=report.to_dict())
        else:
            inv = extract_inventory(source, client=client, tenant_id=tenant_id, tenant_host=tenant_host,
                                    space_environment_map=space_environment_map,
                                    expected_spaces=expected_spaces, retry=retry)   # get_connection/list_spaces
            budget.assert_within()          # respaldo posterior (la cota real es previa, por llamada/espera)
            d = inv.to_dict()
            outcome = ProbeOutcome("OK", EXIT_OK if d["status"] != "FAILED" else EXIT_INVENTORY_FAILED,
                                   preflight=report.to_dict(), inventory=d)
    except BudgetError as e:
        preflight = source._last_preflight.to_dict() if getattr(source, "_last_preflight", None) else None
        outcome = ProbeOutcome("ABORTED", EXIT_ABORTED, preflight=preflight,
                               abort_code=e.code, abort_message=e.message)
    outcome.budget = _budget_stats(budget)

    if before is not None and profile_fingerprint(real_profile) != before:
        outcome.real_profile_modified = True
        outcome.exit_code = EXIT_REAL_PROFILE_MODIFIED
    return outcome


# ── Salida COMPARTIBLE (B1) ───────────────────────────────────────────────────
# El `ProbeOutcome` conserva el detalle técnico SOLO en memoria. Lo único que se imprime es `review_summary`:
# estados del preflight, estado/exhaustividad, conteos, presencia/ausencia de atributos, ordinales locales y
# códigos de error de un vocabulario CERRADO. Nunca hosts/URLs/SID/mandantes/nombres de espacios, conexiones o
# agentes/Client ID/secretos/tokens, ni mensajes de excepción, ni hashes/huellas de identificadores.
_KNOWN_CODES = frozenset({
    # clasificación de la fuente / orquestador
    "AUTH_401", "CLI_EXECUTION_ERROR", "CLI_UNAVAILABLE", "GET_FAILED", "INVALID_OR_INCOMPLETE_JSON",
    "OAUTH_FAILED", "RATE_LIMITED", "SCOPE_DENIED", "SPACE_INACCESSIBLE", "TIMEOUT",
    "BAD_LIST_ITEM", "ENVIRONMENT_UNGOVERNED", "PARSE_FAILED",
    # preflight OAuth (DatasphereCli)
    "PRECHECK_OAUTH_401", "PRECHECK_OAUTH_NETWORK_ERROR", "PRECHECK_OAUTH_ERROR", "PRECHECK_OAUTH_INVALID_RESPONSE",
    # presupuesto / seguridad
    "REQUESTS_EXCEEDED", "DURATION_EXCEEDED", "RETRIES_EXCEEDED",
    "DEVQA_NOT_CONFIRMED", "CONFIG_INCOMPLETE", "CONFIG_INVALID", "ALLOWLIST_EMPTY", "HOST_NOT_ALLOWLISTED",
    "TOKEN_URL_NOT_ALLOWLISTED", "WORKDIR_CLEANUP_FAILED", "NO_INTERACTIVE_CONSOLE",
})
_PRECHECK_HTTP_RE = re.compile(r"\bPRECHECK_OAUTH_HTTP_\d{3}\b")
_PRECHECK_RE = re.compile(r"\bPRECHECK_[A-Z0-9_]+\b")
_SCOPE_KINDS = ("preflight", "spaces", "space", "connection")
_ABORT_TEXT = {
    "REQUESTS_EXCEEDED": "presupuesto de solicitudes agotado",
    "DURATION_EXCEEDED": "duración máxima agotada",
    "RETRIES_EXCEEDED": "reintentos totales agotados",
}


def safe_code(code: Optional[str]) -> str:
    """Código técnico SOLO si pertenece al vocabulario cerrado; cualquier otra cosa ⇒ UNCLASSIFIED."""
    if code in _KNOWN_CODES:
        return code
    if code and _PRECHECK_HTTP_RE.fullmatch(code):
        return code
    return "UNCLASSIFIED"


def _detail_code(status: str, detail: str) -> Optional[str]:
    """Reduce el detalle de un eslabón del preflight a un código (el texto nunca sale)."""
    if status == "OK":
        return None
    if status == "NOT_VERIFIED":
        return "NOT_EXECUTED"
    m = _PRECHECK_HTTP_RE.search(detail or "") or _PRECHECK_RE.search(detail or "")
    if m:
        return safe_code(m.group(0))
    import subprocess
    from .sources import classify_cli_error           # clasificación por palabras clave; solo se usa el código
    timeout_err = classify_cli_error(subprocess.TimeoutExpired("cli", 0))
    if detail == timeout_err.message:                  # mensaje fijo de TIMEOUT (fuente única)
        return timeout_err.code
    return safe_code(classify_cli_error(Exception(detail or "")).code)


def _scope_kind(scope: str) -> str:
    kind = (scope or "").split(":", 1)[0]
    return kind if kind in _SCOPE_KINDS else "other"


def review_summary(outcome: ProbeOutcome) -> dict:
    """Vista compartible del resultado (ver comentario de sección)."""
    out: dict = {"result": outcome.result if outcome.result in ("OK", "PREFLIGHT_INSUFFICIENT", "ABORTED")
                 else "UNCLASSIFIED", "exit_code": int(outcome.exit_code)}
    if outcome.preflight is not None:
        out["preflight"] = {
            name: ({"status": v.get("status")} | ({"code": c} if (c := _detail_code(v.get("status"), v.get("detail", ""))) else {}))
            for name, v in outcome.preflight.items()
            if name in ("oauth_token", "list_spaces", "list_connections", "read_detail")
        }
    if outcome.result == "ABORTED":
        code = safe_code(outcome.abort_code)
        out["abort"] = {"code": code, "text": _ABORT_TEXT.get(code, "ejecución interrumpida")}
    inv = outcome.inventory
    if inv is not None and outcome.result == "OK":
        cov = inv["coverage"]
        by_code: dict = {}
        by_kind: dict = {}
        for e in cov["errors"]:
            c = safe_code(e.get("code"))
            by_code[c] = by_code.get(c, 0) + 1
            k = _scope_kind(e.get("scope", ""))
            by_kind[k] = by_kind.get(k, 0) + 1
        space_ref = {s: f"S{i}" for i, s in enumerate(cov["expected_spaces"], 1)}   # ordinal local
        conns = []
        for i, c in enumerate(inv["connections"], 1):
            phys, agent = c["physical_system"], c["agent_observed"]
            conns.append({
                "ref": f"C{i}",
                "space_ref": space_ref.get(c["identity"]["space"], "S?"),
                "physical_present": {k: phys.get(k) not in (None, "") for k in ("host", "sysid", "systemnumber", "client")},
                "agent_present": {k: agent.get(k) not in (None, "") for k in ("agent_name", "agent_group", "location", "status")},
            })
        out["inventory"] = {
            "status": inv["status"],
            "exhaustiveness": inv["exhaustiveness"],
            "coverage": {
                "expected_spaces": len(cov["expected_spaces"]),
                "accessible_spaces": len(cov["accessible_spaces"]),
                "inaccessible_spaces": len(cov["inaccessible_spaces"]),
                "discovered_connections": cov["discovered_connections"],
                "extracted_connections": cov["extracted_connections"],
                "errors_total": len(cov["errors"]),
                "errors_by_code": dict(sorted(by_code.items())),
                "errors_by_scope": dict(sorted(by_kind.items())),
            },
            "connections": conns,
        }
    b = outcome.budget or {}
    out["budget"] = {k: b[k] for k in ("requests_used", "max_requests", "retries_used", "max_total_retries",
                                       "elapsed_s", "max_duration_s") if k in b}
    out["real_profile_modified"] = bool(outcome.real_profile_modified)
    return out


def render_review(outcome: ProbeOutcome) -> str:
    """Texto compartible: SOLO el resumen saneado (JSON indentado)."""
    return json.dumps(review_summary(outcome), ensure_ascii=False, indent=2)


def render_abort(code: Optional[str]) -> str:
    """Abortos previos a la red (SafetyError) y similares: solo el código, nunca el mensaje."""
    return f"ABORT [{safe_code(code)}]"


def render_unexpected(exc: BaseException) -> str:
    """Excepción inesperada: solo el NOMBRE del tipo; el mensaje (que puede llevar datos) nunca se imprime."""
    name = type(exc).__name__
    return f"ABORT [UNEXPECTED]: {name if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,60}', name) else 'Exception'}"
