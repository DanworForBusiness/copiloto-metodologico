"""
assessment_presenter.py — vista estructurada (view-model) del assessment para Jinja.

Toma el JSON CANÓNICO del assessment (``modeling_assessment.assessment/v1``) y arma los datos que
consume ``partials/catalog_assessment_report.html`` y la cabecera del detalle del flujo (``catalog_flow_content.html``). NO emite HTML,
NO relee ni reinterpreta hechos, NO decide reglas: solo ordena el documento en secciones y traduce
ENUMS a español con la API PÚBLICA de presentación del paquete (``modeling_assessment.presentation``), la
misma fuente de vocabulario y formato que usa el Markdown, de modo que UI y exportación no puedan divergir.
Solo se importan nombres públicos del paquete (invariante R-I2b).

Prohibido aquí (y verificado por tests): renderizar Markdown, scrapear el Markdown exportado, importar símbolos
privados del paquete o duplicar lógica de negocio del assessment. El texto de cada ocurrencia sale de
``presentation.occurrence_text(..., plain=True)`` (el MISMO formateador de una línea por ocurrencia que usa la
exportación, sin delimitadores de código inline): es TEXTO PLANO que Jinja escapa (autoescape), nunca HTML
marcado como seguro.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from tools.catalog.modeling_assessment import presentation as P

from webapp.services import assessment_service as svc
from webapp.services import catalog_reads

# ── etiquetas de ESTADO DE VIGENCIA (producto del Catálogo, no del motor) ──
STATE_LABELS = {
    svc.CURRENT: "Assessment vigente",
    svc.STALE: "Assessment desactualizado",
    svc.NOT_YET_ASSESSED: "Sin assessment",
    svc.NOT_APPLICABLE: "No aplica",
    svc.FAILED: "Assessment no disponible",
    svc.INCOMPATIBLE_SCHEMA: "Informe no compatible",
}
STATE_NOTES = {
    svc.STALE: "El modelo cambió desde la última evaluación.",
}
# Tono del badge de vigencia: reusa las variantes del badge del veredicto legacy (`catalog-card__verdict--*`) para no
# inventar un segundo lenguaje visual. Sin tono = badge neutro (sin assessment / no aplica).
STATE_TONES = {
    svc.CURRENT: "plena", svc.STALE: "buena", svc.FAILED: "parcial", svc.INCOMPATIBLE_SCHEMA: "parcial",
}
# Acción de la CABECERA del detalle del flujo por estado: (clave, método, rótulo). En STALE ofrece "Ver informe";
# la acción de regenerar vive dentro del informe (REPORT_ACTIONS). NOT_APPLICABLE no ofrece ninguna.
ACTIONS = {
    svc.CURRENT: ("view", "get", "Ver Assessment"),
    svc.STALE: ("view", "get", "Ver Assessment"),
    svc.NOT_YET_ASSESSED: ("generate", "post", "Generar informe"),
    svc.FAILED: ("retry", "post", "Reintentar"),
    svc.INCOMPATIBLE_SCHEMA: ("regenerate", "post", "Regenerar assessment"),
}
# Acción de generación DENTRO del informe: (clave, rótulo). CURRENT no ofrece ninguna (nada que regenerar).
REPORT_ACTIONS = {
    svc.STALE: ("regenerate", "Regenerar assessment"),
    svc.NOT_YET_ASSESSED: ("generate", "Generar informe"),
    svc.FAILED: ("retry", "Reintentar"),
    svc.INCOMPATIBLE_SCHEMA: ("regenerate", "Regenerar assessment"),
}
MAX_FOCUS_NAMES = 200
# Vertical del Hub al que pertenece el Catálogo de modelado (eyebrow del informe).
MODULE_LABEL = "Modelado & Desarrollo"
# Títulos de las secciones de primer nivel del informe (macro-dimensiones). Misma jerarquía que los `## …` del Markdown gobernado
# (Arquitectura / Modelado, Convención de nombres, Fuente / Extracción), con el rotulado de la UI.
SECTION_TITLES = {"ARCHITECTURE_MODELING": "Arquitectura & Modelado", "NAMING": "Convención de nombres", "SOURCE_EXTRACTION": "Fuente & Extracción"}
# El informe ya establece qué es el objeto: el prefijo genérico del label de la vista se omite SOLO en el título del informe.
_TITLE_PREFIX = re.compile(r"^\s*Modelo anal[ií]tico\s*[-–—:]\s*", re.I)


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return P.plural(n, singular, plural)


# ═══════════════════════════ URLs ═══════════════════════════
def assessment_url(entry: Any, pack: str, **extra: str) -> str:
    q = {"flow": entry.entry_id, "pack": pack, **extra}
    return "/catalog/flow/assessment?" + urlencode(q)


def flow_url(entry: Any, pack: str, focus: list[str] | None = None) -> str:
    """Detalle del flujo (con foco opcional en el linaje) — contrato ``/catalog/flow?flow=<id>&focus=A,B``."""
    qs = urlencode({"cliente": entry.cliente, "dominio": entry.dominio, "flow": entry.entry_id, "pack": pack})
    names = [n for n in dict.fromkeys(focus or []) if n][:MAX_FOCUS_NAMES]
    return f"/catalog/flow?{qs}" + (f"&focus={quote(','.join(names), safe=',')}" if names else "")


def parse_focus(raw: str) -> list[str]:
    """``focus=A,B`` → lista de nombres técnicos únicos y acotados (cualquier otra cosa se descarta)."""
    out: list[str] = []
    for part in (raw or "").split(","):
        name = part.strip()
        if name and len(name) <= 200 and name not in out:
            out.append(name)
        if len(out) >= MAX_FOCUS_NAMES:
            break
    return out


# ═══════════════════════════ cabecera del detalle del flujo ═══════════════════════════
def header_view(status: svc.AssessmentStatus, entry: Any, pack: str) -> dict:
    """Estado + métricas mínimas + acción para la cabecera del detalle. Las métricas salen de ``status.summary``,
    que lee el MISMO perfil canónico validado que el informe y los exports (sin recalcular nada)."""
    action = ACTIONS.get(status.state)
    summary = status.summary
    view: dict[str, Any] = {
        "state": status.state,
        "label": STATE_LABELS[status.state],
        "tone": STATE_TONES.get(status.state, ""),
        "reason": status.reason if status.state in (svc.NOT_APPLICABLE, svc.FAILED, svc.INCOMPATIBLE_SCHEMA) else None,
        "url": assessment_url(entry, pack),
        "entry_id": entry.entry_id,
        "action": None,
        "coverage": None, "confidence": None, "reviews": None, "reviews_title": None,
    }
    if action:
        view["action"] = {"key": action[0], "method": action[1], "label": action[2]}
    if summary:
        view["coverage"] = P.percent_text(summary["strict"])
        view["confidence"] = P.confidence_level_text(summary["confidence"]).capitalize()
        n = summary["review_occurrences"]
        view["reviews"] = _plural(n, "revisión", "revisiones") if n else "Sin revisiones"
        view["reviews_title"] = (f"{_plural(n, 'ocurrencia')} en {_plural(summary['review_groups'], 'grupo')} de revisión requerida"
                                 if n else "Ningún grupo de revisión requerida")
    return view


def header_for(entry: Any, pack: str) -> dict | None:
    """Vista de la cabecera del flujo. Fail-soft: si el assessment falla, el detalle del Catálogo se abre igual
    (sin la línea de assessment); jamás rompe el detalle legacy. Nunca genera ni escribe la caché."""
    try:
        return header_view(svc.status_for_entry(entry), entry, pack)
    except Exception:                                   # noqa: BLE001 — el detalle del Catálogo no debe depender del assessment
        svc.log.exception("assessment: no se pudo calcular el estado de la cabecera")
        return None


# ═══════════════════════════ informe ═══════════════════════════
@dataclass(frozen=True)
class AdhocReport:
    """Contexto de navegación de un informe AD-HOC (sin flujo del Catálogo): a dónde vuelve y dónde se exporta. Sin linaje ni regenerar."""
    back_url: str
    json_url: str
    md_url: str


def _focus_link(entry: Any, pack: str, names: list[str]) -> str | None:
    if entry is None:                                   # informe ad-hoc: sin flujo del Catálogo → sin "Ver en linaje"
        return None
    names = [n for n in dict.fromkeys(names) if n and P.is_artifact_name(n)]
    return flow_url(entry, pack, names) if names else None


def _group_view(g: dict, entry: Any, pack: str) -> dict:
    occurrences = []
    for o in g["occurrences"]:
        try:
            text = P.occurrence_text(g["id"], o, plain=True)
        except Exception:                               # noqa: BLE001 — formateo defensivo: nunca rompe el informe
            text = ", ".join(f"{k}={o['values'][k]}" for k in sorted(o.get("values") or {}))
        occurrences.append({"id": o["occurrence_id"], "text": text, "artifacts": list(o["affected_artifacts"]),
                            "lineage_url": _focus_link(entry, pack, o["affected_artifacts"])})
    state = g.get("evidence_state")
    evidence_limit = None
    if state and state["assessability"] != "ASSESSED":
        evidence_limit = (f"los insumos de la regla están {P.assessability_plural_text(state['assessability'])} "
                          f"({state['reason']['reason_summary']}); pueden existir más ocurrencias fuera de la evidencia que pudo evaluarse.")
    return {
        "dom_id": f"asr-g-{g['id']}", "id": g["id"], "title": g["title"], "kind": g["kind"], "kind_text": P.finding_kind_text(g["kind"]),
        "area_title": P.area_title(g["area"]), "explanation": g["explanation"], "why_shown": g["why_shown"],
        "question": (g.get("context_required") or {}).get("question") if g["kind"] != "DESCRIPTIVE" else None,
        "legitimate_reasons": g.get("legitimate_reasons") or [], "interpretation_limits": g.get("interpretation_limits") or [],
        "occurrence_count": g["occurrence_count"], "occurrence_unit": g["occurrence_unit"],
        "artifacts": list(g["affected_artifacts"]), "evidence_limit": evidence_limit,
        "lineage_url": _focus_link(entry, pack, g["affected_artifacts"]),
        "occurrences": occurrences,
    }


def _report_title(entry: Any, am: str | None) -> tuple[str | None, str | None]:
    """(descripción, nombre técnico) del título del informe: misma lógica compartida que el heading del detalle del flujo,
    sin el prefijo genérico "Modelo analítico - ". Sin etiqueta confiable la descripción es el nombre técnico."""
    if not am:
        return None, None
    desc, name = catalog_reads.artifact_title(entry, am)
    stripped = _TITLE_PREFIX.sub("", desc).strip()
    return (stripped or desc), name


def report_title(entry: Any, am: str | None) -> tuple[str | None, str | None]:
    """Título (descripción, nombre técnico) de un activo: misma lógica que el informe individual (API pública para el consolidado)."""
    return _report_title(entry, am)


def percent(value: float | None, decimals: int = 1) -> str:
    """Proporción [0,1] como porcentaje (mismo formato que el informe y el Markdown); None → "n/d".
    ``decimals`` (por defecto 1 = el formato canónico) solo cambia la PRESENTACIÓN: se redondea half-up sobre el texto, nunca el valor."""
    if decimals == 1 or value is None:
        return P.percent_text(value)
    from decimal import ROUND_HALF_UP, Decimal
    q = Decimal(1).scaleb(-decimals)
    return f"{(Decimal(str(value)) * 100).quantize(q, rounding=ROUND_HALF_UP)}%"


def confidence_label(level: str) -> str:
    """Nivel de confianza en español con mayúscula inicial ("Alta", "Media", "Baja"), igual que la cabecera del flujo."""
    return P.confidence_level_text(level).capitalize()


def plural(n: int, singular: str, plural_form: str | None = None) -> str:
    return P.plural(n, singular, plural_form)


ROOT_TYPE_TEXT = {"analytic-model": "Modelo analítico", "view": "Vista", "local-table": "Tabla local", "remote-table": "Tabla remota"}


def _canonical_label(doc: dict | None) -> str | None:
    """Nombre de negocio del AM terminal en el perfil canónico (``scope.terminal_analytic_model_label``); None en perfiles que no lo traen
    (p. ej. cacheados antes de este campo). Mismo tratamiento de prefijo genérico que el título del Catálogo."""
    sc = (doc or {}).get("scope") or {}
    raw = str(sc.get("root_label") or sc.get("terminal_analytic_model_label") or "").strip()    # root_label (genérico) con respaldo en el campo de AM
    return (_TITLE_PREFIX.sub("", raw).strip() or raw) or None


def _column_title(key: str) -> str:
    return {"name": "Nombre", "count": "Cantidad"}.get(key) or P.row_key_text(key).capitalize()


# Jerarquía de lectura de los hechos descriptivos de cada área (SOLO presentación; no se quita ni se recalcula ningún hecho):
#   `headline` = pocos hechos de síntesis (máx. 3–4) que orientan en un vistazo → tiles KPI compartidos;
#   `groups`   = evidencia de apoyo agrupada por tema → tablas compactas. Los ids son los de los hechos canónicos del JSON.
# Un hecho no listado cae en "Otros hechos" (nunca se pierde); un área sin layout usa sus 3 primeros números como titulares.
FACT_LAYOUT: dict[str, dict] = {
    "ANALYTIC_MODEL": {"headline": ["fields", "measures", "associations"],
                       "groups": [("Composición del modelo", ["derived_fields", "keys", "labelled_fields", "numeric_non_measure_attributes", "numeric_non_measure_fields"]),
                                  ("Cierre semántico", ["semantic_closure"]),
                                  ("Linaje y asociaciones", ["direct_lineage_providers", "association_targets"]),
                                  ("Composición del cierre por tipo de vista", ["views_in_closure", "view_type_distribution", "view_artifacts"])]},
    "PROPAGATION": {"headline": ["lineage_steps_evaluated", "artifacts_with_derived_fields", "rederived_field_occurrences"], "groups": []},
    "HARMONIZATION": {"headline": ["declared_dimensions", "composition_artifacts", "cross_model_reuse_artifacts"],
                      "groups": [("Reutilización y asociaciones", ["dimensions_with_own_associations", "most_reused_across_models"])]},
    "INGESTION": {"headline": ["boundary_artifacts", "duplicated_remote_entities_in_closure"], "groups": [("Artefactos de frontera", ["boundary_artifacts"])]},
    "NAMING": {"headline": ["section_28_applicable", "section_28_compliant", "section_28_non_compliant"],
               "groups": [("Familias de incumplimiento", ["violation_families"]),
                          ("Procedencia del nombre", ["by_provenance"]),
                          ("Vocabulario de campos", ["field_occurrences", "fields_with_label", "fields_with_label_equal_to_name", "shared_label_groups", "shared_label_numbered_series"]),
                          ("Anclas del nombre en el modelo terminal", ["terminal_model_name_anchors", "terminal_model_remote_source_anchor"])]},
    "SOURCE_EXTRACTION": {"headline": ["remote_artifacts", "connections", "sources_with_filter"], "groups": [("Por estado de la fuente", ["by_source_state"])]},
}


# Ayuda OPCIONAL de los KPI del informe individual (tooltip (i) del Hub; ver `kpi_tile` en assessment_report_macros.html). El significado sale de los
# contratos canónicos del paquete (`profile.COVERAGE_MEANING`, `profile.CONFIDENCE_MEANING`, `summary.context_required`, el estado "abierto — requiere
# contexto" de las revisiones); acá solo se redacta para lectura humana. Cada KPI sin entrada se muestra exactamente como siempre. Nunca un puntaje.
KPI_HELP: dict[str, tuple[str, ...]] = {
    "coverage_strict": (
        "Qué parte de la evidencia metodológica requerida y aplicable pudo evaluarse por completo con la información disponible; la evaluada solo en parte no suma.",
        "No mide la calidad del modelo."),
    "confidence": (
        "Qué tan fiable es la evidencia que sí se evaluó (alta, media o baja); manda el componente más débil.",
        "No mide la calidad del modelo y es independiente de la cobertura y del contexto aún abierto."),
    "review_required": (
        "Cantidad de ocurrencias que el análisis automático no puede cerrar solo con la evidencia disponible y que requieren la revisión de una persona.",
        "No son defectos confirmados."),
    "context_open": (
        "Cantidad de preguntas distintas que solo el equipo del proyecto puede responder y que el análisis dejó abiertas.",
        "Cuenta tipos de pregunta, no ocurrencias, y no mide la calidad ni la confianza."),
}


def _kpi(key: str, label: str, value: Any) -> dict:
    return {"label": label, "value": value, "help": list(KPI_HELP[key])}


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fact_into(group: dict, h: dict) -> None:
    """Coloca un hecho en su grupo de evidencia según el TIPO de valor: escalares y listas de nombres → tabla de propiedades
    (Hecho | Valor); composiciones, distribuciones y mapas → tabla propia."""
    v, label, bd = h["value"], h["label"], h.get("breakdown") or []
    if _is_number(v) and bd:
        group["tables"].append({"title": label, "columns": ["Concepto", "Cantidad"], "rows": [["Total", v]] + [[b["name"], b["count"]] for b in bd]})
    elif _is_number(v):
        group["properties"].append({"label": label, "value": v, "names": None})
    elif isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
        keys = sorted(dict.fromkeys(k for r in v for k in r), key=lambda k: k != "name")
        group["tables"].append({"title": label, "columns": [_column_title(k) for k in keys],
                                "rows": [[(P.percent_text(r[k]) if k == "share" and k in r else r.get(k, "")) for k in keys] for r in v]})   # `share` = proporción 0–1
    elif isinstance(v, dict):
        group["tables"].append({"title": label, "columns": ["Concepto", "Valor"], "rows": [[P.row_key_text(k).capitalize(), x] for k, x in v.items()]})
    elif isinstance(v, list):
        group["properties"].append({"label": label, "value": None, "names": [str(x) for x in v]})
    else:
        group["properties"].append({"label": label, "value": None, "names": [P.value_text(v)]})


def _facts_view(area_id: str, highlights: list[dict]) -> dict:
    """Hechos descriptivos de un área en dos niveles de lectura: ``headline`` (tiles KPI de síntesis) y ``groups`` (evidencia de apoyo)."""
    by_id = {h["id"]: h for h in highlights}
    layout = FACT_LAYOUT.get(area_id)
    if layout is None:
        numbers = [h for h in highlights if _is_number(h["value"]) and not h.get("breakdown")]
        layout = {"headline": [h["id"] for h in numbers[:3]], "groups": [("Hechos de apoyo", [h["id"] for h in highlights])]}
    head_ids = [i for i in layout["headline"] if i in by_id and _is_number(by_id[i]["value"])]
    headline = [{"label": by_id[i]["label"], "value": by_id[i]["value"]} for i in head_ids]
    shown = {i for i in head_ids if not by_id[i].get("breakdown")}                        # con composición: tile + su tabla en el grupo
    groups = []
    for title, ids in layout["groups"]:
        g = {"title": title, "properties": [], "tables": []}
        for i in ids:
            if i in by_id and i not in shown:
                _fact_into(g, by_id[i])
                shown.add(i)
        groups.append(g)
    rest = {"title": "Otros hechos", "properties": [], "tables": []}
    for h in highlights:                                                                  # nada se pierde: lo no mapeado también se muestra
        if h["id"] not in shown:
            _fact_into(rest, h)
    if rest["properties"] or rest["tables"]:
        groups.append(rest)
    groups = [g for g in groups if g["properties"] or g["tables"]]
    notes = []
    views = by_id.get("views_in_closure")
    if area_id == "ANALYTIC_MODEL" and views is not None:                                  # misma frase que el Markdown (función del paquete), sin juicio
        notes.append(P.view_composition_text(views["value"], views.get("breakdown") or []))
    for g in groups:                                                                      # grupo de una sola tabla: el título de la tabla ES el del grupo
        if not g["properties"] and len(g["tables"]) == 1:
            g["title"] = g["tables"][0]["title"]
    return {"headline": headline, "groups": groups, "notes": notes}


def _area_view(a: dict, groups_by_id: dict[str, dict], rules_by_area: dict[str, list[dict]]) -> dict:
    rs = a["required_surfaces"]
    return {
        "id": a["id"], "dom_id": f"asr-a-{a['id']}", "title": P.area_title(a["id"]), "macro": P.macro_dimension_title(a["macro_dimension"]),
        "state": a["assessability"], "state_text": P.assessability_text(a["assessability"]),
        "reason": (f"{a['reason']['reason_summary']} ({a['reason']['reason_code']})" if a["reason"] else None),
        "surfaces": (f"{_plural(rs['assessed'], 'evaluada')}, {_plural(rs['partially_assessed'], 'parcialmente evaluada')}, "
                     f"{_plural(rs['not_assessed'], 'no evaluada')}, {rs['not_applicable']} no aplican"),
        "facts": _facts_view(a["id"], a["highlights"]), "macro_id": a["macro_dimension"],
        "findings": [{"title": groups_by_id[gid]["title"], "dom_id": f"asr-g-{gid}", "count": groups_by_id[gid]["occurrence_count"],
                      "kind_text": P.finding_kind_text(groups_by_id[gid]["kind"])} for gid in a["finding_groups"]],
        "rules": [{"title": r["title"], "result": r["result"], "result_text": P.rule_result_text(r["result"]),
                   "reason": r["reason"]["reason_summary"] if r["reason"] else None} for r in rules_by_area.get(a["id"], [])],
        "limitations_not_observable": bool(a["limitations_not_observable"]),
    }


def _context_questions(ctx: dict, groups_by_id: dict[str, dict]) -> dict:
    """Preguntas de contexto abiertas, separadas por su respaldo: (a) respaldadas por un hallazgo, agrupadas por tipo de hallazgo
    (cada tabla lleva el tipo en su encabezado, sin repetirlo por fila) y (b) preguntas del proyecto SIN hallazgo. Es solo
    presentación del mismo bloque canónico: ``backed + independent == open_questions``."""
    by_kind: dict[str, list[dict]] = {}
    independent: list[dict] = []
    for q in ctx["questions"]:
        g = groups_by_id.get(q["id"])
        if q["occurrence_count"] is None or g is None:
            independent.append({"text": q["question"]})
            continue
        by_kind.setdefault(q["origin"], []).append({"text": q["question"], "occurrences": q["occurrence_count"],
                                                    "finding_title": g["title"], "finding_dom_id": f"asr-g-{q['id']}"})
    backed = [{"heading": P.finding_kind_text(kind).capitalize(), "rows": rows} for kind, rows in by_kind.items()]
    return {"context_backed": backed, "context_independent": independent,
            "context_backed_n": sum(len(b["rows"]) for b in backed), "context_independent_n": len(independent)}


def report_view(status: svc.AssessmentStatus, entry: Any, pack: str, adhoc: AdhocReport | None = None) -> dict:
    """View-model del informe. ``status.profile`` es el JSON canónico validado; nada se deriva de otra fuente.
    ``adhoc`` (opcional) reemplaza el contexto del Catálogo (flujo/URLs/linaje) por el de un assessment ad-hoc; el contenido es el mismo."""
    doc = status.profile
    link_entry = None if adhoc else entry
    view: dict[str, Any] = {
        "state": status.state, "state_label": STATE_LABELS[status.state], "state_tone": STATE_TONES.get(status.state, ""), "state_note": STATE_NOTES.get(status.state),
        "reason": status.reason, "error_code": status.error_code,
        "client": status.client, "entry_id": status.entry_id if adhoc else entry.entry_id, "dominio": "" if adhoc else entry.dominio,
        "terminal_am": status.terminal_am, "title_desc": None, "title_id": None, "generated_at": status.generated_at,
        "action": ({"key": REPORT_ACTIONS[status.state][0], "label": REPORT_ACTIONS[status.state][1]} if status.state in REPORT_ACTIONS else None),
        "previous_profile_retained": status.previous_profile_retained,
        "report_url": None if adhoc else assessment_url(entry, pack), "flow_url": adhoc.back_url if adhoc else flow_url(entry, pack),
        "json_url": adhoc.json_url if adhoc else "/catalog/flow/assessment.json?" + urlencode({"flow": entry.entry_id}),
        "md_url": adhoc.md_url if adhoc else "/catalog/flow/assessment.md?" + urlencode({"flow": entry.entry_id}),
        "has_profile": doc is not None,
        "adhoc": adhoc is not None,
    }
    canonical = _canonical_label(doc)
    if canonical and status.terminal_am:                              # nombre de negocio del perfil canónico (respaldado por la evidencia)
        view["title_desc"], view["title_id"] = canonical, status.terminal_am
    else:                                                             # sin etiqueta canónica: metadato del Catálogo (si lo hay) o nombre técnico
        view["title_desc"], view["title_id"] = (None, status.terminal_am) if adhoc else _report_title(entry, status.terminal_am)
    view["module_label"] = MODULE_LABEL
    if doc is None:
        return view
    scope, summary = doc["scope"], doc["summary"]
    cov, conf, ctx = summary["coverage"], summary["confidence"], summary["context_required"]
    rs = cov["required_surfaces"]
    groups_by_id = {g["id"]: g for g in (*doc["observations"], *doc["review_required"], *doc["descriptive_signals"])}
    rules_by_area: dict[str, list[dict]] = {}
    for r in doc["rule_results"]:
        rules_by_area.setdefault(r["area"], []).append(r)
    lim = conf["main_limitation"]
    view.update({
        "execution_text": P.execution_state_text(doc["assessment_state"]),
        "scope_text": (f"{_plural(scope['closure_size'], 'artefacto')} en el cierre semántico del modelo analítico terminal"
                       if scope.get("root_type") in (None, "analytic-model") else
                       f"{ROOT_TYPE_TEXT.get(scope['root_type'], 'Objeto')} · {_plural(scope['closure_size'], 'artefacto')} en el cierre semántico del objeto raíz"),
        "root_type": scope.get("root_type"),
        "schema_version": doc["schema_version"],
        "versions": [("Señales", doc["versions"]["signals"]), ("Evaluabilidad", doc["versions"]["assessability"]),
                     ("Confianza", doc["versions"]["confidence"])],
        "summary": {
            "coverage_strict": P.percent_text(cov["strict"]), "coverage_inclusive": P.percent_text(cov["inclusive"]), "coverage_meaning": cov["meaning"],
            "surfaces": (f"{rs['applicable']} aplicables — {_plural(rs['assessed'], 'evaluada')}, "
                         f"{_plural(rs['partially_assessed'], 'parcialmente evaluada')}, {_plural(rs['not_assessed'], 'no evaluada')} "
                         f"({rs['not_applicable']} que no aplican quedan excluidas)"),
            "confidence": P.confidence_level_text(conf["level"]), "confidence_level": conf["level"], "confidence_meaning": conf["meaning"],
            "confidence_limitation": lim["summary"] if lim else None,
            "context_open": _plural(ctx["open_questions"], "pregunta de contexto abierta", "preguntas de contexto abiertas"),
            "context_open_n": ctx["open_questions"],
            **_context_questions(ctx, groups_by_id),
            "no_evidence_dimensions": [P.macro_dimension_title(m["id"]) for m in summary["macro_dimensions"] if m["state"] == "COMPLETELY_NOT_ASSESSED"],
            "observation_groups": len(doc["observations"]), "review_groups": len(doc["review_required"]),
            "observation_occurrences": sum(g["occurrence_count"] for g in doc["observations"]),
            "review_occurrences": sum(g["occurrence_count"] for g in doc["review_required"]),
        },
        "areas": [_area_view(a, groups_by_id, rules_by_area) for a in doc["areas"]],
        "kpis": [
            _kpi("coverage_strict", "Cobertura estricta", P.percent_text(cov["strict"])),
            _kpi("confidence", "Confianza", P.confidence_level_text(conf["level"])),
            _kpi("review_required", "Revisiones requeridas", sum(g["occurrence_count"] for g in doc["review_required"])),
            _kpi("context_open", "Contexto abierto", ctx["open_questions"]),
        ],
        "observations": [_group_view(g, link_entry, pack) for g in doc["observations"]],
        "review_required": [_group_view(g, link_entry, pack) for g in doc["review_required"]],
        "descriptive": [_group_view(g, link_entry, pack) for g in doc["descriptive_signals"]],
        "limitations": [{"title": s["title"], "surface_id": s["surface_id"], "area": P.area_title(s["area"]),
                         "state": P.assessability_text(s["assessability"]), "reason": s["reason"]["reason_summary"] if s["reason"] else "",
                         "reason_code": s["reason"]["reason_code"] if s["reason"] else "",
                         "counts": s["counts_toward_coverage"],
                         "units": [P.unit_label(u) for u in s["affected_units"]]} for s in doc["evidence_limitations"]],
        "evidence_not_acquired": P.evidence_not_acquired(scope.get("evidence_scope")),     # solo alcance CONOCIDO y limitado; del documento, no del formulario
        "absence_not_established": [r["title"] for r in doc["rule_results"] if r["result"] == "ABSENCE_NOT_ESTABLISHED"],
        "confidence_components": [{"label": c["label"], "value": c["value"], "level": P.confidence_level_text(c["level"])} for c in conf["components"]],
        "not_applicable": [{"title": s["title"], "surface_id": s["surface_id"], "area": P.area_title(s["area"]),
                            "reason": s["reason"]["reason_summary"], "reason_code": s["reason"]["reason_code"]} for s in doc["not_applicable"]],
        "deduplicated_questions": [{"id": q["id"], "covered_by": q["covered_by"]} for q in ctx["deduplicated_questions"]],
        "rule_results": [{"title": r["title"], "rule": r["rule"], "result_text": P.rule_result_text(r["result"]), "count": r["finding_count"]}
                         for r in doc["rule_results"]],
        "area_coverage": [{"title": P.area_title(a["id"]), **a["required_surfaces"]} for a in doc["areas"]],
        "technical": {"pool_signature": scope.get("pool_signature"),
                      "fact_families": len({f["fact"].split("@")[0] for f in doc["fact_index"]}), "fact_instances": len(doc["fact_index"]),
                      "versions_all": sorted(doc["versions"].items())},
    })
    groups: dict[str, dict] = {}
    for a in view["areas"]:
        groups.setdefault(a["macro_id"], {"id": a["macro_id"], "title": SECTION_TITLES.get(a["macro_id"], a["macro"]), "areas": []})["areas"].append(a)
    view["macro_groups"] = list(groups.values())
    return view
