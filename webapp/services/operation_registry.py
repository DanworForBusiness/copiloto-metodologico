"""
operation_registry.py — registro EN MEMORIA de operaciones largas del Copiloto (diagnóstico ad hoc y publicación del Catálogo).

Mudado desde ``datasphere_assessment_service`` (donde nació para desacoplar la extracción de la conexión HTTP) para que la publicación use
el MISMO mecanismo en lugar de su cola propia. La operación corre en su hilo y NO depende de la respuesta HTTP que la inició; acá se
registran sus eventos ya saneados (start/line/phase/done) para que el navegador:
  · reciba un latido mientras no haya salida,
  · retome el avance y el resultado si la conexión se corta (o si el usuario navegó / recargó), SIN reiniciar nada, y
  · liste sus operaciones propias al entrar al Copiloto (aviso global), sin depender de un ``run_id`` guardado en el navegador.

Contrato:
  · SOLO eventos ya saneados y metadatos NO sensibles (tipo, cliente, Space, objeto, módulo). Nunca Client Secrets, tokens ni credenciales.
  · La operación es del usuario que la inició: toda lectura exige ``owner``; la de otro usuario es indistinguible de una inexistente.
  · Vence a las ``OPERATION_TTL_S`` (24 h, vida del resultado ad hoc). Sin almacenamiento persistente (por diseño).
  · ``max_s``: plazo máximo de SEGUIMIENTO. Vencido, la operación se cierra con el ``timeout_event`` de su tipo (terminal y visible). No
    cancela el proceso. Con ``accept_late_done`` el resultado REAL que llegue después reemplaza ese cierre (el monitor no inventa resultados).

DEPENDENCIA DE DESPLIEGUE — UN SOLO PROCESO (réplica única + un worker de uvicorn): ver el comentario en datasphere_assessment_service.
Un reinicio o deploy pierde el registro: la operación desaparece (nunca queda «en curso» para siempre).
"""
from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field

OPERATION_TTL_S = 24 * 3600
OPERATION_MAX_S = 3 * 3600
_OPERATION_MAX_EVENTS = 20000
# Metadatos permitidos (whitelist): cualquier otra clave se descarta, así nunca entra una credencial por descuido.
META_KEYS = ("kind", "stem", "pack", "client", "space", "root_object")


@dataclass
class _Operation:
    owner: str
    created_at: float
    events: list = field(default_factory=list)
    finished_at: float | None = None
    dropped: int = 0
    meta: dict = field(default_factory=dict)
    max_s: float = OPERATION_MAX_S
    max_events: int = _OPERATION_MAX_EVENTS
    timeout_event: dict | None = None
    accept_late_done: bool = False
    timed_out: bool = False


_OPERATIONS: dict[str, _Operation] = {}
_OPERATIONS_COND = threading.Condition()


def _purge(now: float) -> None:
    for key in [k for k, op in _OPERATIONS.items() if now - op.created_at > OPERATION_TTL_S]:
        del _OPERATIONS[key]


def start_operation(owner: str, *, now: float | None = None, meta: dict | None = None, max_s: float | None = None,
                    timeout_event: dict | None = None, accept_late_done: bool = False, max_events: int | None = None) -> str:
    now = time.time() if now is None else now
    run_id = uuid.uuid4().hex
    safe_meta = {k: str(v) for k, v in (meta or {}).items() if k in META_KEYS and v is not None}
    with _OPERATIONS_COND:
        _purge(now)
        _OPERATIONS[run_id] = _Operation(owner=(owner or "").strip().lower(), created_at=now, meta=safe_meta,
                                         max_s=OPERATION_MAX_S if max_s is None else float(max_s),
                                         timeout_event=dict(timeout_event) if timeout_event else None, accept_late_done=accept_late_done,
                                         max_events=_OPERATION_MAX_EVENTS if max_events is None else int(max_events))
    return run_id


