"""Importación histórica de CVs — incremento I3.2: ejecución del plan (perfiles + documentos), idempotencia ante cortes e inciertos, revalidación del Banco, excepciones y libro durable. Graph y SharePoint SIMULADOS (con
estado): ninguna escritura real sobre CCHH ni SharePoint."""
from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse

import pytest

import config
from core import microsoft365
from tests.webapp.test_cv_history_import import analyze_all, banco_bytes, start
from tests.webapp.test_cv_history_inventory import http_error
from tests.webapp.test_cv_history_pilot import OWNER, PII, TOKEN, home, qx, seed_profiles, sp, tree_bytes  # noqa: F401  (home y sp son fixtures)
from webapp.services import (cv_history_import as planning, cv_history_import_exec as ex, cv_history_manifest as mf, cv_history_pilot as pilot, profile_document_repository, profile_document_service as docsvc,
                             profile_repository)

CV_BASE = "/sites/site-1/drives/drive-1/items"


class CVStore:
    """Raíz de CVs simulada (carpetas por candidato y archivos) con inyección de fallos. Solo atiende URLs del site/drive de CVs; el origen histórico lo sigue atendiendo el `World` del piloto."""

    def __init__(self):
        self.folders: dict[str, str] = {}                         # id -> nombre
        self.files: dict[str, dict] = {}                          # id -> {folder, name, data}
        self.puts: list[str] = []
        self.deletes: list[str] = []
        self.posts: list[str] = []
        self.lost_ack: set[str] = set()                           # nombres de archivo (subcadena): el PUT SE GUARDA pero el cliente recibe un error de red
        self.deny_put = False
        self.deny_probe = False
        self.fail_delete = False
        self.fail_lookup: set[str] = set()
        self.n = 0

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}-{self.n}"

    def _decode(self, url):
        return urllib.parse.unquote(url.split(CV_BASE, 1)[1].lstrip("/"))

    def get_json(self, url, token, **kw):
        rest = self._decode(url)
        if rest.startswith("root-1:/"):
            name = rest.split(":/", 1)[1]
            for fid, fname in self.folders.items():
                if fname == name:
                    return {"id": fid, "name": fname, "folder": {}, "parentReference": {"id": "root-1"}}
            for fid, f in self.files.items():                      # archivo directamente en la raíz de CVs (p. ej. una sonda residual)
                if f["folder"] == "root-1" and f["name"] == name:
                    return self._item(fid)
            raise http_error(404)
        if ":/" in rest:                                           # archivo por ruta dentro de una carpeta
            folder, name = rest.split(":/", 1)
            if any(s in name for s in self.fail_lookup):
                raise http_error(500)
            for fid, f in self.files.items():
                if f["folder"] == folder and f["name"] == name:
                    return self._item(fid)
            raise http_error(404)
        fid = rest.split("?", 1)[0]
        if fid in self.folders:
            return {"id": fid, "name": self.folders[fid], "folder": {}, "parentReference": {"id": "root-1"}}
        raise http_error(404)

    def _item(self, fid):
        f = self.files[fid]
        return {"id": fid, "name": f["name"], "size": len(f["data"]), "webUrl": f"https://sp/{fid}", "file": {"hashes": {"quickXorHash": qx(f["data"])} if f.get("hashed", True) else {}}}

    def post_json(self, url, token, body):
        assert self._decode(url) == "root-1/children"
        self.posts.append(body["name"])
        if body["name"] in self.folders.values():
            raise http_error(409)
        fid = self._id("folder")
        self.folders[fid] = body["name"]
        return {"id": fid, "name": body["name"]}

    def put_bytes(self, url, token, data, content_type=None):
        rest = self._decode(url)
        folder, name = rest[: -len(":/content")].split(":/", 1) if rest.endswith(":/content") else (None, None)
        self.puts.append(name)
        if name.startswith("__dw_write_probe__"):
            if self.deny_probe:
                raise http_error(403)
        elif self.deny_put:
            raise http_error(403)
        existing = next((fid for fid, f in self.files.items() if f["folder"] == folder and f["name"] == name), None)
        fid = existing or self._id("file")
        self.files[fid] = {"folder": folder, "name": name, "data": bytes(data)}
        if any(s in name for s in self.lost_ack):
            raise urllib.error.URLError("conexión perdida tras guardar")
        return self._item(fid)

    def get_bytes(self, url, token, **kw):
        fid = self._decode(url).rsplit("/content", 1)[0]
        if fid not in self.files:
            raise http_error(404)
        return self.files[fid]["data"]

    def delete(self, url, token):
        fid = self._decode(url)
        self.deletes.append(fid)
        if self.fail_delete:
            raise http_error(500)
        self.files.pop(fid, None)

    def names(self, folder_name):
        fid = next((i for i, n in self.folders.items() if n == folder_name), None)
        return sorted(f["name"] for f in self.files.values() if f["folder"] == fid)

    def real_files(self):
        return {fid: f for fid, f in self.files.items() if not f["name"].startswith("__dw_write_probe__")}


@pytest.fixture()
def env(sp, monkeypatch):
    """Origen histórico (World del piloto) + raíz de CVs simulada, con el Banco de prueba sembrado."""
    for name, value in (("HC_SHAREPOINT_SITE_ID", "site-1"), ("HC_SHAREPOINT_DRIVE_ID", "drive-1"), ("HC_CV_ROOT_ITEM_ID", "root-1"), ("HC_DOCUMENT_WRITE_SCOPE", "https://danwor.sharepoint.com/Sites.Selected")):
        monkeypatch.setattr(config, name, value)
    store = CVStore()
    world = sp()
    source_json, source_bytes = microsoft365.graph_get_json, microsoft365.graph_get_bytes
    monkeypatch.setattr(microsoft365, "graph_get_json", lambda url, token, **kw: store.get_json(url, token, **kw) if CV_BASE in url else source_json(url, token, **kw))
    monkeypatch.setattr(microsoft365, "graph_get_bytes", lambda url, token, **kw: store.get_bytes(url, token, **kw) if CV_BASE in url else source_bytes(url, token, **kw))

    def guarded(fn):
        def call(url, *a, **k):
            assert CV_BASE in url, f"escritura fuera de la carpeta administrada de CVs: {url}"
            return fn(url, *a, **k)
        return call
    monkeypatch.setattr(microsoft365, "graph_post_json", guarded(store.post_json))
    monkeypatch.setattr(microsoft365, "graph_put_bytes", guarded(store.put_bytes))
    monkeypatch.setattr(microsoft365, "graph_delete", guarded(store.delete))
    seed_profiles()
    return store, world


