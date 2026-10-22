"""Operaciones → Búsquedas: ELIMINAR solicitudes que Capital Humano todavía no tomó (≠ cancelar).

Regla: Operaciones elimina mientras la solicitud está enviada / observada / reenviada y CCHH NO la tomó nunca; una vez tomada (estado `en_gestion` o marca/historial de toma) pierde para siempre
eliminar y cancelar. Sin candidatos / entrevistas / procesos asociados. La decisión de autorización usa el HISTORIAL, no solo el estado visible. Borrado físico durable con la MISMA persistencia
(`busqueda_repository` + `remote_store`); desaparece de Operaciones y de la bandeja de CCHH. Fixtures reutilizadas de I1/I2; sin Spaces, SharePoint ni Entra."""
from __future__ import annotations

import json
import re

import pytest

import config
from webapp.services import busqueda_repository as repo, human_capital_service as svc
from tests.webapp.test_busqueda_solicitud_i1 import (  # noqa: F401  (fixtures y helpers reutilizados)
    CCHH_EMAIL, OPS_A, OPS_B, SID, SID2, env, files, remote, req, rev, send, to_state,
)
from tests.webapp.test_operations_busquedas_i2 import BASE, ORIGIN, OUTSIDER, form_payload, operations_users, ops, session_client  # noqa: F401

HX = {"HX-Request": "true"}


def cchh():
    return session_client(CCHH_EMAIL)


def delete(c, sid=SID, revision=None, headers=ORIGIN):
    return c.post(f"{BASE}/{sid}/eliminar", data={"expected_revision": str(rev(sid) if revision is None else revision)}, headers=headers)


def exists(sid=SID) -> bool:
    return repo.get(sid) is not None


# ═══════════════════════ borrado permitido: antes de la toma ═══════════════════════
@pytest.mark.parametrize("estado", ["enviada", "observada", "reenviada"])
def test_operations_can_delete_a_request_cchh_has_not_taken(estado, remote):
    to_state(estado)
    r = delete(ops(OPS_B))
    assert r.status_code == 303 and r.headers["location"] == f"{BASE}?ok=eliminada"
    assert not exists() and files() == []                                             # ni local ni en la copia remota
    assert f"{SID}.json" not in remote.objects


def test_the_deletion_confirmation_is_shown_and_the_request_is_gone_from_the_operations_list():
    send()
    send(SID2, comentarios="otra")
    c = ops()
    assert SID in c.get(BASE).text
    page = c.get(delete(c).headers["location"]).text
    assert "Solicitud eliminada" in page and SID not in page and SID2 in page         # solo se fue la eliminada
    assert c.get(f"{BASE}/{SID}").status_code == 404 and c.get(f"{BASE}/{SID}/editar").status_code == 404


@pytest.mark.parametrize("estado", ["enviada", "observada"])
def test_a_deleted_request_disappears_from_the_cchh_inbox_and_counts(estado):
    to_state(estado)
    to_state("enviada", SID2)
    inbox_before = cchh().get("/cchh/solicitudes", headers=HX).text
    assert SID in inbox_before
    assert delete(ops()).status_code == 303
    inbox = cchh().get("/cchh/solicitudes", headers=HX).text
    assert SID not in inbox and SID2 in inbox
    assert svc.resumen_solicitudes()["total"] == 1 and [v["busqueda_id"] for v in svc.list_solicitudes_view()] == [SID2]


def test_deleting_one_request_does_not_touch_the_others_or_their_files():
    send()
    send(SID2, comentarios="otra")
    other = repo.get(SID2)
    assert delete(ops()).status_code == 303
    assert repo.get(SID2) == other and [p.name for p in files()] == [f"{SID2}.json"]


# ═══════════════════════ borrado rechazado: ya tomada ═══════════════════════
def test_once_cchh_takes_it_operations_loses_delete_and_cancel_for_good():
    send()
    svc.take_solicitud(SID, rev(), CCHH_EMAIL)
    before = repo.get(SID)
    c = ops()
    assert delete(c).status_code == 409 and repo.get(SID) == before
    assert c.post(f"{BASE}/{SID}/cancelar", data={"expected_revision": str(rev())}, headers=ORIGIN).status_code == 409 and repo.get(SID) == before
    html = c.get(f"{BASE}/{SID}").text
    assert "opsb-delete-dialog" not in html and "Eliminar solicitud" not in html and "/eliminar" not in html and "opsb-cancel-dialog" not in html


