"""Contactar al candidato desde las tarjetas del pipeline (Ajuste 14, integrado con el acceso a Entrevistas del Ajuste 13): ícono de sobre en las CINCO etapas, diálogo por demanda con SOLO el candidato elegido,
canales según los datos cargados, plantillas de la referencia. Abrir/consultar NO cambia etapas, NO crea entrevistas y NO registra envíos. Fixtures de I1 (persistencia aislada)."""
from __future__ import annotations

import json
import re

import pytest

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import busqueda_repository as brepo, entrevista_repository as erepo, human_capital_contact as contact, human_capital_service as svc, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401
from tests.webapp.test_pipeline_interview_shortcut import _card, _manual  # noqa: F401

STAGES = [("Preselección", "Ana"), ("Entrevista CCHH", "Bruno"), ("Entrevista Líder", "Carla"), ("Decisión Final", "Diego"), ("Finalizado", "Elena")]


def _candidate(bid, first="Mateo", last="Biasotto", **extra) -> tuple[str, str]:
    fields = {"first_name": first, "last_name": last, "email": f"{first.lower()}@danwor.com", "phone": "+54 9 11 5555-1234", **extra}
    pid = profile_repository.create(fields)["profile_id"]
    app_id = svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]
    return pid, app_id


def _messages(html: str) -> dict:
    return json.loads(re.search(r'<script type="application/json" data-contact-messages>(.*?)</script>', html, re.S).group(1))


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


def _seed_all_stages(bid) -> dict:
    apps = {}
    for etapa, name in STAGES:
        _, app_id = _candidate(bid, name, "Test")
        if etapa != "Preselección":
            svc.move_application_stage(bid, app_id, etapa, None, actor=CCHH_EMAIL)
        apps[name] = app_id
    return apps


# ═══════════════════ ícono en las cinco etapas, con tooltip y orden ═══════════════════
def test_the_contact_icon_appears_in_all_five_stages_with_tooltip_and_the_right_candidate(c):
    bid = _manual()
    apps = _seed_all_stages(bid)
    html = c.get(f"/cchh/busquedas/{bid}").text
    for etapa, name in STAGES:
        card = _card(html, f"{name} Test")
        assert 'aria-label="Contactar al candidato"' in card and "<p>Contactar al candidato</p>" in card, etapa
        assert f'hx-get="/cchh/busquedas/{bid}/candidatos/{apps[name]}/contacto"' in card, etapa          # cada ícono pide SU candidato
        assert 'data-dialog-open="busqueda-contact-dialog"' in card and 'd="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"' in card   # sobre SVG + diálogo único
    assert html.count('id="busqueda-contact-dialog"') == 1                                                 # un solo diálogo, no uno por tarjeta


def test_control_order_general_stages_and_interview_stages(c):
    bid = _manual()
    _seed_all_stages(bid)
    html = c.get(f"/cchh/busquedas/{bid}").text
    for name in ("Ana", "Diego", "Elena"):                                                               # dropdown → contacto → desvincular
        card = _card(html, f"{name} Test")
        assert card.index("<select") < card.index("busqueda-cand__contact") < card.index('aria-label="Quitar del proceso"') and "busqueda-cand__interview" not in card
    for name in ("Bruno", "Carla"):                                                                      # dropdown → contacto → entrevista → desvincular
        card = _card(html, f"{name} Test")
        assert card.index("<select") < card.index("busqueda-cand__contact") < card.index("busqueda-cand__interview") < card.index('aria-label="Quitar del proceso"')