def planned_run(sp_env, **kw):
    rid = start(**kw)["run_id"]
    analyze_all(rid)
    planning.generate_plan(rid, actor=OWNER)
    return rid


def begin(rid, **kw):
    return ex.start_execution(rid, TOKEN, actor=OWNER, confirm=ex.confirm_phrase(rid), expected=ex.plan_scope(mf.get_plan(rid)), **kw)


def probe_files(store):
    return [n for n in store.puts if n.startswith("__dw_write_probe__")]


def run_all(rid, **kw):
    out = []
    for _ in range(30):
        r = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None, **kw)
        out.append(r)
        if not r["remaining"] or r["stopped_reason"] not in (None, "BATCH_LIMIT", "TIME_BUDGET"):
            break
    return out


def banco_profiles():
    return {p["profile_id"]: p for p in profile_repository.list()}


def seeded_snapshot():
    return {pid: json.dumps(p, sort_keys=True) for pid, p in banco_profiles().items()}


def by_name(rid):
    return {g["representative"]["name"]: g["content_key"] for g in mf.list_groups(rid)}


def ledger_of(rid, name):
    return mf.get_ledger(rid, by_name(rid)[name])


# ═══════════════════ inicio: confirmación y permiso de escritura ═══════════════════
def test_nothing_is_written_until_the_owner_confirms_and_the_write_permission_is_verified(env):
    store, world = env
    rid = planned_run(env)
    before = seeded_snapshot()
    for bad in ("", "IMPORTAR", ex.confirm_phrase(rid).lower(), "IMPORTAR-ch-otro"):
        with pytest.raises(ex.ExecutionError, match="confirmación"):
            ex.start_execution(rid, TOKEN, actor=OWNER, confirm=bad)
    assert store.puts == [] and store.posts == [] and "execution" not in mf.get_run(rid) and seeded_snapshot() == before
    with pytest.raises(ex.ExecutionError):
        ex.execute_batch(rid, TOKEN, actor=OWNER)                                                                        # sin inicio no hay ejecución
    store.deny_probe = True
    with pytest.raises(ex.ExecutionError, match="Sin permiso de escritura"):
        begin(rid)
    assert "execution" not in mf.get_run(rid) and seeded_snapshot() == before and store.real_files() == {}                 # sin permiso: no se escribió nada
    store.deny_probe = False
    run = begin(rid)
    assert run["execution"]["state"] == "RUNNING" and run["execution"]["started_by"] == OWNER and run["execution"]["write_check"]["ok"] and run["execution"]["write_check"]["cleaned"]
    probes = probe_files(store)
    assert len(probes) == 2 and len(set(probes)) == 2 and all(n.startswith(f"__dw_write_probe__{rid}-") for n in probes)       # un nombre ÚNICO por ejecución (el intento denegado y el exitoso no comparten archivo)
    assert len(store.deletes) == 1 and store.real_files() == {} and seeded_snapshot() == before and not any(f["name"].startswith("__dw_write_probe__") for f in store.files.values())        # la sonda se borró
    assert begin(rid)["execution"]["started_at"] == run["execution"]["started_at"] and len(store.puts) == 2                 # iniciar de nuevo no repite nada


def test_the_plan_is_frozen_once_the_execution_starts_and_requires_a_complete_analysis(env):
    rid = start()["run_id"]
    with pytest.raises(ex.ExecutionError):
        ex.start_execution(rid, TOKEN, actor=OWNER, confirm=ex.confirm_phrase(rid))                                      # sin plan
    analyze_all(rid)
    planning.generate_plan(rid, actor=OWNER)
    begin(rid)
    with pytest.raises(pilot.PilotInputError, match="congelado"):
        planning.generate_plan(rid, actor=OWNER)
    pilot_run = pilot.start_run(TOKEN, "https://danwor.sharepoint.com/:f:/s/Comunicaciones/IgSECRETSHARELINK?e=tokenlike", sample_size=7, authorization=pilot.AUTHORIZATION_PHRASE, actor=OWNER, min_sample=1, sleep=lambda s: None)
    with pytest.raises(ex.ExecutionError):
        ex.start_execution(pilot_run["run_id"], TOKEN, actor=OWNER, confirm=ex.confirm_phrase(pilot_run["run_id"]))