def test_history_wins_over_the_visible_state_a_request_taken_once_never_regains_deletion_or_cancellation():
    """Simula una devolución futura: la solicitud figura `enviada` (u `observada`) pero el historial / las marcas dicen que CCHH la tomó."""
    for how in ("historial", "marcas"):
        sid = SID if how == "historial" else SID2
        send(sid)
        path = config.BASE_DIR / "human-capital" / "busquedas" / f"{sid}.json"
        d = json.loads(path.read_text(encoding="utf-8"))
        if how == "historial":
            d["solicitud"]["historial"].append({"transicion": "tomar", "desde": "enviada", "hasta": "en_gestion", "actor": CCHH_EMAIL, "actor_tipo": "cchh", "fecha": "2026-10-22T10:00:00+00:00"})
        else:
            d["solicitud"]["tomada_en"] = "2026-10-22T10:00:00+00:00"
        path.write_text(json.dumps(d), encoding="utf-8")                              # estado visible: sigue `enviada`
        assert repo.solicitud_estado(repo.get(sid)) == "enviada" and repo.solicitud_fue_tomada(repo.get(sid))
        c = ops()
        before = repo.get(sid)
        assert delete(c, sid).status_code == 409
        assert c.post(f"{BASE}/{sid}/cancelar", data={"expected_revision": str(rev(sid))}, headers=ORIGIN).status_code == 409
        assert c.post(f"{BASE}/{sid}/editar", data={"expected_revision": str(rev(sid)), **form_payload(sid)}, headers=ORIGIN).status_code == 409
        assert repo.get(sid) == before
        view = svc.get_solicitud_view(sid)
        assert not (view["puede_eliminar"] or view["puede_cancelar"] or view["puede_editar"] or view["puede_corregir"])
        html = c.get(f"{BASE}/{sid}").text
        assert "Eliminar solicitud" not in html and "opsb-delete-dialog" not in html


def test_a_cancelled_request_is_kept_as_history_and_cannot_be_deleted():
    to_state("cancelada")
    before = repo.get(SID)
    assert delete(ops()).status_code == 409 and repo.get(SID) == before
    assert "Eliminar solicitud" not in ops().get(f"{BASE}/{SID}").text


@pytest.mark.parametrize("closed", [False, True])
def test_a_managed_process_is_never_deletable_by_operations(closed):
    to_state("en_gestion")
    if closed:
        svc.close_busqueda(SID)
    before = repo.get(SID)
    assert delete(ops()).status_code == 409 and repo.get(SID) == before


def test_requests_with_candidates_or_linked_records_cannot_be_deleted(monkeypatch):
    send()
    path = config.BASE_DIR / "human-capital" / "busquedas" / f"{SID}.json"
    d = json.loads(path.read_text(encoding="utf-8"))
    d["candidatos"] = [{"application_id": "a1", "profile_id": "p1", "etapa": "Preselección"}]
    path.write_text(json.dumps(d), encoding="utf-8")
    assert not repo.solicitud_puede_eliminar(repo.get(SID)) and delete(ops()).status_code == 409 and exists()
    # vínculos con entrevistas / onboarding / publicaciones (aunque la solicitud no tenga candidatos)
    d["candidatos"] = []
    path.write_text(json.dumps(d), encoding="utf-8")
    for module in (svc.entrevista_repository, svc.onboarding_repository, svc.publicacion_repository):
        monkeypatch.setattr(module, "list", lambda: [{"busqueda_id": SID}])
        r = delete(ops())
        assert r.status_code == 409 and exists(), module.__name__
        monkeypatch.setattr(module, "list", lambda: [])


