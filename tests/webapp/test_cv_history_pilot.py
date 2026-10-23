"""Piloto de extracción mínima y conciliación del histórico de CVs: Graph SIMULADO, PDF/DOCX de prueba reales, manifiesto persistente reanudable, deduplicación por contenido, idempotencia, errores parciales,
protección de datos personales, retención y eliminación explícita, y las rutas solo-owner. Nada toca SharePoint real, OpenAI, candidatos ni catálogos."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import subprocess
import sys
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from core import microsoft365, remote_store
from tests.webapp.cv_docs import make_blank_pdf, make_docx, make_pdf
from tests.webapp.test_cv_history_inventory import SHARE, TOKEN, FakeGraph, file, folder, http_error
from webapp import access
from webapp.main import app
from webapp.services import auth_service, cv_history_manifest as mf, cv_history_pilot as pilot, profile_repository

OWNER, HC_USER = "owner-cvpilot@danwor.com", "hc-cvpilot@danwor.com"
BODY = "Responsable de la migración del data warehouse corporativo hacia Datasphere"
PHRASE = pilot.AUTHORIZATION_PHRASE

JUAN = make_pdf(["Juan Pérez Gómez", "Consultor SAP Datasphere", "Cel: +54 9 11 5555-1234 | juan.perez@gmail.com", BODY])
JUAN_V2 = make_pdf(["Juan Pérez Gómez", "juan.perez@gmail.com", "Cel +54 9 11 5555-1234", "Versión actualizada del currículum"])
MARIA = make_docx([BODY], header=["María Fernández", "WhatsApp +54 11 4444 5678", "maria.fernandez@empresa.com.ar"])
MULTI = make_pdf(["Pablo Ruiz", "pablo.ruiz@a.com y pruiz@b.com", "Cel 11 5555 1111", "Tel 11 4444 2222"])
SCANNED = make_blank_pdf()
LUCIA = make_pdf(["Lucía Gómez", "lucia.gomez@mail.com", "Cel 11 6666 1111"])
PII = ["Juan", "Pérez", "Perez", "juan.perez", "gmail", "5555", "María", "Fernández", "maria.fernandez", "4444", "Pablo", "Ruiz", "pablo.ruiz", "pruiz", "Lucía", "lucia.gomez", "6666",
       "CV Juan Perez Gomez", "Maria Fernandez.docx", "copia.pdf", "pablo.pdf", "cv.pdf"]


def qx(data: bytes) -> str:
    return "QX" + hashlib.sha1(data).hexdigest()[:14]


class World:
    """SharePoint simulado: `add(especialidad, id, nombre, bytes, hashed)`; sirve el listado (`graph_get_json`) y las descargas (`graph_get_bytes`)."""

    def __init__(self):
        self.specs: dict[str, list] = {}
        self.data: dict[str, bytes] = {}
        self.override: dict[str, bytes] = {}
        self.dl_errors: dict[str, list] = {}
        self.downloads: list[str] = []

    def add(self, spec, item_id, name, data, hashed=True, listed_size=None):
        self.specs.setdefault(spec, []).append(file(item_id, name, len(data) if listed_size is None else listed_size, qx=qx(data) if hashed else None))
        self.data[item_id] = data

    def tree(self):
        t = {"ROOT": [[folder(f"F-{s}", s) for s in sorted(self.specs)]]}
        for s, files in self.specs.items():
            t[f"F-{s}"] = [files]
        return t

    def get_bytes(self, url, token, **kw):
        assert token == TOKEN and url.endswith("/content")
        item = urllib.parse.unquote(url.rsplit("/items/", 1)[1].split("/")[0])
        self.downloads.append(item)
        queue = self.dl_errors.get(item)
        if queue:
            raise queue.pop(0)
        return self.override.get(item, self.data[item])


def standard_world() -> World:
    w = World()
    w.add("Datasphere", "d1", "CV Juan Perez Gomez.pdf", JUAN)
    w.add("Datasphere", "d2", "Maria Fernandez.docx", MARIA)
    w.add("Datasphere", "d3", "CV Juan Perez v2.pdf", JUAN_V2)
    w.add("Databricks", "b1", "copia.pdf", JUAN)                                   # MISMO contenido que d1 en otra especialidad
    w.add("Databricks", "b2", "pablo.pdf", MULTI)
    w.add("Databricks", "b3", "escaneado.pdf", SCANNED)
    w.add("IA", "i1", "lucia.pdf", LUCIA)
    w.add("IA", "i2", "cv.pdf", MULTI, hashed=False)                          # mismo contenido que b2 pero Graph no informa hash
    w.specs["IA"].append(file("x1", "notas.txt", 10))                               # no compatible: nunca entra al lote
    w.specs["IA"].append(file("x2", "viejo.doc", 500))                              # DOC binario: revisión manual
    return w


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    monkeypatch.setattr(config, "PERSIST_READ_ONLY", False)
    return tmp_path


@pytest.fixture()
def sp(home, monkeypatch):
    def install(world: World | None = None, **graph_kw):
        world = world or standard_world()
        monkeypatch.setattr(microsoft365, "graph_get_json", FakeGraph(world.tree(), **graph_kw))
        monkeypatch.setattr(microsoft365, "graph_get_bytes", world.get_bytes)
        for forbidden in ("graph_post_json", "graph_put_bytes", "graph_delete"):
            monkeypatch.setattr(microsoft365, forbidden, lambda *a, **k: (_ for _ in ()).throw(AssertionError("el piloto es de SOLO LECTURA sobre SharePoint")))
        return world
    return install


def seed_profiles():
    profile_repository.create({"first_name": "Lucía", "last_name": "Gómez", "email": "lucia.gomez@mail.com"})          # coincidencia FUERTE (email exacto) con LUCIA
    profile_repository.create({"first_name": "Pablo", "last_name": "Ruiz"})                                           # coincidencia PROBABLE (nombre) con MULTI
    profile_repository.create({"first_name": "Zoe", "last_name": "Zeta", "email": "zoe@mail.com"})


def start(world_installed=None, size=7, **kw):
    return pilot.start_run(TOKEN, SHARE, sample_size=size, authorization=PHRASE, actor=OWNER, min_sample=1, sleep=lambda s: None, **kw)


def groups_by_first_name(run_id):
    return {(g.get("extraction") or {}).get("first_name") or g["content_key"]: g for g in mf.list_groups(run_id)}


def tree_bytes(root: Path, *parts) -> dict:
    base = root.joinpath(*parts)
    return {p.relative_to(base).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}


# ═══════════════════ creación del lote ═══════════════════
def test_start_requires_the_owner_authorization_phrase_and_a_valid_sample_and_creates_nothing_otherwise(sp):
    world = sp()
    for bad in ("", "si", PHRASE.lower(), PHRASE + "x"):
        with pytest.raises(pilot.PilotInputError):
            pilot.start_run(TOKEN, SHARE, sample_size=7, authorization=bad, actor=OWNER, min_sample=1)
    for size in (0, pilot.SAMPLE_MAX + 1):
        with pytest.raises(pilot.PilotInputError):
            pilot.start_run(TOKEN, SHARE, sample_size=size, authorization=PHRASE, actor=OWNER, min_sample=1)
    with pytest.raises(pilot.PilotInputError):
        pilot.start_run(TOKEN, SHARE, sample_size=5, authorization=PHRASE, actor=OWNER)                          # el piso real del piloto es 10
    assert mf.list_runs() == [] and world.downloads == []


def test_start_groups_by_content_keeps_every_specialty_and_downloads_nothing(sp):
    world = sp()
    run = start()
    assert world.downloads == []                                                                                   # crear el lote NO descarga documentos
    groups = mf.list_groups(run["run_id"])
    assert len(groups) == 7 and {g["state"] for g in groups} == {pilot.SELECTED}
    juan = next(g for g in groups if {m["item_id"] for m in g["members"]} == {"d1", "b1"})
    assert juan["specialties"] == ["Databricks", "Datasphere"] and juan["profile_skills_proposal"] == "Databricks, Datasphere" and len(juan["members"]) == 2
    pop = run["population"]
    assert pop["compatible_files"] == 8 and pop["content_groups"] == 7 and pop["redundant_copies"] == 1 and pop["items_without_hash"] == 1 and pop["cross_specialty_groups"] == 1
    assert run["authorization"]["confirmed_by"] == OWNER and run["authorization"]["phrase"] == PHRASE and run["kind"] == "pilot"
    assert {x["item_id"] for g in groups for x in g["members"]} .isdisjoint({"x1", "x2"})                          # .txt y .doc quedan fuera del lote automático


def test_sample_is_deterministic_stratified_and_includes_a_cross_specialty_content(sp):
    sp()
    a = start(size=3)
    b = start(size=3)
    ka = [g["content_key"] for g in mf.list_groups(a["run_id"])]
    kb = [g["content_key"] for g in mf.list_groups(b["run_id"])]
    assert ka == kb and len(ka) == 3
    assert any(len(g["specialties"]) > 1 for g in mf.list_groups(a["run_id"]))
    assert {s for g in mf.list_groups(a["run_id"]) for s in g["specialties"]} >= {"Databricks", "Datasphere", "IA"}   # una vuelta por especialidad


# ═══════════════════ procesamiento de punta a punta ═══════════════════
def test_process_downloads_only_representatives_extracts_reconciles_and_flags_review(sp):
    world = sp()
    seed_profiles()
    run = start()
    out = pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    assert out == {"processed": 7, "failed": 0, "remaining": 0, "stopped_reason": None}
    assert len({"d1", "b1"} & set(world.downloads)) == 1 and len(world.downloads) == 7 and len(set(world.downloads)) == 7          # de las dos copias idénticas se descarga UNA sola
    by = groups_by_first_name(run["run_id"])
    juan, maria, lucia = by["Juan"], by["María"], by["Lucía"]
    assert (juan["extraction"]["email"], juan["extraction"]["last_name"]) == ("juan.perez@gmail.com", "Pérez Gómez") and juan["specialties"] == ["Databricks", "Datasphere"]
    assert maria["review"] == {"required": False, "reasons": []} and maria["extraction"]["email"] == "maria.fernandez@empresa.com.ar"       # DOCX: contacto en el encabezado
    assert lucia["review"]["reasons"] == ["DUP_EXISTING_STRONG"] and lucia["reconciliation"]["profiles"][0]["signals"] == ["EMAIL_EXACT", "NAME_EXACT"]
    assert juan["review"]["reasons"] == [pilot.SAME_PERSON_IN_RUN]                                                 # dos versiones del CV de la misma persona: se SUGIERE, no se fusiona
    multi = next(g for g in mf.list_groups(run["run_id"]) if g["state"] == pilot.EXTRACTED and g["extraction"]["first_name"] == "Pablo")
    assert set(multi["review"]["reasons"]) == {"EMAIL_MULTIPLE", "PHONE_MULTIPLE", "NAME_SINGLE_SOURCE", "DUP_EXISTING_PROBABLE"} and multi["specialties"] and {"Databricks", "IA"} <= set(multi["specialties"])
    scanned = next(g for g in mf.list_groups(run["run_id"]) if (g.get("extraction") or {}).get("failure") == "NO_TEXT")
    assert scanned["review"]["reasons"] == ["NO_TEXT"] and scanned["extraction"]["first_name"] is None


def test_identical_content_without_a_graph_hash_becomes_an_alias_and_merges_specialties(sp):
    sp()
    run = start()
    pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    groups = mf.list_groups(run["run_id"])
    aliases = [g for g in groups if g["state"] == pilot.ALIAS]
    assert len(aliases) == 1
    primary = next(g for g in groups if g["content_key"] == aliases[0]["alias_of"])
    assert primary["state"] == pilot.EXTRACTED and set(primary["specialties"]) == {"Databricks", "IA"} and primary["sha256"] == aliases[0]["sha256"]
    assert {m["item_id"] for m in primary["members"]} >= {"b2", "i2"}
    events = [e["event"] for e in mf.get_audit(run["run_id"], primary["content_key"])["events"]]
    assert "SPECIALTIES_MERGED" in events and "ALIAS" in [e["event"] for e in mf.get_audit(run["run_id"], aliases[0]["content_key"])["events"]]


def test_report_has_counts_rates_and_a_scale_estimate_and_no_personal_data(sp):
    sp()
    seed_profiles()
    run = start()
    pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    r = pilot.run_report(run["run_id"])
    assert r["documents"] == {"in_sample": 7, "extracted": 6, "readable": 5, "failed": 0, "alias": 1, "pending": 0, "multi_specialty": 2}
    assert r["review"]["required"] == 5 and r["review"]["complete_without_review"] == 1 and r["review"]["by_reason"]["NO_TEXT"] == 1
    assert r["field_rates_pct"] == {"name": 100.0, "email": 80.0, "phone": 100.0}                                   # sobre los 5 documentos legibles (el de varios emails no aporta email)
    assert r["reconciliation"]["strong_existing"] == 1 and r["reconciliation"]["probable_existing"] == 1 and r["reconciliation"]["same_person_in_run"] == 2
    assert r["scale_estimate"]["unique_contents"] == 7 and set(r["staging"]) >= {"state", "retention_until", "expired"}
    blob = json.dumps(r, ensure_ascii=False)
    assert not [p for p in PII if p in blob]


# ═══════════════════ idempotencia y reanudación ═══════════════════
def test_processing_again_is_idempotent_and_never_redownloads(sp):
    world = sp()
    seed_profiles()
    run = start()
    pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    first = (tree_bytes(config.BASE_DIR, "human-capital", "import-history", run["run_id"], "staging"), tree_bytes(config.BASE_DIR, "human-capital", "import-history", run["run_id"], "audit"))
    downloads = list(world.downloads)
    again = pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    assert again == {"processed": 0, "failed": 0, "remaining": 0, "stopped_reason": None} and world.downloads == downloads
    assert (tree_bytes(config.BASE_DIR, "human-capital", "import-history", run["run_id"], "staging"), tree_bytes(config.BASE_DIR, "human-capital", "import-history", run["run_id"], "audit")) == first


def test_time_budget_stops_cleanly_and_resuming_reaches_the_same_final_state(sp):
    sp()
    seed_profiles()
    full = start()
    pilot.process_run(full["run_id"], TOKEN, sleep=lambda s: None)
    expected = {k: (g["state"], (g.get("extraction") or {}).get("email"), tuple(g["specialties"])) for k, g in ((g["content_key"], g) for g in mf.list_groups(full["run_id"]))}
    import itertools
    sp()
    seed = start()
    ticks = itertools.count(0, 30)
    cut = pilot.process_run(seed["run_id"], TOKEN, clock=lambda: next(ticks), sleep=lambda s: None, time_budget=50)
    assert cut["stopped_reason"] == "TIME_BUDGET" and 0 < cut["remaining"] < 7 and mf.get_run(seed["run_id"])["state"] == "PROCESSING"
    rest = pilot.process_run(seed["run_id"], TOKEN, sleep=lambda s: None)
    assert rest["remaining"] == 0 and mf.get_run(seed["run_id"])["state"] == "DONE"
    got = {k: (g["state"], (g.get("extraction") or {}).get("email"), tuple(g["specialties"])) for k, g in ((g["content_key"], g) for g in mf.list_groups(seed["run_id"]))}
    assert got == expected


def test_expired_token_stops_without_marking_anything_failed_and_resumes(sp):
    world = sp()
    run = start()
    first_key = sorted(g["content_key"] for g in mf.list_groups(run["run_id"]))[2]
    item = next(g["representative"]["item_id"] for g in mf.list_groups(run["run_id"]) if g["content_key"] == first_key)
    world.dl_errors[item] = [http_error(401)]
    cut = pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    assert cut["stopped_reason"] == "TOKEN_EXPIRED" and cut["processed"] == 2 and cut["failed"] == 0 and cut["remaining"] == 5
    assert all(g["state"] in (pilot.SELECTED, pilot.EXTRACTED) for g in mf.list_groups(run["run_id"]))              # nada quedó FAILED por un token vencido
    done = pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    assert done["remaining"] == 0 and done["stopped_reason"] is None


def test_partial_failures_are_recorded_per_document_the_batch_continues_and_failed_ones_are_retried(sp):
    world = sp()
    run = start()
    keys = sorted(g["content_key"] for g in mf.list_groups(run["run_id"]))
    rep = {g["content_key"]: g["representative"]["item_id"] for g in mf.list_groups(run["run_id"])}
    world.dl_errors[rep[keys[0]]] = [http_error(403)]
    world.dl_errors[rep[keys[1]]] = [http_error(404)]
    world.dl_errors[rep[keys[2]]] = [http_error(500)] * 4
    out = pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    assert out["failed"] == 3 and out["processed"] == 4 and out["remaining"] == 3
    st = {k: mf.get_group(run["run_id"], k) for k in keys[:3]}
    assert [st[k]["failure"] for k in keys[:3]] == ["DOWNLOAD_FORBIDDEN", "DOWNLOAD_NOT_FOUND", "DOWNLOAD_ERROR"] and all(s["state"] == pilot.FAILED for s in st.values())
    assert mf.get_run(run["run_id"])["counters"]["failed"] == 3 and mf.get_run(run["run_id"])["state"] == "PROCESSING"
    retry = pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)                                          # los FAILED se reintentan una vez por pasada
    assert retry["failed"] == 0 and retry["remaining"] == 0 and mf.get_run(run["run_id"])["state"] == "DONE"
    assert [e["event"] for e in mf.get_audit(run["run_id"], keys[0])["events"]][:2] == ["SELECTED", "FAILED"]


def test_size_mismatch_invalid_format_and_changed_content_are_exceptions_never_silently_accepted(sp):
    world = sp()
    run = start()
    keys = sorted(g["content_key"] for g in mf.list_groups(run["run_id"]))
    rep = {g["content_key"]: g["representative"]["item_id"] for g in mf.list_groups(run["run_id"])}
    world.override[rep[keys[0]]] = b"%PDF-1.4 corto"                                                               # tamaño distinto del informado por Graph
    fake_pdf = b"esto no es un pdf aunque se llame pdf"
    world.data[rep[keys[1]]] = fake_pdf
    mf.put_group(run["run_id"], keys[1], {**mf.get_group(run["run_id"], keys[1]), "size": len(fake_pdf)})        # Graph informaría ese tamaño: lo que falla es la FIRMA del formato
    g = mf.get_group(run["run_id"], keys[2])
    mf.put_group(run["run_id"], keys[2], {**g, "sha256": "0" * 64})                                               # un contenido ya registrado que ahora difiere
    pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    got = {k: mf.get_group(run["run_id"], k) for k in keys[:3]}
    assert got[keys[0]]["failure"] == "SIZE_MISMATCH" and got[keys[1]]["failure"] == "FORMAT_INVALID" and got[keys[2]]["failure"] == "CONTENT_CHANGED"
    assert got[keys[2]]["sha256"] == "0" * 64 and got[keys[2]]["extraction"] is None                               # el registro previo NO se pisó


def test_a_persistence_failure_stops_the_batch_and_it_resumes_without_losing_checkpoints(sp, monkeypatch):
    sp()
    run = start()
    real, calls = mf._write, {"n": 0}

    def flaky(path, payload):
        calls["n"] += 1
        if calls["n"] == 5:
            raise mf.ManifestDurabilityError("escritura no confirmada en Spaces")
        return real(path, payload)
    monkeypatch.setattr(mf, "_write", flaky)
    cut = pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    assert cut["stopped_reason"] == "PERSISTENCE_ERROR" and cut["remaining"] > 0
    monkeypatch.setattr(mf, "_write", real)
    done = pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    assert done["remaining"] == 0 and all(g["state"] in (pilot.EXTRACTED, pilot.ALIAS) for g in mf.list_groups(run["run_id"]))


def test_unavailable_profiles_stop_before_any_download(sp, monkeypatch):
    world = sp()
    run = start()
    monkeypatch.setattr(profile_repository, "list", lambda: (_ for _ in ()).throw(profile_repository.ProfileStorageError("x")))
    out = pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    assert out["stopped_reason"] == "PROFILES_UNAVAILABLE" and out["remaining"] == 7 and world.downloads == []


# ═══════════════════ garantías: nada se crea, nada se escribe, nada se filtra ═══════════════════
def test_nothing_is_created_copied_or_written_outside_the_temporary_manifest(sp, home):
    world = sp()
    seed_profiles()
    before = tree_bytes(home, "human-capital", "profiles")
    run = start()
    pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    assert tree_bytes(home, "human-capital", "profiles") == before and len(profile_repository.list()) == 3          # ningún candidato nuevo ni modificado
    written = {p.relative_to(home).as_posix() for p in home.rglob("*") if p.is_file()}
    assert all(w.startswith(("human-capital/import-history/", "human-capital/profiles/")) for w in written), written
    assert not (home / "human-capital" / "catalogs").exists() and not (home / "human-capital" / "profile-documents").exists()
    blobs = b"".join(p.read_bytes() for p in home.rglob("*") if p.is_file())
    assert b"%PDF" not in blobs and b"PK\x03\x04" not in blobs and BODY.encode() not in blobs                         # los bytes del CV nunca se guardan
    assert set(world.downloads) <= set(world.data)                                                                 # y toda lectura fue de `/content`; las escrituras a Graph están prohibidas por el fixture


def test_the_openai_stack_and_the_documents_services_are_not_involved():
    code = subprocess.run([sys.executable, "-c", "import sys, webapp.services.cv_extract, webapp.services.cv_reconcile, webapp.services.cv_history_manifest, webapp.services.cv_history_pilot as p;"
                           "bad=[m for m in ('openai','webapp.services.llm_client','webapp.services.profile_document_service','webapp.services.profile_document_repository','webapp.services.human_capital_service') if m in sys.modules];"
                           "print(bad)"], capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2])
    assert code.returncode == 0 and code.stdout.strip() == "[]", code.stderr


def test_logs_run_json_audit_and_report_never_contain_personal_data_but_staging_does(sp, home, caplog):
    sp()
    seed_profiles()
    with caplog.at_level(logging.DEBUG):
        run = start()
        pilot.process_run(run["run_id"], TOKEN, sleep=lambda s: None)
    base = home / "human-capital" / "import-history" / run["run_id"]
    for leak_free in (caplog.text, (base / "run.json").read_text(encoding="utf-8"), "".join(p.read_text(encoding="utf-8") for p in (base / "audit").glob("*.json"))):
        assert not [p for p in PII if p in leak_free], [p for p in PII if p in leak_free]
        assert TOKEN not in leak_free and "SECRET" not in leak_free and "SECRETSHARELINK" not in leak_free
    staging = "".join(p.read_text(encoding="utf-8") for p in (base / "staging").glob("*.json"))
    assert "juan.perez@gmail.com" in staging and "Pérez" in staging                                               # control positivo: lo personal vive SOLO en el área temporal
    assert "Datasphere" not in "".join(p.read_text(encoding="utf-8") for p in (base / "audit").glob("*.json"))     # la auditoría lleva solo conteos de especialidades, no sus nombres
    assert BODY not in staging and "migración" not in staging                                                      # y nunca el texto del CV


# ═══════════════════ manifiesto: ids, durabilidad, modos ═══════════════════
def test_manifest_rejects_unsafe_ids_and_never_touches_paths_outside_its_directory(home):
    for bad in ("../x", "ch-1", "CH-20260101T000000Z", "ch-20260101T000000Z/../../x", ""):
        with pytest.raises(mf.ManifestInvalidIdError):
            mf.get_run(bad)
    run_id = mf.new_run_id()
    mf.create_run(run_id, {"state": "CREATED"})
    for bad_key in ("../../etc", "ABC", "g" * 16, "a" * 15):
        with pytest.raises(mf.ManifestInvalidIdError):
            mf.get_group(run_id, bad_key)
        with pytest.raises(mf.ManifestInvalidIdError):
            mf.append_audit(run_id, bad_key, "X")
    with pytest.raises(mf.ManifestError):
        mf.create_run(run_id, {})                                                                                 # un lote existente no se pisa
    assert [r["run_id"] for r in mf.list_runs()] == [run_id]


def test_manifest_writes_require_confirmed_durability_in_remote_mode_and_are_refused_in_read_only(home, monkeypatch):
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")
    monkeypatch.setattr(remote_store, "write_through", lambda path, text: "local")                                 # Spaces no confirmó
    with pytest.raises(mf.ManifestDurabilityError):
        mf.create_run(mf.new_run_id(), {})
    monkeypatch.setattr(remote_store, "write_through", lambda path, text: (Path(path).parent.mkdir(parents=True, exist_ok=True), Path(path).write_text(text, encoding="utf-8"), "remote")[2])
    assert mf.create_run(mf.new_run_id(suffix="ab12"), {})["revision"] == 1
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    with pytest.raises(mf.ManifestDurabilityError):
        mf.create_run(mf.new_run_id(suffix="cd34"), {})


# ═══════════════════ retención y eliminación explícita ═══════════════════
def test_the_run_declares_an_explicit_retention_policy_without_automatic_deletion(sp):
    sp()
    run = start()
    st = run["staging"]
    assert st["state"] == "PRESENT" and st["retention_days"] == 14 and st["auto_delete"] is False and st["retention_until"] > run["created_at"] and "owner" in st["policy"]


def test_discard_requires_the_exact_phrase_removes_only_staging_keeps_audit_and_report(sp, home):
    sp()
    seed_profiles()
    run = start()
    rid = run["run_id"]
    pilot.process_run(rid, TOKEN, sleep=lambda s: None)
    before_report = pilot.run_report(rid)
    audit_before = tree_bytes(home, "human-capital", "import-history", rid, "audit")
    for bad in ("", "ELIMINAR", pilot.discard_phrase(rid).lower(), pilot.discard_phrase("ch-20200101T000000Z")):
        with pytest.raises(pilot.PilotInputError):
            pilot.discard_staging(rid, confirm=bad, actor=OWNER)
    assert mf.staging_files_present(rid) == 7
    st = pilot.discard_staging(rid, confirm=pilot.discard_phrase(rid), actor=OWNER)
    assert st["state"] == "DISCARDED" and st["files_removed"] == 7 and st["discarded_by"] == OWNER and mf.staging_files_present(rid) == 0
    assert not [p for p in PII if p in "".join(f.read_text(encoding="utf-8") for f in (home / "human-capital" / "import-history" / rid).rglob("*.json"))]       # ya no queda ningún dato personal en el lote
    after = tree_bytes(home, "human-capital", "import-history", rid, "audit")
    assert set(after) == set(audit_before) and all(json.loads(v)["events"][-1]["event"] == "STAGING_DISCARDED" for v in after.values())
    final = pilot.run_report(rid)
    assert final["documents"] == before_report["documents"] and final["review"] == before_report["review"] and final["staging"]["state"] == "DISCARDED" and pilot.review_view(rid) == []
    assert pilot.discard_staging(rid, confirm=pilot.discard_phrase(rid), actor=OWNER)["state"] == "DISCARDED"          # idempotente
    assert len(profile_repository.list()) == 3


def test_discard_is_resumable_and_rolls_back_a_file_whose_remote_delete_is_not_confirmed(sp, home, monkeypatch):
    sp()
    run = start()
    rid = run["run_id"]
    pilot.process_run(rid, TOKEN, sleep=lambda s: None)
    monkeypatch.setattr(remote_store, "local_only", lambda: False)
    monkeypatch.setattr(remote_store, "write_through", lambda path, text: (Path(path).parent.mkdir(parents=True, exist_ok=True), Path(path).write_text(text, encoding="utf-8"), "remote")[2])      # Spaces confirma las escrituras
    deleted: list[str] = []

    def delete_path(path):
        if len(deleted) == 3:
            return False
        deleted.append(Path(path).name)
        return True
    monkeypatch.setattr(remote_store, "delete_path", delete_path)
    with pytest.raises(mf.ManifestDurabilityError):
        pilot.discard_staging(rid, confirm=pilot.discard_phrase(rid), actor=OWNER)
    assert mf.staging_files_present(rid) == 4 and mf.get_run(rid)["staging"]["state"] == "DISCARDING"              # el archivo no confirmado volvió a existir: local y remoto no divergen
    monkeypatch.setattr(remote_store, "delete_path", lambda path: True)
    assert pilot.discard_staging(rid, confirm=pilot.discard_phrase(rid), actor=OWNER)["state"] == "DISCARDED" and mf.staging_files_present(rid) == 0


def test_an_expired_retention_is_flagged(sp):
    sp()
    run = start()
    mf.update_run(run["run_id"], lambda d: d["staging"].update({"retention_until": "2020-01-01T00:00:00Z"}))
    assert pilot.run_report(run["run_id"])["staging"]["expired"] is True
    pilot.discard_staging(run["run_id"], confirm=pilot.discard_phrase(run["run_id"]), actor=OWNER)
    assert pilot.run_report(run["run_id"])["staging"]["expired"] is False


# ═══════════════════ interfaz administrativa: solo owner ═══════════════════
def client_for(email, monkeypatch) -> TestClient:
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", f"{OWNER},{HC_USER}")
    monkeypatch.setattr(access, "APP_OWNER", OWNER)
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


BASE = "/cchh/diagnostico/cvs-historicos"


def post_start(c, **over):
    data = {"graph_token": TOKEN, "share_url": SHARE, "sample_size": "10", "authorization": PHRASE, **over}
    return c.post(f"{BASE}/piloto", data=data)


def run_id_from(html: str) -> str:
    return re.search(r"(ch-\d{8}T\d{6}Z-[a-z0-9]{4})", html).group(1)


def test_every_route_is_owner_only_and_hidden_from_the_nav(sp, monkeypatch):
    sp()
    rid = mf.new_run_id()
    mf.create_run(rid, {"state": "CREATED"})
    anon = TestClient(app, follow_redirects=False)
    regular = client_for(HC_USER, monkeypatch)
    calls = [("get", BASE, {}), ("get", f"{BASE}/{rid}", {}), ("post", f"{BASE}/piloto", {"data": {"graph_token": TOKEN, "share_url": SHARE, "sample_size": "10", "authorization": PHRASE}}),
             ("post", f"{BASE}/{rid}/procesar", {"data": {"graph_token": TOKEN}}), ("post", f"{BASE}/{rid}/descartar", {"data": {"confirm": pilot.discard_phrase(rid)}})]
    for method, url, kw in calls:
        assert getattr(anon, method)(url, **kw).status_code in (302, 303, 307), url
        assert getattr(regular, method)(url, **kw).status_code == 403, url
    assert mf.list_runs()[0]["run_id"] == rid and len(mf.list_runs()) == 1                                           # ninguna llamada no autorizada creó nada
    assert "/cchh/diagnostico/cvs-historicos" not in client_for(OWNER, monkeypatch).get("/cchh/candidatos").text      # fuera del nav


def test_the_manifest_files_are_not_reachable_over_http(sp, monkeypatch):
    sp()
    run = start()
    key = mf.list_groups(run["run_id"])[0]["content_key"]
    owner = client_for(OWNER, monkeypatch)
    for url in (f"/human-capital/import-history/{run['run_id']}/staging/{key}.json", f"/static/../human-capital/import-history/{run['run_id']}/run.json",
                f"/static/human-capital/import-history/{run['run_id']}/run.json", f"/import-history/{run['run_id']}/run.json"):
        assert owner.get(url).status_code in (404, 307, 308) and "juan" not in owner.get(url).text.lower()


def test_page_shows_the_explicit_authorization_before_anything_runs(sp, monkeypatch):
    sp()
    page = client_for(OWNER, monkeypatch).get(BASE)
    html = page.text
    form = re.search(r'<form id="dw-cvpilot-form"[^>]*>', html).group(0)
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store" and "data-dw-graph-token" in form and f'hx-post="{BASE}/piloto"' in form
    assert f"<code>{PHRASE}</code>" in html and 'id="dw-cvpilot-auth"' in html and 'name="authorization"' in html and 'min="10" max="20"' in html and "data-pilot-no-runs" in html
    assert TOKEN not in html and "sessionStorage.getItem" not in html


def test_start_route_requires_authorization_token_and_valid_input_and_creates_nothing_in_those_cases(sp, monkeypatch):
    sp()
    owner = client_for(OWNER, monkeypatch)
    no_auth = post_start(owner, authorization="")
    assert no_auth.status_code == 200 and 'data-error-kind="invalid_input"' in no_auth.text and "confirmación explícita" in no_auth.text
    assert 'data-error-kind="missing_token"' in post_start(owner, graph_token="").text
    assert 'data-error-kind="invalid_sample"' in post_start(owner, sample_size="abc").text and 'data-error-kind="invalid_input"' in post_start(owner, sample_size="3").text
    assert 'data-error-kind="invalid_input"' in post_start(owner, share_url="http://x").text
    assert mf.list_runs() == []


def test_start_and_process_through_the_routes_render_counts_only_and_report_resumability(sp, monkeypatch):
    world = sp()
    seed_profiles()
    owner = client_for(OWNER, monkeypatch)
    started = post_start(owner, sample_size="10")
    assert started.status_code == 200 and "data-pilot-created" in started.text and TOKEN not in started.text and world.downloads == []
    rid = run_id_from(started.text)
    assert "Procesar muestra (7 pendientes)" in started.text and 'data-dw-graph-token' in started.text and not [p for p in PII if p in started.text]
    done = owner.post(f"{BASE}/{rid}/procesar", data={"graph_token": TOKEN})
    assert done.status_code == 200 and "Procesados 7 · con error 0 · pendientes 0" in done.text and 'data-run-state="DONE"' in done.text
    assert "data-pilot-reasons" in done.text and "DUP_EXISTING_STRONG" in done.text and "data-pilot-scale" in done.text and not [p for p in PII if p in done.text]
    again = owner.post(f"{BASE}/{rid}/procesar", data={"graph_token": TOKEN})
    assert "Procesados 0" in again.text and len(world.downloads) == 7 and "Procesar muestra" not in again.text           # idempotente: sin pendientes no hay botón
    assert 'data-error-kind="missing_token"' in owner.post(f"{BASE}/{rid}/procesar", data={"graph_token": ""}).text
    assert owner.post(f"{BASE}/ch-20200101T000000Z/procesar", data={"graph_token": TOKEN}).status_code == 404 and owner.post(f"{BASE}/no-es-un-lote/procesar", data={"graph_token": TOKEN}).status_code == 404


def test_detail_shows_individual_results_with_personal_data_only_to_the_owner(sp, monkeypatch):
    sp()
    seed_profiles()
    owner = client_for(OWNER, monkeypatch)
    rid = run_id_from(post_start(owner).text)
    owner.post(f"{BASE}/{rid}/procesar", data={"graph_token": TOKEN})
    page = owner.get(f"{BASE}/{rid}")
    html = page.text
    assert page.status_code == 200 and page.headers["cache-control"] == "no-store" and "contiene datos personales" in html
    assert "juan.perez@gmail.com" in html and "Pérez Gómez" in html and "Databricks, Datasphere" in html and "SAME_PERSON_IN_RUN" in html and "DUP_EXISTING_STRONG" in html and "NO_TEXT" in html
    first_review = html.index('data-review="yes"')
    assert first_review < (html.index('data-review="no"') if 'data-review="no"' in html else len(html))              # los que requieren revisión van primero
    assert 'href="/cchh/candidatos/' in html and "migración" not in html                                            # enlaza al perfil coincidente; nunca el texto del CV
    assert client_for(HC_USER, monkeypatch).get(f"{BASE}/{rid}").status_code == 403
    assert owner.get(f"{BASE}/ch-20200101T000000Z").status_code == 404 and owner.get(f"{BASE}/..%2Fx").status_code == 404


def test_discard_route_requires_the_confirmation_and_removes_personal_data_from_the_detail_view(sp, monkeypatch):
    sp()
    owner = client_for(OWNER, monkeypatch)
    rid = run_id_from(post_start(owner).text)
    owner.post(f"{BASE}/{rid}/procesar", data={"graph_token": TOKEN})
    assert "data-pilot-retention" in owner.get(f"{BASE}/{rid}").text and pilot.discard_phrase(rid) in owner.get(f"{BASE}/{rid}").text
    wrong = owner.post(f"{BASE}/{rid}/descartar", data={"confirm": "ELIMINAR"})
    assert 'data-error-kind="invalid_confirmation"' in wrong.text and mf.staging_files_present(rid) == 7
    ok = owner.post(f"{BASE}/{rid}/descartar", data={"confirm": pilot.discard_phrase(rid)})
    assert "data-pilot-discarded" in ok.text and mf.staging_files_present(rid) == 0 and "eliminados" in ok.text
    after = owner.get(f"{BASE}/{rid}").text
    assert not [p for p in PII if p in after] and "data-pilot-discard-form" not in after and "<strong>eliminados</strong>" in after
    assert owner.post(f"{BASE}/ch-20200101T000000Z/descartar", data={"confirm": "x"}).status_code == 404
