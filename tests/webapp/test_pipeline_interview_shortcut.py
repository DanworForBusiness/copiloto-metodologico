"""Acceso directo a Entrevistas desde las tarjetas del pipeline (solo `Entrevista CCHH` / `Entrevista Líder`): ícono + tooltip entre el selector de etapa y «Quitar».

Navegar NO crea entrevistas: con entrevista existente va a su edición; si no existe, al alta de Entrevistas con candidato (y puesto/área del proceso) precargados. Fixtures de I1 (persistencia aislada)."""
from __future__ import annotations

import re

import pytest

from webapp.services import busqueda_repository as brepo, entrevista_repository as erepo, human_capital_service as svc, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client, operations_users  # noqa: F401


def _manual(**o) -> str:
    return svc.create_busqueda({"nombre_puesto": "Solution Architect", "fecha_apertura": "2026-10-01", **o}, actor=CCHH_EMAIL)["busqueda_id"]


def _candidate(bid, first="Mateo", last="Biasotto") -> tuple[str, str]:
    pid = profile_repository.create({"first_name": first, "last_name": last, "email": f"{first.lower()}@danwor.com"})["profile_id"]
    app_id = svc.add_candidate_to_busqueda(bid, pid, actor=CCHH_EMAIL)["candidatos"][-1]["application_id"]
    return pid, app_id


def _card(html: str, name: str) -> str:
    return re.search(r'<div class="busqueda-cand">(?:(?!<div class="busqueda-cand">).)*?' + re.escape(name) + r'.*?(?=<div class="busqueda-cand">|</div>\s*</div>\s*</div>\s*{#|\Z)', html, re.S).group(0)


def _links(bid) -> dict:
    return {a["application_id"]: a for a in brepo.get(bid)["candidatos"]}


def _interview_count() -> int:
    return len(erepo.list())


@pytest.fixture()
def c():
    return session_client(CCHH_EMAIL)


# ═══════════════════ solo en las dos etapas de entrevista, con tooltip y orden ═══════════════════
def test_the_interview_icon_appears_only_in_the_two_interview_stages_with_the_contextual_tooltip(c):
    bid = _manual()
    results = {}
    for etapa, name in (("Preselección", "Ana"), ("Entrevista CCHH", "Bruno"), ("Entrevista Líder", "Carla"), ("Decisión Final", "Diego"), ("Finalizado", "Elena")):
        _, app_id = _candidate(bid, name, "Test")
        if etapa != "Preselección":
            svc.move_application_stage(bid, app_id, etapa, None, actor=CCHH_EMAIL)
        results[name] = etapa
    html = c.get(f"/cchh/busquedas/{bid}").text
    with_icon = {n for n in results if "busqueda-cand__interview" in _card(html, f"{n} Test")}
    assert with_icon == {"Bruno", "Carla"}                                                                           # solo Entrevista CCHH y Entrevista Líder
    assert 'aria-label="Evaluación CCHH"' in _card(html, "Bruno Test") and "<p>Evaluación CCHH</p>" in _card(html, "Bruno Test")      # Entrevista CCHH: abre la EVALUACIÓN CCHH
    assert 'aria-label="Ir a entrevista técnica"' in _card(html, "Carla Test") and "<p>Ir a entrevista técnica</p>" in _card(html, "Carla Test")
    card = _card(html, "Bruno Test")
    assert card.index("<select") < card.index("busqueda-cand__interview") < card.index('aria-label="Quitar del proceso"')   # dropdown → entrevista → eliminar
    assert "busqueda-cand__actions--interview" in card and "calendar" not in card and 'd="M17.5 17.5 16 16.3V14"' in card   # ícono calendar-clock
    assert "busqueda-cand__actions--interview" not in _card(html, "Ana Test")                                         # el resto de las tarjetas no cambia


def test_the_stage_selector_and_the_unlink_button_keep_working_inside_the_interview_card(c):
    bid = _manual()
    pid, app_id = _candidate(bid)
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    html = c.get(f"/cchh/busquedas/{bid}").text
    card = _card(html, "Mateo Biasotto")
    assert f'hx-post="/cchh/busquedas/{bid}/candidatos/{app_id}/etapa"' in card and f'hx-post="/cchh/busquedas/{bid}/candidatos/{app_id}/eliminar"' in card
    r = c.post(f"/cchh/busquedas/{bid}/candidatos/{app_id}/etapa", data={"etapa": "Entrevista Líder", "resultado_final": ""}, headers={"HX-Request": "true"})
    assert r.status_code == 200 and 'aria-label="Ir a entrevista técnica"' in r.text and "Evaluación CCHH" not in r.text    # el fragmento HTMX actualiza el tooltip de la nueva columna
    assert c.post(f"/cchh/busquedas/{bid}/candidatos/{app_id}/eliminar", headers={"HX-Request": "true"}).status_code == 200
    assert brepo.get(bid)["candidatos"] == []


