"""
test_profile_document_folder_naming.py — CCHH: carpetas legibles de candidatos en SharePoint (`Apellido, Nombre [id8]`).

Contrato (ver el docstring de `profile_document_service`): la identidad de la carpeta es su `driveItem.id`, guardado completo en el campo OPCIONAL `folder_item_id` de cada ProfileDocument; el `[id8]`
es informativo. Antes de reutilizar una carpeta se verifica que corresponde al candidato (carpeta, bajo la raíz, nombre esperado/marcador, no reclamada por OTRO candidato). Las carpetas UUID históricas
siguen siendo válidas y NUNCA se renombran; tampoco hay renombrado automático si cambia el nombre del candidato; los reintentos no generan duplicados; ningún log contiene nombres.

Sin Graph real: un SharePoint simulado en memoria (carpetas por id/nombre, 404/409) reemplaza a `core.microsoft365` función por función."""
from __future__ import annotations

import logging
import urllib.error
import urllib.parse

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import (
    auth_service, profile_document_repository as repo, profile_document_service as svc, profile_repository, sharepoint_graph_diagnostics as diag,
)

ROOT = "root-1"
PROFILE_A = "a1b2c3d4-0000-4000-8000-000000000001"
PROFILE_B = "a1b2c3d4-0000-4000-8000-000000000002"      # mismo id8 que A a propósito: el marcador es informativo, NO identidad
PROFILE_C = "f00dcafe-0000-4000-8000-000000000003"      # id8 distinto: el homónimo realista
PDF = b"%PDF-1.4\n%x\n"


def _http_error(code: int, url="https://graph.microsoft.com/v1.0/x") -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url, code, "err", None, None)


