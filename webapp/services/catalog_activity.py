"""catalog_activity.py — contenedor «Catálogos y Assessments» de Métricas de Uso (lectura sobre `hub-activity/v2`, sin almacenamiento propio).

Trazabilidad INDIVIDUAL de la actividad sobre modelos: consultas desde Herramientas de Decisión (Catálogo), Assessments ad hoc desde Herramientas
de Diagnóstico, regeneraciones y el circuito de actualización gobernada. Recibe los eventos YA filtrados por el tablero (período, usuario, ámbito
y visibilidad por vertical) y agrega filtros propios: cliente, modelo, herramienta y tipo de operación.

Reglas:
  · Los indicadores NO mezclan estados: oportunidad mostrada ≠ solicitud registrada ≠ notificación emitida ≠ revisión ≠ dry-run (y, con PR-2,
    ≠ aprobación ≠ actualización aplicada). Cada uno cuenta SU evento.
  · Un Assessment ad hoc se reconstruye por `run_id`: inicio sin fin = interrumpido.
  · Historia del modelo: con cliente + modelo elegidos, los eventos se listan en orden cronológico.
  · Este contenedor NO es el registro autoritativo: enlaza a la solicitud (`/copilot/governed-updates/<id>`), que re-verifica permisos.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

TOOL_DECISION, TOOL_DIAGNOSTIC, TOOL_GOVERNED, TOOL_MAINTENANCE = "decision", "diagnostic", "governed", "maintenance"
TOOL_LABEL = {TOOL_DECISION: "Herramientas de Decisión", TOOL_DIAGNOSTIC: "Herramientas de Diagnóstico",
              TOOL_GOVERNED: "Actualización gobernada", TOOL_MAINTENANCE: "Mantenimiento del Catálogo"}

# evento → (herramienta, operación). La operación de una consulta depende de la vista (modelo / linaje / Assessment).
EVENTS: dict[str, tuple[str, str]] = {
    "catalog_model_viewed": (TOOL_DECISION, "consulta"),
    "catalog_assessment_generate": (TOOL_DECISION, "informe_assessment"),
    "datasphere_assessment_run_started": (TOOL_DIAGNOSTIC, "assessment_inicio"),
    "datasphere_assessment_run": (TOOL_DIAGNOSTIC, "assessment_resultado"),
    "governed_update.opportunity_shown": (TOOL_GOVERNED, "oportunidad_mostrada"),
    "governed_update.requested": (TOOL_GOVERNED, "solicitud"),
    "governed_update.notified": (TOOL_GOVERNED, "notificacion"),
    "governed_update.reviewed": (TOOL_GOVERNED, "revision"),
    "governed_update.dry_run": (TOOL_GOVERNED, "dry_run"),
    "catalog_client_assessment_regenerate": (TOOL_MAINTENANCE, "regeneracion_cliente"),
    "assessment_regenerated": (TOOL_MAINTENANCE, "regeneracion_modelo"),
    "datasphere_publication_run": (TOOL_MAINTENANCE, "publicacion"),
}
OP_LABEL = {
    "consulta_model": "Consulta del modelo", "consulta_lineage": "Consulta de linaje", "consulta_assessment": "Consulta del Assessment",
    "consulta": "Consulta", "informe_assessment": "Informe de Assessment (Catálogo)",
    "assessment_inicio": "Assessment ad hoc — inicio", "assessment_resultado": "Assessment ad hoc — resultado",
    "oportunidad_mostrada": "Oportunidad mostrada", "solicitud": "Solicitud registrada", "notificacion": "Notificación emitida",
    "revision": "Revisión", "dry_run": "Dry-run gobernado",
    "regeneracion_cliente": "Regeneración (cliente)", "regeneracion_modelo": "Regeneración (modelo)", "publicacion": "Publicación",
}
MAX_ROWS = 200
FILTER_KEYS = ("cat_client", "cat_model", "cat_tool", "cat_op")


def _attrs(ev: Mapping[str, Any]) -> dict:
    a = ev.get("payload") if isinstance(ev.get("payload"), dict) else ev.get("attrs")
    return a if isinstance(a, dict) else {}


def _op(ev: Mapping[str, Any]) -> str:
    _tool, op = EVENTS[ev["event"]]
    if op == "consulta":
        view = str(_attrs(ev).get("view") or "")
        return f"consulta_{view}" if view in ("model", "lineage", "assessment") else op
    return op


def _client(a: dict) -> str:
    return str(a.get("client") or a.get("cliente") or "").strip()


def _model(a: dict) -> str:
    return str(a.get("model") or a.get("root_object") or "").strip()


def _detail(event: str, a: dict) -> str:
    if event == "datasphere_assessment_run":
        parts = [str(a.get("state") or "")]
        if a.get("level"):
            parts.append(f"nivel provisional {a['level']}")
        if a.get("error_code"):
            parts.append(str(a["error_code"]))
        return " · ".join(p for p in parts if p)
    if event == "governed_update.opportunity_shown":
        return f"{a.get('vigente_level') or 'ND'} → {a.get('candidato_level') or 'ND'}"
    if event == "governed_update.requested":
        return ("nueva" if a.get("created") else "ya existía") + (f" · procedencia {a['provenance']}" if a.get("provenance") else "")
    if event == "governed_update.notified":
        return str(a.get("dispatch") or "")
    if event == "governed_update.reviewed":
        return " · ".join(str(x) for x in (a.get("role"), a.get("status")) if x)
    if event == "governed_update.dry_run":
        bits = [str(a.get("state") or "")]
        if a.get("projected_level"):
            bits.append(f"proyectado {a['projected_level']}")
        if a.get("plan_digest"):
            bits.append(f"digest {a['plan_digest']}")
        return " · ".join(bits)
    if event == "assessment_regenerated":
        return str(a.get("result") or "")
    if event in ("catalog_client_assessment_regenerate", "catalog_assessment_generate", "datasphere_publication_run"):
        return str(a.get("outcome") or a.get("state") or "")
    return ""


def build(events: list[dict], params: Mapping[str, str], *, pack: str = "") -> dict:
    """Vista del contenedor. ``events``: eventos normalizados del tablero (ya filtrados por período/usuario/visibilidad)."""
    sel = {k: str(params.get(k) or "").strip() for k in FILTER_KEYS}
    rows_all: list[dict] = []
    for ev in events:
        if ev.get("event") not in EVENTS:
            continue
        a = _attrs(ev)
        tool, op = EVENTS[ev["event"]][0], _op(ev)
        rows_all.append({"ts": str(ev.get("ts_utc") or ""), "user": str(ev.get("user") or ""), "event": ev["event"], "tool": tool, "op": op,
                         "tool_label": TOOL_LABEL[tool], "op_label": OP_LABEL.get(op, op), "client": _client(a), "model": _model(a),
                         "detail": _detail(ev["event"], a), "request_id": str(a.get("request_id") or ""), "run_id": str(a.get("run_id") or ""),
                         "outcome": str(a.get("outcome") or "")})
    clients = sorted({r["client"] for r in rows_all if r["client"]}, key=str.lower)
    models = sorted({r["model"] for r in rows_all if r["model"] and (not sel["cat_client"] or r["client"].lower() == sel["cat_client"].lower())}, key=str.lower)
    rows = [r for r in rows_all
            if (not sel["cat_client"] or r["client"].lower() == sel["cat_client"].lower())
            and (not sel["cat_model"] or r["model"].lower() == sel["cat_model"].lower())
            and (not sel["cat_tool"] or r["tool"] == sel["cat_tool"])
            and (not sel["cat_op"] or r["op"] == sel["cat_op"])]
    by_event = Counter(r["event"] for r in rows)
    consult = [r for r in rows if r["event"] == "catalog_model_viewed"]
    starts = {r["run_id"] for r in rows if r["event"] == "datasphere_assessment_run_started" and r["run_id"]}
    ends = [r for r in rows if r["event"] == "datasphere_assessment_run"]
    ended = {r["run_id"] for r in ends if r["run_id"]}
    end_outcomes = Counter(r["outcome"] or "unknown" for r in ends)
    notified = [r for r in rows if r["event"] == "governed_update.notified"]
    not_sent = [r for r in notified if r["detail"] in ("disabled", "error", "")]           # intención sin envío: NO cuenta como emitida
    kpis = [
        {"key": "consultas", "label": "Consultas de modelos", "value": len(consult),
         "hint": " · ".join(f"{OP_LABEL[o]} {n}" for o, n in sorted(Counter(r['op'] for r in consult).items()) if o in OP_LABEL)},
        {"key": "assessments", "label": "Assessments ad hoc", "value": len(ends),
         "hint": " · ".join(filter(None, [f"exitosos {end_outcomes.get('success', 0)}", f"fallidos {end_outcomes.get('failed', 0) + end_outcomes.get('rejected', 0)}",
                                          f"interrumpidos {len(starts - ended)}" if starts - ended else ""]))},
        {"key": "oportunidades", "label": "Oportunidades mostradas", "value": by_event["governed_update.opportunity_shown"], "hint": "vistas, no solicitudes"},
        {"key": "solicitudes", "label": "Solicitudes registradas", "value": sum(1 for r in rows if r["event"] == "governed_update.requested" and r["detail"].startswith("nueva")),
         "hint": f"reintentos sobre una existente {sum(1 for r in rows if r['event'] == 'governed_update.requested' and not r['detail'].startswith('nueva'))}"},
        {"key": "notificaciones", "label": "Notificaciones emitidas", "value": len(notified) - len(not_sent),
         "hint": f"no emitidas (apagadas o con error) {len(not_sent)}" if not_sent else "entrega: ver el estado en la solicitud"},
        {"key": "revisiones", "label": "Revisiones", "value": by_event["governed_update.reviewed"], "hint": f"dry-runs {by_event['governed_update.dry_run']}"},
    ]
    history = bool(sel["cat_client"] and sel["cat_model"])
    rows_sorted = sorted(rows, key=lambda r: r["ts"], reverse=not history)
    shown = rows_sorted[:MAX_ROWS] if not history else rows_sorted[-MAX_ROWS:]
    for r in shown:
        r["request_url"] = f"/copilot/governed-updates/{r['request_id']}" + (f"?pack={pack}" if pack else "") if r["request_id"] else ""
    tools = [{"key": k, "label": v} for k, v in TOOL_LABEL.items() if any(r["tool"] == k for r in rows_all)]
    ops = [{"key": k, "label": OP_LABEL.get(k, k)} for k in sorted({r["op"] for r in rows_all if not sel["cat_tool"] or r["tool"] == sel["cat_tool"]})]
    return {"present": bool(rows_all), "selected": sel, "clients": clients, "models": models, "tools": tools, "ops": ops, "kpis": kpis,
            "rows": shown, "total": len(rows), "truncated": len(rows) > len(shown), "history": history}
