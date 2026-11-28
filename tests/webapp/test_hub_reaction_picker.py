"""Selector de reacciones transversal del feedback del Copiloto (patrón de Comunidad) y «Tengo una idea».

- El picker reemplaza a los emojis en las superficies de feedback, con «Me resultó útil» (rating=up, contrato de siempre) y la lamparita.
- La lamparita NO es un voto: guarda una SUGERENCIA (`tree_suggestion`, `kind=idea`) con su contexto de origen, sin tocar el feedback binario.
- El histórico de votos `down` sigue legible (estado «¡Gracias!»).
"""
from __future__ import annotations

from types import SimpleNamespace

import yaml
from fastapi.testclient import TestClient

from webapp.main import app
from webapp.templating import templates

client = TestClient(app)


def _render(name, **ctx):
    return templates.get_template(name).render(pack="datasphere_core", **ctx)


def test_section_feedback_uses_the_shared_picker_with_up_and_idea_only():
    html = _render("partials/section_feedback.html", section=SimpleNamespace(slug="intro"), stem="03_Integracion_y_Estrategia_de_Datos")
    assert 'data-dw-rxp' in html and 'class="dw-rxp-main"' in html                                    # botón neutro en reposo
    assert 'name="rating" value="up"' in html and 'aria-label="Me resultó útil"' in html             # rating=up: contrato de siempre
    assert 'data-dw-idea-open' in html and 'aria-label="Tengo una idea"' in html                      # la lamparita revela la idea
    assert 'value="down"' not in html and "&#128077;" not in html and "&#128078;" not in html          # sin voto negativo ni emojis
    assert 'name="kind" value="idea"' in html and 'name="context" value="section:/modules/03_Integracion_y_Estrategia_de_Datos/sections/intro"' in html


def test_assistant_and_module_feedback_share_the_same_picker():
    reply = SimpleNamespace(id="r1", conversation_id="c1")
    a = _render("partials/assistant_feedback.html", reply=reply, stem="03_Integracion_y_Estrategia_de_Datos")
    assert 'data-dw-rxp' in a and 'value="up"' in a and 'data-dw-idea-open' in a and 'value="down"' not in a
    assert 'name="context" value="assistant:r1"' in a and "👍" not in a and "👎" not in a
    doc = SimpleNamespace(summary=SimpleNamespace(stem="03_Integracion_y_Estrategia_de_Datos"))
    m = _render("partials/module_feedback.html", document=doc)
    assert 'dw-rxp--center' in m and 'value="up"' in m and 'data-dw-idea-open' in m and 'value="down"' not in m
    assert 'name="context" value="module:03_Integracion_y_Estrategia_de_Datos"' in m


def test_historical_down_votes_still_render_their_thanks_state():
    html = _render("partials/section_feedback.html", section=SimpleNamespace(slug="intro"), submitted=True, rating="down")
    assert 'data-rx="down"' in html and "¡Gracias!" in html
    thanks = _render("partials/assistant_feedback_thanks.html", reply=SimpleNamespace(id="r1"), rating="down")
    assert 'data-rx="down"' in thanks


def test_idea_is_saved_as_a_contextual_suggestion_never_as_a_negative_vote(monkeypatch, tmp_path):
    from webapp.routers import wizard as wizard_router
    monkeypatch.setattr(wizard_router, "FEEDBACK_LOG_DIR", tmp_path)
    r = client.post("/trees/suggest", data={"title": "Sumar un ejemplo con Replication Flow", "module": "03_Integracion_y_Estrategia_de_Datos",
                                            "pack": "datasphere_core", "kind": "idea", "sg_id": "section-feedback-intro",
                                            "sg_class": "section-feedback", "context": "section:/modules/03/sections/intro"})
    assert r.status_code == 200
    assert 'id="section-feedback-intro"' in r.text and 'class="section-feedback"' in r.text            # reemplaza al widget en su lugar
    assert 'data-rx="idea"' in r.text and "Registramos tu idea" in r.text
    [saved] = list(tmp_path.glob("tree_suggestion_*.yaml"))
    payload = yaml.safe_load(saved.read_text(encoding="utf-8"))
    assert payload["type"] == "tree_suggestion" and payload["kind"] == "idea"
    assert payload["context"] == "section:/modules/03/sections/intro" and payload["title"].startswith("Sumar un ejemplo")
    assert "rating" not in payload                                                                      # no es un voto
    assert not list(tmp_path.glob("section_feedback_*.yaml")) and not list(tmp_path.glob("*feedback*.yaml"))   # ni toca el feedback binario


def test_unknown_wrapper_class_is_not_reflected():
    r = client.post("/trees/suggest", data={"title": "x", "kind": "idea", "sg_id": "w", "sg_class": "evil\"><script>"})
    assert "<script>" not in r.text and 'class="tool-suggest"' in r.text


def test_each_surface_resolves_the_idea_with_its_own_existing_comment_form():
    """Picker compartido, composición propia: la idea reusa el formulario de comentario homologado de cada superficie."""
    reply = SimpleNamespace(id="r1", conversation_id="c1")
    a = _render("partials/assistant_feedback.html", reply=reply, stem="03_Integracion_y_Estrategia_de_Datos")
    assert 'class="section-feedback__form" data-dw-idea hidden' in a                                # MISMO primitivo que la sección
    assert 'class="section-feedback__send">Enviar idea' in a and 'class="section-feedback__ghost"' in a
    assert "assistant-feedback-expanded" not in a and "assistant-feedback-form" not in a              # sin tarjeta contenedora propia
    s = _render("partials/section_feedback.html", section=SimpleNamespace(slug="intro"), stem="03_Integracion_y_Estrategia_de_Datos")
    assert 'class="section-feedback__form" data-dw-idea hidden' in s and 'section-feedback__formactions' in s
    doc = SimpleNamespace(summary=SimpleNamespace(stem="03_Integracion_y_Estrategia_de_Datos"))
    m = _render("partials/module_feedback.html", document=doc)
    assert 'class="module-feedback__inner" data-dw-feedback-bar' in m                               # el bloque de reposo se alterna entero
    assert 'module-feedback__inner--form" data-dw-idea hidden' in m and 'class="module-feedback__primary">Enviar idea' in m
    assert "dw-idea__" not in a + s + m                                                              # sin composición genérica impuesta


def test_idea_thanks_keeps_each_surface_composition(monkeypatch, tmp_path):
    from webapp.routers import wizard as wizard_router
    monkeypatch.setattr(wizard_router, "FEEDBACK_LOG_DIR", tmp_path)
    base = {"title": "Una idea", "module": "m", "pack": "datasphere_core", "kind": "idea"}
    r = client.post("/trees/suggest", data={**base, "sg_id": "assistant-feedback-r1", "sg_class": "assistant-feedback", "context": "assistant:r1"})
    assert 'class="assistant-feedback assistant-feedback-thanks"' in r.text and 'data-rx="idea"' in r.text
    r = client.post("/trees/suggest", data={**base, "sg_id": "module-feedback", "sg_class": "module-feedback", "context": "module:m"})
    assert 'module-feedback__inner--thanks' in r.text and 'id="module-feedback"' in r.text and 'data-rx="idea"' in r.text
