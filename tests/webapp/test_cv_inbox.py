"""Bandeja de Carga masiva (`cv_inbox`): `Perfiles/carga-masiva` → revisión → Banco + repositorio gobernado `CVs`. SharePoint SIMULADO con estado (zona de entrada en su propio drive y raíz de CVs del importador
histórico); datos sintéticos. Cubre: análisis sin efectos, estados derivados, conciliación contra el Banco y la propia bandeja, aceptación individual y por lote, idempotencia ante fallos de Graph y de persistencia,
cortes, concurrencia, origen modificado o eliminado, retiro del original y aislamiento de Históricos."""
from __future__ import annotations

import threading
import urllib.error
import urllib.parse

import pytest

import config
from core import microsoft365
from tests.webapp.cv_docs import make_pdf
from tests.webapp.test_cv_history_import_exec import CV_BASE, CVStore
from tests.webapp.test_cv_history_inventory import http_error
from tests.webapp.test_cv_history_pilot import TOKEN, home, qx  # noqa: F401  (home es fixture)
from webapp.services import cv_history_manifest as mf, cv_inbox as inbox, profile_document_repository, profile_repository

GRAPH = "https://graph.microsoft.com/v1.0"
DRIVE = "drive-p"
ITEMS = f"/drives/{DRIVE}/items/"
INBOX_URL = "https://danwor.sharepoint.com/:f:/s/Comunicaciones/IgCARGAMASIVALINK?e=tok"
ACTOR = "cchh-inbox@danwor.com"
BODY = "Consultora con experiencia en migraciones de datos y analítica avanzada"

ANA = make_pdf(["Ana Torres", "ana.torres@mail.com", "Cel +54 9 11 5555 0001", BODY])
NONAME = make_pdf(["Experiencia profesional", BODY, "Disponibilidad inmediata"])
LUCIA = make_pdf(["Lucía Gómez", "lucia.gomez@mail.com", "Cel 11 6666 1111", BODY])
PABLO = make_pdf(["Pablo Ruiz", "pablo.ruiz@correo.com", "Cel 11 4444 2222", BODY])
MARTIN_1 = make_pdf(["Martín Sosa", "martin.sosa@mail.com", "Cel 11 7777 1234", BODY])
MARTIN_2 = make_pdf(["Martín Sosa", "martin.sosa@mail.com", "Cel 11 7777 1234", "Versión actualizada", BODY])


class Intake:
    """Link fijo → `Perfiles/carga-masiva` MISMA, con archivos directos; `incorporados`/`descartados` se crean al retirar. Mueve con PATCH (If-Match); borra (If-Match) SOLO si el test lo habilita."""

    def __init__(self, has_inbox=True):
        self.has_inbox = has_inbox
        self.files: dict[str, dict] = {}
        self.folders = {"perfiles": ("Perfiles", None), "historico": ("Datasphere", "perfiles")}
        if has_inbox:
            self.folders["inbox"] = ("carga-masiva", "perfiles")
        self.downloads: list[str] = []
        self.requests: list[str] = []
        self.share_target = "inbox"                                                       # a qué carpeta apunta el link de compartir
        self.patches: list[str] = []
        self.deny_patch = False
        self.deny_delete = False
        self.allow_delete = False
        self.fail_patch: list = []
        self.deletes: list[str] = []
        self.fail_download: dict[str, list] = {}
        self.n = 0

    def add(self, fid, name, data, parent="inbox"):
        self.n += 1
        self.files[fid] = {"name": name, "data": bytes(data), "parent": parent, "v": self.n}

    def modify(self, fid, data):
        self.n += 1
        self.files[fid].update({"data": bytes(data), "v": self.n})

    def item(self, fid):
        f = self.files[fid]
        return {"id": fid, "name": f["name"], "size": len(f["data"]), "file": {"hashes": {"quickXorHash": qx(f["data"])}}, "cTag": f"c{fid}-{f['v']}", "eTag": f"e{fid}-{f['v']}",
                "parentReference": {"id": f["parent"], "driveId": DRIVE}, "webUrl": f"https://sp/{fid}"}

    def _folder(self, fid):
        name, parent = self.folders[fid]
        parent_name = self.folders[parent][0] if parent in self.folders else None
        return {"id": fid, "name": name, "folder": {}, "parentReference": {"id": parent, "driveId": DRIVE, **({"name": parent_name} if parent_name else {})},
                "webUrl": f"https://danwor.sharepoint.com/sites/Comunicaciones/{name}"}

    def get_json(self, url, token, **kw):
        assert token == TOKEN
        self.requests.append(urllib.parse.unquote(url))
        if "/shares/" in url:
            return {**self._folder(self.share_target), "parentReference": {"driveId": DRIVE, "siteId": "site-p"}}
        rest = urllib.parse.unquote(url.split(ITEMS, 1)[1]).split("?", 1)[0]
        if rest.endswith("/children"):
            parent = rest[: -len("/children")]
            return {"value": [self.item(fid) for fid, f in self.files.items() if f["parent"] == parent] + [self._folder(fid) for fid, (n, p) in self.folders.items() if p == parent]}
        if ":/" in rest:
            parent, name = rest.split(":/", 1)
            for fid, (fname, fparent) in self.folders.items():
                if fparent == parent and fname == name:
                    return self._folder(fid)
            raise http_error(404)
        if rest in self.files:
            return self.item(rest)
        if rest in self.folders:
            return self._folder(rest)
        raise http_error(404)

    def get_bytes(self, url, token, **kw):
        fid = urllib.parse.unquote(url.split(ITEMS, 1)[1]).rsplit("/content", 1)[0]
        self.downloads.append(fid)
        queue = self.fail_download.get(fid)
        if queue:
            raise queue.pop(0)
        if fid not in self.files:
            raise http_error(404)
        return self.files[fid]["data"]

    def post_json(self, url, token, body):
        parent = urllib.parse.unquote(url.split(ITEMS, 1)[1]).rsplit("/children", 1)[0]
        assert parent == "inbox" and body["name"] in ("incorporados", "descartados"), "la zona de entrada solo admite crear incorporados/descartados"
        if any(n == body["name"] and p == "inbox" for n, p in self.folders.values()):
            raise http_error(409)
        self.folders["sub-" + body["name"]] = (body["name"], "inbox")
        return self._folder("sub-" + body["name"])

    def patch_json(self, url, token, body, extra_headers=None):
        fid = urllib.parse.unquote(url.split(ITEMS, 1)[1]).split("?", 1)[0]
        self.patches.append(fid)
        assert set(body) == {"parentReference"} and body["parentReference"]["id"] in ("sub-incorporados", "sub-descartados"), "solo se retira a incorporados/descartados"
        if self.deny_patch:
            raise http_error(403)
        if self.fail_patch:
            raise self.fail_patch.pop(0)
        if fid not in self.files:
            raise http_error(404)
        if extra_headers and extra_headers.get("If-Match") != self.item(fid)["eTag"]:
            raise http_error(412)
        self.files[fid]["parent"] = body["parentReference"]["id"]
        return self.item(fid)

    def delete(self, url, token, extra_headers=None):
        fid = urllib.parse.unquote(url.split(ITEMS, 1)[1]).split("?", 1)[0]
        assert self.allow_delete, "eliminación física no habilitada en este escenario"
        assert self.files.get(fid, {}).get("parent") in ("inbox", "sub-incorporados"), "solo se elimina dentro de carga-masiva (o su subcarpeta incorporados)"
        self.deletes.append(fid)
        if self.deny_delete:
            raise http_error(403)
        if extra_headers and extra_headers.get("If-Match") != self.item(fid)["eTag"]:
            raise http_error(412)
        del self.files[fid]

    def in_folder(self, folder):
        return sorted(f["name"] for f in self.files.values() if f["parent"] == folder)

    def in_inbox(self):
        return sorted(f["name"] for f in self.files.values() if f["parent"] == "inbox")