def publish_event(run_id: str, event: dict) -> None:
    """Agrega un evento ya saneado. ``done`` cierra la operación. Un exceso de líneas se descarta (contado), nunca el ``done``.
    Cerrada por el monitor (``timed_out``) y con ``accept_late_done``, el ``done`` REAL que llega después se agrega y pasa a ser el resultado."""
    with _OPERATIONS_COND:
        op = _OPERATIONS.get(run_id)
        if op is None:
            return
        if op.finished_at is not None:
            if op.timed_out and op.accept_late_done and event.get("t") == "done":
                op.events.append(event)
                op.timed_out = False
                op.finished_at = time.time()
                _OPERATIONS_COND.notify_all()
            return
        if event.get("t") == "line" and len(op.events) >= op.max_events:
            op.dropped += 1
            return
        op.events.append(event)
        if event.get("t") == "done":
            op.finished_at = time.time()
        _OPERATIONS_COND.notify_all()


def _close_if_overdue(op: _Operation, now: float) -> None:
    """Bajo ``_OPERATIONS_COND``: cierra con el ``timeout_event`` de su tipo la operación que superó su plazo de seguimiento sin terminar."""
    if op.finished_at is None and op.timeout_event and now - op.created_at > op.max_s:
        op.events.append(dict(op.timeout_event))
        op.finished_at = now
        op.timed_out = True
        _OPERATIONS_COND.notify_all()


def wait_events(run_id: str, cursor: int, timeout: float) -> tuple[list[dict], int, bool]:
    """Eventos desde ``cursor`` (espera hasta ``timeout`` si no hay nuevos): ``(eventos, cursor nuevo, terminada)``."""
    with _OPERATIONS_COND:
        op = _OPERATIONS.get(run_id)
        if op is None:
            return [], cursor, True
        _close_if_overdue(op, time.time())
        if cursor >= len(op.events) and op.finished_at is None:
            _OPERATIONS_COND.wait(timeout)
            op = _OPERATIONS.get(run_id)
            if op is None:
                return [], cursor, True
            _close_if_overdue(op, time.time())
        new = op.events[cursor:]
        return list(new), cursor + len(new), op.finished_at is not None


def _own(run_id: str, owner: str) -> _Operation | None:
    """Bajo ``_OPERATIONS_COND``: la operación PROPIA y vigente, o None (ajena, inexistente o vencida: indistinguibles)."""
    op = _OPERATIONS.get(str(run_id or ""))
    owner = (owner or "").strip().lower()
    if op is None or not owner or op.owner != owner or time.time() - op.created_at > OPERATION_TTL_S:
        return None
    return op


def operation_events(run_id: str, owner: str, since: int = 0) -> dict | None:
    """Recuperación tras un corte: eventos desde ``since`` de una operación PROPIA. La de otro usuario o una vencida es ``None`` (404)."""
    with _OPERATIONS_COND:
        op = _own(run_id, owner)
        if op is None:
            return None
        _close_if_overdue(op, time.time())
        since = max(0, int(since))
        new = op.events[since:]
        return {"events": list(new), "next": since + len(new), "finished": op.finished_at is not None}


def _summary(run_id: str, op: _Operation) -> dict:
    done = next((e for e in reversed(op.events) if e.get("t") == "done"), None)
    return {"run_id": run_id, "meta": dict(op.meta), "created_at": op.created_at, "finished": op.finished_at is not None,
            "state": (str(done.get("state") or "") if done else "RUNNING"),
            "error_code": (done or {}).get("error_code"), "title": (done or {}).get("title"), "message": (done or {}).get("message"),
            "result_url": (done or {}).get("result_url")}


def list_operations(owner: str) -> list[dict]:
    """Operaciones PROPIAS vigentes (más recientes primero), solo con metadatos no sensibles y el resultado final si terminaron."""
    now = time.time()
    with _OPERATIONS_COND:
        _purge(now)
        out = []
        for run_id, op in list(_OPERATIONS.items()):
            if _own(run_id, owner) is None:
                continue
            _close_if_overdue(op, now)
            out.append(_summary(run_id, op))
    out.sort(key=lambda r: r["created_at"], reverse=True)
    return out


def operation_detail(run_id: str, owner: str) -> dict | None:
    """Resumen + líneas del log (ya saneadas) de una operación PROPIA, para «Ver ejecución». None si es ajena/inexistente/vencida."""
    with _OPERATIONS_COND:
        op = _own(run_id, owner)
        if op is None:
            return None
        _close_if_overdue(op, time.time())
        out = _summary(run_id, op)
        out["log_lines"] = [str(e.get("v") or "") for e in op.events if e.get("t") == "line"]
        out["next"] = len(op.events)
        out["dropped"] = op.dropped
        return out
