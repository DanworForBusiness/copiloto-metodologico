"""test_assistant_fallback_no_local_evidence.py — forensic fix: dos defectos
reportados por el owner en el Asistente (ver ticket "Fallback LLM
inconsistente + referencias genéricas sin activo concreto").

CASE A (TERMINAL_BRANCH_BUG): "mejores practicas para variables MTD" — el
contenido interno no cubre el tema en NINGÚN módulo. `route()` correctamente
no encuentra nada (`None`), pero antes de este fix esa respuesta grounded
quedaba como punto muerto: no existía ningún affordance para pedir una
respuesta general (a diferencia de los filtros de catálogo, que sí ofrecen
"Explicame vos" cuando el catálogo dice "no tenemos"). El fix agrega
`AssistantRoutingService.no_local_evidence_explain` — mismo mecanismo
(explain_module/explain_heading/explain_prompt), reusado, no paralelo.

CASE B (RETRIEVAL_RESOLUTION_BUG + REFERENCE_RENDERING_BUG): "como configurar
SLT ?" — `_find_cross_module_section` matcheaba "Colección How-To" (A04) con
score=16 impulsado ÚNICAMENTE por la palabra "configurar" (compartida con la
keyword curada "configurar datasphere") — "SLT" no aparece en NINGÚN lado del
corpus. `assistant_routing.py` lo presentaba como "Este tema está cubierto
con más precisión en...", una afirmación fuerte sin ningún recurso concreto
que resuelva "SLT". El fix agrega `_section_is_concrete_enough` (guardrail de
objeto, mismo principio que `_asset_covers_query_object`): sin una keyword
curada multi-palabra exacta, sólo se acepta el match si el OBJETO real de la
consulta aparece en el contenido propio de la sección.

Efecto colateral necesario del guardrail de objeto (`_asset_covers_query_object`,
`core/routing_scorers.py`): antes, una consulta que era PURAMENTE el verbo
genérico ("configurar" a secas, sin ningún objeto) pasaba el guardrail por su
default permisivo ("sin objeto, no aplica") y terminaba resolviendo a un
asset cualquiera que comparta ese verbo (ver CONTROL WEAK abajo) — ahora
ese default sólo aplica cuando la consulta tampoco tiene NINGÚN token
significativo (pregunta vacía), no cuando el único contenido es el verbo.

No se hardcodea "MTD", "SLT" ni "configurar" en ningún lado del fix — el
guardrail es genérico (objeto de la consulta vs. contenido del candidato).
"""
from __future__ import annotations

from webapp.services.assistant_routing import AssistantRoutingService
from webapp.services.content_service import load_module

PACK = "datasphere_core"
MOD_01 = "01_Arquitectura_y_Conectividad"

_docs: dict[str, object] = {}


def _doc(stem: str):
    if stem not in _docs:
        _docs[stem] = load_module(stem, PACK)
    return _docs[stem]


def _route(stem: str, question: str, active_section: str = ""):
    return AssistantRoutingService().route(_doc(stem), question, PACK, active_section)


# --- CASE A: sin evidencia local en ningún módulo ---------------------------

def test_case_a_no_local_coverage_query_yields_no_route():
    # "variables MTD" no existe en ningún módulo del corpus -- route() debe
    # seguir devolviendo None (no se inventa un match), la corrección vive en
    # lo que el router hace DESPUÉS de ese None (ver assistant.py/test_routes.py).
    route = _route(MOD_01, "mejores practicas para variables MTD")
    assert route is None


def test_case_a_no_local_evidence_explain_offers_general_explain():
    explain = AssistantRoutingService().no_local_evidence_explain(
        MOD_01, "", "mejores practicas para variables MTD",
    )
    assert explain.explain_module == MOD_01
    assert explain.explain_general is True
    assert explain.actions == ()  # sin recurso concreto que ofrecer, sólo el affordance
    assert explain.explain_prompt  # hay una directiva -- no es un fallback silencioso


def test_case_a_explain_prompt_does_not_repeat_the_catalog_general_explain_text():
    # No es una reimplementación paralela de _GENERAL_EXPLAIN, pero tampoco es
    # el MISMO texto -- ese está redactado para "el catálogo dijo que no
    # tenemos X", acá no hay catálogo de por medio.
    from webapp.services.assistant_routing import _GENERAL_EXPLAIN
    explain = AssistantRoutingService().no_local_evidence_explain(MOD_01, "", "x")
    assert explain.explain_prompt != _GENERAL_EXPLAIN


# --- CASE B: referencia genérica sin activo concreto ------------------------

def test_case_b_generic_collection_match_is_not_presented_as_precise_coverage():
    # Antes: route() devolvía "está cubierto con más precisión en A04,
    # sección Colección How-To" -- ninguno de sus ~20 how-to es sobre SLT.
    route = _route(MOD_01, "como configurar SLT ?")
    assert route is None


def test_case_b_object_alone_without_the_generic_verb_also_finds_nothing():
    # Control de ablación del forensic: "SLT" sin "configurar" tampoco matchea
    # nada -- confirma que el corpus no cubre SLT en absoluto (no es un bug
    # de scoring puntual, es ausencia real de contenido).
    route = _route(MOD_01, "que es SLT")
    assert route is None


# --- CONTROL WEAK: el verbo genérico a secas no debe resolver nada ----------

def test_control_weak_bare_generic_verb_resolves_to_nothing_confidently():
    route = _route(MOD_01, "configurar")
    assert route is None


# --- CONTROL STRONG: un match exacto/fuerte existente se preserva ----------

def test_control_strong_exact_section_title_match_is_preserved():
    route = _route(MOD_01, "federacion vs persistencia cual conviene")
    assert route is not None
    assert route.explain_module == "03_Integracion_y_Estrategia_de_Datos"
    assert "Federación vs. Persistencia" in (route.message or route.explain_heading)
    assert any(a.label == "Ir a la sección" for a in route.actions)


# --- CASE 3 (aceptación, contenido nuevo del owner en A04) ------------------
# La guía "automatización de alerta de memoria HANA en Datasphere" es un
# how-to NUEVO (intent_assets + bullet en "Colección How-To", A04). Debe
# resolver como RECURSO CONCRETO (no "consultá la Colección How-To") -- sin
# ningún cambio de código dedicado a este tema: es consecuencia del mismo
# mecanismo de intent_assets que ya resuelve cualquier otro how-to curado.

def test_case_3_memory_alert_howto_resolves_to_the_concrete_asset():
    route = _route(MOD_01, "cómo automatizar alertas de memoria en Datasphere")
    assert route is not None
    assert "How-to Danwor: Guia de automatizacion alerta memoria HANA Datasphere" in route.message
    assert any(
        a.label == "Abrir recurso"
        and a.url == "https://danwor.sharepoint.com/:w:/s/PrcticaAnalytics/IQBeg98nVm1FR6k73lLVFdTEASMcDTe0NjkzmUiqEHStHaI?e=uEZUA0"
        for a in route.actions
    )
    # NO debe quedarse en la referencia genérica a la colección.
    assert "Colección How-To" not in route.message