def test_a_cchh_manual_busqueda_cannot_be_deleted_through_the_operations_route_nor_the_solicitud_operation():
    manual = svc.create_busqueda({"nombre_puesto": "Manual", "fecha_apertura": "2026-10-01", "modalidad": "Remoto"}, actor=CCHH_EMAIL)
    bid = manual["busqueda_id"]
    c = ops()
    assert c.post(f"{BASE}/{bid}/eliminar", data={"expected_revision": "1"}, headers=ORIGIN).status_code == 404     # para Operaciones no existe (no es una solicitud)
    with pytest.raises(repo.BusquedaInvalidTransitionError):
        repo.delete_solicitud(bid, 1, OPS_A)
    with pytest.raises(repo.BusquedaInvalidTransitionError):
        svc.delete_solicitud(bid, 1, OPS_A)
    assert exists(bid)


def test_the_cchh_delete_path_still_refuses_any_operations_request():
    send()
    with pytest.raises(repo.BusquedaInvalidTransitionError):
        repo.delete(SID)
    with pytest.raises(ValueError):
        svc.delete_busqueda(SID)
    assert exists()


# ═══════════════════════ concurrencia ═══════════════════════
def test_a_stale_revision_is_a_conflict_and_nothing_is_deleted():
    send()
    stale = rev()
    svc.edit_solicitud(SID, stale, req(comentarios="editada"), OPS_B)
    r = delete(ops(), revision=stale)
    assert r.status_code == 409 and exists() and "cambió" in r.text


def test_if_cchh_takes_it_between_opening_the_detail_and_confirming_the_delete_is_rejected():
    send()
    c = ops()
    opened = c.get(f"{BASE}/{SID}").text
    revision = int(re.search(r'name="expected_revision" value="(\d+)"', opened.split("opsb-delete-dialog")[1]).group(1))
    assert "opsb-delete-dialog" in opened
    cchh().post(f"/cchh/busquedas/{SID}/tomar", data={"expected_revision": str(revision)}, headers=ORIGIN)       # CCHH gana la carrera
    assert repo.solicitud_estado(repo.get(SID)) == "en_gestion"
    r = delete(c, revision=revision)
    assert r.status_code == 409 and exists() and repo.solicitud_estado(repo.get(SID)) == "en_gestion"
    assert "Eliminar solicitud" not in r.text                                                                      # la pantalla re-renderizada ya no ofrece la acción


def test_the_delete_revalidates_under_the_write_lock_even_if_the_service_check_was_stale(monkeypatch):
    """CCHH toma justo después de las lecturas del servicio y antes del borrado: la revisión chequeada bajo el lock lo frena."""
    send()
    revision = rev()
    real = repo.delete_solicitud

    def racing(busqueda_id, expected_revision, actor):
        svc.take_solicitud(busqueda_id, expected_revision, CCHH_EMAIL)
        return real(busqueda_id, expected_revision, actor)

    monkeypatch.setattr(repo, "delete_solicitud", racing)
    with pytest.raises(repo.BusquedaConcurrentModificationError):
        svc.delete_solicitud(SID, revision, OPS_A)
    assert exists() and repo.solicitud_estado(repo.get(SID)) == "en_gestion"


def test_two_deletes_of_the_same_request_the_second_is_not_found():
    send()
    c = ops()
    revision = rev()
    assert delete(c, revision=revision).status_code == 303
    assert delete(c, revision=revision).status_code == 404