# ═══════════════════ ejecución: perfiles y documentos ═══════════════════
def test_execution_creates_profiles_links_uploads_documents_and_records_the_durable_ledger(env):
    store, world = env
    rid = planned_run(env)
    existing_before = seeded_snapshot()
    begin(rid)
    results = run_all(rid)
    assert results[-1]["remaining"] == 0 and results[-1]["stopped_reason"] is None and mf.get_run(rid)["execution"]["state"] == "DONE"
    now = banco_profiles()
    created = [p for pid, p in now.items() if pid not in existing_before]
    assert len(created) == 2 and all(p["first_name"].strip() and p["last_name"].strip() for p in created)                  # el contrato nombre + apellido se respeta
    assert {pid: json.dumps(p, sort_keys=True) for pid, p in now.items() if pid in existing_before} == existing_before      # los perfiles existentes NO cambian (ni el vinculado)
    juan = next(p for p in created if p["first_name"] == "Juan")
    assert juan["tags"] == "Histórico SharePoint, Revisar identidad" and sorted(juan["profile_skills"].split(", ")) == ["Databricks", "Datasphere"] and juan["email"] == "juan.perez@gmail.com"
    assert juan["import_origin"]["run_id"] == rid and juan["import_origin"]["warnings"] == [ex.cv_extract.NAME_SPLIT_AMBIGUOUS] and juan["import_origin"]["review_identity"] is True
    maria = next(p for p in created if p["first_name"] == "María")
    assert maria["tags"] == "Histórico SharePoint" and maria["import_origin"]["review_identity"] is False and maria["import_origin"]["warnings"] == []
    docs = [d for p in now for d in profile_document_repository.list_for_profile(p)]
    assert len(docs) == 4 and len(store.real_files()) == 4                                                                # Juan (2 versiones), María y Lucía (perfil existente)
    juan_docs = profile_document_repository.list_for_profile(juan["profile_id"])
    assert len(juan_docs) == 2 and len({d["document_id"] for d in juan_docs}) == 2 and all(d["import_origin"]["run_id"] == rid and d["import_origin"]["sha256"] for d in juan_docs)
    assert all(re.match(r"^[0-9a-f-]{36}__", f["name"]) for f in store.real_files().values()) and any(n.startswith("Pérez Gómez, Juan [") for n in store.folders.values())
    plan = mf.get_plan(rid)
    for key, d in plan["decisions"].items():
        if d["action"] in (planning.CREATE, planning.ATTACH, planning.LINK):
            led = mf.get_ledger(rid, key)
            assert led["state"] == ex.DONE and led["profile_id"] and led["document_id"] == ex.document_id_for(led["profile_id"], key) and led["item_id"] and led["sha256"]
            assert profile_document_repository.get(led["document_id"])["item_id"] == led["item_id"]                        # content_key → profile_id → document_id → archivo
    lucia = mf.get_ledger(rid, by_name(rid)["lucia.pdf"])
    assert lucia["action"] == planning.LINK and lucia["profile_id"] in existing_before and not lucia["profile_created"]
    assert not [d for d in store.deletes if not d.startswith("file-")]


