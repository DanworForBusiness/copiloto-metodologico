"""Historial unificado del PROCESO (CCHH): movimientos de candidatos (asociación, cambio de etapa, finalización, desvinculación) dentro del MISMO historial/persistencia que las transiciones de la solicitud.

Fixtures de I1 (`env`/`remote`, persistencia aislada). Los eventos se registran dentro de la mutación atómica del pipeline: sin persistencia confirmada no hay evento; un movimiento sin efecto no duplica."""
from __future__ import annotations

import re

import pytest

from webapp.services import auth_service, busqueda_repository as repo, human_capital_service as svc, profile_repository
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, OPS_A, SID, doc, env, remote, rev, send, to_state  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import ORIGIN, operations_users, session_client  # noqa: F401

OTHER_CCHH = "cchh.otra@danwor.com"


@pytest.fixture(autouse=True)
def two_cchh_users(monkeypatch):
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", f"{CCHH_EMAIL},{OTHER_CCHH}")


def _profile(first="Ana", last="Pérez") -> str:
    return profile_repository.create({"first_name": first, "last_name": last, "email": f"{first.lower()}@danwor.com"})["profile_id"]


def _manual() -> str:
    return svc.create_busqueda({"nombre_puesto": "Manual", "fecha_apertura": "2026-10-01"}, actor=CCHH_EMAIL)["busqueda_id"]


def _taken() -> str:
    to_state("en_gestion")
    return SID


def _events(bid) -> list[dict]:
    return [h for h in repo.historial_proceso_of(repo.get(bid)) if str(h.get("transicion", "")).startswith("candidato_")]


def _rows(html: str) -> list[tuple[str, str, str]]:
    block = re.search(r"data-historial.*?</tbody>", html, re.S).group(0)
    rows = re.findall(r'<tr>\s*<td class="left">(.*?)</td>\s*<td class="left">.*?</td>\s*<td class="left">(.*?)</td>\s*<td class="left">(.*?)</td>', block, re.S)
    return [tuple(re.sub(r"<[^>]+>", "", c).strip() for c in r) for r in rows]


def _post(client, path, **data):
    return client.post(path, data=data, headers=ORIGIN, follow_redirects=False)


@pytest.fixture(params=["manual", "solicitud"])
def bid(request):
    return _manual() if request.param == "manual" else _taken()


# ═══════════════════ cada movimiento, con el ejecutor real y la foto del nombre ═══════════════════
def test_the_full_candidate_lifecycle_is_logged_with_executor_name_stages_and_result(bid):
    c = session_client(CCHH_EMAIL)
    pid = _profile()
    assert _post(c, f"/cchh/busquedas/{bid}/candidatos", profile_id=pid).status_code == 303
    app_id = repo.get(bid)["candidatos"][0]["application_id"]
    for etapa in ("Entrevista CCHH", "Entrevista Líder", "Decisión Final"):
        assert _post(c, f"/cchh/busquedas/{bid}/candidatos/{app_id}/etapa", etapa=etapa, resultado_final="").status_code == 303
    assert _post(c, f"/cchh/busquedas/{bid}/candidatos/{app_id}/etapa", etapa="Finalizado", resultado_final="").status_code == 303          # finaliza sin resultado todavía
    assert _post(c, f"/cchh/busquedas/{bid}/candidatos/{app_id}/etapa", etapa="Finalizado", resultado_final="Ingreso").status_code == 303   # y luego se registra el resultado
    assert _post(c, f"/cchh/busquedas/{bid}/candidatos/{app_id}/eliminar").status_code == 303
    ev = _events(bid)
    assert [(e["transicion"], e["desde"], e["hasta"], e.get("resultado")) for e in ev] == [
        ("candidato_asociado", None, "Preselección", None),
        ("candidato_etapa", "Preselección", "Entrevista CCHH", None),
        ("candidato_etapa", "Entrevista CCHH", "Entrevista Líder", None),
        ("candidato_etapa", "Entrevista Líder", "Decisión Final", None),
        ("candidato_finalizado", "Decisión Final", "Finalizado", None),
        ("candidato_finalizado", "Finalizado", "Finalizado", "Ingreso"),
        ("candidato_desvinculado", "Finalizado", None, "Ingreso"),
    ]
    assert all(e["actor"] == CCHH_EMAIL and e["actor_tipo"] == "cchh" and e["profile_id"] == pid and e["candidato"] == "Ana Pérez" and e["application_id"] == app_id and e["fecha"] for e in ev)
    # tabla existente: descripciones claras, «Realizado por» = identidad real (nunca «Capital Humano»), más reciente primero
    rows = _rows(c.get(f"/cchh/busquedas/{bid}").text)
    cand = [r for r in rows if r[0].startswith("Candidato") or r[0] == "Cambio de etapa"]
    assert [r[0] for r in cand] == ["Candidato desvinculado", "Candidato finalizado", "Candidato finalizado", "Cambio de etapa", "Cambio de etapa", "Cambio de etapa", "Candidato asociado"]
    assert {r[1] for r in cand} == {CCHH_EMAIL}
    assert cand[0][2] == "Ana Pérez (estaba en Finalizado)" and cand[1][2] == "Ana Pérez: Finalizado → Finalizado · Ingreso" and cand[2][2] == "Ana Pérez: Decisión Final → Finalizado · sin resultado"
    assert cand[-1][2] == "Ana Pérez → Preselección" and cand[-2][2] == "Ana Pérez: Preselección → Entrevista CCHH"