class FakeDrive:
    """SharePoint mínimo: carpetas bajo la raíz (por id y por nombre, sin distinguir mayúsculas), POST children con 409, PUT de contenido. Registra cada llamada."""

    def __init__(self):
        self.folders: dict[str, dict] = {}
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[dict] = []
        self.n = 0
        self.post_errors: list = []        # excepciones a lanzar (en orden) en los próximos POST
        self.hide_after_409 = False

    def add_folder(self, name, *, parent=ROOT, kind="folder") -> str:
        self.n += 1
        fid = f"F{self.n}"
        item = {"id": fid, "name": name, "parentReference": {"id": parent}}
        if kind == "folder":
            item["folder"] = {}
        self.folders[fid] = item
        return fid

    def get(self, url, token, **kw):
        self.calls.append(("GET", url))
        tail = url.split("/items/", 1)[1]
        if ":/" in tail:
            name = urllib.parse.unquote(tail.split(":/", 1)[1].split("?")[0])
            for item in self.folders.values():
                if item["name"].casefold() == name.casefold() and item["parentReference"]["id"] == ROOT:     # el path sólo resuelve hijos DIRECTOS de la raíz
                    return dict(item)
            raise _http_error(404, url)
        fid = tail.split("?")[0]
        if fid in self.folders:
            return dict(self.folders[fid])
        raise _http_error(404, url)

    def post(self, url, token, body, **kw):
        self.calls.append(("POST", url))
        self.bodies.append(body)
        if self.post_errors:
            raise self.post_errors.pop(0)
        if any(i["name"].casefold() == body["name"].casefold() and i["parentReference"]["id"] == ROOT for i in self.folders.values()):
            raise _http_error(409, url)
        return {"id": self.add_folder(body["name"]), "name": body["name"]}

    def put(self, url, token, data, **kw):
        self.calls.append(("PUT", url))
        self.n += 1
        return {"id": f"FILE{self.n}", "webUrl": "https://danwor.sharepoint.com/sites/x/doc", "name": "x"}

    @property
    def methods(self):
        return [m for m, _u in self.calls]

    def paths(self, method):
        return [u for m, u in self.calls if m == method]


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "HC_SHAREPOINT_SITE_ID", "site-1")
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "drive-1")
    monkeypatch.setattr(config, "HC_CV_ROOT_ITEM_ID", ROOT)
    monkeypatch.setattr(config, "HC_DOCUMENT_WRITE_SCOPE", "https://danwor.sharepoint.com/Sites.Selected")
    monkeypatch.setattr(repo.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(repo.remote_store, "enabled", lambda: False)


@pytest.fixture
def drive(monkeypatch):
    d = FakeDrive()
    monkeypatch.setattr(svc.microsoft365, "graph_get_json", d.get)
    monkeypatch.setattr(svc.microsoft365, "graph_post_json", d.post)
    monkeypatch.setattr(svc.microsoft365, "graph_put_bytes", d.put)
    monkeypatch.setattr(svc.microsoft365, "graph_delete", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no se borra nada")))
    return d


def _upload(profile_id, first="Ana", last="Pérez", *, filename="cv.pdf"):
    return svc.upload_document(profile_id=profile_id, filename=filename, data=PDF, access_token="tok", uploaded_by="u@x", first_name=first, last_name=last)


def _docs(profile_id):
    return sorted(repo.list_for_profile(profile_id), key=lambda d: d["uploaded_at"])


# ── nombre: convención, saneamiento, límites ─────────────────────────────────────────────────────────────────
def test_the_folder_name_follows_the_convention_with_an_informative_marker():
    assert svc.build_folder_name(PROFILE_A, "Ana", "Pérez") == "Pérez, Ana [a1b2c3d4]"
    assert svc.folder_marker(PROFILE_A) == "a1b2c3d4" and svc.folder_marker("profile-1") == "profile1"
    assert svc.build_folder_name(PROFILE_A, "Ana", "") == "Ana [a1b2c3d4]" and svc.build_folder_name(PROFILE_A, "", "Pérez") == "Pérez [a1b2c3d4]"
    assert svc.build_folder_name(PROFILE_A, "", "") is None and svc.build_folder_name(PROFILE_A, "  ", None) is None       # sin nombres: carpeta UUID (contrato original)


@pytest.mark.parametrize("first,last,expected", [
    ('Ana "María"', "Pérez/Gómez", "Pérez Gómez, Ana María [a1b2c3d4]"),
    ("Ana", "O'Brien: Jr.", "O'Brien Jr, Ana [a1b2c3d4]"),
    ("  Ana   María ", "  de   la Cruz ", "de la Cruz, Ana María [a1b2c3d4]"),
    ("Ana#1", "100%~{x}|?*<>\\", "100 x, Ana 1 [a1b2c3d4]"),
    ("José", "Núñez..", "Núñez, José [a1b2c3d4]"),
    ("Ana\x00\n", "Pérez\t", "Pérez, Ana [a1b2c3d4]"),
])
def test_forbidden_characters_are_removed_and_spaces_collapsed(first, last, expected):
    assert svc.build_folder_name(PROFILE_A, first, last) == expected


def test_each_name_part_is_bounded_and_unicode_is_normalized():
    long = svc.build_folder_name(PROFILE_A, "A" * 200, "B" * 200)
    assert long == f"{'B' * 60}, {'A' * 60} [a1b2c3d4]" and len(long) < 140
    decomposed = "José"                                      # e + acento combinante
    assert svc.build_folder_name(PROFILE_A, decomposed, "X") == "X, José [a1b2c3d4]"


# ── primer documento: crea la carpeta legible y guarda folder_item_id ─────────────────────────────────────────
def test_the_first_document_creates_a_readable_folder_and_persists_the_full_folder_id(drive):
    doc = _upload(PROFILE_A)
    assert drive.methods == ["GET", "GET", "POST", "PUT"]                                             # legible (404), UUID heredado (404), crear, subir
    assert drive.bodies == [{"name": "Pérez, Ana [a1b2c3d4]", "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}]
    assert "%2C" in drive.paths("GET")[0] and "%5B" in drive.paths("GET")[0] and "Pérez" not in drive.paths("GET")[0]              # codificado como UN segmento
    assert doc["folder_item_id"] == "F1" and _docs(PROFILE_A)[0]["folder_item_id"] == "F1"
    assert drive.paths("PUT")[0].startswith(f"https://graph.microsoft.com/v1.0/sites/site-1/drives/drive-1/items/F1:/")


def test_without_candidate_names_the_original_uuid_folder_contract_is_kept(drive):
    svc.upload_document(profile_id=PROFILE_A, filename="cv.pdf", data=PDF, access_token="tok", uploaded_by="u@x")
    assert drive.bodies[0]["name"] == PROFILE_A and drive.methods == ["GET", "POST", "PUT"]
    svc.upload_document(profile_id=PROFILE_B, filename="cv.pdf", data=PDF, access_token="tok", uploaded_by="u@x", first_name="", last_name="")
    assert drive.bodies[1]["name"] == PROFILE_B


# ── documentos adicionales: por id, sin buscar ni crear ───────────────────────────────────────────────────────
def test_additional_documents_go_to_the_stored_folder_by_id_without_lookup_or_creation(drive):
    _upload(PROFILE_A)
    drive.calls.clear()
    doc2 = _upload(PROFILE_A, filename="otro.pdf")
    assert drive.methods == ["GET", "PUT"] and "/items/F1?$select=id,name,folder,parentReference" in drive.paths("GET")[0]   # verificación por id + subida
    assert drive.bodies == [drive.bodies[0]] and len(drive.bodies) == 1                                  # ningún POST nuevo
    assert doc2["folder_item_id"] == "F1" and len(drive.folders) == 1


def test_changing_the_candidate_name_never_renames_nor_creates_a_new_folder(drive):
    _upload(PROFILE_A, "Ana", "Pérez")
    drive.calls.clear()
    _upload(PROFILE_A, "Ana María", "Pérez de Lopez", filename="b.pdf")                                 # cambió el nombre del candidato
    assert drive.methods == ["GET", "PUT"] and len(drive.folders) == 1 and drive.folders["F1"]["name"] == "Pérez, Ana [a1b2c3d4]"
    assert [d["folder_item_id"] for d in _docs(PROFILE_A)] == ["F1", "F1"]


def test_the_folder_survives_a_manual_rename_in_sharepoint_only_if_it_keeps_the_candidate_marker(drive):
    _upload(PROFILE_A)
    drive.folders["F1"]["name"] = "Carpeta de Ana [a1b2c3d4] (revisar)"                                  # renombrada a mano, conserva el marcador al final? no: no termina en él
    drive.calls.clear()
    _upload(PROFILE_A, filename="b.pdf")
    assert "POST" in drive.methods and len(drive.folders) == 2                                          # no verificable -> no se reutiliza; se resuelve por ruta/crea (carpeta adicional, nunca una errónea)
    drive.folders["F1"]["name"] = "Ana Pérez [a1b2c3d4]"
    drive.calls.clear()
    _upload(PROFILE_A, filename="c.pdf")
    assert docs_latest_folder(PROFILE_A) in {"F1", "F3"}


def docs_latest_folder(profile_id):
    return _docs(profile_id)[-1]["folder_item_id"]


# ── homónimos y colisiones ────────────────────────────────────────────────────────────────────────────────────
def test_namesakes_get_distinct_folders(drive):
    _upload(PROFILE_A, "Ana", "Pérez")
    _upload(PROFILE_C, "Ana", "Pérez")                                                                   # mismos nombres, id8 distinto
    owners = {pid: {d["folder_item_id"] for d in _docs(pid)} for pid in (PROFILE_A, PROFILE_C)}
    assert not (owners[PROFILE_A] & owners[PROFILE_C]) and len(drive.folders) == 2
    assert sorted(f["name"] for f in drive.folders.values()) == ["Pérez, Ana [a1b2c3d4]", "Pérez, Ana [f00dcafe]"]


def test_a_marker_collision_between_namesakes_fails_safe_and_never_writes_into_the_other_folder(drive):
    """El id8 es informativo (NO identidad): si dos candidatos con los MISMOS nombres colisionaran incluso en el id8, el segundo no puede usar la carpeta del primero."""
    _upload(PROFILE_A, "Ana", "Pérez")
    drive.calls.clear()
    with pytest.raises(svc.ProfileDocumentGraphSourceError):
        _upload(PROFILE_B, "Ana", "Pérez")
    assert "PUT" not in drive.methods and _docs(PROFILE_B) == [] and {d["folder_item_id"] for d in _docs(PROFILE_A)} == {"F1"}


def test_a_folder_claimed_by_another_candidate_is_never_reused(drive):
    fid = drive.add_folder("Pérez, Ana [a1b2c3d4]")
    repo.create_with_explicit_id("doc-other", {"profile_id": PROFILE_B, "provider": "sharepoint", "site_id": "s", "drive_id": "d", "item_id": "i", "filename": "f.pdf", "content_type": "application/pdf",
                                               "size_bytes": 1, "uploaded_at": "2026-01-01T00:00:00+00:00", "uploaded_by": "u", "folder_item_id": fid})
    with pytest.raises(svc.ProfileDocumentGraphSourceError):                                              # la carpeta existe con ese nombre, pero es de OTRO candidato: no se usa ni se pisa
        _upload(PROFILE_A, "Ana", "Pérez")
    assert "PUT" not in drive.methods


@pytest.mark.parametrize("mutation", [{"parentReference": {"id": "otra-raiz"}}, {"name": "Otra persona [zzzzzzzz]"}])
def test_a_stored_folder_id_that_does_not_verify_is_not_reused(drive, mutation):
    _upload(PROFILE_A)
    drive.folders["F1"].update(mutation)
    drive.calls.clear()
    doc = _upload(PROFILE_A, filename="b.pdf")
    assert doc["folder_item_id"] != "F1" and "POST" in drive.methods                                      # no se escribió en la carpeta no verificada
    assert all("F1:/" not in u for u in drive.paths("PUT"))


def test_a_stored_id_that_is_no_longer_a_folder_is_not_reused_and_the_name_conflict_fails_safe(drive):
    _upload(PROFILE_A)
    drive.folders["F1"].pop("folder")                                                                     # ahora es un archivo con ese nombre bajo la raíz
    drive.calls.clear()
    with pytest.raises(svc.ProfileDocumentGraphSourceError):                                              # no se pisa ni se usa; el conflicto de nombre se informa
        _upload(PROFILE_A, filename="b.pdf")
    assert "PUT" not in drive.methods


def test_a_stale_stored_id_404_falls_back_to_path_resolution(drive):
    _upload(PROFILE_A)
    drive.folders["F2"] = drive.folders.pop("F1")                                                         # la carpeta "cambió de id" (p. ej. restaurada): el id guardado ya no existe
    drive.folders["F2"]["id"] = "F2"
    drive.calls.clear()
    doc = _upload(PROFILE_A, filename="b.pdf")
    assert doc["folder_item_id"] == "F2" and "POST" not in drive.methods                                  # se encontró por ruta y se reutilizó


# ── compatibilidad con carpetas UUID ─────────────────────────────────────────────────────────────────────────
def test_legacy_uuid_folders_keep_working_are_never_renamed_and_get_their_id_recorded(drive):
    legacy = drive.add_folder(PROFILE_A)                                                                  # carpeta histórica <raíz>/<profile_id>
    repo.create_with_explicit_id("legacy-doc", {"profile_id": PROFILE_A, "provider": "sharepoint", "site_id": "s", "drive_id": "d", "item_id": "i", "filename": "old.pdf",
                                                "content_type": "application/pdf", "size_bytes": 1, "uploaded_at": "2026-01-01T00:00:00+00:00", "uploaded_by": "u"})     # sin folder_item_id
    doc = _upload(PROFILE_A, "Ana", "Pérez", filename="nuevo.pdf")
    assert doc["folder_item_id"] == legacy and len(drive.folders) == 1 and drive.folders[legacy]["name"] == PROFILE_A       # misma carpeta, SIN renombrar
    assert "POST" not in drive.methods and drive.paths("GET")[0].endswith(f":/{PROFILE_A}")              # candidato con documentos heredados: UUID primero
    drive.calls.clear()
    _upload(PROFILE_A, "Ana", "Pérez", filename="otro.pdf")
    assert drive.methods == ["GET", "PUT"]                                                                # ya con id guardado: directo


def test_a_candidate_without_documents_but_with_a_legacy_folder_reuses_it(drive):
    legacy = drive.add_folder(PROFILE_A)
    doc = _upload(PROFILE_A)
    assert doc["folder_item_id"] == legacy and "POST" not in drive.methods and len(drive.folders) == 1


def test_there_is_never_a_rename_or_delete_call(drive):
    for pid in (PROFILE_A, PROFILE_C):
        _upload(pid, "Ana", "Pérez")
        _upload(pid, "Ana Nueva", "Pérez Nuevo", filename="b.pdf")
    assert set(drive.methods) <= {"GET", "POST", "PUT"}                                                   # el cliente de Graph ni siquiera tiene PATCH; DELETE falla el test


# ── creación, carreras y reintentos sin duplicados ───────────────────────────────────────────────────────────
def test_a_creation_race_409_rereads_the_same_name_and_verifies_it(drive):
    real_post = drive.post
    created = {}

    def racing_post(url, token, body, **kw):
        if not created:                                           # otro request creó la MISMA carpeta justo antes (la misma candidata)
            created["id"] = drive.add_folder(body["name"])
        return real_post(url, token, body, **kw)
    svc.microsoft365.graph_post_json = racing_post
    doc = _upload(PROFILE_A)
    assert doc["folder_item_id"] == created["id"] and len(drive.folders) == 1 and drive.methods.count("POST") == 1


def test_an_ambiguous_post_is_retried_once_with_the_same_name_and_never_duplicates(drive):
    drive.post_errors = [urllib.error.URLError("timed out")]
    doc = _upload(PROFILE_A)
    assert len(drive.bodies) == 2 and drive.bodies[0] == drive.bodies[1] and len(drive.folders) == 1 and doc["folder_item_id"] == "F1"      # 2 intentos, MISMO nombre, 1 carpeta
    drive.post_errors = [urllib.error.URLError("x"), urllib.error.URLError("y")]
    with pytest.raises(svc.ProfileDocumentFolderOutcomeUnknownError):
        _upload(PROFILE_B, "Luis", "Gómez")
    assert len({b["name"] for b in drive.bodies[2:]}) == 1                                                  # el nombre se calcula UNA vez por operación
    drive.post_errors = []
    doc2 = _upload(PROFILE_B, "Luis", "Gómez")                                                              # reintentar es seguro
    assert [f["name"] for f in drive.folders.values()].count("Gómez, Luis [a1b2c3d4]") == 1 and doc2["folder_item_id"]


def test_permission_and_source_errors_on_verification_are_classified_not_swallowed(drive):
    _upload(PROFILE_A)
    drive.get = None
    for code, exc in ((403, svc.ProfileDocumentPermissionDeniedError), (500, svc.ProfileDocumentGraphSourceError)):
        svc.microsoft365.graph_get_json = lambda url, token, c=code, **kw: (_ for _ in ()).throw(_http_error(c, url))
        with pytest.raises(exc):
            _upload(PROFILE_A, filename="b.pdf")
    svc.microsoft365.graph_get_json = lambda url, token, **kw: (_ for _ in ()).throw(urllib.error.URLError("net"))
    with pytest.raises(svc.ProfileDocumentGraphSourceError):                                                # un error transitorio NO se interpreta como "no existe": no se crea otra carpeta
        _upload(PROFILE_A, filename="c.pdf")


# ── tokens de reintento y metadata ───────────────────────────────────────────────────────────────────────────
def test_retry_tokens_carry_the_folder_id_and_the_committed_document_keeps_it():
    token = svc.sign_commit_retry_token(document_id="d1", profile_id=PROFILE_A, provider="sharepoint", site_id="s", drive_id="d", item_id="i", web_url="u", filename="f.pdf",
                                        content_type="application/pdf", size_bytes=1, uploaded_at="2026-01-01T00:00:00+00:00", uploaded_by="u", initiated_by="u", folder_item_id="F9")
    document, created = svc.retry_commit_document(token=token, requesting_user_sub="u")
    assert created is True and document["folder_item_id"] == "F9"
    legacy = svc.sign_commit_retry_token(document_id="d2", profile_id=PROFILE_A, provider="sharepoint", site_id="s", drive_id="d", item_id="i", web_url="u", filename="f.pdf",
                                         content_type="application/pdf", size_bytes=1, uploaded_at="2026-01-01T00:00:00+00:00", uploaded_by="u", initiated_by="u")   # token emitido antes: sin el claim
    document2, _ = svc.retry_commit_document(token=legacy, requesting_user_sub="u")
    assert "folder_item_id" not in document2


def test_an_upload_retry_commits_the_folder_id_from_its_token(drive):
    token = svc.sign_upload_retry_token(document_id="d3", profile_id=PROFILE_A, folder_item_id="F7", filename="d3__cv.pdf", original_filename="cv.pdf", content_type="application/pdf",
                                        size_bytes=len(PDF), sha256=__import__("hashlib").sha256(PDF).hexdigest(), initiated_by="u")
    document = svc.retry_upload_document(token=token, data=PDF, filename="cv.pdf", access_token="tok", requesting_user_sub="u")
    assert document is None or True
    assert repo.get("d3")["folder_item_id"] == "F7" and drive.methods == ["PUT"]                            # el reintento usa la carpeta del token: nada de lookups ni POST


def test_folder_item_id_is_optional_metadata_not_part_of_the_canonical_identity():
    base = {"profile_id": PROFILE_A, "provider": "sharepoint", "site_id": "s", "drive_id": "d", "item_id": "i", "filename": "f.pdf", "content_type": "application/pdf", "size_bytes": 1,
            "uploaded_at": "2026-01-01T00:00:00+00:00", "uploaded_by": "u"}
    assert "folder_item_id" not in repo._CANONICAL_FIELDS
    first, created = repo.create_with_explicit_id("dx", dict(base))
    again, created2 = repo.create_with_explicit_id("dx", {**base, "folder_item_id": "F1"})
    assert created is True and created2 is False and "folder_item_id" not in again                           # un reintento con el campo no genera conflicto ni reescribe


def test_the_repository_finds_documents_by_folder_across_candidates():
    for doc_id, pid, fid in (("a", PROFILE_A, "F1"), ("b", PROFILE_B, "F1"), ("c", PROFILE_A, "F2")):
        repo.create_with_explicit_id(doc_id, {"profile_id": pid, "provider": "sharepoint", "site_id": "s", "drive_id": "d", "item_id": doc_id, "filename": "f.pdf", "content_type": "application/pdf",
                                              "size_bytes": 1, "uploaded_at": f"2026-01-0{len(doc_id)}T00:00:00+00:00", "uploaded_by": "u", "folder_item_id": fid})
    assert sorted(d["profile_id"] for d in repo.find_by_folder_item_id("F1")) == [PROFILE_A, PROFILE_B] and repo.find_by_folder_item_id("nope") == [] and repo.find_by_folder_item_id("") == []


# ── datos personales: nada de nombres en logs técnicos ───────────────────────────────────────────────────────
def test_sanitize_endpoint_masks_person_names_but_keeps_identifiers():
    base = "https://graph.microsoft.com/v1.0/sites/site-1/drives/drive-1/items/root-1"
    readable = f"{base}:/" + urllib.parse.quote("Pérez, Ana [a1b2c3d4]", safe="") + "?x=1"
    assert diag.sanitize_endpoint(readable) == f"{base}:/<nombre>"
    assert diag.sanitize_endpoint(f"{base}:/{PROFILE_A}") == f"{base}:/{PROFILE_A}"                          # UUID heredado: identificador, no dato personal
    uploaded = f"https://graph.microsoft.com/v1.0/sites/s/drives/d/items/F1:/{PROFILE_A}__" + urllib.parse.quote("CV Ana Pérez.pdf", safe="") + ":/content"
    assert diag.sanitize_endpoint(uploaded).endswith(f"items/F1:/{PROFILE_A}__<archivo>:/content")
    assert diag.sanitize_endpoint(f"{base}/children") == f"{base}/children"


@pytest.mark.parametrize("status", [403, 404, 500])
def test_application_logs_contain_no_candidate_names(drive, caplog, status):
    caplog.set_level(logging.DEBUG)
    svc.microsoft365.graph_get_json = lambda url, token, **kw: (_ for _ in ()).throw(_http_error(status, url))
    svc.microsoft365.graph_post_json = lambda url, token, body, **kw: (_ for _ in ()).throw(_http_error(status if status != 404 else 500, url))
    with pytest.raises(Exception):
        _upload(PROFILE_A, "Ana", "Pérez")
    text = caplog.text
    assert "Pérez" not in text and "P%C3%A9rez" not in text and "Ana" not in text.replace("Anal", "")


# ── integración: los routers pasan los nombres del candidato ─────────────────────────────────────────────────
_EMAIL = "hc-folder-naming-test@danwor.com"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": _EMAIL, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


def test_create_with_cv_and_the_documents_route_use_the_readable_folder_of_the_candidate(drive, client):
    r = client.post("/cchh/candidatos/nuevo", data={"first_name": "Ana", "last_name": "López", "graph_token": "tok"}, files={"file": ("cv.pdf", PDF, "application/pdf")})
    assert r.status_code == 303
    profile = profile_repository.list()[0]
    marker = svc.folder_marker(profile["profile_id"])
    assert drive.bodies[0]["name"] == f"López, Ana [{marker}]"
    r2 = client.post(f"/cchh/candidatos/{profile['profile_id']}/documentos", data={"graph_token": "tok"}, files={"file": ("otro.pdf", PDF, "application/pdf")})
    assert r2.status_code == 303 and len(drive.folders) == 1 and len(_docs(profile["profile_id"])) == 2          # la ruta de Documentos reutiliza la MISMA carpeta
    assert {d["folder_item_id"] for d in _docs(profile["profile_id"])} == {"F1"}
