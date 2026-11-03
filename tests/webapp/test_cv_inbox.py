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
PERFILES_URL = "https://danwor.sharepoint.com/:f:/s/Comunicaciones/IgPERFILESLINK?e=tok"
ACTOR = "cchh-inbox@danwor.com"
BODY = "Consultora con experiencia en migraciones de datos y analítica avanzada"

ANA = make_pdf(["Ana Torres", "ana.torres@mail.com", "Cel +54 9 11 5555 0001", BODY])
NONAME = make_pdf(["Experiencia profesional", BODY, "Disponibilidad inmediata"])
LUCIA = make_pdf(["Lucía Gómez", "lucia.gomez@mail.com", "Cel 11 6666 1111", BODY])
PABLO = make_pdf(["Pablo Ruiz", "pablo.ruiz@correo.com", "Cel 11 4444 2222", BODY])
MARTIN_1 = make_pdf(["Martín Sosa", "martin.sosa@mail.com", "Cel 11 7777 1234", BODY])
MARTIN_2 = make_pdf(["Martín Sosa", "martin.sosa@mail.com", "Cel 11 7777 1234", "Versión actualizada", BODY])


class Intake:
    """`Perfiles` (link fijo) → `carga-masiva` con archivos directos; `incorporados`/`descartados` se crean al retirar. Mueve con PATCH (If-Match); borra (If-Match) SOLO si el test lo habilita."""

    def __init__(self, has_inbox=True):
        self.has_inbox = has_inbox
        self.files: dict[str, dict] = {}
        self.folders = {"perfiles": ("Perfiles", None), "historico": ("Datasphere", "perfiles")}
        if has_inbox:
            self.folders["inbox"] = ("carga-masiva", "perfiles")
        self.downloads: list[str] = []
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
        return {"id": fid, "name": name, "folder": {}, "parentReference": {"id": parent, "driveId": DRIVE}, "webUrl": f"https://danwor.sharepoint.com/sites/Comunicaciones/{name}"}

    def get_json(self, url, token, **kw):
        assert token == TOKEN
        if "/shares/" in url:
            return {**self._folder("perfiles"), "parentReference": {"driveId": DRIVE, "siteId": "site-p"}}
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
        assert self.files.get(fid, {}).get("parent") == "inbox", "solo se elimina dentro de carga-masiva"
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
    for name, value in (("HC_SHAREPOINT_SITE_ID", "site-1"), ("HC_SHAREPOINT_DRIVE_ID", "drive-1"), ("HC_CV_ROOT_ITEM_ID", "root-1"), ("HC_PERFILES_FOLDER_URL", PERFILES_URL)):
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
    monkeypatch.setattr(microsoft365, "graph_delete", only_intake(intake.delete))
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
def test_the_intake_is_resolved_from_the_fixed_perfiles_location_and_a_missing_folder_is_reported(world, monkeypatch):
    store, intake = world
    found = inbox.resolve_inbox(TOKEN)
    assert found["id"] == "inbox" and found["drive_id"] == DRIVE and found["same_drive"] is False
    intake.folders.pop("inbox")
    with pytest.raises(inbox.InboxError) as err:
        inbox.resolve_inbox(TOKEN)
    assert err.value.code == "INBOX_MISSING" and "Perfiles/carga-masiva" in str(err.value)
    monkeypatch.setattr(config, "HC_PERFILES_FOLDER_URL", "")
    with pytest.raises(inbox.InboxError) as err:
        inbox.resolve_inbox(TOKEN)
    assert err.value.code == "CONFIG_INCOMPLETE"


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
