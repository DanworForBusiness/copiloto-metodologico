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

from tools.catalog.methodological_judgment import contract as C
from tools.catalog.methodological_judgment import explain_determination
from webapp.services import assessment_service as svc

canonical_signal_id = P.canonical_signal_id        # los consumidores normalizan ids historicos por aca (API publica de presentacion)
from webapp.services import catalog_reads
from webapp.services import asset_evaluation as AE
from webapp.services import assessment_acquisition as AQ
from webapp.services import domain_classification_service as dcs
from webapp.services import module_reference as MR
from webapp.services.status_tone import ATTENTION, NEUTRAL, POSITIVE, status_tone

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
# Mensaje de STALE según su CAUSA (``AssessmentStatus.stale_reason``): una evaluación vieja por cambio de versión NO debe decir que el modelo cambió.
STALE_NOTES = {
    svc.STALE_VERSION_ONLY: "La evaluación fue generada con una versión anterior de la metodología y la evidencia.",
    svc.STALE_INPUT_CHANGED: "El modelo cambió desde la última evaluación.",
    svc.STALE_VERSION_AND_INPUT: "El modelo cambió y la evaluación fue generada con una versión anterior de la metodología.",
}
STALE_BODIES = {
    svc.STALE_VERSION_ONLY: "Se muestra el informe anterior. Regenerarlo lo vuelve a calcular con la versión vigente de la metodología y la evidencia.",
    svc.STALE_INPUT_CHANGED: "Se muestra el informe anterior. Regenerarlo calcula el assessment contra el modelo actual.",
    svc.STALE_VERSION_AND_INPUT: "Se muestra el informe anterior. Regenerarlo calcula el assessment contra el modelo actual y la versión vigente de la metodología.",
}


def stale_note(status: "svc.AssessmentStatus") -> str | None:
    """Nota de STALE según su causa (sin causa conocida, la nota histórica)."""
    if status.state != svc.STALE:
        return STATE_NOTES.get(status.state)
    return STALE_NOTES.get(status.stale_reason) or STATE_NOTES[svc.STALE]


def stale_body(status: "svc.AssessmentStatus") -> str | None:
    return (STALE_BODIES.get(status.stale_reason) or STALE_BODIES[svc.STALE_INPUT_CHANGED]) if status.state == svc.STALE else None
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
        "tone": STATE_TONES.get(status.state, NEUTRAL),
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
        view["reviews"] = _plural(n, "caso a revisar", "casos a revisar") if n else "Sin casos a revisar"
        view["reviews_title"] = (f"{_plural(n, 'caso')} en {_plural(summary['review_groups'], 'tipo de revisión', 'tipos de revisión')}"
                                 if n else "Ningún caso a revisar")
    # Resumen COMPACTO de Adopción metodológica para el Catálogo (resultado persistido; el detalle vive en el Assessment).
    # Es el MISMO view-model que usa el informe (``judgment_view``): sin recálculo, solo presentación.
    view["adoption"] = judgment_view(status.judgment)
    view["has_profile"] = status.profile is not None
    view["governance_findings"] = governance_findings(status.profile)       # None sin Assessment
    view["grade"] = tile_grade(status)          # UN activo, UN grado primario: la MISMA fuente que la tile (Adopción persistida; sin Juicio → estado neutro)
    view["terminal_am"] = status.terminal_am
    view["title_desc"], view["title_id"] = entry_title(status, entry)     # identidad del modelo (= informe)
    return view


# Línea de negocio EFECTIVA tal como se muestra en el Informe de Cliente y en la cabecera del detalle: contexto de negocio (NO adopción, cobertura, confianza ni Juicio).
# Fuente única: `dcs.effective_lob_by_entry` (manual > automática HIGH > sin clasificar, de la v2 PERSISTIDA); acá solo se traduce a texto. Una sugerencia MEDIUM jamás se
# muestra como asignación: queda «Sin clasificar». Un error real de lectura NO se disfraza de «Sin clasificar».
LOB_UNCLASSIFIED_LABEL, LOB_UNAVAILABLE_LABEL = "Sin clasificar", "Clasificación no disponible"


def lob_display(effective: dict | None) -> dict:
    """{label, source} de una entrada de ``dcs.effective_lob_by_entry``. ``source``: MANUAL | AUTOMATIC | UNCLASSIFIED | UNAVAILABLE."""
    if not effective or not effective.get("available", True) or effective.get("source") == dcs.UNAVAILABLE:
        return {"label": LOB_UNAVAILABLE_LABEL, "source": dcs.UNAVAILABLE}
    return {"label": effective.get("label") or LOB_UNCLASSIFIED_LABEL, "source": effective.get("source") or dcs.UNCLASSIFIED}


def lob_for(entry: Any, status: Any) -> dict:
    """Línea de negocio efectiva de UN activo con el estado ya leído (sin segunda lectura). Fail-soft: un error se informa como «no disponible»."""
    try:
        return lob_display(dcs.effective_lob_by_entry([entry], {entry.entry_id: status})[entry.entry_id])
    except Exception:                                   # noqa: BLE001
        svc.log.exception("línea de negocio: no se pudo resolver la clasificación efectiva de %s", getattr(entry, "entry_id", "?"))
        return lob_display(None)


def header_for(entry: Any, pack: str) -> dict | None:
    """Vista de la cabecera del flujo. Fail-soft: si el assessment falla, el detalle del Catálogo se abre igual
    (sin la línea de assessment); jamás rompe el detalle legacy. Nunca genera ni escribe la caché.
    Incluye ``line_of_business`` ({label, source}) con el MISMO estado leído, para la cabecera «Catálogo Danwor · {Línea} · {Cliente}»."""
    try:
        status = svc.status_for_entry(entry)
        view = header_view(status, entry, pack)
        view["line_of_business"] = lob_for(entry, status)
        return view
    except Exception:                                   # noqa: BLE001 — el detalle del Catálogo no debe depender del assessment
        svc.log.exception("assessment: no se pudo calcular el estado de la cabecera")
        return None


# Grado PRIMARIO de la tile del Catálogo: SOLO la Adopción metodológica persistida (nunca el veredicto del catálogo, ni como fallback).
# Adopción y vigencia son ejes separados: un Juicio persistido sobre un Assessment STALE conserva su grado y la vigencia va aparte.
_TILE_NO_JUDGMENT = {svc.NOT_YET_ASSESSED: "Sin assessment", svc.NOT_APPLICABLE: "No aplica", svc.FAILED: "Assessment no disponible",
                     svc.INCOMPATIBLE_SCHEMA: "Informe no compatible"}
_TILE_JUDGMENT_UNAVAILABLE = "Juicio no disponible"


def governance_findings(profile: dict | None) -> list[dict] | None:
    """Hallazgos de GOBIERNO del Assessment gobernado persistido (compuestos por ``asset_evaluation.governed_findings`` desde el perfil: grupos del
    área NAMING + evidencia canónica de la sección 28). ``None`` si no hay perfil (sin Assessment no hay conclusiones)."""
    if profile is None:
        return None
    doc = P.display_doc(profile)
    groups = [{"id": g["id"], "title": g["title"], "explanation": g["explanation"], "kind_text": P.finding_kind_text(g["kind"])}
              for key in ("observations", "review_required", "descriptive_signals") for g in doc.get(key) or [] if g.get("area") == "NAMING"]
    s28 = (((doc.get("canonical_evidence") or {}).get("naming_evidence") or {}).get("hygiene") or {}).get("artifact_name_populations", {}).get("section_28")
    if s28:                                                      # motivos persistidos con ordinales históricos («§29: …») → sin ordinal (compatibilidad de lectura)
        s28 = {**s28, "violation_reason_histogram": [{**r, "key": MR.strip_ordinals(r.get("key"))} for r in s28.get("violation_reason_histogram") or []]}
    ref = MR.resolve_ref(MR.NAMING_CONVENTION)                   # módulo/sección por CLAVE; el número se resuelve acá, contra la estructura VIGENTE del módulo
    return AE.governed_findings(groups, s28, ref)


def tile_grade(status: svc.AssessmentStatus) -> dict:
    """{tone, label, freshness}. ``tone`` sale de ``status_tone`` (vía ``judgment_view``); sin Juicio → neutro con el vocabulario de estado existente."""
    j = judgment_view(status.judgment)
    freshness = STATE_LABELS[status.state] if status.state in (svc.STALE, svc.FAILED) and j else None   # vigencia / falla de la regeneración: eje APARTE del grado
    findings = governance_findings(status.profile) or []                # SOLO del Assessment persistido (STALE: del último snapshot evaluado)
    extra = {"findings": findings, "findings_summary": AE.governance_summary(len(findings)) if findings else None}
    if j:
        return {"tone": j["tone"], "label": j["level_label"], "freshness": freshness, "has_judgment": True, **extra}
    return {"tone": NEUTRAL, "label": _TILE_NO_JUDGMENT.get(status.state, _TILE_JUDGMENT_UNAVAILABLE), "freshness": freshness, "has_judgment": False, **extra}


def tile_grades(entries: Any, statuses: dict | None = None) -> dict[str, dict]:
    """entry_id → grado de tile, en UNA lectura por lote (o reutilizando ``statuses`` ya leídos por el llamador, sin una segunda lectura). Fail-soft: si el
    estado no se puede leer, neutro «Assessment no disponible» (jamás el veredicto del catálogo)."""
    entries = list(entries)
    try:
        statuses = statuses if statuses is not None else svc.statuses_for_entries(entries)
        return {e.entry_id: tile_grade(statuses[e.entry_id]) for e in entries}
    except Exception:                                   # noqa: BLE001 — la lista del Catálogo no debe depender del assessment
        svc.log.exception("assessment: no se pudo calcular el grado de las tiles")
        return {e.entry_id: {"tone": NEUTRAL, "label": "Assessment no disponible", "freshness": None, "has_judgment": False, "findings": [], "findings_summary": None} for e in entries}