def test_existing_actions_are_preserved_and_the_contact_icon_survives_a_stage_change(c):
    bid = _manual()
    _, app_id = _candidate(bid)
    card = _card(c.get(f"/cchh/busquedas/{bid}").text, "Mateo Biasotto")
    assert f'hx-post="/cchh/busquedas/{bid}/candidatos/{app_id}/etapa"' in card and f'hx-post="/cchh/busquedas/{bid}/candidatos/{app_id}/eliminar"' in card
    r = c.post(f"/cchh/busquedas/{bid}/candidatos/{app_id}/etapa", data={"etapa": "Entrevista Líder", "resultado_final": ""}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and "busqueda-cand__contact" in r.text and 'aria-label="Ir a entrevista técnica"' in r.text      # fragmento HTMX: contacto + entrevista conviven
    assert c.post(f"/cchh/busquedas/{bid}/candidatos/{app_id}/eliminar", headers={"HX-Request": "true"}).status_code == 200
    assert brepo.get(bid)["candidatos"] == []


def test_a_closed_process_has_no_card_actions_so_no_contact_icon():
    bid = _manual()
    _candidate(bid)
    svc.close_busqueda(bid, [], actor=CCHH_EMAIL)
    assert "busqueda-cand__contact" not in session_client(CCHH_EMAIL).get(f"/cchh/busquedas/{bid}").text


# ═══════════════════ el diálogo: solo el candidato elegido, datos y canales ═══════════════════
def test_the_dialog_content_is_only_the_selected_candidate_never_the_others(c):
    bid = _manual()
    _, app_a = _candidate(bid, "Ana", "Uno", email="ana.uno@danwor.com", phone="+54 11 1111-1111")
    _, app_b = _candidate(bid, "Beto", "Dos", email="beto.dos@danwor.com", phone="+54 11 2222-2222")
    page = c.get(f"/cchh/busquedas/{bid}").text
    assert "ana.uno@danwor.com" not in page and "beto.dos@danwor.com" not in page and "1111-1111" not in page and "2222-2222" not in page   # el detalle NO expone contactos
    frag = c.get(f"/cchh/busquedas/{bid}/candidatos/{app_a}/contacto").text
    assert "Contactar a Ana Uno" in frag and "ana.uno@danwor.com" in frag and "+54 11 1111-1111" in frag
    assert "beto.dos@danwor.com" not in frag and "2222-2222" not in frag and "Beto" not in frag and "Dos" not in frag
    assert c.get(f"/cchh/busquedas/{bid}/candidatos/{app_b}/contacto").text.count("danwor.com") >= 1 and "ana.uno" not in c.get(f"/cchh/busquedas/{bid}/candidatos/{app_b}/contacto").text


def test_an_application_of_another_process_is_not_reachable_through_this_one(c):
    bid, other = _manual(), _manual(nombre_puesto="Otro puesto")
    _, foreign_app = _candidate(other, "Zoe", "Ajena", email="zoe.ajena@danwor.com")
    r = c.get(f"/cchh/busquedas/{bid}/candidatos/{foreign_app}/contacto")
    assert r.status_code == 404 and "zoe.ajena" not in r.text
    assert c.get(f"/cchh/busquedas/no-existe/candidatos/{foreign_app}/contacto").status_code == 404


def test_the_dialog_requires_the_cchh_session_and_authorization(c):
    bid = _manual()
    _, app_id = _candidate(bid)
    url = f"/cchh/busquedas/{bid}/candidatos/{app_id}/contacto"
    r = TestClient(app, follow_redirects=False).get(url)                                                  # sin sesión: redirige al login, sin datos
    assert r.status_code in (302, 303, 307) and "mateo@danwor.com" not in r.text
    assert c.get(url).status_code == 200 and c.get(url).headers["cache-control"] == "no-store"


def test_all_stages_open_the_dialog_with_process_context_and_templates(c):
    bid = _manual()
    apps = _seed_all_stages(bid)
    for etapa, name in STAGES:
        frag = c.get(f"/cchh/busquedas/{bid}/candidatos/{apps[name]}/contacto").text
        assert f"Contactar a {name} Test" in frag and "Solution Architect" in frag and f"Etapa: {etapa}" in frag
        m = _messages(frag)
        assert set(m) == {"primer_contacto", "actualizacion", "cierre", "pausa"} and all(set(v) == {"whatsapp", "mail"} for v in m.values())
        assert f"Hola, {name}" in m["primer_contacto"]["mail"]["cuerpo"] and "Solution Architect" in m["primer_contacto"]["mail"]["asunto"]   # personalizado con candidato y puesto del proceso


def test_the_templates_are_the_reference_ones_with_name_and_position():
    m = contact.build_messages("Solution Architect", "Mateo")
    assert m["primer_contacto"]["whatsapp"] == {"asunto": None, "cuerpo": "¡Hola, Mateo! ¿Cómo estás?\nTe contacto desde el equipo de Capital Humano de Danwor porque vimos tu perfil y nos pareció muy interesante para una búsqueda de Solution Architect.\nNos gustaría conocerte y contarte un poco más sobre la oportunidad. ¿Tendrías disponibilidad para coordinar una entrevista?\n¡Quedo atenta!"}
    assert m["primer_contacto"]["mail"]["asunto"] == "Oportunidad laboral en Danwor | Posición de Solution Architect" and "[día] a las [hora]" in m["primer_contacto"]["mail"]["cuerpo"]
    assert m["cierre"]["mail"]["asunto"] == "Proceso de selección | Posición de Solution Architect" and m["pausa"]["whatsapp"]["asunto"] is None
    assert contact.build_messages("X", "")["primer_contacto"]["whatsapp"]["cuerpo"].startswith("¡Hola! ¿Cómo estás?\n")        # sin nombre: el saludo original de la referencia


# ═══════════════════ disponibilidad de canales / datos ausentes ═══════════════════
def _flags(frag: str) -> dict:
    return dict(re.findall(r'data-(has-whatsapp|has-email|default-channel)="([^"]*)"', frag))


def test_channel_availability_follows_the_loaded_contact_data(c):
    bid = _manual()
    cases = {
        "both": dict(email="ambos@danwor.com", phone="+54 9 11 5555-1234"),
        "nophone": dict(email="solomail@danwor.com", phone=""),
        "noemail": dict(email="", phone="+54 9 11 5555-1234"),
        "none": dict(email="", phone=""),
        "badmail": dict(email="no-es-un-mail", phone=""),
    }
    got = {}
    for key, extra in cases.items():
        _, app_id = _candidate(bid, key.capitalize(), "Caso", **extra)
        got[key] = c.get(f"/cchh/busquedas/{bid}/candidatos/{app_id}/contacto").text
    assert _flags(got["both"]) == {"has-whatsapp": "1", "has-email": "1", "default-channel": "whatsapp"}
    assert _flags(got["nophone"]) == {"has-whatsapp": "0", "has-email": "1", "default-channel": "mail"} and "Sin teléfono cargado" in got["nophone"]
    assert _flags(got["noemail"]) == {"has-whatsapp": "1", "has-email": "0", "default-channel": "whatsapp"} and "Sin email cargado" in got["noemail"]
    assert _flags(got["none"]) == {"has-whatsapp": "0", "has-email": "0", "default-channel": "mail"} and "Sin email cargado" in got["none"] and "Sin teléfono cargado" in got["none"]
    assert _flags(got["badmail"])["has-email"] == "0" and "no-es-un-mail" not in got["badmail"]                            # un email mal formado no habilita el correo ni se ofrece copiarlo
    assert 'data-whatsapp-digits="5491155551234"' in got["both"] and 'data-email="ambos@danwor.com"' in got["both"]          # `wa.me` solo con dígitos


def test_whatsapp_digits_and_email_validation():
    assert contact.whatsapp_digits("+54 (9) 11-5555 1234") == "5491155551234" and contact.whatsapp_digits(None) == "" and contact.whatsapp_digits("sin datos") == ""
    assert contact.valid_email(" a@b.co ") == "a@b.co" and contact.valid_email("a@b") == "" and contact.valid_email(None) == ""


def test_a_candidate_removed_from_the_bank_shows_an_explanation_not_a_form(c):
    bid = _manual()
    pid, app_id = _candidate(bid)
    profile_repository.soft_delete(pid, actor=CCHH_EMAIL)
    r = c.get(f"/cchh/busquedas/{bid}/candidatos/{app_id}/contacto")
    assert r.status_code == 200 and "ya no está disponible en el Banco" in r.text and "mateo@danwor.com" not in r.text and "data-contact-root" not in r.text
    assert f"/candidatos/{app_id}/contacto" not in c.get(f"/cchh/busquedas/{bid}").text      # sin perfil no se ofrece el ícono


# ═══════════════════ abrir / consultar no cambia nada ni registra envíos ═══════════════════
def test_opening_the_dialog_never_changes_stages_creates_interviews_or_logs_a_contact(c):
    bid = _manual()
    pid, app_id = _candidate(bid)
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    before = brepo.get(bid)
    interviews = len(erepo.list())
    for _ in range(3):
        assert c.get(f"/cchh/busquedas/{bid}/candidatos/{app_id}/contacto").status_code == 200
    after = brepo.get(bid)
    assert after == before and len(erepo.list()) == interviews                                           # ni etapa, ni historial, ni revisión, ni entrevistas
    assert not any("contact" in str(h.get("tipo", "")).lower() for h in brepo.historial_proceso_of(after))   # nada «enviado» en el historial
    html = c.get(f"/cchh/busquedas/{bid}/candidatos/{app_id}/contacto").text
    assert 'method="post"' not in html and "hx-post" not in html and 'data-contact-open="whatsapp"' in html   # solo enlaces explícitos; ningún POST de registro


def test_the_channel_links_are_explicit_user_actions_and_the_dialog_says_nothing_is_sent(c):
    bid = _manual()
    _, app_id = _candidate(bid)
    html = c.get(f"/cchh/busquedas/{bid}/candidatos/{app_id}/contacto").text
    assert 'target="_blank" rel="noopener noreferrer"' in html and "no envía nada ni se registra como enviado" in html
    body = c.get(f"/cchh/busquedas/{bid}").text
    assert "https://wa.me/" in body and "mailto:" in body and "__hcContactWired" in body                     # los enlaces se arman en el navegador con el texto editado; nunca se envían solos