def test_closing_a_process_logs_each_finalized_candidate_as_rejected(bid):
    c = session_client(OTHER_CCHH)
    p1, p2 = _profile("Ana"), _profile("Luis", "Gómez")
    for pid in (p1, p2):
        _post(c, f"/cchh/busquedas/{bid}/candidatos", profile_id=pid)
    a1, a2 = (a["application_id"] for a in repo.get(bid)["candidatos"])
    _post(c, f"/cchh/busquedas/{bid}/candidatos/{a1}/etapa", etapa="Finalizado", resultado_final="Ingreso")
    assert _post(c, f"/cchh/busquedas/{bid}/cerrar", finalize_application_id=a2).status_code == 303
    closing = [e for e in _events(bid) if e["transicion"] == "candidato_finalizado" and e["application_id"] == a2]
    assert [(e["candidato"], e["desde"], e["hasta"], e["resultado"], e["actor"]) for e in closing] == [("Luis Gómez", "Preselección", "Finalizado", "Rechazado", OTHER_CCHH)]   # el ya finalizado no se toca


def test_each_event_records_its_own_executor_with_different_users(bid):
    a, b = session_client(CCHH_EMAIL), session_client(OTHER_CCHH)
    pid = _profile()
    _post(a, f"/cchh/busquedas/{bid}/candidatos", profile_id=pid)
    app_id = repo.get(bid)["candidatos"][0]["application_id"]
    _post(b, f"/cchh/busquedas/{bid}/candidatos/{app_id}/etapa", etapa="Entrevista CCHH", resultado_final="")
    _post(a, f"/cchh/busquedas/{bid}/candidatos/{app_id}/eliminar")
    assert [e["actor"] for e in _events(bid)] == [CCHH_EMAIL, OTHER_CCHH, CCHH_EMAIL]
    assert [r[1] for r in _rows(a.get(f"/cchh/busquedas/{bid}").text)[:3]] == [CCHH_EMAIL, OTHER_CCHH, CCHH_EMAIL]


# ═══════════════════ idempotencia, persistencia y orden ═══════════════════
def test_retries_and_double_submits_do_not_duplicate_events(bid):
    c = session_client(CCHH_EMAIL)
    pid = _profile()
    for _ in range(3):
        _post(c, f"/cchh/busquedas/{bid}/candidatos", profile_id=pid)                                           # asociar dos veces el mismo candidato activo: una sola postulación y un solo evento
    app_id = repo.get(bid)["candidatos"][0]["application_id"]
    for _ in range(3):
        _post(c, f"/cchh/busquedas/{bid}/candidatos/{app_id}/etapa", etapa="Entrevista CCHH", resultado_final="")
    _post(c, f"/cchh/busquedas/{bid}/candidatos/{app_id}/etapa", etapa="Entrevista CCHH", resultado_final="")
    assert len(repo.get(bid)["candidatos"]) == 1 and [e["transicion"] for e in _events(bid)] == ["candidato_asociado", "candidato_etapa"]
    assert len(repo.get(bid)["candidatos"][0]["historial"]) == 2                                                  # tampoco se duplica la fila de etapas de la postulación