# ── Clasificación de dominio (presentación de la SUGERENCIA persistida; hermana del Juicio, sin grado ni color propios) ──
_DC_HEADLINE = {"SUGGEST_RECLASSIFICATION": "La evidencia disponible sugiere una clasificación más específica.",
                "ALIGNED": "El dominio actual es consistente con la evidencia disponible.",
                "UNDETERMINED": "No hay evidencia suficiente para recomendar otro dominio."}
_DC_REASON = {"CONFLICTING_CANDIDATES": "La evidencia respalda más de un dominio existente por igual; no se recomienda ninguno.",
              "CURRENT_DOMAIN_NO_EVIDENCE": "El dominio actual no tiene respaldo ni contradicción en la evidencia disponible; no se recomienda otro dominio."}
_DC_CONFIDENCE = {"HIGH": "Alta", "MEDIUM": "Media", "LOW": "Baja"}
_DC_TYPE = {"EXISTING_DOMAIN": "Dominio existente del cliente", "NEW_DOMAIN_CANDIDATE": "Dominio nuevo, derivado del vocabulario observado"}
_DC_FAMILY = {"ROOT": "Objeto raíz", "CLOSURE": "Vocabulario repetido en el cierre", "ROOT+CLOSURE": "Objeto raíz y vocabulario repetido en el cierre"}


def domain_classification_view(dc: dict | None) -> dict | None:
    """View-model de la Clasificación de dominio. SOLO presentación: no decide ni recalcula; es una SUGERENCIA (no mueve ni modifica nada)."""
    if not dc:
        return None
    status = dc.get("classification_status")
    cur = dc.get("current_domain") or "Sin dominio"
    sug = dc.get("suggested_domain")
    conf = _DC_CONFIDENCE.get(dc.get("confidence") or "", "—")
    headline = _DC_HEADLINE.get(status, _DC_HEADLINE["UNDETERMINED"])
    if status == "UNDETERMINED" and dc.get("reason") in _DC_REASON:
        headline = _DC_REASON[dc["reason"]]
    rows = [("Dominio actual", cur)]
    if sug:
        rows.append(("Sugerencia", sug))
        rows.append(("Tipo de sugerencia", _DC_TYPE.get(dc.get("suggestion_type") or "", "")))
    rows.append(("Confianza", conf))
    summary = (f"Sugerencia: {sug} · confianza {conf.lower()}" if status == "SUGGEST_RECLASSIFICATION" and sug
               else (f"Alineado con «{cur}»" if status == "ALIGNED" else "Sin recomendación"))
    return {"status": status, "headline": headline, "summary": summary, "rows": [{"label": a, "value": b} for a, b in rows if b],
            "evidence": ([{"term": e["term"], "family": _DC_FAMILY.get(e["family"], e["family"]), "artifacts": e["artifacts"]} for e in dc.get("evidence") or []]
                         if status == "SUGGEST_RECLASSIFICATION" else []),     # ALIGNED / UNDETERMINED: presentación compacta (el sustento solo acompaña una sugerencia)
            "alternatives": list(dc.get("alternatives") or []), "suggested": bool(sug) and status == "SUGGEST_RECLASSIFICATION",
            "note": "Es una sugerencia: no mueve el modelo ni modifica el catálogo."}


# ── Clasificación de NEGOCIO v2 (línea de negocio + dominio): evoluciona la presentación de la clasificación de dominio; sin grado ni color propios ──
_BC_HEADLINE = {"CLASSIFIED": "La evidencia canónica respalda esta línea de negocio con varias familias de evidencia independientes.",
                "SUGGESTED": "La evidencia disponible sugiere esta línea de negocio, pero sin corroboración independiente suficiente: queda como sugerencia.",
                "CROSS_LINE_CONFLICT": "La evidencia respalda más de una línea de negocio por igual; no se recomienda ninguna.",
                "INSUFFICIENT_BUSINESS_EVIDENCE": "No hay evidencia suficiente para determinar la línea de negocio."}
_BC_FAMILY = {"ROOT": "Objeto raíz", "LINEAGE": "Linaje del cierre", "SOURCE": "Fuentes del origen"}
_BC_SOURCE_STATE = {"MANUAL": "Manual", "CLASSIFIED": "Clasificación automática", "SUGGESTED": "Sugerida", "UNDETERMINED": "Sin determinar"}


def business_classification_view(bc: dict | None, dc_view: dict | None, manual_lob: str | None = None) -> dict | None:
    """View-model de la Clasificación de negocio (v2) sobre la MISMA estructura que la de dominio (mismo bloque de Assessment [04]). SOLO presentación: no
    decide ni recalcula; es una SUGERENCIA salvo asignación manual. Sin v2 (sobre histórico) devuelve la vista de dominio v1 tal cual (nunca la presenta como v2)."""
    if not bc:
        return dc_view
    from tools.catalog import business_classification as BC
    eff = BC.effective_classification(bc, manual_lob)
    lob = (bc.get("line_of_business") or {}).get("label")
    dom = bc.get("domain") or {}
    conf = _DC_CONFIDENCE.get(bc.get("confidence") or "", "—")
    state_key = "MANUAL" if eff["source"] == "MANUAL" else bc.get("status")
    rows = [("Línea de negocio", BC.lob_label(manual_lob) if eff["source"] == "MANUAL" else (lob or "Sin determinar"))]
    if dom.get("label"):
        rows.append(("Dominio", f"{dom['label']} ({'actual' if dom.get('origin') == 'CURRENT' else 'sugerido'})"))
    cur = (bc.get("domain_classification") or {}).get("current_domain")
    if cur and cur != dom.get("label"):
        rows.append(("Dominio actual", cur))
    rows += [("Confianza", conf), ("Estado", _BC_SOURCE_STATE.get(state_key, "Sin determinar")),
             ("Agrupación efectiva", eff["label"] or "Sin clasificar")]
    if eff.get("automatic_alignment") == "DISAGREES":
        rows.append(("Clasificación automática", f"{lob or '—'} (difiere de la asignación manual)"))
    elif bc.get("alignment") == "DISAGREES":
        rows.append(("Dominio actual del cliente", "su vocabulario sugiere otra línea de negocio"))
    summary = (f"{rows[0][1]} · confianza {conf.lower()}" if lob or eff["source"] == "MANUAL" else "Sin determinar")
    headline = _BC_HEADLINE["CLASSIFIED" if bc.get("status") == "CLASSIFIED" else "SUGGESTED"] if lob else _BC_HEADLINE.get(bc.get("reason_code"), _BC_HEADLINE["INSUFFICIENT_BUSINESS_EVIDENCE"])
    return {"status": bc.get("status"), "headline": headline, "summary": summary, "rows": [{"label": a, "value": b} for a, b in rows if b],
            "evidence": [{"term": e["signal"], "family": _BC_FAMILY.get(e["family"], e["family"]), "artifacts": e["artifacts"]} for e in bc.get("evidence") or []],
            "alternatives": [c["label"] for c in bc.get("competing") or [] if c.get("label")], "suggested": bc.get("status") in ("SUGGESTED", "CLASSIFIED") and eff["source"] != "MANUAL",
            "note": "Es una clasificación derivada de la evidencia local: no mueve el modelo ni modifica el catálogo.", "contract_version": bc.get("contract_version")}