def test_the_source_is_only_read_and_every_write_goes_to_the_managed_cv_root(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    run_all(rid)
    assert {n for n in store.puts if not n.startswith("__dw_write_probe__")} == {f["name"] for f in store.real_files().values()}
    assert all(item in world.data for item in world.downloads)                                                           # solo se descargó (GET) del origen; las escrituras del fixture exigen la raíz de CVs


def test_execution_is_idempotent_and_resumable_in_small_batches(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    first = ex.execute_batch(rid, TOKEN, actor=OWNER, max_units=1, sleep=lambda s: None)
    assert first == {**first, "processed": 1, "stopped_reason": "BATCH_LIMIT", "remaining": 3}
    run_all(rid, max_units=1)
    profiles, files, puts = set(banco_profiles()), dict(store.files), list(store.puts)
    snapshot = {k: json.dumps(v, sort_keys=True) for k, v in mf.list_ledgers(rid).items()}
    again = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
    assert again["processed"] == 0 and again["remaining"] == 0 and set(banco_profiles()) == profiles and store.files == files and store.puts == puts       # nada se repite
    assert {k: json.dumps(v, sort_keys=True) for k, v in mf.list_ledgers(rid).items()} == snapshot


def test_a_batch_that_stops_midway_never_duplicates_profiles_or_documents(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    ex.execute_batch(rid, TOKEN, actor=OWNER, max_units=2, sleep=lambda s: None)
    run_all(rid)
    created = [p for p in banco_profiles().values() if p.get("import_origin")]
    assert len(created) == 2 and len(store.real_files()) == 4 and len(store.puts) == 5                                   # 4 archivos + la sonda, una vez cada uno


# ═══════════════════ subidas inciertas: conciliación con el estado REAL ═══════════════════
def test_a_lost_acknowledgement_is_reconciled_against_sharepoint_and_never_uploaded_twice(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    store.lost_ack = {"Maria-Fernandez"}
    first = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
    led = ledger_of(rid, "Maria Fernandez.docx")
    assert led["state"] == ex.UPLOADING and led["attempts"] == 1 and led["last_error"] and profile_document_repository.get(led["document_id"]) is None        # subido pero sin confirmar ni registrar
    puts_for_file = [n for n in store.puts if "Maria-Fernandez" in n]
    assert len(puts_for_file) == 2 and len([f for f in store.real_files().values() if "Maria-Fernandez" in f["name"]]) == 1                # el único reintento del servicio apuntó al MISMO archivo
    store.lost_ack = set()
    run_all(rid)
    led = ledger_of(rid, "Maria Fernandez.docx")
    assert led["state"] == ex.DONE and led.get("reconciled") is True and profile_document_repository.get(led["document_id"])["item_id"] == led["item_id"]
    assert len([n for n in store.puts if "Maria-Fernandez" in n]) == 2 and len([f for f in store.real_files().values() if "Maria-Fernandez" in f["name"]]) == 1   # sin PUT nuevo: se conciliró con lo remoto
    assert first["retry"] == 1


def test_the_metadata_already_persisted_wins_over_any_remote_lookup(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    run_all(rid)
    key = by_name(rid)["Maria Fernandez.docx"]
    led = mf.get_ledger(rid, key)
    mf.put_ledger(rid, key, {**led, "state": ex.UPLOADING, "attempts": 1})                                                # se perdió el estado final del libro, no el documento
    store.fail_lookup = {"Maria"}                                                                                        # ni siquiera se consulta SharePoint
    puts = list(store.puts)
    run_all(rid)
    assert mf.get_ledger(rid, key)["state"] == ex.DONE and store.puts == puts


def test_a_remote_file_with_different_content_is_never_overwritten_and_never_adopted(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    key = by_name(rid)["Maria Fernandez.docx"]
    plan = mf.get_plan(rid)
    pid = ex.profile_id_for(rid, plan["decisions"][key]["cluster"])
    ex_group = mf.get_group(rid, key)
    doc_id = ex.document_id_for(pid, key)
    store.folders["folder-pre"] = "Fernández, María [" + docsvc.folder_marker(pid) + "]"
    store.files["file-pre"] = {"folder": "folder-pre", "name": docsvc.build_remote_filename(doc_id, ex_group["representative"]["name"]), "data": b"otro contenido"}
    run_all(rid)
    led = mf.get_ledger(rid, key)
    assert led["state"] == ex.FAILED and led["reason"] == "REMOTE_CONTENT_MISMATCH" and store.files["file-pre"]["data"] == b"otro contenido"
    assert profile_document_repository.get(doc_id) is None and not [n for n in store.puts if "Maria-Fernandez" in n]


def test_remote_verification_uses_the_comparable_hash_or_the_content_never_name_and_size_alone(env):
    store, world = env
    group = {"size": 5, "sha256": hashlib.sha256(b"hello").hexdigest(), "hash": {"algo": "quickXorHash", "value": "H-SAME"}}
    same = {"id": "f1", "size": 5, "file": {"hashes": {"quickXorHash": "H-SAME"}}}
    assert ex._verify_remote(same, group, TOKEN) is True
    other = {"id": "f1", "size": 5, "file": {"hashes": {"quickXorHash": "H-OTHER"}}}
    assert ex._verify_remote(other, group, TOKEN) is False                                                               # mismo nombre y tamaño, otro contenido
    assert ex._verify_remote({"id": "f1", "size": 6, "file": {"hashes": {"quickXorHash": "H-SAME"}}}, group, TOKEN) is False
    no_hash = {"id": "f2", "size": 5, "file": {"hashes": {}}}                                                            # sin hash comparable: se descarga y se compara el sha256
    store.files["f2"] = {"folder": "x", "name": "n", "data": b"hello", "hashed": False}
    assert ex._verify_remote(no_hash, {**group, "hash": None}, TOKEN) is True
    store.files["f2"]["data"] = b"hellp"
    assert ex._verify_remote(no_hash, {**group, "hash": None}, TOKEN) is False


# ═══════════════════ permiso, token y persistencia ═══════════════════
def test_a_permission_denied_stops_the_run_and_it_resumes_without_duplicating_profiles(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    store.deny_put = True
    first = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
    assert first["stopped_reason"] == "PERMISSION_DENIED" and mf.get_run(rid)["execution"]["state"] == "PERMISSION_DENIED" and store.real_files() == {}
    created = {pid for pid, p in banco_profiles().items() if p.get("import_origin")}
    assert len(created) == 1                                                                                             # el alta del primer contenido quedó hecha; el documento, pendiente
    still = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
    assert still["stopped_reason"] == "PERMISSION_DENIED" and still["processed"] == 0
    store.deny_put = False
    run_all(rid)
    assert mf.get_run(rid)["execution"]["state"] == "DONE" and len(store.real_files()) == 4 and len({pid for pid, p in banco_profiles().items() if p.get("import_origin")}) == 2


def test_an_expired_token_stops_without_consuming_attempts_and_resumes(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    first_key = next(k for k, d in mf.get_plan(rid)["decisions"].items() if d["action"] == planning.CREATE)
    item = mf.get_group(rid, first_key)["representative"]["item_id"]
    world.dl_errors[item] = [http_error(401)]
    first = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
    assert first["stopped_reason"] == "TOKEN_EXPIRED" and mf.get_ledger(rid, first_key)["attempts"] == 0 and mf.get_ledger(rid, first_key)["state"] != ex.FAILED
    run_all(rid)
    assert mf.get_run(rid)["execution"]["state"] == "DONE" and mf.get_ledger(rid, first_key)["state"] == ex.DONE


def test_repeated_transient_errors_stop_the_batch_and_exhaust_the_retries_into_a_visible_failure(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    store.fail_lookup = {"__"}                                                                                           # SharePoint responde 500 al conciliar: error reintentable
    r1 = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
    assert r1["stopped_reason"] == "ERRORS_STREAK" and r1["retry"] == 3 and r1["failed"] == 0
    for _ in range(6):                                                                                                   # cada tanda corta tras 3 errores seguidos: 4 contenidos × 3 intentos
        ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
    reasons = {e.get("reason") for e in mf.list_ledgers(rid).values() if e["state"] == ex.FAILED}
    assert reasons == {"RETRIES_EXHAUSTED"} and store.real_files() == {}
    report = ex.execution_report(rid)
    assert report["failed_by_reason"] == {"RETRIES_EXHAUSTED": 3} and report["blocked_by_reason"] == {"LEADER_NOT_IMPORTED": 1}      # el adjunto no se intenta si su perfil no se importó
    assert report["exceptions_open"] == 4 + 2 and report["states"]["DONE"] == 0                                           # 4 contenidos del plan automático + los 2 pendientes del plan


# ═══════════════════ revalidación del Banco al ejecutar ═══════════════════
def create_in_banco(first, last, email=None, **extra):
    return profile_repository.create({"first_name": first, "last_name": last, "email": email, **extra})


def test_a_profile_that_appears_after_the_plan_with_the_same_email_blocks_the_creation_without_touching_it(env):
    store, world = env
    rid = planned_run(env)
    intruder = create_in_banco("Otra", "Persona", "maria.fernandez@empresa.com.ar")
    snapshot = json.dumps(profile_repository.get(intruder["profile_id"]), sort_keys=True)
    begin(rid)
    run_all(rid)
    led = ledger_of(rid, "Maria Fernandez.docx")
    assert led["state"] == ex.BLOCKED and led["reason"] == "EMAIL_NOW_EXISTS" and profile_repository.get(ex.profile_id_for(rid, led["cluster"])) is None
    assert json.dumps(profile_repository.get(intruder["profile_id"]), sort_keys=True) == snapshot and not [f for f in store.real_files().values() if "Maria" in f["name"]]
    assert ex.execution_report(rid)["blocked_by_reason"] == {"EMAIL_NOW_EXISTS": 1}


def test_a_blocked_leader_blocks_its_attachments_and_a_name_duplicate_that_appeared_blocks_too(env):
    store, world = env
    rid = planned_run(env)
    create_in_banco("Juan", "Pérez Gómez", "otro.mail@old.com")                                                        # mismo nombre, otro email: posible duplicado por nombre
    begin(rid)
    run_all(rid)
    maria_key = by_name(rid)["Maria Fernandez.docx"]
    juan = [led for k, led in mf.list_ledgers(rid).items() if led["action"] in (planning.CREATE, planning.ATTACH) and k != maria_key]
    assert sorted(led["reason"] for led in juan) == ["LEADER_NOT_IMPORTED", "POSSIBLE_DUPLICATE_NOW"] and all(led["state"] == ex.BLOCKED for led in juan)
    assert [p for p in banco_profiles().values() if p.get("first_name") == "Juan"] and len([p for p in banco_profiles().values() if p.get("first_name") == "Juan"]) == 1


def test_a_link_target_that_was_deleted_after_the_plan_blocks_the_link(env):
    store, world = env
    rid = planned_run(env)
    key = by_name(rid)["lucia.pdf"]
    target = mf.get_plan(rid)["decisions"][key]["link_profile_id"]
    profile_repository.soft_delete(target, actor="x")
    begin(rid)
    run_all(rid)
    assert mf.get_ledger(rid, key)["state"] == ex.BLOCKED and mf.get_ledger(rid, key)["reason"] == "LINK_TARGET_MISSING" and not [f for f in store.real_files().values() if "lucia" in f["name"]]


# ═══════════════════ excepciones ═══════════════════
def test_the_exceptions_overview_lists_pending_and_blocked_items_with_reasons_and_no_personal_data(env):
    store, world = env
    rid = planned_run(env)
    create_in_banco("Otra", "Persona", "maria.fernandez@empresa.com.ar")
    begin(rid)
    run_all(rid)
    over = ex.exceptions_overview(rid)
    assert over["total"] == 3 and over["by_reason"] == {"EMAIL_NOW_EXISTS": 1, planning.MULTIPLE_EMAILS: 1, planning.READ_FAILURE: 1} and over["counts"] == {"open": 2, "blocked": 1}
    assert {r["status"] for r in over["rows"]} == {"open", "blocked"} and ex.exceptions_overview(rid, reason=planning.MULTIPLE_EMAILS)["total"] == 1
    assert ex.exceptions_overview(rid, status="resolved")["total"] == 0 and ex.exceptions_overview(rid, status="all")["total"] == 3
    assert not [p for p in PII if p in json.dumps(over, ensure_ascii=False)]
    details = ex.exception_details(rid, over["rows"])
    pablo = next(d for d in details if d["plan_reasons"][:1] == [planning.MULTIPLE_EMAILS])
    assert pablo["filename"] in ("pablo.pdf", "cv.pdf") and pablo["alternatives"] and pablo["available"] and pablo["emails"] == ["pablo.ruiz@a.com", "pruiz@b.com"] and pablo["email"] is None
    assert [s["name"] for s in pablo["suggestions"]] == ["Pablo Ruiz"] and pablo["suggestions"][0]["why"] in ("NAME_EXACT", "NAME_TOKENS_EQUAL")           # el perfil «Pablo Ruiz» del Banco aparece como sugerencia


def test_complete_identity_creates_a_real_profile_after_the_duplicate_advisory_and_uploads_the_cv(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    run_all(rid)
    pablo_key = next(k for k, d in mf.get_plan(rid)["decisions"].items() if d["reasons"][:1] == [planning.MULTIPLE_EMAILS])
    before = set(banco_profiles())
    for bad in ({"first_name": "", "last_name": "Ruiz"}, {"first_name": "Pablo", "last_name": ""}):
        assert ex.complete_identity(rid, pablo_key, TOKEN, actor=OWNER, **bad)["ok"] is False
    assert set(banco_profiles()) == before                                                                               # nunca un perfil sin nombre y apellido
    advisory = ex.complete_identity(rid, pablo_key, TOKEN, actor=OWNER, first_name="Pablo", last_name="Ruiz")
    assert advisory["ok"] is False and advisory["advisory"]["name"] == "Pablo Ruiz"                                          # ya existe «Pablo Ruiz» en el Banco de prueba: aviso, sin crear nada
    assert set(banco_profiles()) == before and mf.get_ledger(rid, pablo_key) is None
    done = ex.complete_identity(rid, pablo_key, TOKEN, actor=OWNER, first_name="Pablo", last_name="Ruiz", email="pablo.ruiz@a.com", duplicate_confirmed=True)
    assert done["ok"] and done["outcome"] == ex.DONE
    created = banco_profiles()[done["profile_id"]]
    assert (created["first_name"], created["last_name"], created["tags"]) == ("Pablo", "Ruiz", "Histórico SharePoint") and created["import_origin"]["manual"] is True and created["profile_skills"]
    led = mf.get_ledger(rid, pablo_key)
    assert led["state"] == ex.DONE and led["action"] == ex.MANUAL_CREATE and led["source"] == "manual" and profile_document_repository.get(led["document_id"])["profile_id"] == done["profile_id"]
    with pytest.raises(ex.ExecutionError, match="resuelto"):                                                             # un contenido resuelto no se vuelve a resolver (ni se duplica el perfil)
        ex.complete_identity(rid, pablo_key, TOKEN, actor=OWNER, first_name="Pablo", last_name="Ruiz", email="pablo.ruiz@a.com", duplicate_confirmed=True)
    assert len(banco_profiles()) == len(before) + 1


def test_link_to_an_existing_profile_adds_the_cv_without_modifying_the_profile(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    run_all(rid)
    scanned = by_name(rid)["escaneado.pdf"]
    zoe = next(p for p in profile_repository.list() if p["first_name"] == "Zoe")
    snapshot = seeded_snapshot()
    assert ex.link_existing(rid, scanned, TOKEN, actor=OWNER, profile_id="no-existe")["ok"] is False
    res = ex.link_existing(rid, scanned, TOKEN, actor=OWNER, profile_id=zoe["profile_id"])
    assert res["ok"] and res["outcome"] == ex.DONE and seeded_snapshot() == snapshot
    assert [d["filename"] for d in profile_document_repository.list_for_profile(zoe["profile_id"])] == ["escaneado.pdf"]
    assert mf.get_ledger(rid, scanned)["action"] == ex.MANUAL_LINK and ex.exceptions_overview(rid)["by_reason"].get(planning.READ_FAILURE) is None


def test_dismiss_reopen_and_retry(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    run_all(rid)
    key = by_name(rid)["escaneado.pdf"]
    assert ex.dismiss(rid, key, actor=OWNER, reason="NADA")["ok"] is False
    assert ex.dismiss(rid, key, actor=OWNER, reason="NOT_A_CV")["ok"] and mf.get_ledger(rid, key)["state"] == ex.DISMISSED
    assert ex.exceptions_overview(rid)["total"] == 1 and ex.exceptions_overview(rid, status="dismissed")["total"] == 1
    assert ex.reopen(rid, key, actor=OWNER)["ok"] and ex.exceptions_overview(rid)["total"] == 2
    assert ex.reopen(rid, key, actor=OWNER)["ok"] is False
    blocked_key = by_name(rid)["Maria Fernandez.docx"]
    assert ex.retry(rid, blocked_key, actor=OWNER)["ok"] is False                                                        # solo se reintenta lo bloqueado o con error
    mf.put_ledger(rid, blocked_key, {**mf.get_ledger(rid, blocked_key), "state": ex.FAILED, "reason": "X"})
    assert ex.retry(rid, blocked_key, actor=OWNER)["ok"] and mf.get_ledger(rid, blocked_key)["state"] == ex.PLANNED
    assert any(h["event"] == "RETRY" for h in mf.get_ledger(rid, blocked_key)["history"])


def test_manual_actions_require_the_execution_and_do_not_apply_to_automatic_items(env):
    store, world = env
    rid = planned_run(env)
    pablo = next(k for k, d in mf.get_plan(rid)["decisions"].items() if d["reasons"][:1] == [planning.MULTIPLE_EMAILS])
    with pytest.raises(ex.ExecutionError, match="todavía no comenzó"):
        ex.complete_identity(rid, pablo, TOKEN, actor=OWNER, first_name="Pablo", last_name="Ruiz")
    begin(rid)
    with pytest.raises(ex.ExecutionError, match="plan automático"):
        ex.dismiss(rid, by_name(rid)["Maria Fernandez.docx"], actor=OWNER, reason="NOT_A_CV")                            # lo automático no se descarta a mano mientras no esté bloqueado


# ═══════════════════ libro durable, reporte y descarte ═══════════════════
def test_the_ledger_and_report_carry_no_personal_data_and_survive_the_discard_which_waits_for_the_execution(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    ex.execute_batch(rid, TOKEN, actor=OWNER, max_units=1, sleep=lambda s: None)
    with pytest.raises(pilot.PilotInputError, match="3 contenidos sin terminar y 2 excepciones abiertas"):
        ex.assert_can_discard(rid)
    run_all(rid)
    with pytest.raises(pilot.PilotInputError, match="0 contenidos sin terminar y 2 excepciones abiertas"):                 # las excepciones abiertas también impiden el descarte normal
        ex.assert_can_discard(rid)
    assert ex.discard_blockers(rid) == {"remaining": 0, "exceptions_open": 2}
    for key, d in mf.get_plan(rid)["decisions"].items():
        if d["action"] == planning.PENDING:
            assert ex.dismiss(rid, key, actor=OWNER, reason="NOT_A_CV")["ok"]                                              # se resuelven desde la interfaz (aquí, descartando)
    assert ex.discard_blockers(rid) is None
    ex.assert_can_discard(rid)
    ledger_dir = config.BASE_DIR / "human-capital" / "import-history" / rid / "ledger"
    text = "".join(p.read_text(encoding="utf-8") for p in ledger_dir.glob("*.json"))
    assert text and not [p for p in PII if p in text] and "@" not in text.replace(OWNER, "")
    before = ex.execution_report(rid)
    pilot.discard_staging(rid, confirm=pilot.discard_phrase(rid), actor=OWNER)
    assert mf.staging_files_present(rid) == 0 and len(list(ledger_dir.glob("*.json"))) == 6                                # el libro (sin datos personales) sobrevive: 4 importados + 2 descartados
    assert ex.execution_report(rid) == before
    assert not [p for p in PII if p in json.dumps(before, ensure_ascii=False)]
    assert before["documents_uploaded"] == 4 and before["profiles_created"] == 2 and before["profiles_with_warnings"] == 1 and before["linked_documents"] == 1 and before["remaining"] == 0
    assert before["write_check"]["ok"] and before["states"]["DONE"] == 4 and before["exceptions_open"] == 0 and before["dismissed"] == 2


def test_deterministic_identities_do_not_depend_on_the_order_or_the_run_state(env):
    rid = "ch-20261024T000000Z-abcd"
    assert ex.profile_id_for(rid, "c0001") == ex.profile_id_for(rid, "c0001") != ex.profile_id_for(rid, "c0002")
    pid = ex.profile_id_for(rid, "c0001")
    assert ex.document_id_for(pid, "a" * 16) != ex.document_id_for(pid, "b" * 16) and ex.manual_profile_id(rid, "a" * 16) != pid


# ═══════════════════ servicio de documentos: extensiones opcionales y conciliación ═══════════════════
def test_upload_document_keeps_its_contract_and_accepts_a_deterministic_id(env):
    store, world = env
    profile = create_in_banco("Ana", "Prueba", "ana.prueba@x.com")
    data = world.data["d2"]
    doc = docsvc.upload_document(profile_id=profile["profile_id"], filename="Maria Fernandez.docx", data=data, access_token=TOKEN, uploaded_by=OWNER, first_name="Ana", last_name="Prueba")
    assert re.match(r"^[0-9a-f-]{36}$", doc["document_id"]) and "import_origin" not in doc                              # sin parámetros opcionales: el comportamiento de siempre
    fixed = "11111111-1111-4111-8111-111111111111"
    doc2 = docsvc.upload_document(profile_id=profile["profile_id"], filename="Maria Fernandez.docx", data=data, access_token=TOKEN, uploaded_by=OWNER, first_name="Ana", last_name="Prueba", document_id=fixed, extra_fields={"import_origin": {"k": 1}})
    assert doc2["document_id"] == fixed and doc2["import_origin"] == {"k": 1} and any(f["name"].startswith(fixed + "__") for f in store.files.values())


@pytest.mark.parametrize("code, error", [(403, docsvc.ProfileDocumentPermissionDeniedError), (500, docsvc.ProfileDocumentGraphSourceError)])
def test_remote_lookup_classifies_failures_and_never_reads_an_error_as_absent(env, code, error):
    store, world = env
    store.folders["f1"] = "x"
    original = store.get_json

    def boom(url, token, **kw):
        raise http_error(code)
    store.get_json = boom
    with pytest.raises(error):
        docsvc.find_remote_document("f1", "n.pdf", TOKEN)
    store.get_json = original
    assert docsvc.find_remote_document("f1", "n.pdf", TOKEN) is None


def test_the_write_probe_validates_the_effective_permission_and_cleans_up(env):
    store, world = env
    first = docsvc.probe_write_access(TOKEN, "lote-1")
    second = docsvc.probe_write_access(TOKEN, "lote-1")
    assert first["ok"] and first["cleaned"] and store.files == {} and first["file"] != second["file"] and first["file"].startswith("__dw_write_probe__lote-1-")      # nombre único por ejecución
    store.deny_probe = True
    with pytest.raises(docsvc.ProfileDocumentPermissionDeniedError):
        docsvc.probe_write_access(TOKEN, "lote-1")

# ═══════════════════ cortes entre el alta del perfil y el libro; almacenamiento ═══════════════════
def test_a_profile_created_before_a_crash_is_recognized_on_resume_and_never_duplicated(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    key = next(k for k, d in mf.get_plan(rid)["decisions"].items() if d["action"] == planning.CREATE and k == by_name(rid)["Maria Fernandez.docx"])
    cluster = mf.get_plan(rid)["decisions"][key]["cluster"]
    pid = ex.profile_id_for(rid, cluster)
    group = mf.get_group(rid, key)
    profile_repository.create_with_explicit_id(pid, {"first_name": "María", "last_name": "Fernández", "email": group["extraction"]["email"], "phone": None, "linkedin_url": None, "location": None, "profile_skills": "Datasphere",
                                                      "seniority": None, "source": None, "tags": "Histórico SharePoint", "notes": None, "potential_rating": None, "potential_mark": None, "import_origin": {"run_id": rid}})
    run_all(rid)
    assert mf.get_ledger(rid, key)["state"] == ex.DONE and len([p for p in profile_repository.list() if p.get("email") == group["extraction"]["email"]]) == 1          # se reconoce el perfil propio: ni duplicado ni conflicto


def test_a_profile_id_that_belongs_to_another_origin_is_a_conflict_not_an_overwrite(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    key = by_name(rid)["Maria Fernandez.docx"]
    pid = ex.profile_id_for(rid, mf.get_plan(rid)["decisions"][key]["cluster"])
    profile_repository.create_with_explicit_id(pid, {"first_name": "Otra", "last_name": "Persona", "email": "otra@x.com", "phone": None, "linkedin_url": None, "location": None, "profile_skills": None, "seniority": None,
                                                      "source": None, "tags": None, "notes": None, "potential_rating": None, "potential_mark": None})
    snapshot = json.dumps(profile_repository.get(pid), sort_keys=True)
    run_all(rid)
    assert mf.get_ledger(rid, key)["state"] == ex.BLOCKED and mf.get_ledger(rid, key)["reason"] == "PROFILE_ID_CONFLICT" and json.dumps(profile_repository.get(pid), sort_keys=True) == snapshot


def test_an_unconfigured_or_unconfirmed_storage_stops_the_batch_without_marking_failures(env, monkeypatch):
    store, world = env
    rid = planned_run(env)
    begin(rid)

    def not_configured(*a, **k):
        raise profile_repository.ProfileConfigIncompleteError("sin Spaces")
    original = profile_repository.create_with_explicit_id
    monkeypatch.setattr(profile_repository, "create_with_explicit_id", not_configured)
    stopped = ex.execute_batch(rid, TOKEN, actor=OWNER, sleep=lambda s: None)
    assert stopped["stopped_reason"] == "CONFIG_INCOMPLETE" and stopped["failed"] == 0 and not [e for e in mf.list_ledgers(rid).values() if e["state"] == ex.FAILED]
    monkeypatch.setattr(profile_repository, "create_with_explicit_id", original)
    run_all(rid)
    assert mf.get_run(rid)["execution"]["state"] == "DONE"

def test_a_blocked_item_resolved_by_hand_stays_visible_as_resolved_and_rectification_from_the_banco_keeps_the_origin(env):
    store, world = env
    rid = planned_run(env)
    intruder = create_in_banco("Otra", "Persona", "maria.fernandez@empresa.com.ar")
    begin(rid)
    run_all(rid)
    key = by_name(rid)["Maria Fernandez.docx"]
    assert ex.exception_row(rid, key)["status"] == "blocked"
    res = ex.link_existing(rid, key, TOKEN, actor=OWNER, profile_id=intruder["profile_id"])
    assert res["ok"] and res["outcome"] == ex.DONE
    row = ex.exception_row(rid, key)
    assert row["status"] == "resolved" and row["ledger"]["profile_id"] == intruder["profile_id"] and ex.exceptions_overview(rid, status="resolved")["total"] == 1
    # rectificación posterior desde la edición existente del Banco: nombre, apellido, email, celular y especialidades; el origen y los documentos se conservan
    created = next(p for p in banco_profiles().values() if p["first_name"] == "Juan")
    run_all(rid)
    from webapp.services import human_capital_service
    updated = human_capital_service.update_profile(created["profile_id"], created["revision"], {"first_name": "Juan Carlos", "last_name": "Pérez", "email": "jc.perez@gmail.com", "phone": "+54 11 0000 0000",
                                                                                         "profile_skills": "SAP, Datasphere", "tags": created["tags"]}, duplicate_confirmed=True)
    assert (updated["first_name"], updated["last_name"], updated["email"], updated["phone"], updated["profile_skills"]) == ("Juan Carlos", "Pérez", "jc.perez@gmail.com", "+54 11 0000 0000", "SAP, Datasphere")
    assert updated["import_origin"]["run_id"] == rid and updated["tags"] == created["tags"] and len(profile_document_repository.list_for_profile(created["profile_id"])) == 2
    with pytest.raises(ValueError):
        human_capital_service.update_profile(created["profile_id"], updated["revision"], {"first_name": "", "last_name": "Pérez"}, duplicate_confirmed=True)   # el contrato nombre + apellido sigue vigente

# ═══════════════════ sonda con nombre único y limpieza fallida; alcance confirmado; descarte ═══════════════════
def test_a_failed_probe_cleanup_reports_the_residual_file_blocks_the_start_and_never_reuses_the_name(env):
    store, world = env
    rid = planned_run(env)
    before = seeded_snapshot()
    store.fail_delete = True
    with pytest.raises(ex.ExecutionError) as caught:
        begin(rid)
    residue = mf.get_run(rid)["probe_residue"]
    assert len(residue) == 1 and f"«{residue[0]}»" in str(caught.value) and "no se inició la importación" in str(caught.value)
    assert "execution" not in mf.get_run(rid) and any(f["name"] == residue[0] for f in store.files.values()) and seeded_snapshot() == before and store.real_files() == {}
    assert ex.execution_report(rid)["probe_residue"] == residue                                                            # visible en el reporte y en el panel
    puts_before = len(store.puts)
    with pytest.raises(ex.ExecutionError, match="residual de una sonda anterior"):                                           # mientras el archivo siga ahí, ni siquiera se vuelve a probar
        begin(rid)
    assert len(store.puts) == puts_before and mf.get_run(rid)["probe_residue"] == residue
    store.fail_delete = False
    with pytest.raises(ex.ExecutionError, match="residual de una sonda anterior"):                                           # arreglar el borrado no alcanza: hay que eliminar el residuo
        begin(rid)
    residual_id = next(fid for fid, f in store.files.items() if f["name"] == residue[0])
    del store.files[residual_id]                                                                                           # el owner lo elimina en SharePoint
    run = begin(rid)
    assert run["execution"]["state"] == "RUNNING" and "probe_residue" not in mf.get_run(rid) and run["execution"]["write_check"]["cleaned"]
    names = probe_files(store)
    assert len(names) == 2 and len(set(names)) == 2 and residue[0] in names                                                # la sonda nueva usó OTRO nombre
    assert not any(f["name"].startswith("__dw_write_probe__") for f in store.files.values())


def test_a_previous_probe_is_never_overwritten(env):
    store, world = env
    store.fail_delete = True
    first = docsvc.probe_write_access(TOKEN, "lote-x")
    assert first["cleaned"] is False
    store.fail_delete = False
    second = docsvc.probe_write_access(TOKEN, "lote-x")
    assert second["cleaned"] and second["file"] != first["file"] and [f["name"] for f in store.files.values()] == [first["file"]]     # la sonda residual sigue intacta


def test_the_start_requires_the_exact_scope_the_owner_saw_and_a_plan_change_never_starts_silently(env):
    store, world = env
    rid = planned_run(env)
    scope = ex.plan_scope(mf.get_plan(rid))
    assert scope == {"revision": 1, "profiles": 2, "documents": 4}
    for bad in (None, {}, {"revision": 1, "profiles": 2}, {"revision": "x", "profiles": 2, "documents": 4}):
        with pytest.raises(ex.ExecutionError, match="Falta el alcance"):
            ex.start_execution(rid, TOKEN, actor=OWNER, confirm=ex.confirm_phrase(rid), expected=bad)
    planning.generate_plan(rid, actor=OWNER)                                                                               # el plan se regenera (otra revisión) mientras el owner tenía la pantalla abierta
    assert mf.get_plan(rid)["revision"] == 2
    with pytest.raises(ex.ExecutionError, match="El plan cambió desde que lo revisaste") as stale:
        ex.start_execution(rid, TOKEN, actor=OWNER, confirm=ex.confirm_phrase(rid), expected=scope)
    assert "revisión 1" in str(stale.value) and "revisión 2" in str(stale.value) and "execution" not in mf.get_run(rid) and store.puts == [] and store.real_files() == {}
    for wrong in ({**scope, "revision": 2, "profiles": 3}, {**scope, "revision": 2, "documents": 5}):                          # mismo número de revisión pero otro alcance
        with pytest.raises(ex.ExecutionError, match="El plan cambió"):
            ex.start_execution(rid, TOKEN, actor=OWNER, confirm=ex.confirm_phrase(rid), expected=wrong)
    assert store.puts == []
    run = ex.start_execution(rid, TOKEN, actor=OWNER, confirm=ex.confirm_phrase(rid), expected={**scope, "revision": 2})
    assert run["execution"]["plan_revision"] == 2


def test_the_discard_stays_explicit_and_is_only_allowed_once_nothing_depends_on_the_temporary_area(env):
    store, world = env
    rid = planned_run(env)
    begin(rid)
    run_all(rid)
    with pytest.raises(pilot.PilotInputError, match="2 excepciones abiertas"):
        ex.assert_can_discard(rid)
    with pytest.raises(pilot.PilotInputError, match="Falta la confirmación"):                                                # incluso sin bloqueos, la frase explícita sigue siendo obligatoria
        pilot.discard_staging(rid, confirm="ELIMINAR", actor=OWNER)
    assert mf.staging_files_present(rid) > 0