def test_no_event_is_recorded_when_the_persistence_is_not_confirmed(bid, remote):
    c = session_client(CCHH_EMAIL)
    pid = _profile()
    _post(c, f"/cchh/busquedas/{bid}/candidatos", profile_id=pid)
    app_id = repo.get(bid)["candidatos"][0]["application_id"]
    before = repo.get(bid)
    remote.enabled = True
    remote.push_script = [False] * 6                                                                              # Spaces no confirma
    with pytest.raises((repo.BusquedaStorageError, repo.BusquedaRemoteOutcomeUnknownError)):
        svc.move_application_stage(bid, app_id, "Entrevista CCHH", None, actor=CCHH_EMAIL)
    with pytest.raises((repo.BusquedaStorageError, repo.BusquedaRemoteOutcomeUnknownError)):
        svc.remove_application(bid, app_id, actor=CCHH_EMAIL)
    assert repo.get(bid) == before and [e["transicion"] for e in _events(bid)] == ["candidato_asociado"]            # ni el cambio ni el evento


def test_events_keep_chronological_order_and_the_table_lists_the_newest_first(bid):
    c = session_client(CCHH_EMAIL)
    for first in ("Ana", "Luis", "Marta"):
        _post(c, f"/cchh/busquedas/{bid}/candidatos", profile_id=_profile(first, "Test"))
    stamps = [e["fecha"] for e in repo.historial_proceso_of(repo.get(bid))]
    assert stamps == sorted(stamps) and [e["candidato"] for e in _events(bid)] == ["Ana Test", "Luis Test", "Marta Test"]
    assert [r[2] for r in _rows(c.get(f"/cchh/busquedas/{bid}").text) if r[0] == "Candidato asociado"] == ["Marta Test → Preselección", "Luis Test → Preselección", "Ana Test → Preselección"]


# ═══════════════════ contratos que no cambian ═══════════════════
def test_solicitud_transitions_stay_intact_and_candidate_events_never_reach_operaciones():
    bid = _taken()
    c = session_client(CCHH_EMAIL)
    _post(c, f"/cchh/busquedas/{bid}/candidatos", profile_id=_profile())
    s = doc(bid)["solicitud"]
    assert [h["transicion"] for h in s["historial"]] == ["enviar", "tomar", "candidato_asociado"] and s["estado"] == "en_gestion"      # mismas transiciones; el evento convive en el MISMO historial
    assert repo.solicitud_fue_tomada(doc(bid)) and repo.solicitud_reenvios(doc(bid)) == 0
    ops_view = svc.get_solicitud_view(bid)
    assert [h["transicion"] for h in ops_view["historial"]] == ["enviar", "tomar"]                                  # Operaciones: sin movimientos de candidatos
    ops_page = session_client(OPS_A).get(f"/operaciones/busquedas/{bid}").text
    assert "Ana Pérez" not in ops_page and "Candidato asociado" not in ops_page and CCHH_EMAIL not in ops_page


def test_a_manual_process_keeps_its_classification_and_historical_pipeline_data_is_not_reconstructed():
    bid = _manual()
    c = session_client(CCHH_EMAIL)
    doc_before = repo.get(bid)
    assert repo.solicitud_of(doc_before) is None and repo.historial_proceso_of(doc_before) == []
    pid = _profile()
    # candidatura PREVIA a esta funcionalidad (sin evento): no se reconstruye ni se inventa
    repo.add_candidate(bid, pid)                                                                                   # sin actor/names: historial legado
    assert [e["transicion"] for e in _events(bid)] == ["candidato_asociado"] and _events(bid)[0]["actor"] is None and _events(bid)[0]["candidato"] is None
    page = c.get(f"/cchh/busquedas/{bid}").text
    assert _rows(page)[0][1:] == ("Usuario no identificado", "Candidato (nombre no disponible) → Preselección")
    after = repo.get(bid)
    assert repo.solicitud_of(after) is None and repo.es_gestionada(after) and "solicitud" not in after                 # no se convirtió en «solicitud»