# BEGIN judgment-presentation (Gate 8D.2) — taxonomía AUTORIZADA del Juicio metodológico (BUENA/ADECUADA/PARCIAL/No determinable).
# ═══════════════════════════ Juicio metodológico (presentación del resultado DETERMINISTA persistido) ═══════════════════════════
# Mapeo SOLO de presentación: traduce el resultado ya decidido por el motor canónico a copy en español. NO decide nivel,
# NO recalcula, NO codifica lógica de calidad (sin puntaje/banda/madurez). El Juicio llega PERSISTIDO en ``status.judgment``.
_JDG_LEVEL_LABEL = {"BUENA": "Buena", "ADECUADA": "Adecuada", "PARCIAL": "Parcial", "PLENA": "Plena"}
# El TONO (color) del nivel sale del mapeo canónico ÚNICO del Hub (`status_tone`), el MISMO que usa el veredicto del Catálogo:
# 'Buena' es verde en todos lados (por valencia, no por rango). No hay mapa de color propio del Juicio.
# Copy conciso, orientado al usuario (sin terminología de implementación). Por nivel cuando aporta; genérico si no.
# Copy orientada al producto: conclusión INTERPRETATIVA ("qué muestra la evidencia"), constructiva y progresiva, nunca
# punitiva. Es el cierre editorial de la sección (punch del Hub), después de los contratos y la evidencia.
_JDG_LEVEL_HEADLINE = {
    "BUENA": "La evidencia observada muestra una adopción consistente de los cuatro contratos metodológicos.",
    "ADECUADA": "La evidencia observada muestra una adopción adecuada, con oportunidades puntuales de mejora.",
    "PARCIAL": "La adopción es parcial: existen señales favorables, pero persisten aspectos que requieren atención.",
}
_JDG_GENERIC_HEADLINE = "La evidencia observada refleja la adopción metodológica en los cuatro contratos."
_JDG_ND_HEADLINE = "No fue posible determinar la adopción metodológica con la evidencia disponible."
# LVL-06: la evidencia es evaluable pero no hay evidencia afirmativa suficiente en alguna dimensión (≠ evidencia insuficiente). Estados/causas estables de ``not_determinable_detail``.
_JDG_ND_STATE_TEXT = {"NO_STRENGTH": "Sin evidencia afirmativa suficiente (evaluado)", "INSUFFICIENT_EVIDENCE": "Evidencia insuficiente"}
_JDG_ND_CAUSE = {
    "NO_SOURCE_CONTACT_OBSERVED": "no se observó contacto con la fuente",
    "SOURCE_CONTRACT_CLASS_UNDETERMINED": "la clase del contrato de fuente no está determinada",
    "SOURCE_CONTACT_NOT_ALL_GOVERNED": "no todo contacto con la fuente es un contrato gobernado",
    "NO_HARMONIZATION_BOUNDARY_OBSERVED": "no se observó una frontera de armonización",
    "HARMONIZATION_MAPPING_NOT_OBSERVABLE": "el mapeo de la armonización no es observable",
    "HARMONIZATION_NOT_A_LEGITIMATE_REUSABLE_BOUNDARY": "la armonización no es una frontera técnica reutilizable legítima",
    "HARMONIZATION_CANDIDATE_CHARACTER_NOT_DETERMINABLE": "no se puede determinar el carácter de un candidato de armonización (sus etiquetas o su mapeo no son observables)",
    "NO_PRODUCT_NODE_OBSERVED": "no se observó un nodo de producto",
    "PRODUCT_DOES_NOT_COMPOSE_RESOLVED_CONTRACTS": "el producto no compone contratos ya resueltos",
    "PUBLISHED_LINEAGE_INCOMPLETE": "el linaje de los campos publicados está incompleto",
    "UNRESOLVED_VOCABULARY_REACHES_THE_CONSUMER": "vocabulario sin resolver llega al consumidor",
    "NO_AFFIRMATIVE_PUBLISHED_FAMILY": "el contrato publicado no acredita evidencia afirmativa propia",
    "PUBLISHED_CONTRACT_SUBSTANCE_NOT_ESTABLISHED": "el contrato publicado no acredita sustancia de consumo (medidas con identidad de negocio, sin nombres técnicos de columna)",
    "NO_AFFIRMATIVE_EVIDENCE": "sin evidencia afirmativa",
}
# T2 — EXPLICACIÓN COMPLEMENTARIA de «No determinable». El valor oficial sigue siendo «No determinable» (nunca un nivel alternativo): estos textos salen SOLO del código de
# motivo, la regla de nivel y el detalle estructurado que el Juicio ya persiste (``not_determinable_reason`` / ``level_rule`` / ``not_determinable_detail`` / ``contracts_not_evaluable`` /
# ``reasons`` / ``review_candidates``); ni inferencias del LLM ni heurísticas de presentación. Una causa distinta, un texto distinto.
ND_SUFFICIENT_NO_STRENGTH = "SUFFICIENT_EVIDENCE_NO_STRENGTH"
ND_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
ND_PARTIAL_EVIDENCE = "PARTIALLY_INSUFFICIENT_EVIDENCE"
ND_NO_STRENGTH_UNDETAILED = "NO_AFFIRMATIVE_EVIDENCE_UNDETAILED"
ND_CONTRACT_NOT_EVALUABLE = "CONTRACT_NOT_EVALUABLE"
ND_SOURCE_CLASS_CRITICAL = "EXTERNAL_SOURCE_CLASS_UNDETERMINED"
ND_NOT_JUDGEABLE = "ASSESSMENT_NOT_JUDGEABLE"
ND_EVIDENCE_V3_REQUIRED = "CANONICAL_EVIDENCE_V3_REQUIRED"
ND_UNSPECIFIED = "UNSPECIFIED"
_JDG_ND_LABEL = {
    ND_SUFFICIENT_NO_STRENGTH: "Sin deficiencias determinables · fortaleza no establecida",
    ND_INSUFFICIENT_EVIDENCE: "Evidencia insuficiente",
    ND_PARTIAL_EVIDENCE: "Evidencia insuficiente en parte de los contratos",
    ND_NO_STRENGTH_UNDETAILED: "Falta evidencia afirmativa de fortaleza",
    ND_CONTRACT_NOT_EVALUABLE: "Contrato no evaluable",
    ND_SOURCE_CLASS_CRITICAL: "Clase de la fuente externa sin determinar",
    ND_NOT_JUDGEABLE: "Assessment aún no juzgable",
    ND_EVIDENCE_V3_REQUIRED: "Evidencia canónica desactualizada",
    ND_UNSPECIFIED: "No determinable",
}
_JDG_DIMENSIONS = (("SOURCE_INGESTION", "Ingesta"), ("HARMONIZATION", "Armonización"),
                   ("PROPAGATION", "Propagación"), ("PUBLISHED_CONTRACT", "Contrato publicado"))
_JDG_CLASSIFICATION = {"MATERIAL_DEVIATION": "Desvío material", "RELEVANT_WEAKNESS": "Debilidad relevante",
                       "LOCAL_WEAKNESS": "Debilidad local", "POSITIVE_EVIDENCE": "Evidencia positiva"}
_JDG_STATEMENT = {
    "har.legitimate_reusable_boundary_over_governed_contract": "Capa de armonización 1:1 legítima y reutilizable sobre un contrato gobernado",
    "har.reusable_semantic_enrichment": "Enriquecimiento semántico reutilizable consumido aguas abajo",
    "pro.product_composes_resolved_contracts": "El producto compone contratos ya resueltos (no reintegra la fuente)",
    "pub.semantically_intentional_consumer_contract": "Contrato publicado intencional y autónomo para el consumidor",
    "src.governed_acquisition_explicit": "Adquisición de la fuente gobernada en todo contacto con el origen",
    "src.explicit_incremental_acquisition_observed": "Carga incremental explícita observada en la adquisición",
    "pro.product_node_reperforms_integration": "Un nodo de producto reintegra la fuente o rearma la armonización",
    "lek.unrestored_responsibility_leakage": "Responsabilidad estructural no restaurada que se filtra aguas abajo",
    "har.technical_projection_with_downstream_semantic_consequence": "Proyección técnica que traslada vocabulario no armonizado al consumidor",
    "har.fragmented_layers_with_undeclared_roles": "Capas de armonización fragmentadas con roles no declarados",
    "lek.unrestored_raw_vocabulary_reaches_published_without_contract_centrality": "Vocabulario crudo no restaurado llega al contrato publicado sin centralidad de contrato",
    "src.physical_boundary_bypassed_and_compensated_downstream": "Frontera física cruda evitada y compensada aguas abajo",
    "pro.branch_alignment_completed_at_product": "Alineación de ramas completada recién en el producto",
    "src.physical_reach_without_bypass_and_compensation": "Acceso a tabla física cruda (sin bypass ni compensación material)",
}
_JDG_CONTRACT_STATE_TEXT = {"positive": "Evidencia positiva", "attention": "Requiere atención", "neutral": "Sin señal específica"}
# Estado del contrato → tono canónico del Hub (valencia: positivo / atención / neutro); sin mapa de color propio.
_CONTRACT_TONE = {"positive": POSITIVE, "attention": ATTENTION, "neutral": NEUTRAL}