@pytest.fixture()
def world(home, monkeypatch):
    # Configuración COMPLETA y autocontenida (la misma que el fixture de Históricos): sin `HC_DOCUMENT_WRITE_SCOPE`, `profile_document_service.require_graph_config` corta con
    # CONFIG_INCOMPLETE antes de llegar a Graph. Localmente lo completaba el `.env`; en CI no existe.
    for name, value in (("HC_SHAREPOINT_SITE_ID", "site-1"), ("HC_SHAREPOINT_DRIVE_ID", "drive-1"), ("HC_CV_ROOT_ITEM_ID", "root-1"), ("HC_CV_INBOX_FOLDER_URL", INBOX_URL),
                        ("HC_DOCUMENT_WRITE_SCOPE", "https://danwor.sharepoint.com/Sites.Selected")):
        monkeypatch.setattr(config, name, value)
    store, intake = CVStore(), Intake()

    def route(cv, src):
        return lambda url, *a, **k: cv(url, *a, **k) if CV_BASE in url else src(url, *a, **k)

    def only_cvs(fn):
        def call(url, *a, **k):
            assert CV_BASE in url, f"escritura fuera del repositorio de CVs: {url}"
            return fn(url, *a, **k)
        return call

    def only_intake(fn):
        def call(url, *a, **k):
            assert ITEMS in url and CV_BASE not in url, f"PATCH fuera de la zona de entrada: {url}"
            return fn(url, *a, **k)
        return call
    monkeypatch.setattr(microsoft365, "graph_get_json", route(store.get_json, intake.get_json))
    monkeypatch.setattr(microsoft365, "graph_get_bytes", route(store.get_bytes, intake.get_bytes))
    monkeypatch.setattr(microsoft365, "graph_post_json", route(store.post_json, intake.post_json))
    monkeypatch.setattr(microsoft365, "graph_put_bytes", only_cvs(store.put_bytes))
    # DELETE: en la bandeja (original) o en «CVs» SOLO por el servicio documental de Candidatos (eliminar un CV incorporado = eliminarlo como desde Candidatos)
    monkeypatch.setattr(microsoft365, "graph_delete", route(lambda url, token, **k: store.delete(url, token), only_intake(intake.delete)))
    monkeypatch.setattr(microsoft365, "graph_patch_json", only_intake(intake.patch_json))
    profile_repository.create({"first_name": "Lucía", "last_name": "Gómez", "email": "lucia.gomez@mail.com"})
    profile_repository.create({"first_name": "Pablo", "last_name": "Ruiz"})
    profile_repository.create({"first_name": "Pablo", "last_name": "Ruiz", "phone": "11 4444 2222"})
    return store, intake


def analyze_all():
    for _ in range(10):
        r = inbox.analyze(TOKEN, actor=ACTOR, sleep=lambda s: None)
        if not r["remaining"]:
            return r
    raise AssertionError("el análisis no terminó")


def rows():
    return {r["name"]: r for r in inbox.overview(TOKEN)["rows"]}


def banco():
    return {p["profile_id"]: p for p in profile_repository.list()}


def docs_of(pid):
    return profile_document_repository.list_for_profile(pid)


def batch(*names):
    view = rows()
    return inbox.accept_batch([(view[n]["key"], view[n]["fingerprint"]) for n in names], TOKEN, actor=ACTOR)


def form(row, **over):
    return {**row["fields"], **over}


# ═══════════════════ ubicación ═══════════════════
def test_the_fixed_link_is_the_intake_itself_and_no_subfolder_is_resolved(world, monkeypatch):
    store, intake = world
    found = inbox.resolve_inbox(TOKEN)
    assert found["id"] == "inbox" and found["drive_id"] == DRIVE and found["same_drive"] is False
    assert not any(":/carga-masiva" in u or "/items/inbox:/" in u for u in intake.requests)             # nunca se busca `carga-masiva` dentro de `carga-masiva`
    assert any(u.endswith("/items/inbox?$select=id,name,folder,webUrl,parentReference") for u in intake.requests)
    intake.share_target = "perfiles"                                                                     # un link a `Perfiles` (la carpeta padre) no es la bandeja
    with pytest.raises(inbox.InboxError) as err:
        inbox.resolve_inbox(TOKEN)
    assert err.value.code == "INBOX_INVALID"
    intake.share_target = "historico"                                                                     # otra carpeta de `Perfiles`, tampoco
    with pytest.raises(inbox.InboxError):
        inbox.resolve_inbox(TOKEN)
    monkeypatch.setattr(config, "HC_CV_INBOX_FOLDER_URL", "")
    with pytest.raises(inbox.InboxError) as err:
        inbox.resolve_inbox(TOKEN)
    assert err.value.code == "CONFIG_INCOMPLETE"


