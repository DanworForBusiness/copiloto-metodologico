"""Referencias navegables INLINE del Asistente: los objetos MENCIONADOS en una respuesta se resuelven como link sobre su propio nombre.

Distinción transversal por NATURALEZA de la interacción (no por cantidad de botones):
  · continuidad conversacional y alternativas de decisión / desambiguación → botones contextuales;
  · referencias a objetos mencionados → link inline, con el MISMO destino que tenía su botón, sin repetirlo debajo.
El productor marca cada mención con el índice de su destino ya resuelto (sin coincidencias textuales); lo persistido es texto limpio.
"""
from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from webapp.copilot_runtime import assistant_store
from webapp.services import assistant_refs, assistant_routing, auth_service
from webapp.services.content_service import load_module
from webapp.templating import templates

PACK = "datasphere_core"
STEM = "03_Integracion_y_Estrategia_de_Datos"
CDS = ["ZCDS_DSP_FACT_MCHB", "ZCDS_DSP_FACT_VBAP", "ZCDS_DSP_DIM_KNA1", "ZCDS_DSP_PEDIDOS", "ZCDS_DSP_V2_PEDIDOS"]


@pytest.fixture()
def cds(monkeypatch):
    monkeypatch.setattr(assistant_routing, "_list_cds_names", lambda: list(CDS))
    return CDS


def _route(q):
    return assistant_routing.AssistantRoutingService().route(load_module(STEM, PACK), q, PACK)


def _client():
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "t@d.com", "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def test_markers_are_structural_and_never_leak():
    refs = assistant_refs.References()
    text = f"- **FACT**: {refs('A004', 'x')}, {refs('B' + assistant_refs.SEP + 'X', 'y')}"
    assert assistant_refs.plain(text) == "- **FACT**: A004, BX"                                  # texto limpio (marcas fuera, nombre intacto)
    html = assistant_refs.resolve(f"<p>{text} {assistant_refs.OPEN}</p>", lambda i, inner: f"[{i}:{inner}]")
    assert html == "<p>- **FACT**: [0:A004], [1:BX] </p>" and refs.tuple() == ("x", "y")          # cada marca → su destino por ÍNDICE; restos sueltos se descartan


def test_cds_inventory_keeps_groups_and_resolves_every_object_inline_without_buttons(cds):
    r = _route("¿Tenemos catálogo de vistas CDS?")
    assert r.actions == () and len(r.references) == len(CDS)                                    # TODOS los objetos (no los primeros 12) y ninguna botonera
    assert "**FACT** (2): MCHB, VBAP" in r.message and "**DIM** (1): KNA1" in r.message and "**Otras** (2)" in r.message
    assert {a.url for a in r.references} == {f"/catalog/cds?name={n}&pack={PACK}" for n in CDS} and all(a.embed for a in r.references)
    assert assistant_refs.OPEN not in r.message                                                 # `message` (persistido, historial del LLM, métricas) siempre limpio


def test_same_visible_name_keeps_its_own_destination(cds):
    r = _route("¿Tenemos catálogo de vistas CDS?")
    pedidos = [a.url for a in r.references if a.url.endswith(f"PEDIDOS&pack={PACK}")]
    assert len(pedidos) == 2 and len(set(pedidos)) == 2                                         # dos «PEDIDOS» → dos destinos: la identidad la da la estructura, no el texto


def test_link_and_button_share_exactly_the_same_action():
    attrs = templates.get_template("partials/assistant_route_link.html").module.route_target_attrs
    embed = assistant_routing.AssistantRouteAction("x", "/catalog/cds?name=Z&pack=p", embed=True)
    tab = assistant_routing.AssistantRouteAction("x", "/modules/m?tab=catalogo", activate_tab="catalogo")
    ext = assistant_routing.AssistantRouteAction("x", "https://help.sap.com/x", external=True)
    assert str(attrs(embed)) == ' href="/catalog/cds?name=Z&amp;pack=p" hx-get="/catalog/cds?name=Z&amp;pack=p" hx-target="#catalog-detail-slot" hx-swap="innerHTML"'
    assert "@click.prevent=\"toolTab='catalogo'\"" in str(attrs(tab)) and 'target="_blank" rel="noopener noreferrer"' in str(attrs(ext))


def test_chat_renders_inline_links_feedback_right_after_the_bubble_and_persists_plain_text(cds):
    conv = assistant_store.start(STEM, PACK).id
    page = _client().post(f"/modules/{STEM}/assistant/chat", data={"question": "¿Tenemos catálogo de vistas CDS?", "conversation_id": conv, "pack": PACK}).text
    bubble = page.split('section-analysis-output">', 1)[1].split("</div>", 1)[0]
    links = re.findall(r'<a class="assistant-ref" href="([^"]+)" hx-get="\1" hx-target="#catalog-detail-slot" hx-swap="innerHTML" title="[^"]+">([^<]+)</a>', bubble)
    assert [name for _u, name in links] == ["MCHB", "VBAP", "KNA1", "PEDIDOS", "PEDIDOS"]
    assert "assistant-route-action" not in page and assistant_refs.OPEN not in page            # sin botonera redundante ni marcas sueltas
    assert page.index("section-analysis-output") < page.index('class="assistant-feedback"')     # Feedback inmediatamente después de la burbuja
    stored = assistant_store.history(conv, STEM, PACK)
    assert stored and all(assistant_refs.OPEN not in str(m) and "assistant-ref" not in str(m) for m in stored)


def test_decision_alternatives_stay_as_buttons():
    choice = _route("¿Cuál es el mejor modelo?")
    assert choice.references == () and len([a for a in choice.actions if a.ask]) >= 2           # desambiguación: alternativas de decisión
