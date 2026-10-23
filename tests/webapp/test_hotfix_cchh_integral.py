"""Hotfix integral CCHH + Operaciones (A persistencia transversal, B preloader de subida, C baja de documento + carpeta, D baja contextual de candidato, E solicitudes en LOCAL).

Todo offline: `core.microsoft365` se reemplaza por un Graph simulado en memoria (ninguna llamada real, ningún dato real). Los modos de persistencia se fijan explícitamente."""
from __future__ import annotations

import re
import urllib.error
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import (auth_service, busqueda_repository as brepo, commercial_licensing_priority_repository as lrepo, entrevista_repository as erepo,
                             human_capital_service as hcs, profile_document_repository as drepo, profile_document_service as svc, profile_repository as prepo)
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, OPS_A, SID, env, remote, req, send  # noqa: F401  (fixtures y helpers reutilizados)
from tests.webapp.test_operations_busquedas_i2 import BASE, ORIGIN, operations_users, ops, post_new  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
PID = "abcd1234-0000-4000-8000-000000000000"
FOLDER = "fold-1"
EMAIL = "hc-integral-test@danwor.com"


def _http(code):
    return urllib.error.HTTPError("https://graph.microsoft.com/v1.0/x", code, "err", None, None)


# ═══════════════════════ A. persistencia transversal (local / remote / read-only) ═══════════════════════
@pytest.fixture()
def boom_remote(monkeypatch):
    """Cualquier push/delete remoto desde `local` es un fallo del test."""
    def boom(*a, **k):
        raise AssertionError("local no debe llamar a Spaces")
    monkeypatch.setattr(brepo.remote_store, "push_path", boom)
    monkeypatch.setattr(brepo.remote_store, "delete_path", boom)
    monkeypatch.setattr(brepo.remote_store, "enabled", lambda: False)