def _plural_es(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def nd_explanation(judgment: dict | None, profile: dict | None = None) -> dict | None:
    """T2: ¿POR QUÉ el Juicio no llegó a un nivel? ``None`` si hay nivel (o no hay Juicio). Complementaria: el valor oficial sigue siendo «No determinable».

    Devuelve ``{cause, label, text, dimensions: [{dimension, state_text, cause_text}], reason_code, level_rule}``. Causas (cada una con su propio texto):
      · SUFFICIENT_EVIDENCE_NO_STRENGTH — LVL-06 y TODAS las dimensiones sin fortaleza se evaluaron (``NO_STRENGTH``): no hay desvíos ni debilidades relevantes; falta evidencia AFIRMATIVA de fortaleza.
      · INSUFFICIENT_EVIDENCE / PARTIALLY_INSUFFICIENT_EVIDENCE — LVL-06 con dimensiones ``INSUFFICIENT_EVIDENCE`` (todas / solo algunas): falta evidencia para poder afirmar.
      · NO_AFFIRMATIVE_EVIDENCE_UNDETAILED — LVL-06 de un Juicio persistido SIN detalle: no se puede decir si la evidencia alcanzaba; no se afirma nada que el Juicio no registró.
      · CONTRACT_NOT_EVALUABLE (LVL-01) · EXTERNAL_SOURCE_CLASS_UNDETERMINED (LVL-05) · ASSESSMENT_NOT_JUDGEABLE / CANONICAL_EVIDENCE_V3_REQUIRED (sin Juicio emitido).
    ``profile`` (opcional): con el perfil persistido se agrega ``blockers`` (impedimentos concretos por dimension: artefactos, evidencia requerida, tipo de resolucion; proyeccion de
    ``explain_determination``, sin recalcular el nivel). Sin perfil la salida es exactamente la de siempre."""
    if not judgment or (judgment.get("status") == "DETERMINED" and judgment.get("level") is not None):
        return None
    code = judgment.get("not_determinable_reason") or ""
    rule = judgment.get("level_rule")
    detail = judgment.get("not_determinable_detail") or {}
    missing = [d for d in detail.get("missing_strength_dimensions") or [] if isinstance(d, dict)]

    def _dim(d: dict) -> dict:
        return {"dimension": _DIM_LABEL.get(d.get("dimension") or "", d.get("dimension") or ""), "state_text": _JDG_ND_STATE_TEXT.get(d.get("state") or "", ""),
                "cause_text": _JDG_ND_CAUSE.get(d.get("cause") or "", "")}

    def names(ds: list[dict]) -> str:
        return ", ".join(_DIM_LABEL.get(d.get("dimension") or "", d.get("dimension") or "") for d in ds)

    dims: list[dict] = []
    if code == "INSUFFICIENT_AFFIRMATIVE_EVIDENCE" and missing:
        insuf = [d for d in missing if d.get("state") == "INSUFFICIENT_EVIDENCE"]
        nostr = [d for d in missing if d.get("state") != "INSUFFICIENT_EVIDENCE"]
        dims = [_dim(d) for d in missing]
        if not insuf:
            cause = ND_SUFFICIENT_NO_STRENGTH
            local = sum(1 for r in judgment.get("reasons") or [] if r.get("classification") == "LOCAL_WEAKNESS" and r.get("state") not in C.REMOVING_STATES)
            lead = ("Sin deficiencias determinables" if not local else
                    f"Sin desvíos materiales ni debilidades relevantes ({local} {_plural_es(local, 'debilidad local sin efecto', 'debilidades locales sin efecto')} en el nivel)")
            text = f"{lead}; fortaleza no establecida en: {names(nostr)}. La evidencia alcanzó para evaluar; falta evidencia afirmativa de fortaleza."
        elif not nostr:
            cause = ND_INSUFFICIENT_EVIDENCE
            text = f"Evidencia insuficiente para afirmar en: {names(insuf)}. Mientras falte esa evidencia no se puede determinar un nivel."
        else:
            cause = ND_PARTIAL_EVIDENCE
            text = f"Evidencia insuficiente para afirmar en: {names(insuf)}; fortaleza no establecida (evidencia suficiente) en: {names(nostr)}."
    elif code == "INSUFFICIENT_AFFIRMATIVE_EVIDENCE":
        cause = ND_NO_STRENGTH_UNDETAILED
        text = "No hay evidencia afirmativa suficiente en al menos un contrato; este Juicio no detalla en cuáles ni si la evidencia disponible alcanzaba."
    elif code == "CONTRACT_NOT_EVALUABLE":
        cause = ND_CONTRACT_NOT_EVALUABLE
        ne = [_DIM_LABEL.get(d, d) for d in judgment.get("contracts_not_evaluable") or []]
        text = (f"No se pudo evaluar {_plural_es(len(ne), 'el contrato', 'los contratos')}: {', '.join(ne)}; sin esa evaluación no hay nivel." if ne
                else "Al menos un contrato metodológico no pudo evaluarse; sin esa evaluación no hay nivel.")
    elif code == "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL":
        cause = ND_SOURCE_CLASS_CRITICAL
        arts = sorted({a for r in judgment.get("review_candidates") or [] if r.get("rule_id") == "SRC-REV-01" for a in r.get("affected_artifacts") or []})
        scope = f" ({len(arts)} {_plural_es(len(arts), 'artefacto', 'artefactos')})" if arts else ""
        text = (f"La clase del contrato de la fuente externa{scope} (cruda o gobernada) no está determinada y decide cómo se lee el contrato publicado; "
                "mientras no se resuelva no se puede determinar un nivel.")
    elif code == "ASSESSMENT_NOT_JUDGEABLE":
        cause, text = ND_NOT_JUDGEABLE, "El Assessment todavía no está en un estado que permita juzgarlo; no se emitió un Juicio."
    elif code == "CANONICAL_EVIDENCE_V3_REQUIRED":
        cause, text = ND_EVIDENCE_V3_REQUIRED, "El Assessment no tiene la evidencia canónica v3 que el Juicio requiere; hay que regenerarlo con la versión vigente para poder juzgarlo."
    else:
        cause, text = ND_UNSPECIFIED, "El Juicio no llegó a un nivel" + (f" (motivo registrado: {code})." if code else ".")
    out = {"cause": cause, "label": _JDG_ND_LABEL[cause], "text": text, "dimensions": dims, "reason_code": code or None, "level_rule": rule}
    if profile is not None:
        out.update(_nd_blockers_view(explain_determination(judgment, profile)))
    return out


def _plural_n(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


# Explicabilidad de «No determinable» (``explain_determination``): categorias, tipos de resolucion y estados en lenguaje de negocio. El motor entrega CLAVES estables; el texto vive aqui.
_ND_CATEGORY = {"EVIDENCE_NOT_ACQUIRED": "Evidencia no adquirida", "EVIDENCE_NOT_INTERPRETABLE": "Evidencia existente pero no interpretable",
                "SOURCE_CONTRACT_UNDETERMINED": "Contrato de origen sin determinar", "ARCHITECTURAL_RESPONSIBILITY_UNPROVEN": "Responsabilidad arquitectónica sin demostrar",
                "GOVERNANCE_DECISION_PENDING": "Decisión de gobierno pendiente", "ASSESSMENT_PRECONDITION": "Assessment no apto para juzgar", "UNDETERMINED_CAUSE": "Causa no registrada"}
_ND_RESOLUTION = {"TECHNICAL_EVIDENCE": "Evidencia técnica", "GOVERNANCE_DECISION": "Decisión de gobierno", "METHODOLOGICAL_REVIEW": "Revisión metodológica", "MODEL_REMEDIATION": "Remediación del modelo"}
_ND_STATE = {"NOT_EVALUABLE": "No evaluable (falta evidencia)", "UNRESOLVED_NO_POSITIVE_EVIDENCE": "Sin resolver (evidencia suficiente, sin veredicto positivo)"}
_ND_CONFIDENCE = {"HIGH": "Alta", "MEDIUM": "Media", "LOW": "Baja"}
_ND_REQUIRED = {
    "SOURCE_CONTACT_EVIDENCE": "Evidencia del contacto con la fuente", "SOURCE_CONTRACT_CLASS": "Clase explícita (gobernada o cruda) del contrato de la fuente",
    "SOURCE_CONTRACT_CLASS_DECISION": "Clase del contrato de la fuente, definida por una decisión de gobierno",
    "GOVERNED_ACQUISITION_OF_EVERY_SOURCE_CONTACT": "Adquisición gobernada de todo contacto con la fuente", "HARMONIZATION_ATTRIBUTE_MAPPING": "Mapeo de atributos de la carga que produce el candidato",
    "FIELD_LABEL_ORIGIN_OF_THE_CANDIDATE": "Origen de la etiqueta de cada campo del candidato", "REUSABLE_HARMONIZATION_BOUNDARY_OR_JUSTIFIED_NOT_APPLICABLE": "Una frontera de armonización reutilizable o su justificación de «no aplica»",
    "REUSABLE_HARMONIZATION_BOUNDARY": "Una frontera de armonización técnica reutilizable sobre un contrato gobernado", "PRODUCT_POSITION_NODE": "Un nodo de producto identificable",
    "PRODUCT_COMPOSES_ONLY_RESOLVED_CONTRACTS": "Evidencia de que el producto compone solo contratos ya resueltos", "PUBLISHED_FIELD_LINEAGE": "Linaje completo de cada campo publicado",
    "PUBLISHED_CONTRACT_WITHOUT_UNRESOLVED_VOCABULARY": "Contrato publicado sin vocabulario sin resolver", "PUBLISHED_CONTRACT_AFFIRMATIVE_FAMILY": "Evidencia afirmativa propia del contrato publicado",
    "PUBLISHED_CONTRACT_CONSUMPTION_SUBSTANCE": "Sustancia de consumo del contrato publicado", "AFFIRMATIVE_EVIDENCE_OF_THE_DIMENSION": "Evidencia afirmativa de la dimensión",
    "AT_LEAST_ONE_AFFIRMATIVE_STRENGTH": "Al menos una fortaleza demostrada", "ASSESSED_AREA_EVIDENCE": "Evidencia evaluada en las áreas del contrato", "ASSESSED_ASSESSMENT": "Un Assessment en estado evaluado",
    "CANONICAL_EVIDENCE_V3": "Evidencia canónica v3 (regenerar el Assessment)"}
_ND_HINT = {
    "HARMONIZATION_CANDIDATE_CHARACTER_NOT_DETERMINABLE": "Verificar el origen de las etiquetas de los campos del candidato —en especial los derivados, que no tienen un campo proveedor con el cual compararlas— y completar esa evidencia.",
    "HARMONIZATION_MAPPING_NOT_OBSERVABLE": "Adquirir la carga (flujo) que produce cada candidato indicado o su mapeo de atributos, o confirmar que no existe.",
    "SOURCE_CLASS_RATIFICATION_HAS_NO_EFFECT_OUTSIDE_LVL05": "Una ratificación de gobierno no resuelve este caso hoy: la clase de estas fuentes no es decisión-crítica para este modelo. Se necesita una revisión metodológica de cómo se reconoce su clase.",
    "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL": "Decidir si el contrato de cada fuente indicada es gobernado (ratificación de gobierno con su justificación) o crudo; la decisión es del owner, no del Assessment.",
    "PUBLISHED_LINEAGE_INCOMPLETE": "Completar la evidencia de linaje de los campos indicados (re-extracción o consulta que permita rastrearlos hasta la fuente).",
    "NO_SOURCE_CONTACT_OBSERVED": "Verificar que la extracción incluya las hojas de origen del modelo.",
    "NO_PRODUCT_NODE_OBSERVED": "Verificar que la extracción incluya el nodo que compone el modelo analítico.",
    "CONTRACT_NOT_EVALUABLE": "Completar la evidencia de las áreas indicadas y regenerar el Assessment.",
    "ASSESSMENT_NOT_JUDGEABLE": "Regenerar el Assessment hasta que quede evaluado.", "CANONICAL_EVIDENCE_V3_REQUIRED": "Regenerar el Assessment con la versión vigente de la evidencia."}
_ND_UNMET = {"measures_business_identity": "medidas con identidad de negocio", "no_source_column_names": "ausencia de nombres técnicos de columna de la fuente",
             "lineage_complete": "linaje completo de los campos", "no_vocabulary_leak": "ausencia de vocabulario sin resolver", "field_level_affirmative_family": "una familia afirmativa a nivel de campo"}
_ND_REASON_UNKNOWN = {"NO_ACQUIRED_PRODUCER": "no se adquirió ninguna carga que lo produzca", "NO_ATTRIBUTE_MAPPINGS_EXPORTED": "la carga no exportó el mapeo de atributos",
                       "MAPPING_NOT_DETERMINABLE": "el mapeo no es determinable", "NO_QUERY_EVIDENCE": "sin evidencia de la consulta", "NO_FIELDS": "sin campos",
                       "LABELS_NOT_DETERMINABLE": "las etiquetas de sus campos no son atribuibles a un proveedor"}
_ND_LIMIT = {"UNKNOWN_LABEL_FIELDS_NOT_PERSISTED": "El perfil registra cuántos campos tienen etiqueta indeterminada, no cuáles; identificarlos exige volver a ejecutar el motor sobre el pool.",
             "HISTORICAL_JUDGMENT_WITHOUT_DIMENSION_STATES": "Juicio anterior a judgment-rules/v4: los estados por dimensión se derivan del detalle persistido; el diagnóstico es menos preciso.",
             "NO_VERIFIABLE_CAUSE_PERSISTED": "El Juicio no registró un detalle suficiente para identificar el impedimento; no se infiere una causa.",
             "CAUSE_NOT_RECOGNIZED": "El Juicio registró una causa que esta versión del explicador no reconoce.", "PROFILE_NOT_READABLE": "No se pudo leer el perfil: solo se informa lo que registró el Juicio.",
             "DETAIL_NOT_RECOVERABLE": "No se pudo recuperar el detalle de un impedimento.", "CONTRACTS_NOT_EVALUABLE_NOT_LISTED": "El Juicio no registró cuáles contratos no pudieron evaluarse."}
_ND_FOOTNOTE = "Resolver un impedimento no garantiza un nivel concreto: habilita que el Juicio se determine con el resto de la evidencia."


def _first(names: list, n: int = 4) -> str:
    shown = [str(x) for x in names[:n]]
    return ", ".join(shown) + (f" (+{len(names) - n})" if len(names) > n else "")


def _nd_why(b: dict) -> str:
    """Texto concreto de un impedimento, SOLO desde la causa estable y los hechos del bloqueo (sin inferencias nuevas)."""
    c, f, arts = b["condition_id"], b.get("facts") or {}, b.get("artifacts") or []
    if c == "SOURCE_CONTRACT_CLASS_UNDETERMINED":
        return f"La clase del contrato de {_plural_n(len(arts), 'fuente externa', 'fuentes externas')} no está determinada ({_first(arts)}): no se puede afirmar si son contratos gobernados o crudos."
    if c == "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL":
        n = sum(len(x.get("exposed_published_fields") or []) for x in f.get("leaves") or [])
        return (f"La clase del contrato de {_plural_n(len(arts), 'fuente externa', 'fuentes externas')} ({_first(arts)}) no está determinada y su vocabulario llega al contrato publicado "
                f"({_plural_n(n, 'campo', 'campos')}): decide cómo se lee el contrato publicado.")
    if c == "HARMONIZATION_MAPPING_NOT_OBSERVABLE":
        why = sorted({_ND_REASON_UNKNOWN.get(x.get("reason_unknown") or "", "") for x in f.get("candidates") or [] if x.get("reason_unknown")} - {""})
        return f"No se puede observar el mapeo de {_plural_n(len(arts), 'candidato', 'candidatos')} de armonización ({_first(arts)})" + (f": {'; '.join(why)}." if why else ".")
    if c == "HARMONIZATION_CANDIDATE_CHARACTER_NOT_DETERMINABLE":
        parts = []
        for x in f.get("candidates") or []:
            s = f"No se puede determinar el carácter del candidato de armonización {x['artifact']}: {_plural_n(x.get('unknown_label_field_count') or 0, 'campo', 'campos')}"
            s += f" de {x['field_count']}" if x.get("field_count") else ""
            s += " con etiqueta de origen no atribuible a un proveedor."
            s += (f" Alimenta al producto ({_first(x['consumed_by'])})." if x.get("feeds_product") else (f" Lo consume {_first(x['consumed_by'])}." if x.get("consumed_by") else " No tiene consumidores en el cierre."))
            if x.get("derived_fields"):
                s += f" Tiene {_plural_n(len(x['derived_fields']), 'campo derivado', 'campos derivados')}: {_first([d['field'] for d in x['derived_fields']])} ({', '.join(sorted(set(x.get('transformation_classes') or [])))})."
                if x.get("unknown_labels_equal_derived_fields"):
                    s += " La cantidad de etiquetas indeterminadas coincide con la de campos derivados; el perfil no indica cuál es."
            parts.append(s)
        return " ".join(parts) or (_JDG_ND_CAUSE[c][:1].upper() + _JDG_ND_CAUSE[c][1:] + ".")
    if c == "HARMONIZATION_NOT_A_LEGITIMATE_REUSABLE_BOUNDARY":
        cs = f.get("candidates") or []
        kinds = ", ".join(f"{x['artifact']} ({(x.get('character') or 'sin carácter').lower().replace('_', ' ')})" for x in cs[:4])
        return f"Hay {_plural_n(len(cs), 'candidato', 'candidatos')} de armonización, pero ninguno se reconoce como frontera técnica reutilizable legítima sobre un contrato gobernado: {kinds}" + (f" (+{len(cs) - 4})." if len(cs) > 4 else ".")
    if c == "NO_HARMONIZATION_BOUNDARY_OBSERVED":
        v = f.get("views_with_joins_or_unions") or []
        return "No se observa una capa de armonización y no se puede justificar que no aplique" + (f": {_first(v)} integra{'n' if len(v) > 1 else ''} varias entradas." if v else ".")
    if c == "PRODUCT_DOES_NOT_COMPOSE_RESOLVED_CONTRACTS":
        rows = f.get("product_nodes") or []
        reads = "; ".join(f"{r['node']} lee {', '.join(_first(v) for v in r['providers_by_kind'].values())}" for r in rows[:3])
        return "El producto no compone solo contratos ya resueltos" + (f" ({reads})." if reads else ".")
    if c == "PUBLISHED_LINEAGE_INCOMPLETE":
        return f"El linaje de {_plural_n(f.get('incomplete_field_count') or 0, 'campo publicado', 'campos publicados')} no llega a la fuente ({_first(f.get('incomplete_fields') or [])})."
    if c in ("PUBLISHED_CONTRACT_SUBSTANCE_NOT_ESTABLISHED", "NO_AFFIRMATIVE_PUBLISHED_FAMILY", "UNRESOLVED_VOCABULARY_REACHES_THE_CONSUMER"):
        unmet = ", ".join(_ND_UNMET.get(u, u) for u in f.get("unmet_conditions") or [])
        return "El contrato publicado no acredita" + (f" {unmet}." if unmet else " evidencia afirmativa propia.")
    if c == "CONTRACT_NOT_EVALUABLE":
        areas = ", ".join(f"{P.area_title(a)} ({P.assessability_text(s)})" for a, s in sorted((f.get("areas") or {}).items()) if s)
        return "El área del contrato no está evaluada" + (f": {areas}." if areas else ".")
    if c == "NO_STRENGTH_ANCHOR":
        return "Las cuatro dimensiones tienen veredicto, pero ninguna es una fortaleza demostrada: el nivel exige al menos una."
    if c == "NO_SOURCE_CONTACT_OBSERVED":
        return "No se observó contacto con una fuente: el cierre no incluye hojas de origen que permitan evaluar la ingesta."
    if c == "NO_PRODUCT_NODE_OBSERVED":
        return "No se identificó un nodo de producto entre las fuentes y el modelo analítico."
    if c == "SOURCE_CONTACT_NOT_ALL_GOVERNED":
        return "No todo contacto con la fuente es un contrato gobernado y no hay evidencia afirmativa que lo compense."
    if c == "ASSESSMENT_NOT_JUDGEABLE":
        return "El Assessment todavía no está en un estado que permita juzgarlo."
    if c == "CANONICAL_EVIDENCE_V3_REQUIRED":
        return "El Assessment no tiene la evidencia canónica v3 que el Juicio requiere."
    return _JDG_ND_CAUSE.get(c) or "El Juicio no registró un detalle suficiente para identificar el impedimento."


def _nd_blockers_view(exp: dict | None) -> dict:
    """Filas de presentacion de ``explain_determination`` (solo vocabulario y orden; ningun hecho se recalcula)."""
    if not exp:
        return {}
    rows = []
    for i, b in enumerate(exp["blockers"], 1):
        dim = b.get("dimension")
        arts = list(b.get("artifacts") or [])
        why = _nd_why(b)
        rows.append({
            "dom_id": f"asr-nd-{i}", "dimension": _DIM_LABEL.get(dim or "", "Juicio global"), "area_id": (C.CONTRACT_AREAS.get(dim or "", (None,))[-1] if dim else None),
            "state_text": _ND_STATE.get(b["state"], b["state"]), "category_text": _ND_CATEGORY.get(b["category"], b["category"]), "why": why, "short": why.split(". ")[0].rstrip("."),
            "artifacts": arts[:8], "artifacts_more": max(0, len(arts) - 8), "required": [_ND_REQUIRED.get(k, k) for k in b.get("required_evidence") or []],
            "resolution_text": _ND_RESOLUTION.get(b["resolution_type"], b["resolution_type"]), "hint": _ND_HINT.get(b.get("resolution_hint_key") or ""),
            "confidence_text": _ND_CONFIDENCE.get(b["confidence"], b["confidence"]), "corroborated": b.get("corroborated"), "evidence_refs": list(b.get("evidence_refs") or []),
            "condition_id": b["condition_id"], "rule_id": b.get("rule_id"), "lineage_url": None})
    return {"blockers": rows, "blockers_summary": "; ".join(f"{r['dimension']}: {r['short']}" for r in rows[:3]) + (f" (+{len(rows) - 3} más)" if len(rows) > 3 else ""),
            "blockers_resolved": [{"dimension": _DIM_LABEL.get(r["dimension"] or "", r["dimension"]), "state_text": {"STRENGTH": "Fortaleza", "RESPONSIBILITY_MET": "Responsabilidad cumplida", "DEFICIENCY_DEMONSTRATED": "Deficiencia demostrada (con efecto local)", "NOT_APPLICABLE": "No aplica"}.get(r["state"], r["state"])} for r in exp["resolved_dimensions"]],
            "diagnosis_limits": [_ND_LIMIT.get(l["key"], l["key"]) for l in exp["diagnosis_limits"]], "corroboration": exp["corroboration"]["status"], "blockers_footnote": _ND_FOOTNOTE}


def _jdg_statement_text(item: dict) -> str:
    return _JDG_STATEMENT.get(item.get("statement_key") or "") or _DIM_LABEL.get(item.get("dimension") or "", "Observación metodológica")


_DIM_LABEL = dict(_JDG_DIMENSIONS)


def judgment_view(judgment: dict | None, profile: dict | None = None) -> dict | None:
    """View-model del Juicio metodológico PERSISTIDO. Solo presentación: lee el resultado ya decidido (status/level,
    razones, fortalezas, motivo de no-determinación) y lo traduce. Devuelve ``None`` si no hay Juicio (estado sin perfil)."""
    if not judgment:
        return None
    status, level = judgment.get("status"), judgment.get("level")
    determined = status == "DETERMINED" and level is not None

    def _rows(items, with_kind=False):
        seen, out = set(), []
        for it in items or []:
            key = (it.get("dimension"), it.get("statement_key"))
            if key in seen:
                continue
            seen.add(key)
            row = {"dimension": _DIM_LABEL.get(it.get("dimension") or "", it.get("dimension") or ""), "text": _jdg_statement_text(it)}
            if with_kind:
                row["kind"] = _JDG_CLASSIFICATION.get(it.get("classification") or "", it.get("classification") or "")
            out.append(row)
        return out

    strengths = _rows(judgment.get("strengths"))
    reasons = _rows(judgment.get("reasons"), with_kind=True)
    strong_dims = {s.get("dimension") for s in judgment.get("strengths") or []}
    weak_dims = {r.get("dimension") for r in judgment.get("reasons") or []}
    contracts = []
    for dim, label in _JDG_DIMENSIONS:
        state = "attention" if dim in weak_dims else ("positive" if dim in strong_dims else "neutral")
        contracts.append({"label": label, "state": state, "state_text": _JDG_CONTRACT_STATE_TEXT[state], "tone": _CONTRACT_TONE[state]})
    nd = nd_explanation(judgment, profile)
    nd_reason = None if determined else (nd["label"] if nd else "No determinable")
    nd_detail = (judgment.get("not_determinable_detail") or {}) if not determined else {}
    nd_missing = [{"dimension": _DIM_LABEL.get(d.get("dimension") or "", d.get("dimension") or ""), "state": d.get("state"),
                   "state_text": _JDG_ND_STATE_TEXT.get(d.get("state") or "", ""), "cause_text": _JDG_ND_CAUSE.get(d.get("cause") or "", "")}
                  for d in nd_detail.get("missing_strength_dimensions") or []]
    headline = (_JDG_LEVEL_HEADLINE.get(level or "") or _JDG_GENERIC_HEADLINE) if determined else (_JDG_ND_HEADLINE + (" " + nd_reason if nd_reason else ""))
    if nd:
        headline = nd["text"]                      # T2: texto propio de la causa real (ver ``nd_explanation``); el nivel oficial sigue siendo «No determinable»
        if nd.get("blockers"):                     # con el perfil: el resumen CONCRETO de los impedimentos reemplaza (no se suma a) el texto por dimensiones: una sola explicación por pantalla
            headline = f"{_JDG_ND_HEADLINE} {nd['blockers_summary']}."
    return {
        "determined": determined,
        "level": level,
        "level_label": _JDG_LEVEL_LABEL.get(level or "", level) if determined else "No determinable",
        "tone": status_tone(level) if determined else "neutra",
        "headline": headline,
        "nd_missing": nd_missing,
        "nd": nd,
        "nd_evidence_sufficient": nd_detail.get("evidence_sufficient") if nd_missing else None,
        "nd_reason": nd_reason,
        "nd_reason_id": None if determined else judgment.get("not_determinable_reason"),
        "strengths": strengths,
        "reasons": reasons,
        # §5/§6 — las agrupaciones son SOLO presentación: dos granularidades distintas conviven sin inconsistencia.
        # CONTRATOS = 4 (una fila por dimensión metodológica); EVIDENCIAS = ítems deterministas individuales, varios por contrato.
        # Se preserva cada ítem canónico; solo se agrupan por dimensión para que una dimensión repetida no parezca un error.
        "strengths_grouped": _group_by_dimension(strengths),
        "reasons_grouped": _group_by_dimension(reasons),
        "strengths_count": len(strengths),
        "reasons_count": len(reasons),
        "tiles": [{"value": len(strengths), "label": "Evidencias favorables"}, {"value": len(reasons), "label": "Puntos de atención"}],
        "contracts": contracts,
        "ruleset_version": judgment.get("ruleset_version"),
        "schema_version": judgment.get("judgment_schema_version"),
    }


_DIM_ORDER = [label for _, label in _JDG_DIMENSIONS]


def _group_by_dimension(rows: list[dict]) -> list[dict]:
    """Agrupa filas de evidencia por dimensión (label), en el orden canónico de los contratos. Presentación pura: no
    descarta ni fusiona ítems; una dimensión con N ítems queda ``{dimension, count: N, items:[...]}``."""
    buckets: dict[str, list] = {}
    for r in rows:
        buckets.setdefault(r["dimension"], []).append(r)
    ordered = sorted(buckets, key=lambda d: (_DIM_ORDER.index(d) if d in _DIM_ORDER else len(_DIM_ORDER), d))
    # clave ``rows`` (no ``items``): en Jinja ``grp.items`` resolvería al método del dict, no a la lista.
    return [{"dimension": d, "count": len(buckets[d]), "rows": buckets[d]} for d in ordered]
# END judgment-presentation (Gate 8D.2)


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
                          f"({P.reason_text(state['reason'])}); pueden existir más ocurrencias fuera de la evidencia que pudo evaluarse.")
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


_LAYER_PREFIX = re.compile(r"^\d?(?:AM|MA)_", re.I)


def readable_model_name(technical: str) -> str:
    """Nombre técnico legible del modelo terminal, sin inventar nada: se quita SOLO el prefijo de capa (``4MA_``/``AM_``), se separa por «_» y por camelCase, y las
    palabras en MAYÚSCULAS se capitalizan (las de ≤2 letras, p. ej. «MP», se conservan). ``4MA_PEDIDOS_ENTREGAS`` → «Pedidos Entregas»."""
    core = re.sub(r"(?<=[a-z])(?=[A-Z])", "_", _LAYER_PREFIX.sub("", technical or ""))
    words = [w if len(w) <= 2 or not w.isupper() else w.capitalize() for w in re.split(r"[_\s]+", core) if w]
    return " ".join(words) or technical


NO_TERMINAL_TITLE = "Flujo sin modelo terminal"          # fallback NEUTRO: solo cuando no hay identidad terminal verificable (el dominio legacy queda como metadato interno)


def model_title(entry: Any, am: str | None) -> tuple[str | None, str | None]:
    """(título, identificador técnico) de un modelo terminal, con prioridad única: 1) nombre de negocio (etiqueta del Catálogo, sin el prefijo genérico) y, si no
    hay, 2) nombre técnico LEGIBLE (``readable_model_name``). Es el resolver que comparten el detalle del Catálogo y el informe de Assessment; el identificador técnico
    se conserva aparte (en el informe se muestra bajo el título). No usa dominio ni agrupación legacy."""
    if not am:
        return None, None
    desc, name = _report_title(entry, am)
    return (desc if desc and desc != name else readable_model_name(am)), am


def terminal_titles(status: svc.AssessmentStatus, entry: Any, doc: dict | None, adhoc: bool = False) -> tuple[str | None, str | None]:
    """(título, identificador técnico) del MODELO TERMINAL del Assessment: el MISMO resolver para el informe y para el encabezado del Catálogo. Prioridad única: nombre de
    negocio del perfil canónico → nombre de negocio del Catálogo → nombre técnico legible; sin modelo terminal, ``NO_TERMINAL_TITLE``. Solo presentación: no lee ni modifica el
    documento canónico más allá de su etiqueta, y nunca usa dominio/agrupación legacy como título."""
    am = status.terminal_am
    canonical = _canonical_label(doc)
    if canonical and am:
        return canonical, am
    if adhoc:
        return None, am
    return model_title(entry, am) if am else (NO_TERMINAL_TITLE, None)


def entry_title(status: svc.AssessmentStatus, entry: Any) -> tuple[str | None, str | None]:
    """(título, identificador técnico) del modelo de una entrada con el estado YA leído: el MISMO resolver que el encabezado del detalle del Catálogo (``header_view``) y el informe
    (``terminal_titles``), para que un listado (p. ej. el del asistente) nombre cada entrada igual que el Catálogo. Nunca usa el dominio legado."""
    return terminal_titles(status, entry, P.display_doc(status.profile) if status.profile is not None else None)


def flow_heading(entry: Any, header: dict | None = None) -> tuple[str, str | None]:
    """(título, identificador técnico) del encabezado del detalle del flujo. Con el header del Assessment usa SU título resuelto (``terminal_titles``: idéntico al del informe);
    sin él (el Assessment no pudo calcularse) resuelve por el Catálogo con el único artefacto de consumo. Sin modelo terminal identificable → ``NO_TERMINAL_TITLE``."""
    if header and "title_desc" in header:
        return header["title_desc"] or NO_TERMINAL_TITLE, header.get("title_id")
    ams = catalog_reads.consumption_artifacts(entry)
    return model_title(entry, ams[0]) if len(ams) == 1 else (NO_TERMINAL_TITLE, None)


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
                                  ("Vistas del cierre (nombre técnico y tipo)", ["view_artifacts"])]},
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
        "No mide la calidad del modelo y es independiente de la cobertura y de las preguntas aún abiertas."),
    "review_required": (
        "Casos concretos detectados por el análisis que requieren criterio técnico del consultor antes de poder cerrarse. No implican defectos confirmados.",
        "Cuenta ocurrencias y no mide la calidad del modelo."),
    "context_open": (
        "Información que aún debe aportar el equipo del proyecto para completar o interpretar el análisis. Una misma pregunta puede estar relacionada con varios hallazgos.",
        "Cuenta tipos de pregunta, no ocurrencias, y no mide la calidad ni la confianza."),
}