def test_a_closed_process_shows_no_card_actions_so_no_interview_icon():
    bid = _manual()
    _, app_id = _candidate(bid)
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    svc.close_busqueda(bid, [], actor=CCHH_EMAIL)
    assert "busqueda-cand__interview" not in session_client(CCHH_EMAIL).get(f"/cchh/busquedas/{bid}").text


# ═══════════════════ destino: entrevista existente / alta precargada, sin crear nada ═══════════════════
def test_cchh_stage_opens_the_cchh_evaluation_and_leader_stage_keeps_the_administrative_record_without_creating_anything(c):
    bid = _manual()
    pid, app_id = _candidate(bid)
    svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)                              # la entrevista se crea al ENTRAR a la etapa (flujo vigente), no por navegar
    existing = erepo.find_by_profile_and_busqueda(pid, bid)
    assert existing and existing["profile_id"] == pid and existing["busqueda_id"] == bid and existing["application_id"] == app_id
    before = _interview_count()
    expected = (("Entrevista CCHH", "Evaluación CCHH", f"/cchh/busquedas/{bid}/candidatos/{app_id}/evaluacion-cchh"),
                ("Entrevista Líder", "Ir a entrevista técnica", f"/cchh/entrevistas/{existing['entrevista_id']}/editar?return_to=%2Fcchh%2Fbusquedas%2F{bid}"))   # con contexto explícito: «Volver» regresa a este Pipeline
    for etapa, label, url in expected:
        svc.move_application_stage(bid, app_id, etapa, None, actor=CCHH_EMAIL)
        card = _card(c.get(f"/cchh/busquedas/{bid}").text, "Mateo Biasotto")
        m = re.search(r'<a href="([^"]+)" class="table-action-btn chart-tooltip-anchor busqueda-cand__interview" aria-label="([^"]+)"', card)
        assert m and m.group(1) == url and m.group(2) == label
        assert c.get(m.group(1)).status_code == 200
    assert _interview_count() == before == 1                                                                           # navegar no crea entrevistas


def test_without_an_interview_the_icon_opens_the_new_form_with_candidate_and_process_prefilled_and_creates_nothing(c, monkeypatch):
    bid = _manual(area_consultora=None)
    pid, app_id = _candidate(bid)
    svc.move_application_stage(bid, app_id, "Entrevista Líder", None, actor=CCHH_EMAIL)
    erepo.delete(erepo.find_by_profile_and_busqueda(pid, bid)["entrevista_id"])                                          # aún no hay entrevista asociada
    assert _interview_count() == 0
    html = c.get(f"/cchh/busquedas/{bid}").text
    href = re.search(r'<a href="([^"]+)" class="table-action-btn chart-tooltip-anchor busqueda-cand__interview"', _card(html, "Mateo Biasotto")).group(1).replace("&amp;", "&")
    assert href == f"/cchh/entrevistas/nueva?profile_id={pid}&busqueda_id={bid}&return_to=%2Fcchh%2Fbusquedas%2F{bid}"
    page = c.get(href).text
    assert re.search(rf'<option value="{pid}" selected>', page) and 'value="Solution Architect"' in page             # candidato y puesto del proceso precargados
    assert _interview_count() == 0 and c.get(f"/cchh/busquedas/{bid}").status_code == 200 and _interview_count() == 0   # ni abrir el alta ni el detalle crean la entrevista


@pytest.mark.parametrize("query_of", [
    lambda pid, bid: "profile_id=no-existe&busqueda_id=" + bid,                                                         # candidato inexistente: sin precarga
    lambda pid, bid: "",                                                                                                # sin contexto: alta vacía de siempre
])
def test_the_new_form_ignores_unverifiable_context(c, query_of):
    bid = _manual()
    pid, _ = _candidate(bid)
    page = c.get("/cchh/entrevistas/nueva?" + query_of(pid, bid)).text
    assert "selected>" not in page.split('id="ent-profile"')[1].split("</select>")[0] and 'value="Solution Architect"' not in page


def test_a_candidate_outside_the_process_is_preselected_but_the_process_data_is_not_applied(c):
    bid, other = _manual(), _manual(nombre_puesto="Otro puesto")
    pid, _ = _candidate(other)
    page = c.get(f"/cchh/entrevistas/nueva?profile_id={pid}&busqueda_id={bid}").text                                   # el candidato NO participa en `bid`
    assert f'<option value="{pid}" selected>' in page and 'value="Solution Architect"' not in page


def test_a_read_failure_degrades_to_the_interviews_list_never_to_a_creating_form(c, monkeypatch):
    bid = _manual()
    _, app_id = _candidate(bid)
    svc.move_application_stage(bid, app_id, "Entrevista Líder", None, actor=CCHH_EMAIL)
    monkeypatch.setattr(erepo, "find_by_profile_and_busqueda", lambda *a: (_ for _ in ()).throw(erepo.EntrevistaStorageError("boom")))
    card = _card(c.get(f"/cchh/busquedas/{bid}").text, "Mateo Biasotto")
    assert re.search(r'<a href="/cchh/entrevistas" class="table-action-btn chart-tooltip-anchor busqueda-cand__interview"', card)