def test_busquedas_local_create_edit_cancel_and_delete_persist_on_disk_without_spaces(env, boom_remote, monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    hcs.create_solicitud(SID, req(), OPS_A)
    assert brepo.get(SID)["solicitud"]["estado"] == "enviada"
    hcs.edit_solicitud(SID, 1, req(comentarios="editada"), OPS_A)
    hcs.cancel_solicitud(SID, 2, OPS_A, "ya no")
    assert brepo.get(SID)["solicitud"]["estado"] == "cancelada" and brepo.get(SID)["revision"] == 3
    sid2 = "8d1f1a6e-2b53-4c1d-9b1e-6a3a5c8e7f10"
    hcs.create_solicitud(sid2, req(), OPS_A)
    hcs.delete_solicitud(sid2, 1, OPS_A)                                             # eliminación física local
    assert brepo.get(sid2) is None and brepo.get(SID) is not None


def test_busquedas_remote_keeps_the_confirmation_contract_and_read_only_rejects_writes(env, remote, monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")
    remote.enabled = False                                                           # remote sin credenciales: NUNCA cae a escritura solo local
    remote.push_script = [False] * 6
    with pytest.raises(brepo.BusquedaConfigIncompleteError):
        hcs.create_solicitud(SID, req(), OPS_A)
    assert brepo.get(SID) is None
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    hcs.create_solicitud(SID, req(), OPS_A)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    with pytest.raises(brepo.BusquedaConfigIncompleteError):
        hcs.cancel_solicitud(SID, 1, OPS_A, None)
    with pytest.raises(brepo.BusquedaConfigIncompleteError):
        hcs.delete_solicitud(SID, 1, OPS_A)
    assert brepo.get(SID)["solicitud"]["estado"] == "enviada" and not list((config.BASE_DIR / "human-capital" / "busquedas").glob(".tmp-*"))


def test_entrevistas_and_licensing_priority_local_write_and_delete_without_spaces(tmp_path, boom_remote, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(lrepo.remote_store, "push_path", lambda p: (_ for _ in ()).throw(AssertionError("spaces")))
    monkeypatch.setattr(lrepo.remote_store, "delete_path", lambda p: (_ for _ in ()).throw(AssertionError("spaces")))
    e = erepo.create({"fecha_entrevista": "2026-10-01", "puesto_evaluado": "X"}, profile_id="p1", busqueda_id=None, application_id=None)
    assert erepo.get(e["entrevista_id"]) is not None
    erepo.delete(e["entrevista_id"])
    assert erepo.get(e["entrevista_id"]) is None
    lrepo.defer(42, by="a@danwor.com")
    assert lrepo._record_path(42).exists()
    lrepo.restore(42)
    assert not lrepo._record_path(42).exists()
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    with pytest.raises(erepo.EntrevistaConfigIncompleteError):
        erepo.create({"fecha_entrevista": "2026-10-01"}, profile_id="p1", busqueda_id=None, application_id=None)
    with pytest.raises(lrepo.CommercialLicensingPriorityStorageError):
        lrepo.defer(43, by="a@danwor.com")


# ═══════════════════════ E. Operaciones en LOCAL → visible para CCHH ═══════════════════════
def test_a_request_created_from_operaciones_in_local_is_visible_to_cchh(env, monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", CCHH_EMAIL)
    r = post_new(ops())
    assert r.status_code == 303 and r.headers["location"].startswith(f"{BASE}/{SID}")                # antes: «no configurado» (revertía la escritura)
    assert env.pushes == []                                                                          # sin Spaces
    assert [v["busqueda_id"] for v in hcs.list_solicitudes_view()] == [SID] and hcs.resumen_solicitudes()["pendiente"] == 1
    cchh = TestClient(app, follow_redirects=False)
    cchh.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": CCHH_EMAIL, "name": "C", "first_name": "C", "family_name": "T"}))
    assert SID in cchh.get("/cchh/solicitudes", headers={"HX-Request": "true"}).text                 # bandeja de CCHH por los servicios existentes


# ═══════════════════════ B. preloader de la subida en Editar ═══════════════════════
def test_the_documents_panel_upload_forms_reuse_the_hub_loader_with_double_submit_and_error_reset():
    panel = (ROOT / "webapp/templates/partials/human_capital_profile_documents_panel.html").read_text(encoding="utf-8")
    assert panel.count('class="btn-primary asr-generate"') == 2 and panel.count("Subiendo documento…") >= 2          # subida normal + reintento
    assert panel.count('class="asr-generate__busy htmx-indicator"') >= 2
    for needle in ('form.dataset.hcBusy === "true"', "htmx-request", "setBusy(form)", "clearBusy", "tokenFailed", '"pageshow"', "data-hc-token-error"):
        assert needle in panel, needle
    css = (ROOT / "webapp/static/human_capital.css").read_text(encoding="utf-8")
    assert ".hc-document-upload-form .btn-primary.asr-generate .asr-generate__spinner" in css


# ═══════════════════════ C. baja de documento + carpeta segura ═══════════════════════
def _st(etag="E1", lm="2026-10-23T10:00:00Z", size=0, count=0) -> dict:
    return {"eTag": etag, "lastModifiedDateTime": lm, "size": size, "childCount": count}


class Graph:
    """Graph simulado con la forma REAL de una carpeta de SharePoint: `id` + `eTag` + `folder.childCount`, y NUNCA `cTag` (Microsoft: «the cTag property isn't returned if the driveItem has a folder facet»)."""
    def __init__(self, folder=None, children=None, get_error=None, delete_error=None, children_error=None, fail_items=(), states=None, state_error=None):
        self.fail_items = set(fail_items)
        self.states = list(states) if states is not None else None    # secuencia de estados por lectura (None = sin eTag); None global = estable
        self.state_error = state_error
        self.folder = folder if folder is not None else {"id": FOLDER, "name": "Pérez, Ana [abcd1234]", "folder": {}, "parentReference": {"id": "root-1"}}
        self.children = children if children is not None else {"value": []}
        self.get_error, self.delete_error, self.children_error = get_error, delete_error, children_error
        self.calls, self.delete_headers = [], []

    def get(self, url, token):
        self.calls.append(("GET", url))
        if "$select=id,eTag" in url:                                   # lectura del estado de la carpeta
            if self.state_error:
                raise self.state_error
            if self.get_error:
                raise self.get_error
            st = _st() if self.states is None else (self.states.pop(0) if len(self.states) > 1 else self.states[0])
            if st is None:
                return {"id": FOLDER}                                  # respuesta sin eTag
            return {"id": FOLDER, "eTag": st["eTag"], "lastModifiedDateTime": st["lastModifiedDateTime"], "size": st["size"], "folder": {"childCount": st["childCount"]}}
        if "/children" in url:
            if self.children_error:
                raise self.children_error
            return self.children
        if self.get_error:
            raise self.get_error
        return self.folder

    def delete(self, url, token, **kw):
        self.calls.append(("DELETE", url))
        self.delete_headers.append((kw.get("extra_headers") or {}).get("If-Match"))
        if any(url.endswith(f"/items/{i}") for i in self.fail_items):
            raise _http(403)
        if url.endswith("/items/item-1") or url.endswith("/items/item-2"):
            return None
        if self.delete_error:
            raise self.delete_error

    def folder_deletes(self):
        return [u for m, u in self.calls if m == "DELETE" and u.endswith(f"/items/{FOLDER}")]


@pytest.fixture()
def hc(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(config, "HC_SHAREPOINT_SITE_ID", "site-1")
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "drive-1")
    monkeypatch.setattr(config, "HC_CV_ROOT_ITEM_ID", "root-1")
    monkeypatch.setattr(config, "HC_DOCUMENT_WRITE_SCOPE", "https://danwor.sharepoint.com/Sites.Selected")
    monkeypatch.setattr(svc, "_FOLDER_RETRY_DELAY_S", 0)

    def install(graph):
        monkeypatch.setattr(svc.microsoft365, "graph_get_json", graph.get)
        monkeypatch.setattr(svc.microsoft365, "graph_delete", graph.delete)
        return graph
    return install


def _doc(doc_id, item, profile=PID, folder=FOLDER, **o):
    f = {"profile_id": profile, "provider": "sharepoint", "site_id": "site-1", "drive_id": "drive-1", "item_id": item, "folder_item_id": folder, "filename": f"{doc_id}.pdf",
         "content_type": "application/pdf", "size_bytes": 5, "uploaded_at": "2026-10-23T00:00:00Z", "uploaded_by": "a@danwor.com", **o}
    drepo.create_with_explicit_id(doc_id, f)


def test_deleting_the_last_document_deletes_the_physical_file_then_the_metadata_then_the_verified_empty_folder(hc):
    g = hc(Graph())
    _doc("d1", "item-1")
    out = svc.delete_document(profile_id=PID, document_id="d1", access_token="t", remove_empty_folder=True)
    assert out == {"folder": "deleted", "last_document": True} and drepo.get("d1") is None
    kinds = [(m, u.split("/items/")[1].split("?")[0]) for m, u in g.calls]
    assert kinds[0] == ("DELETE", "item-1") and ("GET", FOLDER) in kinds and ("GET", f"{FOLDER}/children") in kinds and kinds[-1] == ("DELETE", FOLDER)   # físico → verificación → carpeta


def test_the_folder_is_kept_when_other_documents_or_other_candidates_reference_it(hc):
    g = hc(Graph())
    _doc("d1", "item-1")
    _doc("d2", "item-2")                                                                  # otro documento del MISMO candidato en la carpeta
    assert svc.delete_document(profile_id=PID, document_id="d1", access_token="t", remove_empty_folder=True) == {"folder": "kept_shared_metadata", "last_document": False}
    assert g.folder_deletes() == [] and not any("/children" in u for m, u in g.calls)    # ni siquiera consulta Graph


def test_the_folder_is_kept_when_a_document_of_another_candidate_references_it(hc):
    g = hc(Graph())
    _doc("d1", "item-1")
    _doc("dx", "item-9", profile="otro-candidato")                                         # referencia de OTRO candidato a la misma carpeta
    out = svc.delete_document(profile_id=PID, document_id="d1", access_token="t", remove_empty_folder=True)
    assert out["folder"] == "kept_shared_metadata" and out["last_document"] is True        # último DEL CANDIDATO, pero otro registro referencia la carpeta
    assert g.folder_deletes() == [] and drepo.get("dx") is not None


@pytest.mark.parametrize("graph_kw,expected", [
    ({"children": {"value": [{"id": "x", "name": "otro.pdf"}]}}, "kept_has_content"),                       # archivos adicionales
    ({"children": {"value": [], "@odata.nextLink": "https://graph.microsoft.com/next"}}, "kept_has_content"),   # paginación pendiente = incierto
    ({"children": {"unexpected": True}}, "kept_unverified"),                                                # respuesta irreconocible
    ({"children_error": _http(500)}, "kept_error"),
    ({"children_error": _http(403)}, "kept_permission"),
    ({"children_error": urllib.error.URLError("net")}, "kept_error"),
    ({"folder": {"id": FOLDER, "name": "Otra carpeta", "folder": {}, "parentReference": {"id": "root-1"}}}, "kept_unverified"),            # identidad: nombre no corresponde
    ({"folder": {"id": FOLDER, "name": "Pérez, Ana [abcd1234]", "folder": {}, "parentReference": {"id": "otra-raiz"}}}, "kept_unverified"),  # ubicación distinta
    ({"folder": {"id": FOLDER, "name": "Pérez, Ana [abcd1234]", "parentReference": {"id": "root-1"}}}, "kept_unverified"),                   # no es carpeta
    ({"states": [_st("E1"), _st("E1", lm="t2"), _st("E3", lm="t3"), _st("E4", lm="t4")]}, "kept_changed"),   # cambió mientras se listaba (en ambos intentos): concurrencia detectada
    ({"states": [_st(count=0), _st(count=1), _st(count=2), _st(count=3)]}, "kept_changed"),                  # entró un hijo mientras se verificaba
    ({"states": [None]}, "kept_unverified"),                                                               # sin eTag = incierto (nunca «sin cambios»)
    ({"states": [{**_st(), "childCount": None}]}, "kept_unverified"),                                       # sin `folder.childCount` numérico = ambiguo
    ({"state_error": _http(403)}, "kept_permission"),
    ({"state_error": _http(500)}, "kept_error"),
    ({"state_error": urllib.error.URLError("net")}, "kept_error"),
    ({"state_error": TimeoutError("timeout")}, "kept_error"),                                              # un timeout de lectura no debe escapar como excepción
    ({"children_error": TimeoutError("timeout")}, "kept_error"),
    ({"children_error": ValueError("json ilegible")}, "kept_error"),
    ({"children": {"value": [{"id": "item-1"}, {"id": "ajeno"}]}}, "kept_has_content"),                    # el archivo recién borrado + uno ajeno: se conserva
    ({"children": {"value": [{"name": "sin-id.pdf"}]}}, "kept_has_content"),                              # hijo sin identidad legible: no se puede descartar
    ({"children": {"value": ["raro"]}}, "kept_has_content"),
    ({"delete_error": _http(412)}, "kept_changed"),                                                        # DELETE condicionado: la carpeta cambió (precondición fallida)
    ({"get_error": _http(404)}, "already_absent"),
    ({"get_error": _http(403)}, "kept_permission"),
    ({"get_error": _http(500)}, "kept_error"),
    ({"delete_error": _http(403)}, "kept_permission"),
    ({"delete_error": _http(404)}, "already_absent"),                                                      # idempotente: otro proceso ya la borró
    ({"delete_error": _http(500)}, "kept_error"),
    ({"delete_error": urllib.error.URLError("net")}, "kept_error"),                                          # ambiguo: se conserva el estado «incierto»
])
def test_folder_cleanup_only_deletes_when_identity_location_and_emptiness_are_verified(hc, graph_kw, expected):
    g = hc(Graph(**graph_kw))
    _doc("d1", "item-1")
    out = svc.delete_document(profile_id=PID, document_id="d1", access_token="t", remove_empty_folder=True)
    assert out["folder"] == expected
    assert drepo.get("d1") is None                                                         # la metadata queda consistente aunque la carpeta no se pueda borrar
    if expected.startswith("kept_") and "delete_error" not in graph_kw:
        assert g.folder_deletes() == []                                                    # nunca se intenta borrar ante una duda


def test_folder_cleanup_is_idempotent_and_requires_the_stored_identity_to_match_the_current_config(hc, monkeypatch):
    g = hc(Graph())
    assert svc.cleanup_empty_profile_folder(profile_id=PID, folder_item_id=FOLDER, site_id="site-1", drive_id="drive-1", access_token="t") == "deleted"
    g.get_error = _http(404)
    assert svc.cleanup_empty_profile_folder(profile_id=PID, folder_item_id=FOLDER, site_id="site-1", drive_id="drive-1", access_token="t") == "already_absent"
    g2 = hc(Graph())
    assert svc.cleanup_empty_profile_folder(profile_id=PID, folder_item_id=FOLDER, site_id="site-OLD", drive_id="drive-1", access_token="t") == "kept_unverified" and g2.calls == []
    assert svc.cleanup_empty_profile_folder(profile_id=PID, folder_item_id=None, site_id="site-1", drive_id="drive-1", access_token="t") == "not_requested"


def test_a_real_shaped_folder_response_without_ctag_is_deleted_with_a_conditional_delete(hc):
    """Defecto de producción: SharePoint no devuelve `cTag` en carpetas; con `id` + `eTag` la carpeta vacía SÍ se elimina, con `If-Match: <eTag>`, y jamás se pide ni se compara un cTag."""
    g = hc(Graph(states=[_st(etag='"{ABC-1},4"')]))
    _doc("d1", "item-1")
    out = svc.delete_document(profile_id=PID, document_id="d1", access_token="t", remove_empty_folder=True)
    assert out == {"folder": "deleted", "last_document": True}
    assert g.delete_headers[-1] == '"{ABC-1},4"' and len(g.folder_deletes()) == 1                    # DELETE condicionado con el eTag leído
    assert all("cTag" not in u for _, u in g.calls)


def test_only_the_just_deleted_file_is_tolerated_in_the_listing_never_a_foreign_one(hc):
    g = hc(Graph(children={"value": [{"id": "item-1"}]}))                                            # consistencia eventual: el archivo recién borrado aún figura
    _doc("d1", "item-1")
    assert svc.delete_document(profile_id=PID, document_id="d1", access_token="t", remove_empty_folder=True)["folder"] == "deleted"
    g2 = hc(Graph(children={"value": [{"id": "item-1"}, {"id": "item-ajeno"}]}))
    _doc("d2", "item-1", profile=PID)
    assert svc.delete_document(profile_id=PID, document_id="d2", access_token="t", remove_empty_folder=True)["folder"] == "kept_has_content" and g2.folder_deletes() == []
    g3 = hc(Graph(children={"value": [{"id": "item-1"}]}))                                           # un id recién borrado de OTRO documento no se tolera si no se pasó como borrado ahora
    assert svc.cleanup_empty_profile_folder(profile_id=PID, folder_item_id=FOLDER, site_id="site-1", drive_id="drive-1", access_token="t") == "kept_has_content" and g3.folder_deletes() == []


def test_pagination_pending_or_an_over_full_listing_keeps_the_folder(hc):
    g = hc(Graph(children={"value": [], "@odata.nextLink": "https://graph.microsoft.com/next"}))
    assert svc.cleanup_empty_profile_folder(profile_id=PID, folder_item_id=FOLDER, site_id="site-1", drive_id="drive-1", access_token="t") == "kept_has_content" and g.folder_deletes() == []


def test_a_transient_state_lag_is_resolved_by_one_full_reverification_and_a_persistent_change_is_not(hc, monkeypatch):
    sleeps = []
    monkeypatch.setattr(svc.time, "sleep", lambda s: sleeps.append(s))
    lag = hc(Graph(states=[_st("E1", lm="t1"), _st("E2", lm="t2"), _st("E2", lm="t2")]))                # 1er intento: cambió; 2do (re-verificación desde cero): estable
    assert svc.cleanup_empty_profile_folder(profile_id=PID, folder_item_id=FOLDER, site_id="site-1", drive_id="drive-1", access_token="t") == "deleted"
    assert len(sleeps) == 1 and len(lag.folder_deletes()) == 1
    sleeps.clear()
    busy = hc(Graph(states=[_st("E1", lm="t1"), _st("E2", lm="t2"), _st("E3", lm="t3"), _st("E4", lm="t4")]))
    assert svc.cleanup_empty_profile_folder(profile_id=PID, folder_item_id=FOLDER, site_id="site-1", drive_id="drive-1", access_token="t") == "kept_changed"
    assert len(sleeps) == 1 and busy.folder_deletes() == []                                          # una única re-verificación; nunca se borra si sigue cambiando


def test_a_412_precondition_failed_keeps_the_folder_as_a_concurrent_change(hc):
    g = hc(Graph(delete_error=_http(412)))
    assert svc.cleanup_empty_profile_folder(profile_id=PID, folder_item_id=FOLDER, site_id="site-1", drive_id="drive-1", access_token="t") == "kept_changed"
    assert g.delete_headers and all(h == "E1" for h in g.delete_headers)                              # siempre condicionado al eTag leído


def test_identity_checks_are_unchanged_root_parent_and_name_discrepancies_keep_the_folder(hc):
    for folder in ({"id": FOLDER, "name": "Pérez, Ana [abcd1234]", "folder": {}, "parentReference": {"id": "ROOT-1"}},          # la raíz se compara EXACTA (sin normalizar mayúsculas)
                   {"id": FOLDER, "name": "Pérez, Ana [abcd1234]", "folder": {}, "parentReference": {}},
                   {"id": FOLDER, "name": "Otra [zzzz9999]", "folder": {}, "parentReference": {"id": "root-1"}}):
        g = hc(Graph(folder=folder))
        assert svc.cleanup_empty_profile_folder(profile_id=PID, folder_item_id=FOLDER, site_id="site-1", drive_id="drive-1", access_token="t") == "kept_unverified"
        assert g.folder_deletes() == [] and not any("/children" in u for m, u in g.calls)              # sin identidad verificada ni siquiera se listan los hijos


def test_the_bulk_delete_tolerates_all_of_its_own_confirmed_files_and_deletes_the_folder_once(hc):
    g = hc(Graph(children={"value": [{"id": "item-1"}, {"id": "item-2"}]}))
    _doc("d1", "item-1")
    _doc("d2", "item-2")
    out = svc.delete_profile_documents(profile_id=PID, access_token="t")
    assert out == {"deleted": 2, "folders": {FOLDER: "deleted"}} and len(g.folder_deletes()) == 1
    g2 = hc(Graph(children={"value": [{"id": "item-1"}, {"id": "ajeno"}]}))
    _doc("d3", "item-1")
    assert svc.delete_profile_documents(profile_id=PID, access_token="t")["folders"] == {FOLDER: "kept_has_content"} and g2.folder_deletes() == []


def test_the_keep_reason_is_logged_structured_without_personal_data_tokens_or_urls(hc, caplog):
    import logging
    hc(Graph(children={"value": [{"id": "x"}]}))
    with caplog.at_level(logging.INFO, logger="danwor.cchh.profile_document"):
        svc.cleanup_empty_profile_folder(profile_id=PID, folder_item_id=FOLDER, site_id="site-1", drive_id="drive-1", access_token="SECRET-TOKEN")
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "profile_folder_cleanup outcome=kept_has_content stage=children" in text and "attempt=1" in text
    for leak in ("SECRET-TOKEN", "Pérez", "Ana", "graph.microsoft.com", FOLDER, PID, "http"):
        assert leak not in text.replace("http_status", "")
    caplog.clear()
    hc(Graph(delete_error=_http(403)))
    with caplog.at_level(logging.INFO, logger="danwor.cchh.profile_document"):
        svc.cleanup_empty_profile_folder(profile_id=PID, folder_item_id=FOLDER, site_id="site-1", drive_id="drive-1", access_token="t")
    assert "outcome=kept_permission stage=delete http_status=403" in " ".join(r.getMessage() for r in caplog.records)


def test_the_default_service_delete_does_not_touch_the_folder(hc):
    g = hc(Graph())
    _doc("d1", "item-1")
    assert svc.delete_document(profile_id=PID, document_id="d1", access_token="t") == {"folder": "not_requested", "last_document": False}
    assert [m for m, u in g.calls] == ["DELETE"]


def test_an_unconfirmed_physical_delete_never_removes_the_metadata_nor_the_folder(hc):
    g = hc(Graph(fail_items={"item-1"}))
    _doc("d1", "item-1")
    with pytest.raises(svc.ProfileDocumentDeletePermissionDeniedError):
        svc.delete_document(profile_id=PID, document_id="d1", access_token="t", remove_empty_folder=True)
    assert drepo.get("d1") is not None


# ═══════════════════════ D. baja contextual de candidato ═══════════════════════
def _profile(first="Ana", pid=PID):
    prepo.create_with_explicit_id(pid, {"first_name": first, "last_name": "Pérez"})
    return pid


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": EMAIL, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


def test_the_dialog_shows_the_unchecked_checkbox_only_when_the_candidate_has_documents(hc, client):
    hc(Graph())
    with_docs, without = _profile("Con"), _profile("Sin", pid="eeee5555-0000-4000-8000-000000000000")
    _doc("d1", "item-1", profile=with_docs, folder="f-x")
    page = client.get(f"/cchh/candidatos/{with_docs}/editar").text
    assert "data-hc-baja-check" in page and "Eliminar también los documentos de SharePoint" in page and re.search(r"<button[^>]*data-hc-baja-open", page)
    assert "data-hc-baja-check checked" not in page and 'name="delete_documents" value="true" data-hc-baja-check>' in page          # desmarcada por defecto
    plain = client.get(f"/cchh/candidatos/{without}/editar").text
    assert "data-hc-baja-check" not in plain and "No tiene documentos cargados." in plain and "data-dw-confirm-form" in plain
    listing = client.get("/cchh/candidatos").text
    assert len(re.findall(r"<button[^>]*data-hc-baja-open", listing)) == 1 and "No tiene documentos cargados." in listing and 'hx-post="/cchh/candidatos/' in listing


def test_the_baja_without_the_checkbox_keeps_the_documents_and_never_calls_graph(hc, client):
    g = hc(Graph())
    pid = _profile()
    _doc("d1", "item-1", profile=pid)
    r = client.post(f"/cchh/candidatos/{pid}/eliminar")
    assert r.status_code == 303 and g.calls == [] and prepo.get(pid)["deleted_at"] and drepo.get("d1") is not None


def test_the_baja_with_the_checkbox_deletes_documents_then_the_empty_folder_then_soft_deletes(hc, client):
    g = hc(Graph())
    pid = _profile()
    _doc("d1", "item-1", profile=pid)
    _doc("d2", "item-2", profile=pid)
    r = client.post(f"/cchh/candidatos/{pid}/eliminar", data={"delete_documents": "true", "graph_token": "tok"})
    assert r.status_code == 303 and prepo.get(pid)["deleted_at"]
    assert drepo.list_for_profile(pid) == [] and len(g.folder_deletes()) == 1
    assert [u.split("/items/")[1] for m, u in g.calls if m == "DELETE"][:2] == ["item-1", "item-2"]          # físicos primero


def test_the_baja_with_the_checkbox_requires_the_token_and_changes_nothing_without_it(hc, client):
    g = hc(Graph())
    pid = _profile()
    _doc("d1", "item-1", profile=pid)
    r = client.post(f"/cchh/candidatos/{pid}/eliminar", data={"delete_documents": "true"})
    assert r.status_code == 200 and "NO fue dado de baja" in r.text and g.calls == []
    assert not prepo.get(pid).get("deleted_at") and drepo.get("d1") is not None


def test_a_partial_failure_keeps_the_candidate_active_reports_the_counts_and_the_retry_finishes_the_job(hc, client):
    g = hc(Graph(fail_items={"item-2"}))
    pid = _profile()
    _doc("d1", "item-1", profile=pid)
    _doc("d2", "item-2", profile=pid)
    r = client.post(f"/cchh/candidatos/{pid}/eliminar", data={"delete_documents": "true", "graph_token": "tok"})
    assert r.status_code == 200 and "NO fue dado de baja" in r.text and "Se eliminaron 1 documento(s) y quedan 1" in r.text
    assert not prepo.get(pid).get("deleted_at") and [d["document_id"] for d in drepo.list_for_profile(pid)] == ["d2"] and g.folder_deletes() == []
    g.fail_items.clear()                                                                   # permisos resueltos: el reintento es idempotente y completa
    r2 = client.post(f"/cchh/candidatos/{pid}/eliminar", data={"delete_documents": "true", "graph_token": "tok"})
    assert r2.status_code == 303 and prepo.get(pid)["deleted_at"] and drepo.list_for_profile(pid) == [] and len(g.folder_deletes()) == 1


def test_a_kept_folder_after_the_baja_is_reported_as_a_partial_success_never_a_silent_redirect(hc, client):
    g = hc(Graph(children={"value": [{"id": "x"}]}))                                          # la carpeta tiene un archivo ajeno: se conserva
    pid = _profile()
    _doc("d1", "item-1", profile=pid)
    r = client.post(f"/cchh/candidatos/{pid}/eliminar", data={"delete_documents": "true", "graph_token": "tok"})
    assert r.status_code == 200 and "data-delete-notice" in r.text and "se conservó en SharePoint" in r.text and "contiene otros archivos" in r.text
    assert "fue dado de baja y sus 1 documento(s) se eliminaron de SharePoint" in r.text
    assert prepo.get(pid)["deleted_at"] and drepo.list_for_profile(pid) == [] and g.folder_deletes() == []


def test_a_baja_that_fails_after_the_documents_were_deleted_says_so(hc, client, monkeypatch):
    hc(Graph())
    pid = _profile()
    _doc("d1", "item-1", profile=pid)
    monkeypatch.setattr(hcs, "soft_delete_profile", lambda *a, **k: (_ for _ in ()).throw(prepo.ProfileStorageError("disco lleno")))
    r = client.post(f"/cchh/candidatos/{pid}/eliminar", data={"delete_documents": "true", "graph_token": "tok"})
    assert r.status_code == 200 and "disco lleno" in r.text and "YA fueron eliminados de SharePoint" in r.text
    assert not prepo.get(pid).get("deleted_at") and drepo.list_for_profile(pid) == []


def test_the_individual_document_delete_route_also_removes_the_verified_empty_folder(hc, client):
    g = hc(Graph())
    pid = _profile()
    _doc("d1", "item-1", profile=pid)
    r = client.post(f"/cchh/candidatos/{pid}/documentos/d1/delete", data={"graph_token": "tok"})
    assert r.status_code == 303 and drepo.get("d1") is None and len(g.folder_deletes()) == 1


def test_the_individual_delete_reports_a_kept_folder_after_the_last_document_but_not_when_others_remain(hc, client):
    g = hc(Graph(children={"value": [{"id": "x"}]}))
    pid = _profile()
    _doc("d1", "item-1", profile=pid)
    r = client.post(f"/cchh/candidatos/{pid}/documentos/d1/delete", data={"graph_token": "tok"})
    assert r.status_code == 200 and 'data-document-error-kind="FOLDER_KEPT"' in r.text and "El documento se eliminó de SharePoint." in r.text and "contiene otros archivos" in r.text
    assert drepo.get("d1") is None and g.folder_deletes() == []
    _doc("d2", "item-1", profile=pid)
    _doc("d3", "item-2", profile=pid)                                                         # queda otro documento en la misma carpeta: no es un caso a informar
    r2 = client.post(f"/cchh/candidatos/{pid}/documentos/d2/delete", data={"graph_token": "tok"})
    assert r2.status_code == 303 and drepo.get("d3") is not None
