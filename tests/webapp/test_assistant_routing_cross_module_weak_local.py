"""test_assistant_routing_cross_module_weak_local.py — corrective: un local match
DÉBIL no debe tapar un cross-module match mucho más fuerte.

EL DEFECTO (forensic, ver HANDOFF/ticket): `route()` sólo intentaba
`_find_cross_module_section` cuando la búsqueda LOCAL no encontraba nada
(`if asset and not local_section`). Un match local que apenas calificó (score
8-19, ej. "Configuración de Espacios"=18 por compartir con "conexión" la
familia de variante "conect"/"conex" — ver `_routing_token_variants`) ganaba
SIEMPRE, así el cross-module tuviera un score muchísimo más alto (ej.
"Streaming Read..." en otro módulo = 96). El cross-module ni se calculaba: no
era una pelea de ranking, era que el candidato correcto nunca entraba a
competir.

LA CORRECCIÓN: el cross-module SÍ compite cuando el local es débil
(`score < 20` — el MISMO umbral que `_should_pair_section_with_asset` ya usa
para "score alto = señal fuerte del propio matcher", no una constante nueva).
Si el local es fuerte (>=20), no se abre competencia — evita la regresión real
que demostró el forensic: un apéndice/glosario (`A01_Arquitectura_y_Modelado`)
puntuando 136 por vocabulario amplio le habría ganado a un local correcto de
40 ("Contrato de Datos vs. Data Product") si se comparara siempre por score.

Alcance de este archivo: sólo el contrato de RUTEO (qué sección/acción se
elige). El wording "parece comparativa" vs "es conceptual" es un fix
DISTINTO y no se toca acá (ver test_assistant_routing_wording.py).
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
Q2 = "¿Qué diferencia hay entre Streaming Read OFF y ON en ABAPAdapter?"
Q3 = "¿Streaming Read OFF puede impactar la memoria del DP Agent?"
Q4 = "¿Qué necesito configurar para activar RFC Streaming Read?"
Q5 = "¿Cómo configuro el Source Language y la Space Quota de un espacio en Datasphere?"
Q_ORA = "error ORA-12541 en conexión SDA hacia tabla remota"
Q_DATA_PRODUCT = "que es un data product?"

_docs: dict[str, object] = {}


def _doc(stem: str):
    if stem not in _docs:
        _docs[stem] = load_module(stem, PACK)
    return _docs[stem]


def _route(stem: str, question: str):
    return AssistantRoutingService().route(_doc(stem), question, PACK)


# --- A: el caso reportado — Módulo 01, la query exacta del ticket ----------

def test_reported_query_from_module_01_does_not_route_to_configuracion_de_espacios():
    route = _route(MOD_01, Q1)
    assert route is not None
    assert "Configuración de Espacios" not in (route.message or "")
    assert "configuracion-de-espacios" not in "".join(a.url for a in route.actions)


def test_reported_query_from_module_01_lands_on_streaming_read_or_its_parent():
    route = _route(MOD_01, Q1)
    assert route is not None
    # El parent ("Tablas Remotas") sería aceptable si el scorer alguna vez
    # empujara al padre en vez del hijo — hoy el hijo (más específico) gana.
    assert route.explain_heading in (
        "Streaming Read — Remote Tables vía DPA/ABAPAdapter",
        "{dw-black}Tablas {dw-gold}Remotas",
    )
    assert route.explain_module == MOD_03
    assert any(a.label == "Ir a la sección" for a in route.actions)
    landing = next(a.url for a in route.actions if a.label == "Ir a la sección")
    assert landing.startswith(f"/modules/{MOD_03}")


def test_reported_query_keeps_the_kba_as_related_resource():
    route = _route(MOD_01, Q1)
    assert route is not None
    assert any(
        "userapps.support.sap.com/sap/support/knowledge/en/3209242" in a.url
        for a in route.actions
    )
    assert "KBA 3209242" in route.message


def test_reported_query_from_module_03_is_unaffected_and_still_correct():
    """Ya funcionaba desde el módulo dueño del contenido — no debe cambiar."""
    route = _route(MOD_03, Q1)
    assert route is not None
    assert route.explain_heading == "Streaming Read — Remote Tables vía DPA/ABAPAdapter"
    assert route.explain_module == MOD_03


# --- B: variaciones — no overfitear a Q1 -----------------------------------

def test_q2_diferencia_on_off_unaffected_by_the_fix():
    before_after = {stem: _route(stem, Q2) for stem in (MOD_01, MOD_03)}
    for stem, route in before_after.items():
        assert route is not None, stem
        assert route.explain_heading == "Streaming Read — Remote Tables vía DPA/ABAPAdapter", stem


def test_q3_impacto_memoria_unaffected_by_the_fix():
    for stem in (MOD_01, MOD_03):
        route = _route(stem, Q3)
        assert route is not None, stem
        assert route.explain_heading == "Streaming Read — Remote Tables vía DPA/ABAPAdapter", stem


def test_q4_activar_rfc_streaming_read_unaffected_by_the_fix():
    route = _route(MOD_01, Q4)
    assert route is not None
    assert route.explain_heading == "Streaming Read — Remote Tables vía DPA/ABAPAdapter"


# --- C: control — una pregunta REAL de Configuración de Espacios sigue ahí -

def test_q5_genuine_espacios_question_still_routes_to_configuracion_de_espacios():
    for stem in (MOD_01, MOD_03):
        route = _route(stem, Q5)
        assert route is not None, stem
        assert route.explain_heading == "{dw-black}Configuración de {dw-gold}Espacios", stem


# --- D: segunda manifestación del MISMO defecto (misma familia "conexión") -

def test_ora_error_case_now_resolves_to_tablas_remotas_instead_of_no_section():
    route = _route(MOD_01, Q_ORA)
    assert route is not None
    assert route.explain_heading == "{dw-black}Tablas {dw-gold}Remotas"
    assert any(a.label == "Ir a la sección" for a in route.actions)


# --- E: guardrail — NO comparar siempre por score (evita la regresión real) -

def test_strong_local_match_is_never_overridden_by_a_richer_cross_module_appendix():
    """Contrato exacto que la primera variante del fix rompía: un local FUERTE
    (score >= 20, ej. "Contrato de Datos vs. Data Product"=40) no debe ceder
    ante un apéndice/glosario cross-module que puntúa más alto (ej. "Datos como
    Producto" en A01_Arquitectura_y_Modelado=136) por vocabulario amplio."""
    route = _route(MOD_03, Q_DATA_PRODUCT)
    assert route is not None
    assert route.explain_module == MOD_03
    assert route.explain_heading == "{dw-black}Contrato de Datos vs. {dw-gold}Data Product"
