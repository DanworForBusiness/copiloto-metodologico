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
              space_environment_map: dict, expected_spaces: list, max_attempts: int = 4,
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