# ═══════════════════════ HTTP manipulado y autorización ═══════════════════════
def test_authorization_is_server_side_session_operations_and_same_origin():
    send()
    anon = session_client(OUTSIDER)
    anon.cookies.clear()
    assert anon.post(f"{BASE}/{SID}/eliminar", data={"expected_revision": "1"}, headers=ORIGIN).status_code == 401           # sin sesión
    assert session_client(OUTSIDER).post(f"{BASE}/{SID}/eliminar", data={"expected_revision": "1"}, headers=ORIGIN).status_code == 403   # fuera de Operaciones
    assert cchh().post(f"{BASE}/{SID}/eliminar", data={"expected_revision": "1"}, headers=ORIGIN).status_code == 403          # CCHH tampoco borra por la vía de Operaciones
    assert ops().post(f"{BASE}/{SID}/eliminar", data={"expected_revision": "1"}).status_code == 403                           # sin Origin
    assert ops().post(f"{BASE}/{SID}/eliminar", data={"expected_revision": "1"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert ops().get(f"{BASE}/{SID}/eliminar").status_code == 405                                                           # nunca por GET
    assert exists()


@pytest.mark.parametrize("bad", ["", "abc", "1.5", "1; DROP", " "])
def test_a_missing_or_malformed_revision_is_rejected_without_deleting(bad):
    send()
    assert ops().post(f"{BASE}/{SID}/eliminar", data={"expected_revision": bad}, headers=ORIGIN).status_code == 400
    assert ops().post(f"{BASE}/{SID}/eliminar", data={}, headers=ORIGIN).status_code == 400
    assert exists()


@pytest.mark.parametrize("bad_id", ["../x", "..%2F..%2Fetc", "a" * 80, "no existe", "00000000-0000-4000-8000-000000000000"])
def test_unknown_or_unsafe_ids_are_not_found_and_never_touch_the_disk(bad_id):
    send()
    before = files()
    assert ops().post(f"{BASE}/{bad_id}/eliminar", data={"expected_revision": "1"}, headers=ORIGIN).status_code in (404, 405)
    assert files() == before and exists()


def test_extra_form_fields_cannot_force_a_deletion_the_rules_forbid():
    to_state("en_gestion")
    r = ops().post(f"{BASE}/{SID}/eliminar", data={"expected_revision": str(rev()), "force": "1", "estado": "enviada", "tomada_en": "", "origen": "operaciones"}, headers=ORIGIN)
    assert r.status_code == 409 and exists() and repo.solicitud_estado(repo.get(SID)) == "en_gestion"


# ═══════════════════════ persistencia (misma capa, Spaces) ═══════════════════════
def test_a_remote_delete_that_cannot_be_confirmed_rolls_the_local_delete_back(remote):
    send()
    remote.enabled = True
    remote.delete_fails = True
    before = repo.get(SID)
    r = delete(ops())
    assert r.status_code == 503 and repo.get(SID) == before                       # local y remoto no divergen: la solicitud sigue ahí
    remote.delete_fails = False
    assert delete(ops()).status_code == 303 and not exists() and f"{SID}.json" not in remote.objects


def test_without_remote_configuration_the_delete_fails_deterministically_and_keeps_the_request(remote):
    send()
    remote.delete_fails = True                                                    # `enabled=False` + delete_path falso ⇒ entorno sin Spaces configurado
    r = delete(ops())
    assert r.status_code == 503 and exists()


def test_the_delete_is_audited_without_personal_content(caplog):
    send()
    with caplog.at_level("INFO", logger="danwor.human_capital"):
        assert delete(ops(OPS_B)).status_code == 303
    line = next(r.getMessage() for r in caplog.records if "solicitud_deleted" in r.getMessage())
    assert SID in line and OPS_B in line and "Consultor SAP FI" not in line


# ═══════════════════════ UX ═══════════════════════
@pytest.mark.parametrize("estado,shown", [("enviada", True), ("observada", True), ("reenviada", True), ("en_gestion", False), ("cancelada", False)])
def test_the_delete_action_and_confirmation_only_exist_on_eligible_requests(estado, shown):
    to_state(estado)
    html = ops().get(f"{BASE}/{SID}").text
    assert ("opsb-delete-dialog" in html) is shown and (f'action="{BASE}/{SID}/eliminar"' in html) is shown
    if shown:
        assert 'class="dw-confirm busqueda-dialog"' in html and "no la haya tomado en gestión" in html and "elimina definitivamente" in html
        assert "Podés editarla" in html or "Corregí los datos" in html                                      # el resto de la pantalla no cambia
    # la lista nunca ofrece controles de eliminación
    assert "/eliminar" not in ops().get(BASE).text


def test_cancel_keeps_working_exactly_as_before_and_is_distinct_from_delete():
    send()
    c = ops()
    assert c.post(f"{BASE}/{SID}/cancelar", data={"expected_revision": str(rev())}, headers=ORIGIN).status_code == 303
    d = repo.get(SID)
    assert d["solicitud"]["estado"] == "cancelada" and d["estado"] == "cerrado"                           # la cancelación CONSERVA el registro
    assert svc.get_solicitud_view(SID) is not None and SID in cchh().get("/cchh/solicitudes", params={"solicitud": "canceladas"}, headers=HX).text
