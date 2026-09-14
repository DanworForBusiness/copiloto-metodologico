"""test_assistant_routing_wording.py — corrective: distinguir "conceptual" de
"comparativo" únicamente en el COPY del mensaje, sin tocar ninguna decisión de
routing.

EL DEFECTO (forensic, ver HANDOFF/ticket): `_is_conceptual_or_comparative_query`
agrupa a propósito dos intenciones bajo un solo booleano — preguntas
REALMENTE comparativas ("diferencia", "vs", "comparar") y preguntas apenas
CONCEPTUALES/definicionales ("qué es", "qué significa", "explicame") — porque
para el ROUTING ambas deben preferir la sección metodológica sobre un how-to
puntual (ese booleano queda INTACTO, no se toca acá). El problema es que el
COPY del mensaje asumía "comparativa" para las dos por igual: "¿qué significa
que Streaming Read esté en OFF?" no compara nada, y el usuario leía "Tu
pregunta parece comparativa" — confuso y no lo que preguntó.

LA CORRECCIÓN: un booleano SEGUNDO, sólo para texto, que reusa el mismo
vocabulario ya curado de `_COMPARATIVE_TERMS` (definido en este archivo,
antes usado sólo para el gate de `_is_conceptual_asset_question`) — no una
clasificación nueva, no toca `_is_conceptual_or_comparative_query`, no puede
cambiar qué sección/acción se elige (eso ya lo blinda
test_assistant_routing_cross_module_weak_local.py).
"""
from __future__ import annotations

from webapp.services.assistant_routing import AssistantRoutingService
from webapp.services.content_service import load_module

PACK = "datasphere_core"
MOD_01 = "01_Arquitectura_y_Conectividad"
MOD_03 = "03_Integracion_y_Estrategia_de_Datos"

Q1 = (
    "En una conexión de SAP Datasphere a S/4HANA mediante Remote Tables, "
    "DP Agent y ABAPAdapter, ¿qué significa que Streaming Read esté en OFF?"
)
Q2_COMPARATIVE_CONTROL = "¿Qué diferencia hay entre Streaming Read OFF y ON en ABAPAdapter?"

_docs: dict[str, object] = {}


def _doc(stem: str):
    if stem not in _docs:
        _docs[stem] = load_module(stem, PACK)
    return _docs[stem]


def _route(stem: str, question: str):
    return AssistantRoutingService().route(_doc(stem), question, PACK)


# --- A: la query reportada — conceptual, NO comparativa --------------------

def test_reported_query_is_not_labeled_comparative():
    route = _route(MOD_01, Q1)
    assert route is not None
    assert "parece comparativa" not in route.message


def test_reported_query_uses_conceptual_wording():
    route = _route(MOD_01, Q1)
    assert route is not None
    assert "conceptual" in route.message.lower()


def test_reported_query_from_module_03_also_uses_conceptual_wording():
    """Mismo copy sin importar desde qué módulo se pregunte."""
    route = _route(MOD_03, Q1)
    assert route is not None
    assert "parece comparativa" not in route.message
    assert "conceptual" in route.message.lower()


# --- B: control — una comparación REAL conserva el copy comparativo -------

def test_genuinely_comparative_control_keeps_comparative_wording():
    route = _route(MOD_01, Q2_COMPARATIVE_CONTROL)
    assert route is not None
    assert "parece comparativa" in route.message


# --- C: el fix es SÓLO de texto — el routing no se mueve un milímetro -----

def test_wording_fix_does_not_change_which_section_or_action_is_selected():
    """Ancla el contrato de test_assistant_routing_cross_module_weak_local.py:
    el mismo heading/módulo/acciones de ANTES del wording fix, sólo cambia
    el string del mensaje."""
    route = _route(MOD_01, Q1)
    assert route is not None
    assert route.explain_module == MOD_03
    assert route.explain_heading == "Streaming Read — Remote Tables vía DPA/ABAPAdapter"
    assert any(a.label == "Ir a la sección" for a in route.actions)
    assert any(
        "userapps.support.sap.com/sap/support/knowledge/en/3209242" in a.url
        for a in route.actions
    )


def test_classification_function_itself_is_untouched():
    """El booleano de ROUTING sigue agrupando conceptual+comparativa (a
    propósito, ver docstring del módulo) — sólo el copy se bifurca."""
    from core.routing_scorers import _is_conceptual_or_comparative_query

    assert _is_conceptual_or_comparative_query(Q1) is True
    assert _is_conceptual_or_comparative_query(Q2_COMPARATIVE_CONTROL) is True
