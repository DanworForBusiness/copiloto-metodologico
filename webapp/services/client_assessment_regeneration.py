"""Regeneración CONSOLIDADA de los Assessments desactualizados de un cliente (Informe de Cliente → «Regenerar N Assessments»).

No es otro motor: recorre los modelos ÚNICOS elegibles del consolidado canónico (``client_assessment_portfolio``) y llama, uno por uno, a la MISMA
regeneración individual (``assessment_service.generate``: single-flight por (cliente, AM), idempotente sobre la vigencia, sin Datasphere, y que ante
una falla conserva el perfil anterior). Corre en un hilo registrado en ``operation_registry`` (el mismo de diagnóstico y publicación): el resultado no
depende de la conexión HTTP; el navegador sigue el avance con ``/copilot/operations/{run_id}``.

Reglas:
  · Elegibles = estados canónicos que el informe individual ofrece REGENERAR (``STALE`` e ``INCOMPATIBLE_SCHEMA``). Nunca CURRENT (no se recalcula),
    ni «sin assessment» / fallidos (esos se generan o reintentan desde su detalle).
  · Varios flujos del mismo AM = UNA regeneración (la identidad gobernada es (cliente, AM)).
  · UNA operación consolidada por cliente a la vez (doble clic / otra pestaña / otro usuario): la segunda no arranca nada.
  · Bloqueada durante una activación de ruleset en transición (``judgment_activation``): esas escrituras gobernadas no se admiten. Se vuelve a
    verificar antes de cada modelo; si la transición empieza a mitad de camino, lo pendiente queda omitido.
  · El nivel metodológico resultante es el que produce la metodología/evidencia vigente: esta operación no lo toca.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from webapp.services import assessment_service as svc
from webapp.services import operation_registry

log = logging.getLogger(__name__)

KIND = "regeneration"
ELIGIBLE_STATES = (svc.STALE, svc.INCOMPATIBLE_SCHEMA)
RESULT_UPDATED, RESULT_SKIPPED, RESULT_FAILED = "updated", "skipped", "failed"
BLOCKED_TRANSITION, ALREADY_RUNNING, NOTHING_TO_DO, STARTED = "RULESET_TRANSITION_IN_PROGRESS", "ALREADY_RUNNING", "NOTHING_TO_DO", "STARTED"
TITLE = "Regeneración de Assessments"
MONITOR_MAX_S = 6 * 3600
_TIMEOUT_EVENT = {"t": "done", "state": "UNKNOWN", "error_code": "MONITOR_TIMEOUT", "title": TITLE,
                  "message": "Se dejó de seguir la regeneración: abrí el Informe de Cliente para ver el estado actual de cada Assessment."}

_lock = threading.Lock()
_running: dict[str, tuple[str, str]] = {}                 # cliente (minúsculas) -> (run_id, owner)


@dataclass(frozen=True)
class Target:
    entry: Any
    terminal_am: str
    state: str
    entries: tuple[str, ...]                               # flujos del Catálogo que comparten este AM


def transition_blocked() -> bool:
    from webapp.services import judgment_activation as ja
    return ja.in_transition()


def eligible_targets(snap: dict, entries_by_id: dict[str, Any]) -> list[Target]:
    """Modelos ÚNICOS elegibles del snapshot canónico del consolidado (en el orden del Informe)."""
    by_am: dict[str, dict] = {}
    for r in snap.get("assets") or ():
        am = r.get("asset_id")
        if r.get("state") not in ELIGIBLE_STATES or not am or r.get("entry_id") not in entries_by_id:
            continue
        slot = by_am.setdefault(am, {"first": r, "entries": []})
        slot["entries"].append(r["entry_id"])
    return [Target(entries_by_id[v["first"]["entry_id"]], am, v["first"]["state"], tuple(v["entries"])) for am, v in by_am.items()]


def summary_view(snap: dict) -> dict:
    """Bloque de la advertencia del Informe de Cliente. Solo cuenta modelos únicos; ``visible`` solo con elegibles."""
    seen: dict[str, int] = {}
    for r in snap.get("assets") or ():
        if r.get("state") in ELIGIBLE_STATES and r.get("asset_id"):
            seen[r["asset_id"]] = seen.get(r["asset_id"], 0) + 1
    n = len(seen)
    client = str(snap.get("client") or "")
    with _lock:
        running = _running.get(client.lower())
    return {"visible": n > 0, "count": n, "flows": sum(seen.values()), "blocked": transition_blocked(), "running": bool(running),
            "label": f"Regenerar {n} {'Assessment' if n == 1 else 'Assessments'}"}


def _finish(client: str, run_id: str) -> None:
    with _lock:
        if _running.get(client.lower(), ("", ""))[0] == run_id:
            _running.pop(client.lower(), None)


def _log_model_result(owner: str, client: str, am: str, result: str, run_id: str) -> None:
    """Trazabilidad por MODELO (Métricas de Uso → Catálogos y Assessments): resultado de cada regeneración de la operación consolidada."""
    try:
        from webapp.services.activity_context import AREA_CATALOG, SCOPE_COPILOT
        from webapp.services.activity_service import log_activity
        log_activity("assessment_regenerated", user={"sub": owner, "email": owner}, scope=SCOPE_COPILOT, area=AREA_CATALOG,
                     client=client, model=am, result=result, origin="client_bulk", operation_id=run_id)
    except Exception as exc:                                              # noqa: BLE001 — la telemetría nunca altera la regeneración
        log.warning("client_assessment_regeneration activity log failed exc=%s", type(exc).__name__)


def start(client: str, targets: list[Target], owner: str, *, pack: str, result_url: str | None,
          generate: Callable[[Any], svc.AssessmentStatus] | None = None, status_of: Callable[[Any], svc.AssessmentStatus] | None = None,
          run_async: bool = True) -> dict:
    """Arranca (o reanuda, si es del mismo usuario) la operación consolidada del cliente. Devuelve ``{"outcome", "run_id"?, "total"?}``."""
    generate = generate or svc.generate
    status_of = status_of or svc.status_for_entry
    key = client.lower()
    with _lock:
        cur = _running.get(key)
        if cur is not None:
            run_id, who = cur
            return {"outcome": ALREADY_RUNNING, "run_id": run_id if who == owner.strip().lower() else None}
        if transition_blocked():
            return {"outcome": BLOCKED_TRANSITION}
        if not targets:
            return {"outcome": NOTHING_TO_DO}
        run_id = operation_registry.start_operation(owner, meta={"kind": KIND, "client": client, "pack": pack, "root_object": f"{len(targets)} modelos"},
                                                    max_s=MONITOR_MAX_S, timeout_event=_TIMEOUT_EVENT, accept_late_done=True)
        _running[key] = (run_id, owner.strip().lower())

    def work() -> None:
        counts = {RESULT_UPDATED: 0, RESULT_SKIPPED: 0, RESULT_FAILED: 0}
        total = len(targets)
        try:
            operation_registry.publish_event(run_id, {"t": "line", "v": f"Regenerando {total} {'modelo' if total == 1 else 'modelos'} de {client} con la metodología y la evidencia vigentes."})
            for i, t in enumerate(targets, 1):
                result, detail = RESULT_SKIPPED, ""
                try:
                    if transition_blocked():
                        detail = "activación de ruleset en curso"
                    else:
                        before = status_of(t.entry)
                        if before.state not in ELIGIBLE_STATES:                        # se actualizó por otra vía mientras tanto: nada que hacer
                            detail = "ya no está desactualizado"
                        else:
                            after = generate(t.entry)
                            if after.state == svc.CURRENT:
                                result = RESULT_UPDATED
                            else:
                                result, detail = RESULT_FAILED, (after.reason or after.error_code or after.state)
                except Exception as exc:                                             # noqa: BLE001 — un modelo que falla no detiene el resto
                    log.exception("client_assessment_regeneration: falló %s/%s", client, t.terminal_am)
                    result, detail = RESULT_FAILED, type(exc).__name__
                counts[result] += 1
                operation_registry.publish_event(run_id, {"t": "progress", "done": i, "total": total, "am": t.terminal_am, "result": result, "detail": str(detail)[:200],
                                                          "counts": dict(counts)})
                operation_registry.publish_event(run_id, {"t": "line", "v": f"[{i}/{total}] {t.terminal_am}: {result}" + (f" ({detail})" if detail else "")})
                _log_model_result(owner, client, t.terminal_am, result, run_id)
            state = "SUCCESS" if not counts[RESULT_FAILED] else ("FAILED" if not counts[RESULT_UPDATED] else "PARTIAL")
            msg = f"{counts[RESULT_UPDATED]} actualizados · {counts[RESULT_SKIPPED]} omitidos · {counts[RESULT_FAILED]} fallidos."
            operation_registry.publish_event(run_id, {"t": "done", "state": state, "title": TITLE, "message": msg, "summary": {**counts, "total": total},
                                                      "result_url": result_url})
        except Exception:                                                            # noqa: BLE001 — nunca dejar la operación sin evento terminal
            log.exception("client_assessment_regeneration: falla inesperada")
            operation_registry.publish_event(run_id, {"t": "done", "state": "FAILED", "title": TITLE, "error_code": "REGENERATION_ERROR",
                                                      "message": "La regeneración se interrumpió. Los Assessments ya regenerados se conservan.", "summary": {**counts, "total": total},
                                                      "result_url": result_url})
        finally:
            _finish(client, run_id)

    if run_async:
        threading.Thread(target=work, name="client-assessment-regeneration", daemon=True).start()
    else:
        work()
    return {"outcome": STARTED, "run_id": run_id, "total": len(targets)}
