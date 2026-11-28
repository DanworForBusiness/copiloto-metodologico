"""Evaluaciones del Funnel Operativo registradas en el historial de una solicitud (Operaciones → Solicitudes).

El Funnel es una SIMULACIÓN que asiste la decisión: registrar su resultado no asigna, no cambia estados, no deriva, no notifica y no es una
evaluación metodológica del Copiloto (nada va a case_library ni a la telemetría de metodología).

Persistencia: la existente del Hub. Un YAML por evaluación bajo `feedback_log/solicitudes_funnel/<solicitud>/` (directorio YA persistido:
`core.remote_store.write_through` + respaldo y restore), sin infraestructura nueva y sin tocar versiones de SharePoint.
- Snapshot: preguntas, respuestas y resultado TAL COMO estaban al completar (versión del árbol incluida). El historial nunca se reconstruye
  con una versión futura del YAML.
- Idempotente: la identidad de una evaluación es su run + su inicio + su camino. Un refresco, un reintento HTTP o un re-render de la misma
  evaluación completada no duplica; una evaluación nueva (otra corrida, «Nueva evaluación» o un camino distinto) sí genera otro movimiento.
- Eliminación lógica (`deleted_at` + autor): se reescribe el MISMO registro, así el descarte sobrevive a reinicios/restore aunque un borrado
  remoto fallara, y deja rastro. Solo aplica a estos registros del Hub: nunca a movimientos de SharePoint.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import yaml

from config import FEEDBACK_LOG_DIR
from core import remote_store
from webapp.services.busqueda_repository import BUSINESS_TZ

ROOT_NAME = "solicitudes_funnel"
MOVEMENT = "Funnel Operativo"


def _root() -> Path:
    return FEEDBACK_LOG_DIR / ROOT_NAME


def _item_dir(item_id: str) -> Path:
    if not (isinstance(item_id, str) and item_id.isascii() and item_id.isdecimal()):
        raise ValueError("Solicitud inválida")
    return _root() / item_id


def _path(item_id: str, evaluation_id: str) -> Path:
    if not (isinstance(evaluation_id, str) and len(evaluation_id) == 20 and all(c in "0123456789abcdef" for c in evaluation_id)):
        raise ValueError("Evaluación inválida")
    return _item_dir(item_id) / f"{evaluation_id}.yaml"


def snapshot(run) -> dict:
    """Recorrido efectivo del run: preguntas (texto vigente al completar), respuestas en orden y resultado."""
    nodes = run.tree.get("nodes") or {}
    steps = [{"question": str((nodes.get(node) or {}).get("question") or node), "answer": str(answer)} for node, answer in run.history]
    return {"tree_id": str(run.tree.get("id") or ""), "tree_version": str(run.tree.get("version") or ""),
            "steps": steps, "result": str((run.leaf or {}).get("decision") or "")}


def evaluation_id(run) -> str:
    path = "|".join(f"{node}={answer}" for node, answer in run.history)
    return hashlib.sha256(f"{run.id}|{run.started_at}|{path}".encode("utf-8")).hexdigest()[:20]


def record(run, *, item_id: str, actor: dict) -> bool:
    """Registra la evaluación COMPLETADA (idempotente). Devuelve True si agregó un movimiento nuevo."""
    if run.leaf is None:
        return False                                                            # recorridos incompletos o abandonados: nunca
    eid = evaluation_id(run)
    path = _path(item_id, eid)
    if path.exists():
        return False
    payload = {"type": "solicitudes_funnel_evaluation", "id": eid, "item_id": item_id,
              "actor": str(actor.get("name") or actor.get("email") or ""), "actor_email": str(actor.get("email") or ""),
              "started_at": run.started_at, "completed_at": datetime.now(timezone.utc).isoformat(), **snapshot(run)}
    path.parent.mkdir(parents=True, exist_ok=True)
    remote_store.write_through(path, yaml.safe_dump(payload, allow_unicode=True, sort_keys=False))
    return True


def evaluations(item_id: str) -> list[dict]:
    folder = _item_dir(item_id)
    found = []
    for path in sorted(folder.glob("*.yaml")) if folder.is_dir() else ():
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            continue
        if data.get("type") == "solicitudes_funnel_evaluation" and not data.get("deleted_at") and str(data.get("item_id")) == item_id:
            found.append(data)
    return found


def discard(item_id: str, evaluation_id_: str, actor: dict) -> str | None:
    """Descarta UNA simulación (eliminación lógica durable): "deleted", "already" (otro usuario o pestaña ya la descartó: idempotente)
    o None si no existe para esta solicitud."""
    path = _path(item_id, evaluation_id_)
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if data.get("type") != "solicitudes_funnel_evaluation" or str(data.get("item_id")) != item_id:
        return None
    if data.get("deleted_at"):
        return "already"
    data.update(deleted_at=datetime.now(timezone.utc).isoformat(), deleted_by=str(actor.get("email") or actor.get("name") or ""))
    remote_store.write_through(path, yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
    return "deleted"


def history_rows(item_id: str) -> list[dict]:
    """Movimientos del historial (mismo contrato de fila que `solicitudes_journal`), con su snapshot para el detalle expandible."""
    rows = []
    for data in evaluations(item_id):
        try:
            done = datetime.fromisoformat(str(data["completed_at"]))
            started = datetime.fromisoformat(str(data["started_at"]))
        except (KeyError, ValueError):
            continue
        rows.append({"id": f"funnel:{data['id']}", "movement": MOVEMENT, "date": done.astimezone(BUSINESS_TZ).strftime("%d/%m/%Y %H:%M"),
                     "at": done.isoformat(), "actor": data.get("actor") or "—", "text": f"Resultado: {data.get('result') or '—'}",
                     "source": "Hub", "funnel": {"id": data["id"], "steps": data.get("steps") or [], "result": data.get("result") or "",
                                                 "started": started.astimezone(BUSINESS_TZ).strftime("%d/%m/%Y %H:%M")}})
    return rows