def test_the_fixed_location_is_the_confirmed_carga_masiva_link():
    assert config.HC_CV_INBOX_FOLDER_URL.startswith("https://danwor.sharepoint.com/:f:/s/Comunicaciones/") and config.HC_CV_INBOX_FOLDER_NAME == "carga-masiva"
    assert not hasattr(config, "HC_PERFILES_FOLDER_URL")


def test_only_direct_files_of_the_intake_are_listed(world):
    store, intake = world
    intake.add("h1", "historico.pdf", ANA, parent="historico")
    intake.add("a1", "cv1.pdf", ANA)
    assert [f["name"] for f in inbox.list_inbox(TOKEN, inbox.resolve_inbox(TOKEN))["files"]] == ["cv1.pdf"]


# ═══════════════════ análisis y estados ═══════════════════
def test_analysis_never_creates_profiles_nor_writes_sharepoint_and_derives_each_state(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    intake.add("n1", "documento.pdf", NONAME)
    intake.add("l1", "lucia.pdf", LUCIA)
    intake.add("p1", "pablo.pdf", PABLO)
    intake.add("d1", "cv1 copia.pdf", ANA)
    intake.add("x1", "notas.txt", b"hola")
    before = banco()
    assert {r["status"] for r in rows().values()} == {inbox.PENDING}
    result = analyze_all()
    assert result["analyzed"] == 5 and result["failed"] == 1
    assert banco() == before and store.puts == [] and store.posts == [] and intake.patches == []
    view = rows()
    assert view["cv1.pdf"]["status"] == inbox.READY and view["cv1.pdf"]["op"] == "create" and view["cv1.pdf"]["batch_ok"]
    assert view["cv1.pdf"]["fields"]["email"] == "ana.torres@mail.com" and view["cv1.pdf"]["evidence"]["level"] == "strong"
    assert view["documento.pdf"]["status"] == inbox.INCOMPLETE
    assert view["lucia.pdf"]["status"] == inbox.READY and view["lucia.pdf"]["op"] == "link" and view["lucia.pdf"]["batch_ok"]
    assert view["pablo.pdf"]["status"] == inbox.REVIEW and len(view["pablo.pdf"]["matches"]) == 2 and not view["pablo.pdf"]["batch_ok"]
    assert view["cv1 copia.pdf"]["status"] == inbox.DUPLICATE
    assert view["notas.txt"]["status"] == inbox.ERROR
    assert analyze_all()["analyzed"] == 0                                                            # reanudable: lo analizado no se repite
    assert intake.downloads.count("a1") == 1


def test_two_different_cvs_of_the_same_person_require_human_resolution(world):
    store, intake = world
    intake.add("m1", "martin.pdf", MARTIN_1)
    intake.add("m2", "martin v2.pdf", MARTIN_2)
    analyze_all()
    view = rows()
    assert {view[n]["status"] for n in ("martin.pdf", "martin v2.pdf")} == {inbox.REVIEW}
    assert "SAME_PERSON_IN_INBOX" in view["martin.pdf"]["reasons"]
    refused = batch("martin.pdf", "martin v2.pdf")
    assert refused["counts"]["done"] == 0 and banco().keys() == {p for p in banco()} and len(banco()) == 3
    first = view["martin.pdf"]
    res = inbox.accept_one(first["key"], TOKEN, actor=ACTOR, raw=form(first), op="create", fingerprint_seen=first["fingerprint"], expected_revision=0, acks=[inbox.ACK_MATCHES])
    assert res["ok"] and res["outcome"] == "DONE"
    second = rows()["martin v2.pdf"]
    assert second["status"] == inbox.READY and second["op"] == "link" and second["target"] == res["profile_id"]          # ahora es la misma persona del Banco: se propone vincular
    assert batch("martin v2.pdf")["counts"]["done"] == 1
    assert len(docs_of(res["profile_id"])) == 2 and len(banco()) == 4


# ═══════════════════ aceptación ═══════════════════
def test_batch_acceptance_creates_or_links_copies_into_cvs_and_retires_the_original(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    intake.add("l1", "lucia.pdf", LUCIA)
    intake.add("p1", "pablo.pdf", PABLO)
    analyze_all()
    lucia_before = next(p for p in banco().values() if p.get("email") == "lucia.gomez@mail.com")
    out = batch("cv1.pdf", "lucia.pdf", "pablo.pdf")
    assert out["counts"] == {"done": 2, "pending": 0, "errors": 1}
    ana = next(p for p in banco().values() if p.get("email") == "ana.torres@mail.com")
    assert ana["tags"] == inbox.TAG and ana["import_origin"]["kind"] == inbox.ORIGIN_KIND
    lucia_after = banco()[lucia_before["profile_id"]]
    assert lucia_after == lucia_before                                                              # vincular no modifica el perfil
    assert len(docs_of(ana["profile_id"])) == 1 and len(docs_of(lucia_before["profile_id"])) == 1
    doc = docs_of(ana["profile_id"])[0]
    assert doc["drive_id"] == "drive-1" and doc["item_id"] in store.files and doc["folder_item_id"] in store.folders and doc["web_url"]
    assert intake.in_inbox() == ["pablo.pdf"]                                                       # los incorporados se retiraron; el ambiguo queda
    assert {f["parent"] for fid, f in intake.files.items() if fid in ("a1", "l1")} == {"sub-incorporados"} and intake.deletes == []
    view = rows()
    assert view["cv1.pdf"]["status"] == inbox.INCORPORATED and not view["cv1.pdf"]["can_retire"]
    assert analyze_all()["analyzed"] == 0 and rows()["cv1.pdf"]["status"] == inbox.INCORPORATED   # refrescar no lo devuelve a pendiente


def test_individual_acceptance_of_incomplete_identity_and_no_email_requires_explicit_confirmation(world):
    store, intake = world
    intake.add("n1", "documento.pdf", NONAME)
    analyze_all()
    row = rows()["documento.pdf"]
    res = inbox.accept_one(row["key"], TOKEN, actor=ACTOR, raw=form(row, first_name="Rosa", last_name="Paz", profile_skills="SAP FI"), op="create", fingerprint_seen="", expected_revision=0)
    assert not res["ok"] and "Sin email" in res["error"] and len(banco()) == 3
    row = rows()["documento.pdf"]
    assert row["fields"]["first_name"] == "Rosa"                                                    # la revisión quedó guardada
    assert row["batch_ok"] is False
    res = inbox.accept_one(row["key"], TOKEN, actor=ACTOR, raw=form(row), op="create", fingerprint_seen=row["fingerprint"], expected_revision=row["draft_revision"], acks=[inbox.ACK_NO_EMAIL])
    assert res["ok"]
    created = banco()[res["profile_id"]]
    assert (created["first_name"], created["last_name"], created["profile_skills"], created["email"]) == ("Rosa", "Paz", "SAP FI", None)


def test_ambiguous_matches_cannot_be_created_without_acknowledgement_and_link_needs_a_shown_match(world):
    store, intake = world
    intake.add("p1", "pablo.pdf", PABLO)
    analyze_all()
    row = rows()["pablo.pdf"]
    res = inbox.accept_one(row["key"], TOKEN, actor=ACTOR, raw=form(row), op="create", fingerprint_seen=row["fingerprint"], expected_revision=0)
    assert not res["ok"] and "coincidencias" in res["error"]
    row = rows()["pablo.pdf"]
    res = inbox.accept_one(row["key"], TOKEN, actor=ACTOR, raw=form(row), op="link", target_profile_id="otro-id", fingerprint_seen=row["fingerprint"], expected_revision=row["draft_revision"])
    assert not res["ok"] and len(banco()) == 3
    row = rows()["pablo.pdf"]
    target = row["matches"][0]["profile_id"]
    unconfirmed = inbox.save_decision(row["key"], TOKEN, actor=ACTOR, raw=form(row), op="link", target_profile_id=target, fingerprint_seen=row["fingerprint"], expected_revision=row["draft_revision"])
    assert not unconfirmed["ok"] and "coincidencias" in unconfirmed["error"]                                         # vincular con coincidencias también exige la confirmación
    row = rows()["pablo.pdf"]
    res = inbox.save_decision(row["key"], TOKEN, actor=ACTOR, raw=form(row), op="link", target_profile_id=target, fingerprint_seen=row["fingerprint"], expected_revision=row["draft_revision"],
                              acks=[inbox.ACK_MATCHES])
    assert res["ok"]
    row = rows()["pablo.pdf"]
    assert row["status"] == inbox.READY and "RESOLVED" in row["reasons"] and row["batch_ok"]          # resuelto por una persona: ahora puede ir en un lote
    assert batch("pablo.pdf")["counts"]["done"] == 1 and len(docs_of(target)) == 1 and len(banco()) == 3


# ═══════════════════ fallos, reintentos y concurrencia ═══════════════════
def test_graph_permission_failure_keeps_the_original_and_a_retry_does_not_duplicate(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    store.deny_put = True
    out = batch("cv1.pdf")
    assert out["counts"]["done"] == 0 and out["stopped_reason"] == "PERMISSION_DENIED"
    row = rows()["cv1.pdf"]
    assert row["status"] == inbox.ACCEPTING and intake.in_inbox() == ["cv1.pdf"] and store.real_files() == {}
    assert not row["can_reopen"]                                                                    # el perfil ya se creó: no se cancela a ciegas
    store.deny_put = False
    res = inbox.accept_one(row["key"], TOKEN, actor=ACTOR)
    assert res["ok"] and len([p for p in banco().values() if p.get("email") == "ana.torres@mail.com"]) == 1 and len(store.real_files()) == 1


def test_uncertain_upload_is_reconciled_by_content_without_a_second_file(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    store.lost_ack.add("cv1.pdf")
    first = batch("cv1.pdf")
    assert first["counts"]["done"] == 0 and rows()["cv1.pdf"]["status"] == inbox.ACCEPTING
    store.lost_ack.clear()
    res = inbox.accept_one(rows()["cv1.pdf"]["key"], TOKEN, actor=ACTOR)
    assert res["ok"] and len(store.real_files()) == 1 and len(docs_of(res["profile_id"])) == 1


def test_persistence_failures_never_declare_a_partial_acceptance(world, monkeypatch):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    real_put = mf.put_ledger

    def failing(area, key, entry):
        raise mf.ManifestDurabilityError("Spaces no confirmó")
    monkeypatch.setattr(mf, "put_ledger", failing)
    out = batch("cv1.pdf")
    assert out["counts"]["done"] == 0 and len(banco()) == 3 and store.real_files() == {}             # sin la decisión durable no hay efectos
    monkeypatch.setattr(mf, "put_ledger", real_put)
    calls = {"n": 0}
    real_commit = profile_document_repository.create_with_explicit_id

    def commit_once_unknown(document_id, document):
        calls["n"] += 1
        if calls["n"] == 1:
            real_commit(document_id, document)
            raise profile_document_repository.ProfileDocumentRemoteOutcomeUnknownError("sin confirmación")
        return real_commit(document_id, document)
    monkeypatch.setattr(profile_document_repository, "create_with_explicit_id", commit_once_unknown)
    out = batch("cv1.pdf")
    assert out["counts"]["done"] == 0 and rows()["cv1.pdf"]["status"] == inbox.ACCEPTING and intake.in_inbox() == ["cv1.pdf"]
    res = inbox.accept_one(rows()["cv1.pdf"]["key"], TOKEN, actor=ACTOR)
    assert res["ok"] and len(store.real_files()) == 1 and len(docs_of(res["profile_id"])) == 1 and intake.in_inbox() == []


def test_interruption_after_the_upload_resumes_idempotently(world, monkeypatch):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    real = inbox._save_ledger

    class Killed(BaseException):
        """El proceso muere (deploy, OOM): nada lo atrapa."""

    def crash_on_done(key, entry, event=None):
        if event == "DONE":
            raise Killed()
        return real(key, entry, event)
    monkeypatch.setattr(inbox, "_save_ledger", crash_on_done)
    with pytest.raises(Killed):
        batch("cv1.pdf")
    monkeypatch.setattr(inbox, "_save_ledger", real)
    assert mf.get_ledger(inbox.AREA, rows()["cv1.pdf"]["key"])["state"] == inbox.UPLOADING and len(store.real_files()) == 1
    assert rows()["cv1.pdf"]["status"] == inbox.ACCEPTING
    res = inbox.accept_one(rows()["cv1.pdf"]["key"], TOKEN, actor=ACTOR)
    assert res["ok"] and len(store.real_files()) == 1 and len([p for p in banco().values() if p.get("email") == "ana.torres@mail.com"]) == 1


def test_double_and_concurrent_acceptance_never_duplicates(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    row = rows()["cv1.pdf"]
    held, release = threading.Event(), threading.Event()

    def holder():
        with mf.ledger_lock(inbox.AREA, row["key"]):
            held.set()
            release.wait(5)
    t = threading.Thread(target=holder)
    t.start()
    held.wait(5)
    busy = inbox.accept_batch([(row["key"], row["fingerprint"])], TOKEN, actor=ACTOR)
    release.set()
    t.join()
    assert busy["counts"]["done"] == 0 and "procesando" in busy["results"][0]["message"] and len(banco()) == 3
    assert inbox.accept_batch([(row["key"], row["fingerprint"])], TOKEN, actor=ACTOR)["counts"]["done"] == 1
    again = inbox.accept_batch([(row["key"], row["fingerprint"])], TOKEN, actor=ACTOR)
    assert again["counts"]["done"] == 0 and len(banco()) == 4 and len(store.real_files()) == 1


def test_a_source_modified_or_deleted_after_analysis_requires_revalidation(world, monkeypatch):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    intake.add("l1", "lucia.pdf", LUCIA)
    analyze_all()
    view = rows()
    stale_listing, real_listing = inbox.current_listing(TOKEN), inbox.current_listing
    intake.modify("a1", MARTIN_1)
    del intake.files["l1"]
    monkeypatch.setattr(inbox, "current_listing", lambda token: stale_listing)                     # la persona vio el listado viejo
    out = inbox.accept_batch([(view[n]["key"], view[n]["fingerprint"]) for n in ("cv1.pdf", "lucia.pdf")], TOKEN, actor=ACTOR)
    assert out["counts"]["done"] == 0 and len(banco()) == 3 and store.real_files() == {}
    monkeypatch.setattr(inbox, "current_listing", real_listing)
    view = rows()
    assert view["cv1.pdf"]["status"] == inbox.MODIFIED and view["lucia.pdf"]["status"] == inbox.MISSING
    analyze_all()
    assert rows()["cv1.pdf"]["fields"]["email"] == "martin.sosa@mail.com"                           # se reanalizó el contenido nuevo


def test_content_already_in_the_banco_is_flagged_and_dismissed_files_stay_in_the_intake(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    batch("cv1.pdf")
    intake.add("a2", "otra vez.pdf", ANA)
    intake.add("x1", "notas.txt", b"hola")
    analyze_all()
    view = rows()
    assert view["otra vez.pdf"]["status"] == inbox.PROCESSED
    assert inbox.dismiss(view["notas.txt"]["key"], actor=ACTOR, reason="NOT_A_CV")["ok"]                # sin token: la decisión queda y el retiro físico, pendiente
    assert rows()["notas.txt"]["status"] == inbox.DISCARDED and rows()["notas.txt"]["can_retire"] and "notas.txt" in intake.in_inbox()
    assert inbox.reopen(view["notas.txt"]["key"], actor=ACTOR)["ok"] and rows()["notas.txt"]["status"] == inbox.ERROR


def test_retire_without_write_permission_keeps_the_cv_incorporated_and_can_be_retried(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    intake.deny_patch = True
    assert batch("cv1.pdf")["counts"]["done"] == 1
    row = rows()["cv1.pdf"]
    assert row["status"] == inbox.INCORPORATED and row["can_retire"] and "RETIRE_PERMISSION" in row["reasons"] and intake.in_inbox() == ["cv1.pdf"]
    assert analyze_all()["analyzed"] == 0                                                            # nunca vuelve a pendiente
    intake.deny_patch = False
    assert inbox.retire(row["key"], TOKEN)["ok"] and intake.in_inbox() == []
    assert inbox.retire(row["key"], TOKEN)["ok"]                                                     # idempotente


def test_the_intake_area_is_isolated_from_historical_runs(world, home):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    batch("cv1.pdf")
    assert mf.list_runs() == [] and not (home / "human-capital" / "import-history").exists()
    assert (home / "human-capital" / "cv-inbox" / "ledger").is_dir()


# ═══════════════════ descarte: mover a descartados o eliminar con doble confirmación ═══════════════════
def _key(name):
    return rows()[name]["key"]


def test_discard_moves_the_original_to_descartados_by_default_and_it_is_never_detected_again(world):
    store, intake = world
    intake.add("x1", "notas.txt", b"hola")
    intake.add("p1", "pablo.pdf", PABLO)
    analyze_all()
    out = inbox.dismiss(_key("notas.txt"), TOKEN, actor=ACTOR, reason="NOT_A_CV")
    assert out == {"ok": True, "retire": inbox.RETIRE_DONE, "mode": inbox.MODE_DISCARDED}
    assert intake.in_folder("sub-descartados") == ["notas.txt"] and intake.deletes == [] and intake.in_inbox() == ["pablo.pdf"]
    ledger = mf.get_ledger(inbox.AREA, _key_from_ledger("notas.txt"))
    assert ledger["state"] == inbox.DISMISSED and ledger["by"] == ACTOR and ledger["retire"]["state"] == inbox.RETIRE_DONE
    events = [e["event"] for e in mf.get_audit(inbox.AREA, ledger["content_key"])["events"]]
    assert "INBOX_DISMISSED" in events and "INBOX_RETIRE_DONE" in events
    assert [f["name"] for f in inbox.list_inbox(TOKEN, inbox.resolve_inbox(TOKEN))["files"]] == ["pablo.pdf"]     # descartados/ e incorporados/ no entran a la detección
    assert analyze_all()["analyzed"] == 0
    assert inbox.dismiss(ledger["content_key"], TOKEN, actor=ACTOR, reason="NOT_A_CV")["ok"] is False                   # no se repite el descarte
    assert intake.patches.count("x1") == 1


def _key_from_ledger(name):
    return next(k for k, g in ((g["content_key"], g) for g in mf.list_groups(inbox.AREA)) if g["name"] == name)


def test_physical_deletion_requires_the_explicit_double_confirmation(world):
    store, intake = world
    intake.add("p1", "pablo.pdf", PABLO)
    analyze_all()
    key = _key("pablo.pdf")
    refused = inbox.dismiss(key, TOKEN, actor=ACTOR, reason="OTHER", delete_original=True)
    assert refused["ok"] is False and "segunda confirmación" in refused["error"]
    assert mf.get_ledger(inbox.AREA, key) is None and intake.in_inbox() == ["pablo.pdf"]                                # nada se descartó ni se tocó
    assert inbox.dismiss(key, TOKEN, actor=ACTOR, reason="OTHER", delete_original=True, confirm_delete="si")["ok"] is False
    intake.allow_delete = True
    out = inbox.dismiss(key, TOKEN, actor=ACTOR, reason="OTHER", delete_original=True, confirm_delete=inbox.DELETE_CONFIRMATION)
    assert out["ok"] and out["mode"] == inbox.MODE_DELETE and out["retire"] == inbox.RETIRE_DONE
    assert intake.deletes == ["p1"] and "p1" not in intake.files and store.real_files() == {}
    ledger = mf.get_ledger(inbox.AREA, key)
    assert ledger["retire"]["mode"] == inbox.MODE_DELETE and ledger["by"] == ACTOR
    assert "DELETED_ORIGINAL" in rows_all()[key]["reasons"]
    assert inbox.reopen(key, actor=ACTOR, token=TOKEN)["ok"] is False                                                    # eliminado: no se reabre


def rows_all():
    return {r["key"]: r for r in inbox.overview(TOKEN)["rows"]}


def test_a_graph_failure_keeps_the_discard_and_the_retry_does_not_repeat_it(world):
    store, intake = world
    intake.add("p1", "pablo.pdf", PABLO)
    analyze_all()
    key = _key("pablo.pdf")
    intake.deny_patch = True
    out = inbox.dismiss(key, TOKEN, actor=ACTOR, reason="NOT_A_CANDIDATE")
    assert out["ok"] and out["retire"] == inbox.RETIRE_PENDING and "permiso" in out["warning"]
    row = rows()["pablo.pdf"]
    assert row["status"] == inbox.DISCARDED and row["can_retire"] and "RETIRE_PERMISSION" in row["reasons"] and intake.in_inbox() == ["pablo.pdf"]
    intake.deny_patch = False
    intake.fail_patch = [urllib.error.URLError("corte de red")]                                                         # respuesta incierta: no se movió
    assert inbox.retire(key, TOKEN)["ok"] is False and intake.in_inbox() == ["pablo.pdf"]
    assert inbox.retire(key, TOKEN)["ok"] and intake.in_folder("sub-descartados") == ["pablo.pdf"]
    patches = len(intake.patches)
    assert inbox.retire(key, TOKEN) == {"ok": True, "retire": inbox.RETIRE_DONE, "idempotent": True} and len(intake.patches) == patches
    assert [e["event"] for e in mf.get_audit(inbox.AREA, key)["events"]].count("INBOX_DISMISSED") == 1


def test_a_version_change_blocks_moving_or_deleting(world, monkeypatch):
    store, intake = world
    intake.add("p1", "pablo.pdf", PABLO)
    intake.add("l1", "lucia.pdf", LUCIA)
    analyze_all()
    keys = {n: _key(n) for n in ("pablo.pdf", "lucia.pdf")}
    stale = inbox.current_listing(TOKEN)
    intake.modify("p1", MARTIN_1)
    intake.modify("l1", MARTIN_2)
    monkeypatch.setattr(inbox, "current_listing", lambda token: stale)                                               # la persona decidió sobre la versión vieja
    intake.allow_delete = True
    moved = inbox.dismiss(keys["pablo.pdf"], TOKEN, actor=ACTOR, reason="OTHER")
    deleted = inbox.dismiss(keys["lucia.pdf"], TOKEN, actor=ACTOR, reason="OTHER", delete_original=True, confirm_delete=inbox.DELETE_CONFIRMATION)
    assert moved["retire"] == deleted["retire"] == inbox.RETIRE_BLOCKED and "cambió" in moved["warning"]
    assert intake.deletes == [] and sorted(intake.in_inbox()) == ["lucia.pdf", "pablo.pdf"]                              # ni movido ni eliminado


def test_an_incorporated_cv_is_never_discarded_nor_deleted_and_batch_acceptance_never_deletes(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    intake.allow_delete = True
    assert batch("cv1.pdf")["counts"]["done"] == 1 and intake.deletes == []
    key = next(k for k, e in mf.list_ledgers(inbox.AREA).items() if e["state"] == inbox.DONE)
    out = inbox.dismiss(key, TOKEN, actor=ACTOR, reason="OTHER", delete_original=True, confirm_delete=inbox.DELETE_CONFIRMATION)
    assert out["ok"] is False and intake.deletes == [] and len(store.real_files()) == 1                                   # ni el original ni el repositorio CVs se tocan


def test_reopen_after_moving_requires_the_file_back_in_the_intake(world):
    store, intake = world
    intake.add("p1", "pablo.pdf", PABLO)
    analyze_all()
    key = _key("pablo.pdf")
    inbox.dismiss(key, TOKEN, actor=ACTOR, reason="OTHER")
    refused = inbox.reopen(key, actor=ACTOR, token=TOKEN)
    assert refused["ok"] is False and "descartados" in refused["error"]
    intake.files["p1"]["parent"] = "inbox"                                                                              # Capital Humano lo devolvió a la carpeta desde SharePoint
    assert inbox.reopen(key, actor=ACTOR, token=TOKEN)["ok"] and rows()["pablo.pdf"]["status"] == inbox.REVIEW


def test_batch_discard_equals_discarding_each_one_and_deletion_still_needs_the_double_confirmation(world):
    store, intake = world
    intake.add("x1", "notas.txt", b"hola")
    intake.add("p1", "pablo.pdf", PABLO)
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    keys = [_key("notas.txt"), _key("pablo.pdf")]
    refused = inbox.dismiss_many(keys, TOKEN, actor=ACTOR, reason="OTHER", delete_original=True)
    assert refused["ok"] is False and intake.in_inbox() == ["cv1.pdf", "notas.txt", "pablo.pdf"]
    out = inbox.dismiss_many(keys, TOKEN, actor=ACTOR, reason="NOT_A_CV")
    assert out["counts"] == {"done": 2, "partial": 0, "pending": 0, "errors": 0}
    assert intake.in_folder("sub-descartados") == ["notas.txt", "pablo.pdf"] and intake.in_inbox() == ["cv1.pdf"] and intake.deletes == []
    again = inbox.dismiss_many(keys, TOKEN, actor=ACTOR, reason="NOT_A_CV")
    assert again["counts"]["errors"] == 2                                                                              # no se repite el descarte


# ═══════════════════ eliminar un CV incorporado (como «Eliminar» de Candidatos) ═══════════════════
def test_deleting_an_incorporated_cv_removes_the_candidate_its_cvs_and_the_original(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    assert batch("cv1.pdf")["counts"]["done"] == 1 and intake.in_folder("sub-incorporados") == ["cv1.pdf"]
    row = rows()["cv1.pdf"]
    key, pid = row["key"], mf.get_ledger(inbox.AREA, row["key"])["profile_id"]
    assert row["can_purge"] and len(docs_of(pid)) == 1
    intake.allow_delete = True
    refused = inbox.delete_incorporated(key, TOKEN, actor=ACTOR)                                          # sin la confirmación explícita no se toca nada
    assert not refused["ok"] and intake.deletes == [] and pid in banco() and len(docs_of(pid)) == 1
    out = inbox.delete_incorporated(key, TOKEN, actor=ACTOR, confirm="ELIMINAR")
    assert out["ok"] and out["removed"]["candidate"] == "DELETED" and out["removed"]["documents"] == 1
    assert (profile_repository.get(pid) or {}).get("deleted_at") and docs_of(pid) == []                    # como en Candidatos: baja del candidato y sus CVs fuera de «CVs»
    assert intake.deletes == ["a1"] and intake.in_folder("sub-incorporados") == []                        # y el original fuera de la bandeja
    assert "cv1.pdf" not in rows()                                                                         # no queda nada: sale de la vista
    entry = mf.get_ledger(inbox.AREA, key)
    assert entry["state"] == inbox.DONE and entry["retire"]["mode"] == inbox.MODE_DELETE and entry["removed"]["candidate"] == "DELETED"   # el libro conserva la auditoría
    assert inbox.delete_incorporated(key, TOKEN, actor=ACTOR, confirm="ELIMINAR")["ok"] and intake.deletes == ["a1"]                     # idempotente


def test_deleting_a_cv_linked_to_an_existing_candidate_removes_only_that_cv(world):
    store, intake = world
    lucia = next(p for p in banco().values() if p.get("email") == "lucia.gomez@mail.com")
    intake.add("l1", "lucia2.pdf", LUCIA)
    analyze_all()
    batch("lucia2.pdf")
    row = rows()["lucia2.pdf"]
    entry = mf.get_ledger(inbox.AREA, row["key"])
    assert entry["decision"]["op"] == "link" and entry["profile_id"] == lucia["profile_id"] and row["op"] == "link"
    intake.allow_delete = True
    out = inbox.delete_incorporated(row["key"], TOKEN, actor=ACTOR, confirm="ELIMINAR")
    assert out["ok"] and out["removed"]["candidate"] == "KEPT"
    assert not (profile_repository.get(lucia["profile_id"]) or {}).get("deleted_at") and docs_of(lucia["profile_id"]) == []   # la persona sigue en el Banco; solo sale este CV
    assert intake.deletes == ["l1"]


def test_an_original_left_in_the_intake_by_a_failed_retire_is_deleted_too(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    intake.deny_patch = True
    batch("cv1.pdf")
    row = rows()["cv1.pdf"]
    assert row["can_retire"] and row["can_purge"] and intake.in_inbox() == ["cv1.pdf"]
    intake.allow_delete = True
    assert inbox.delete_incorporated(row["key"], TOKEN, actor=ACTOR, confirm="ELIMINAR")["ok"] and intake.in_inbox() == []
    assert "cv1.pdf" not in rows()


def test_nothing_is_deleted_when_the_original_cannot_be_verified(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    batch("cv1.pdf")
    key = rows()["cv1.pdf"]["key"]
    pid = mf.get_ledger(inbox.AREA, key)["profile_id"]
    intake.allow_delete = True
    intake.modify("a1", ANA + b"cambio")                                                                 # el original ya no es el incorporado
    assert "no coincide con el CV incorporado" in inbox.delete_incorporated(key, TOKEN, actor=ACTOR, confirm="ELIMINAR")["error"]
    intake.modify("a1", ANA)
    intake.files["a1"]["parent"] = "historico"                                                           # fuera de carga-masiva: nunca se toca
    assert "fuera de esas carpetas" in inbox.delete_incorporated(key, TOKEN, actor=ACTOR, confirm="ELIMINAR")["error"]
    assert intake.deletes == [] and not (profile_repository.get(pid) or {}).get("deleted_at") and len(docs_of(pid)) == 1   # nada cambió


def test_a_permission_error_on_the_original_is_reported_and_a_retry_completes(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    batch("cv1.pdf")
    key = rows()["cv1.pdf"]["key"]
    intake.allow_delete, intake.deny_delete = True, True
    out = inbox.delete_incorporated(key, TOKEN, actor=ACTOR, confirm="ELIMINAR")
    assert not out["ok"] and out["stop"] == "PERMISSION_DENIED" and "no tiene permiso" in out["error"] and intake.in_folder("sub-incorporados") == ["cv1.pdf"]
    intake.deny_delete = False
    again = inbox.delete_incorporated(key, TOKEN, actor=ACTOR, confirm="ELIMINAR")                       # el candidato ya se dio de baja: el reintento solo completa el original
    assert again["ok"] and again["removed"]["candidate"] == "ALREADY_GONE" and intake.in_folder("sub-incorporados") == []


def test_an_unexpected_retire_failure_never_breaks_the_incorporation_response(world):
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    analyze_all()
    intake.fail_patch = [ValueError("respuesta inesperada de Graph")]                                    # un fallo fuera de la tabla común, DESPUÉS de la decisión durable
    out = batch("cv1.pdf")
    assert out["counts"]["done"] == 1
    row = rows()["cv1.pdf"]
    assert row["status"] == inbox.INCORPORATED and row["can_retire"] and "RETIRE_GRAPH" in row["reasons"]
    assert inbox.REASON_TEXT["RETIRE_GRAPH"].startswith("Falta retirar") and inbox.retire(row["key"], TOKEN)["ok"]


def test_a_local_transport_contract_error_is_not_reported_as_sharepoint_timeout(world):
    _,intake=world
    intake.add('local-error','cv1.pdf',ANA)
    analyze_all()
    intake.fail_patch=[TypeError('synthetic signature mismatch')]
    out=batch('cv1.pdf')
    assert out['counts']['done']==1
    row=rows()['cv1.pdf']
    assert 'RETIRE_LOCAL' in row['reasons'] and 'RETIRE_GRAPH' not in row['reasons']
    assert 'SharePoint' not in inbox.REASON_TEXT['RETIRE_LOCAL']


def test_an_incorporated_cv_whose_candidate_and_original_are_gone_leaves_the_view_but_keeps_its_record(world):
    """Residuo: el candidato se dio de baja en el Banco y el original ya no está en la carpeta → la fila sale de «Incorporados» (nada que mostrar ni hacer); el libro queda como auditoría.
    Con el original todavía en la carpeta, la fila se mantiene aunque el candidato no exista: hay un archivo que decidir."""
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    intake.add("p1", "pablo.pdf", PABLO)
    analyze_all()
    batch("cv1.pdf")
    view = rows()
    key = view["cv1.pdf"]["key"]
    entry = mf.get_ledger(inbox.AREA, key)
    assert intake.in_folder("sub-incorporados") == ["cv1.pdf"] and "cv1.pdf" in view                     # candidato vigente: sigue en «Incorporados»
    profile_repository.soft_delete(entry["profile_id"], actor=ACTOR)
    assert "cv1.pdf" not in rows() and "pablo.pdf" in rows()                                              # residual: sale de la vista; el resto no cambia
    assert mf.get_ledger(inbox.AREA, key)["state"] == inbox.DONE                                          # el registro queda como auditoría
    intake.files["a1"]["parent"] = "inbox"                                                                 # si el original vuelve a estar en la carpeta, hay algo que decidir
    assert "cv1.pdf" in rows()


def test_a_pending_retire_is_retried_automatically_a_bounded_number_of_times(world):
    """El retiro pendiente se reintenta SOLO (la vista lo marca `auto_retire`) hasta AUTO_RETIRE_MAX intentos en total; nunca si falta permiso (un reintento no lo resuelve)."""
    store, intake = world
    intake.add("a1", "cv1.pdf", ANA)
    intake.add("l1", "lucia2.pdf", LUCIA)
    analyze_all()
    intake.fail_patch = [urllib.error.URLError("sin respuesta")]                                          # SharePoint no confirmó a tiempo
    batch("cv1.pdf")
    row = rows()["cv1.pdf"]
    assert row["can_retire"] and row["auto_retire"] and "RETIRE_GRAPH" in row["reasons"]
    intake.fail_patch = [urllib.error.URLError("sin respuesta")] * 2
    inbox.retire(row["key"], TOKEN)
    inbox.retire(row["key"], TOKEN)
    row = rows()["cv1.pdf"]
    assert row["can_retire"] and not row["auto_retire"]                                                   # agotados los intentos: queda el botón manual
    assert inbox.retire(row["key"], TOKEN)["ok"]                                                          # el manual sigue disponible y concilia
    intake.deny_patch = True
    view = rows()
    batch("lucia2.pdf")
    lucia = rows()["lucia2.pdf"]
    assert lucia["can_retire"] and not lucia["auto_retire"] and "RETIRE_PERMISSION" in lucia["reasons"]   # sin permiso: no se reintenta solo


# ═══════════════════ identidad: nombre en el título, archivo en CamelCase, email que no coincide ═══════════════════
# Datos 100 % ficticios (dominio de prueba): misma ESTRUCTURA que un caso real, sin datos de ninguna persona.
TITLED = make_pdf(["MARIANA JOSEFINA QUIROZ", "Localidad Ficticia, Provincia de Prueba, Argentina", "+54 9 11 0000-0001", "mjq2001@prueba.danwor.com",
                   "PERFIL PROFESIONAL", "Estudiante de la Tecnicatura Superior en Análisis de Sistemas.", "PROYECTO ACADÉMICO"])
UNMATCHED_EMAIL = make_pdf(["Tomás Ferreyra", "tf1987@prueba.danwor.com", "Cel 11 0000 0002", BODY])


def test_section_headings_are_never_names_and_camelcase_filenames_are_split():
    from webapp.services import cv_extract as x
    assert x._name_from_segment("PROYECTO ACADÉMICO") is None and x._name_from_segment("Experiencia Laboral") is None
    assert x._filename_name("MarianaJQuiroz.CVa.pdf")["tokens"] == ["Mariana", "J.", "Quiroz"]                # la mayúscula suelta del CamelCase es una inicial, «CVa» es ruido
    assert x._filename_name("JuanPerez_CV.pdf")["tokens"] == ["Juan", "Perez"]
    assert x._filename_name("Martin Sosa 2024.pdf")["tokens"] == ["Martin", "Sosa"]                          # un nombre ya separado no cambia


def test_the_name_in_the_cv_title_is_proposed_when_it_agrees_with_the_filename(world):
    store, intake = world
    intake.add("t1", "MarianaJQuiroz.CVa.pdf", TITLED)
    analyze_all()
    row = rows()["MarianaJQuiroz.CVa.pdf"]
    assert row["fields"]["first_name"].startswith("Mariana") and row["fields"]["last_name"].endswith("Quiroz")
    assert row["status"] == inbox.READY and row["evidence"]["label"] == "Coincide con el nombre del archivo"
    assert row["fields"]["email"] == "mjq2001@prueba.danwor.com"


def test_an_email_that_does_not_match_the_name_does_not_send_the_cv_to_review(world):
    store, intake = world
    intake.add("u1", "cv-tomas.pdf", UNMATCHED_EMAIL)
    analyze_all()
    row = rows()["cv-tomas.pdf"]
    assert (row["fields"]["first_name"], row["fields"]["last_name"]) == ("Tomás", "Ferreyra")
    assert row["status"] == inbox.READY and "NAME_UNCORROBORATED" not in row["reasons"] and row["batch_ok"]  # el email se admite sin exigir que reproduzca el nombre