# Ayuda (i) de los indicadores de Propagación y Armonización, por id de hecho (mismo primitivo del Hub que los KPI superiores). Redacción alineada con el
# cálculo canónico: unidades y límites exactos; sin juicio de calidad.
AREA_KPI_HELP: dict[str, tuple[str, ...]] = {
    "lineage_steps_evaluated": (
        "Relaciones proveedor→consumidor cuyo contrato pudo compararse. Cuenta pasos de linaje, no artefactos; una relación puede resultar idéntica o mostrar cambios.",),
    "artifacts_with_derived_fields": (
        "Artefactos intermedios con al menos un campo calculado mediante una expresión visible. Cada artefacto cuenta una vez, aunque derive varios campos.",
        "Sin query visible la superficie puede quedar parcialmente evaluada: un cero no garantiza ausencia."),
    "rederived_field_occurrences": (
        "Ocurrencias donde un campo derivado vuelve a calcularse, con el mismo nombre técnico, en otro artefacto aguas arriba. Un mismo campo puede generar más de "
        "una ocurrencia. El consultor determina si la repetición es intencional.",),
    "declared_dimensions": (
        "Artefactos del cierre declarados como dimensión por los metadatos del modelo. No se infieren por nombre ni por capa.",),
    "composition_artifacts": (
        "Artefactos que combinan dos o más proveedores directos de linaje. Describe su estructura y no implica por sí solo una desviación metodológica.",),
    "cross_model_reuse_artifacts": (
        "Entre las dimensiones y destinos de asociación evaluados, cuenta los artefactos que también aparecen en otros modelos analíticos del cliente. "
        "No representa todos los artefactos del cierre.",),
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


def _facts_view(area_id: str, highlights: list[dict], extras: dict | None = None) -> dict:
    """Hechos descriptivos de un área en dos niveles de lectura: ``headline`` (tiles KPI de síntesis) y ``groups`` (evidencia de apoyo). La población de vistas
    de vistas del cierre (``views_in_closure`` / ``view_type_distribution``) NO se muestra en el área: es transversal y vive en el resumen de la sección
    (``closure_views``); en el área solo queda el detalle por artefacto (``view_artifacts``)."""
    extras = extras or {}
    hidden = extras.get("hide") or set()
    highlights = [h for h in highlights if h["id"] not in hidden]                     # indicadores de federación sin artefactos remotos / sin evidencia evaluada: no se muestran
    if extras.get("breakdown") is not None:                                           # Ingesta: mismo hecho, desglose con la procedencia ya resuelta
        highlights = [({**h, "breakdown": extras["breakdown"]} if h["id"] == "boundary_artifacts" and h.get("breakdown") else h) for h in highlights]
    closure = next((h for h in highlights if h["id"] == "views_in_closure"), None) if area_id == "ANALYTIC_MODEL" else None
    structural = P.has_structural_membership(highlights)
    highlights = [{**h, "label": P.highlight_label(h)} for h in highlights
                  if not (area_id == "ANALYTIC_MODEL" and h["id"] in ("views_in_closure", "view_type_distribution"))]   # etiqueta vigente aun en perfiles anteriores
    by_id = {h["id"]: h for h in highlights}
    layout = FACT_LAYOUT.get(area_id)
    if layout is None:
        numbers = [h for h in highlights if _is_number(h["value"]) and not h.get("breakdown")]
        layout = {"headline": [h["id"] for h in numbers[:3]], "groups": [("Hechos de apoyo", [h["id"] for h in highlights])]}
    head_ids = [i for i in layout["headline"] if i in by_id and _is_number(by_id[i]["value"])]
    headline = [{"label": by_id[i]["label"], "value": by_id[i]["value"], **({"help": list(AREA_KPI_HELP[i])} if i in AREA_KPI_HELP else {})} for i in head_ids]
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
    if extras.get("table"):                                                           # tabla de adquisición (Fuente: primera) / de carga (Ingesta: tras la frontera)
        t = extras["table"]
        eg = {"title": t["title"], "properties": [], "tables": [t]}
        groups = [eg, *groups] if area_id == "SOURCE_EXTRACTION" else [*groups, eg]
    notes = list(extras.get("notes") or [])
    for g in groups:                                                                      # grupo de una sola tabla: el título de la tabla ES el del grupo
        if not g["properties"] and len(g["tables"]) == 1:
            g["title"] = g["tables"][0]["title"]
    closure_views = None
    if closure is not None:
        n = {b["name"]: b["count"] for b in closure.get("breakdown") or []}
        closure_views = {"total": closure["value"], "graphical": n.get("gráficas", 0), "sql": n.get("SQL", 0), "unknown": n.get("sin clasificar", 0), "structural": structural}
    return {"headline": headline, "groups": groups, "notes": notes, "closure_views": closure_views}


def _view_overview(cv: dict) -> dict:
    """Tiles del resumen transversal de vistas del cierre (valores del hecho canónico `am.view_type_composition`, sin recalcular). "Sin clasificar" solo si > 0;
    en pools sin carpetas por tipo se agrega la salvedad breve (una vista sin marcador no es detectable)."""
    tiles = [{"label": "Total de vistas", "value": cv["total"]}]
    if cv["total"]:
        tiles += [{"label": "Vistas gráficas", "value": cv["graphical"]}, {"label": "Vistas SQL", "value": cv["sql"]}]
        if cv["unknown"]:
            tiles.append({"label": "Sin clasificar", "value": cv["unknown"]})
    return {"title": "Vistas del cierre semántico", "tiles": tiles, "caveat": P.structural_views_caveat() if cv["structural"] and cv["total"] else None}


def _area_extras(a: dict, doc: dict) -> dict | None:
    """Extras de presentación de Fuente / Extracción e Ingesta desde la evidencia canónica persistida (``assessment_acquisition``); ``None`` = presentación vigente."""
    if a["id"] == "SOURCE_EXTRACTION":
        return AQ.source_facts(doc)
    if a["id"] == "INGESTION":
        load = AQ.load_facts(doc) or {}
        bd = next((h.get("breakdown") for h in a["highlights"] if h["id"] == "boundary_artifacts" and h.get("breakdown")), None)
        breakdown = P.boundary_breakdown(doc, bd) if bd else None            # misma proyección que el Markdown (``presentation``)
        hide = AQ.ingestion_hidden(doc) if AQ._leaves(doc) is not None else set()
        return {**load, "breakdown": breakdown, "hide": hide} if (load or breakdown or hide) else None
    return None


def _rule_view(r: dict, area_groups: dict[str, dict] | None = None) -> dict:
    """Fila de control. El enlace al detalle sale de la identidad canónica (``rule`` == ``id`` del grupo de hallazgos del área), nunca del
    título; si el grupo no está entre los que el informe renderiza, la fila NO ofrece enlace."""
    g = (area_groups or {}).get(r.get("rule"))
    finding = ({"dom_id": f"asr-g-{g['id']}", "count": g["occurrence_count"], "kind_text": P.finding_kind_text(g["kind"])}
               if g is not None else None)
    return {"rule": r.get("rule"), "title": r["title"], "result": r["result"], "result_text": P.rule_result_text(r["result"]),
            "reason": P.reason_text(r["reason"]) if r["reason"] else None, "population": r.get("population"),
            "finding": finding, "dom_id": f"asr-r-{r['rule']}" if r.get("rule") else None}


def _area_view(a: dict, groups_by_id: dict[str, dict], rules_by_area: dict[str, list[dict]], doc: dict | None = None) -> dict:
    rs = a["required_surfaces"]
    rules = rules_by_area.get(a["id"], [])
    split = AQ.split_rules(doc, a["id"], rules) if doc is not None else None
    area_groups = {gid: groups_by_id[gid] for gid in a["finding_groups"] if gid in groups_by_id}
    rule_ids = {r.get("rule") for r in rules}
    return {
        "id": a["id"], "dom_id": f"asr-a-{a['id']}", "title": P.area_title(a["id"]), "macro": P.macro_dimension_title(a["macro_dimension"]),
        "state": a["assessability"], "state_text": P.assessability_text(a["assessability"]),
        "reason": (f"{P.reason_text(a['reason'])} ({a['reason']['reason_code']})" if a["reason"] else None),
        "surfaces": (f"{_plural(rs['assessed'], 'evaluada')}, {_plural(rs['partially_assessed'], 'parcialmente evaluada')}, "
                     f"{_plural(rs['not_assessed'], 'no evaluada')}, {rs['not_applicable']} no aplican"),
        "facts": _facts_view(a["id"], a["highlights"], _area_extras(a, doc) if doc is not None else None), "macro_id": a["macro_dimension"],
        "findings": [{"title": groups_by_id[gid]["title"], "dom_id": f"asr-g-{gid}", "count": groups_by_id[gid]["occurrence_count"],
                      "kind_text": P.finding_kind_text(groups_by_id[gid]["kind"])} for gid in a["finding_groups"]],
        # Hallazgos del área SIN regla que los produzca (p. ej. señales descriptivas): sección residual compacta; con la tabla integrada
        # los demás ya están representados en su fila de control, así que no se repiten en una lista aparte.
        "findings_residual": [{"title": g["title"], "dom_id": f"asr-g-{gid}", "count": g["occurrence_count"], "kind_text": P.finding_kind_text(g["kind"])}
                              for gid, g in area_groups.items() if gid not in rule_ids],
        "rules": [_rule_view(r, area_groups) for r in rules],
        "rules_split": split is not None,
        "rules_open": [_rule_view(r, area_groups) for r in split[0]] if split else [],
        "rules_clean": [_rule_view(r, area_groups) for r in split[1]] if split else [],
        "limitations_not_observable": bool(a["limitations_not_observable"]),
    }


# Por qué existe una pregunta abierta (solo vocabulario visible; los enums canónicos OBSERVATION / REVIEW no cambian).
QUESTION_LINK_LABELS = {"OBSERVATION": "Observación", "REVIEW": "Caso a revisar"}
QUESTION_LINK_PROJECT = "Contexto del proyecto"
_NA = "—"                                                                              # concepto no aplicable (jamás un 0 artificial)


def _context_questions(ctx: dict, groups_by_id: dict[str, dict]) -> dict:
    """Preguntas abiertas en UNA tabla: cada fila dice a qué está vinculada (Observación / Caso a revisar / Contexto del proyecto). Es solo
    presentación del mismo bloque canónico ``summary.context_required``: ``backed + independent == open_questions``. El resumen compacto usa los
    conteos canónicos (no los recalcula la plantilla)."""
    rank = {"OBSERVATION": 0, "REVIEW": 1}
    rows: list[dict] = []
    for i, q in enumerate(ctx["questions"]):
        g = groups_by_id.get(q["id"])
        if q["occurrence_count"] is None or g is None:
            rows.append({"order": (2, i), "text": q["question"], "linked_to": QUESTION_LINK_PROJECT, "finding_title": None, "finding_dom_id": None,
                         "occurrences": _NA, "has_finding": False})
            continue
        rows.append({"order": (rank.get(q["origin"], 1), i), "text": q["question"], "linked_to": QUESTION_LINK_LABELS.get(q["origin"], "Caso a revisar"),
                     "finding_title": g["title"], "finding_dom_id": f"asr-g-{q['id']}", "occurrences": q["occurrence_count"], "has_finding": True})
    rows.sort(key=lambda r: r["order"])
    n, backed, independent = ctx["open_questions"], ctx["questions_backed_by_findings"], ctx["questions_without_findings"]
    return {"context_rows": rows, "context_open_total": n,
            "context_summary": f"{n} {'abierta' if n == 1 else 'abiertas'} · {backed} con hallazgo · {independent} contexto de proyecto",
            "context_backed_n": sum(1 for r in rows if r["has_finding"]), "context_independent_n": sum(1 for r in rows if not r["has_finding"])}


def report_view(status: svc.AssessmentStatus, entry: Any, pack: str, adhoc: AdhocReport | None = None) -> dict:
    """View-model del informe. ``status.profile`` es el JSON canónico validado; nada se deriva de otra fuente.
    ``adhoc`` (opcional) reemplaza el contexto del Catálogo (flujo/URLs/linaje) por el de un assessment ad-hoc; el contenido es el mismo."""
    doc = P.display_doc(status.profile) if status.profile is not None else None      # títulos públicos vigentes aun en perfiles persistidos anteriores
    link_entry = None if adhoc else entry
    view: dict[str, Any] = {
        "state": status.state, "state_label": STATE_LABELS[status.state], "state_tone": STATE_TONES.get(status.state, NEUTRAL), "state_note": stale_note(status), "state_body": stale_body(status), "stale_reason": status.stale_reason,
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
        "judgment": judgment_view(status.judgment, doc),   # Juicio metodológico persistido (distinto de la vigencia del Assessment); el perfil habilita los impedimentos concretos de «No determinable»
    }
    for _b in ((view["judgment"] or {}).get("nd") or {}).get("blockers") or []:        # enlace «Ver en linaje» por impedimento (mismo mecanismo que los hallazgos)
        _b["lineage_url"] = _focus_link(link_entry, pack, _b["artifacts"])
    view["title_desc"], view["title_id"] = terminal_titles(status, entry, doc, adhoc is not None)     # canónico → Catálogo → nombre técnico legible; sin modelo terminal: neutro
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
    areas = [_area_view(a, groups_by_id, rules_by_area, doc) for a in doc["areas"]]
    lim = conf["main_limitation"]
    _rev_head, _rev_detail = P.review_required_text(sum(g["occurrence_count"] for g in doc["review_required"]), len(doc["review_required"]))
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
            "context_open": P.context_open_text(ctx)[0],
            "review_open": _rev_head, "relation_text": P.summary_relation_text(),
            "context_open_n": ctx["open_questions"],
            "review_reading": _rev_detail.replace("*", ""),                                   # el Markdown usa *cursiva*; el HTML no la interpreta
            "context_reading": P.context_open_text(ctx)[1],
            **_context_questions(ctx, groups_by_id),
            "no_evidence_dimensions": [P.macro_dimension_title(m["id"]) for m in summary["macro_dimensions"] if m["state"] == "COMPLETELY_NOT_ASSESSED"],
            "observation_groups": len(doc["observations"]), "review_groups": len(doc["review_required"]),
            "observation_occurrences": sum(g["occurrence_count"] for g in doc["observations"]),
            "review_occurrences": sum(g["occurrence_count"] for g in doc["review_required"]),
        },
        "areas": areas,
        "kpis": [
            _kpi("coverage_strict", "Cobertura estricta", P.percent_text(cov["strict"])),
            _kpi("confidence", "Confianza", P.confidence_level_text(conf["level"])),
            _kpi("review_required", "Casos a revisar", sum(g["occurrence_count"] for g in doc["review_required"])),
            _kpi("context_open", "Preguntas abiertas", ctx["open_questions"]),
        ],
        "observations": [_group_view(g, link_entry, pack) for g in doc["observations"]],
        "review_required": [_group_view(g, link_entry, pack) for g in doc["review_required"]],
        "descriptive": [_group_view(g, link_entry, pack) for g in doc["descriptive_signals"]],
        "limitations": [{"title": s["title"], "surface_id": s["surface_id"], "area": P.area_title(s["area"]),
                         "state": P.assessability_text(s["assessability"]), "reason": P.reason_text(s["reason"]) if s["reason"] else "",
                         "reason_code": s["reason"]["reason_code"] if s["reason"] else "",
                         "counts": s["counts_toward_coverage"],
                         "units": [P.unit_label(u) for u in s["affected_units"]]} for s in doc["evidence_limitations"]],
        "evidence_not_acquired": P.evidence_not_acquired(scope.get("evidence_scope")),     # solo alcance CONOCIDO y limitado; del documento, no del formulario
        "absence_not_established": [r["title"] for r in doc["rule_results"] if r["result"] == "ABSENCE_NOT_ESTABLISHED"],
        # Solo DISPLAY: 2 decimales + tono canónico del nivel; el valor exacto queda en `value` (tooltip / detalle técnico) y el JSON/MD no cambian.
        "confidence_components": [{"label": c["label"], "value": c["value"], "value_text": (f"{c['value']:.2f}" if isinstance(c["value"], (int, float)) else str(c["value"])),
                                   "level": P.confidence_level_text(c["level"]), "tone": status_tone(P.confidence_level_text(c["level"]))} for c in conf["components"]],
        "not_applicable": [{"title": s["title"], "surface_id": s["surface_id"], "area": P.area_title(s["area"]),
                            "reason": P.reason_text(s["reason"]), "reason_code": s["reason"]["reason_code"]} for s in doc["not_applicable"]],
        "deduplicated_questions": [{"id": q["id"], "covered_by": q["covered_by"]} for q in ctx["deduplicated_questions"]],
        "rule_results": [{"title": r["title"], "rule": r["rule"], "result_text": P.rule_result_text(r["result"]), "count": r["finding_count"]}
                         for r in doc["rule_results"]],
        "area_coverage": [{"title": P.area_title(a["id"]), **a["required_surfaces"]} for a in doc["areas"]],
        "technical": {"pool_signature": scope.get("pool_signature"),
                      "fact_families": len({f["fact"].split("@")[0] for f in doc["fact_index"]}), "fact_instances": len(doc["fact_index"]),
                      "versions_all": sorted(doc["versions"].items())},
    })
    for k in ("areas", "summary", "rule_results", "limitations", "observations", "review_required", "descriptive", "not_applicable"):
        view[k] = MR.walk(view[k])                                                              # rótulos persistidos con «sección 28» (histórico) → referencia VIGENTE
    groups: dict[str, dict] = {}
    for a in view["areas"]:
        groups.setdefault(a["macro_id"], {"id": a["macro_id"], "title": SECTION_TITLES.get(a["macro_id"], a["macro"]), "areas": []})["areas"].append(a)
    for g in groups.values():                                                                   # resumen transversal de la sección: composición del cierre por tipo de vista
        cv = next((a["facts"]["closure_views"] for a in g["areas"] if a["facts"].get("closure_views")), None)
        g["view_overview"] = _view_overview(cv) if cv else None
    for g in groups.values():                                                                   # estado por fila SOLO si difiere (no repetir "Evaluado" en cada disclosure)
        g["uniform_state"] = len({a["state"] for a in g["areas"]}) == 1
        g["all_assessed"] = g["uniform_state"] and g["areas"][0]["state"] == "ASSESSED"
    view["macro_groups"] = list(groups.values())
    # Narrativa del informe: cada dimensión macro del perfil se ubica en UNA sección del recorrido (mapeo de presentación, no
    # se inventa información): Arquitectura & Modelado (+ Ingesta / Fuente & Extracción) · Gobierno & estándares (+ hallazgos de
    # cumplimiento del Catálogo) · Portabilidad & preparación BDC (solo la portabilidad del Catálogo: riesgos y adaptaciones).
    by_id = {g["id"]: g for g in groups.values()}
    arch, src = by_id.get("ARCHITECTURE_MODELING"), by_id.get("SOURCE_EXTRACTION")
    if arch and src:                                                                            # la fuente es parte de cómo se construye el modelo, no de su portabilidad
        areas = [*arch["areas"], *src["areas"]]
        arch = {**arch, "areas": areas, "uniform_state": len({a["state"] for a in areas}) == 1,
                "all_assessed": all(a["state"] == "ASSESSED" for a in areas)}
    elif src and not arch:
        arch = src
    ev = (AE.asset_evaluation(link_entry, governance_findings(status.profile), assessed=True) if link_entry is not None
          else {"governance": AE.governance_view(governance_findings(status.profile)), "portability": None})
    view["evaluation"] = ev
    # Clasificación de dominio (hermana del Juicio): persistida si su firma de entradas coincide con el Catálogo actual; si no, derivada en solo lectura.
    dcl, _persisted = (dcs.resolve(status.domain_classification, link_entry, status.terminal_am) if link_entry is not None else (None, False))
    bcl, _bpersisted = (dcs.resolve_business(status.business_classification, link_entry, status.terminal_am) if link_entry is not None else (None, False))
    view["domain_classification"] = business_classification_view(bcl, domain_classification_view(dcl), dcs.manual_lob(link_entry) if link_entry is not None else None)
    view["story"] = {
        "architecture": arch,
        "governance_areas": by_id.get("NAMING"),
    }
    return view
