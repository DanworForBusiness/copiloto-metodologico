"""
test_assessment_service.py — servicio delgado Catálogo ↔ Assessment de modelado (Catalog Assessment Report V1).

Pool CSN SINTÉTICO (`write_inbound`: Acme con dos AM terminales, Solo sin AM, Clash con colisión). Nunca datos
reales ni catalog_local. El motor es el REAL (API Python); donde se prueba "no se invocó" o "se invocó una vez"
se envuelve `_engine_assess`.

Contratos que fija:
  * entrada de Catálogo → AM terminal por el GRAFO del pool (no por nombre/directorio/similitud);
  * leer el estado NUNCA ejecuta el motor ni escribe la caché; un miss es NOT_YET_ASSESSED;
  * generar valida y cachea el JSON canónico en un sobre SEPARADO; CURRENT no recalcula;
  * vigencia = firma del pool + bloque completo de versions (falso STALE posible, falso CURRENT jamás);
  * FAILED estable (sin traza al usuario), reintento, último perfil válido retenido;
  * caché fuera de PERSIST_PATHS/catalog_local (fail-closed); pérdida de caché = NOT_YET_ASSESSED;
  * single-flight por (cliente, AM); esquema desconocido/inválido = INCOMPATIBLE_SCHEMA (nunca se renderiza).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import config
from tests._modeling_assessment_m7_fixtures import write_inbound
from tools.catalog import catalog_service as cs
from tools.catalog.modeling_assessment import paths as ma_paths
from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import shadow
from tools.catalog.modeling_assessment.render_md import render_markdown
from webapp.services import assessment_service as svc


def entry(cliente, artefactos, *, eid=None, dominio="General"):
    return cs.CatalogEntry(path="x", cliente=cliente, dominio=dominio, veredicto="plena", apto="ok",
                           entry_id=eid or f"{cliente.lower()}-{'-'.join(sorted(artefactos))[:30].lower()}", artefactos=list(artefactos))


ACME = ["1VR_RAW", "3VF_P", "4AM_ACME", "RAW_T"]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    inbound = write_inbound(tmp_path / "pool")
    cache = tmp_path / "runtime-cache"
    monkeypatch.setenv("DANWOR_CATALOG_CSN_CACHE", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog"))
    governed = tmp_path / "governed-assessments"
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", cache)
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(governed))
    svc.reset_memory_cache()
    svc.invalidate_pool_index()
    yield SimpleNamespace(inbound=inbound, cache=cache, governed=governed, tmp=tmp_path)
    svc.reset_memory_cache()
    svc.invalidate_pool_index()


@pytest.fixture()
def spy(monkeypatch):
    calls = []
    real = svc._engine_assess

    def wrapped(*a):
        calls.append(a)
        return real(*a)
    monkeypatch.setattr(svc, "_engine_assess", wrapped)
    return calls


def _no_engine(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("el motor NO debe ejecutarse al leer el estado")
    monkeypatch.setattr(svc, "_engine_assess", boom)


def _cache_files(env):
    return sorted(p.name for p in env.cache.rglob("*") if p.is_file()) if env.cache.exists() else []


def _governed_files(env):
    return sorted(env.governed.rglob("*.json")) if env.governed.exists() else []


# ── 1. entrada → AM terminal ────────────────────────────────────────────────
def test_entry_resolves_to_the_single_terminal_am_by_graph_not_by_name(env):
    # artefactos con un NOMBRE que parece AM pero NO es terminal en el grafo (4AM_FAKE) y un directorio sin relación
    e = entry("Acme", [*ACME, "4AM_FAKE"], dominio="Un dominio cualquiera")
    r = svc.resolve_entry(e)
    assert (r.kind, r.terminal_am) == ("OK", "4AM_ACME")


def test_entry_without_terminal_am_is_not_applicable(env):
    r = svc.resolve_entry(entry("Solo", ["1VR_ONLY", "LOCAL_X"]))
    assert r.kind == "NO_TERMINAL_AM" and r.terminal_am is None
    st = svc.status_for_entry(entry("Solo", ["1VR_ONLY"]))
    assert st.state == svc.NOT_APPLICABLE and st.reason and not st.can_generate


def test_entry_with_two_terminal_ams_is_not_applicable_and_never_guessed(env):
    r = svc.resolve_entry(entry("Acme", [*ACME, "4AM_OTHER"]))
    assert r.kind == "MULTIPLE_TERMINAL_AMS" and r.terminal_am is None


def test_unloadable_pool_is_a_stable_failure_not_a_guess(env):
    st = svc.status_for_entry(entry("Clash", ["DUP"]))
    assert st.state == svc.FAILED and st.error_code == "POOL_LOAD_ERROR" and "Traceback" not in (st.reason or "")


def test_missing_pool_is_not_yet_assessed_and_generation_fails_with_a_stable_code(env, monkeypatch):
    monkeypatch.setenv("DANWOR_CATALOG_CSN_CACHE", str(env.tmp / "no-existe"))
    monkeypatch.setenv("DANWOR_CATALOG_INBOUND_DIR", str(env.tmp / "tampoco"))
    e = entry("Acme", ACME)
    assert svc.status_for_entry(e).state == svc.NOT_YET_ASSESSED
    st = svc.generate(e)
    assert st.state == svc.FAILED and st.error_code == "POOL_UNAVAILABLE" and not _cache_files(env)


@pytest.mark.skipif(not os.getenv("DANWOR_REAL_CATALOG_DIR"), reason="corpus real solo local: definir DANWOR_REAL_CATALOG_DIR")
def test_real_corpus_maps_33_terminal_ams_and_danone_is_not_applicable(monkeypatch):
    real = Path(os.environ["DANWOR_REAL_CATALOG_DIR"])
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(real))
    monkeypatch.delenv("DANWOR_CATALOG_CSN_CACHE", raising=False)
    entries = cs.load_catalog(str(real))
    res = svc.resolve_entries(entries)
    assert len(entries) == 34
    ok = [r for r in res.values() if r.kind == "OK"]
    na = [(e.cliente, res[e.entry_id].kind) for e in entries if res[e.entry_id].kind != "OK"]
    assert len(ok) == 33 and len({(r.client, r.terminal_am) for r in ok}) == 33
    assert na == [("Danone", "NO_TERMINAL_AM")]


# ── 2. GET: sin motor, sin escritura ────────────────────────────────────────
def test_status_read_never_runs_the_engine_nor_writes_the_cache(env, monkeypatch):
    _no_engine(monkeypatch)
    e = entry("Acme", ACME)
    for _ in range(3):
        st = svc.status_for_entry(e)
        assert st.state == svc.NOT_YET_ASSESSED and st.profile is None and st.can_generate
    assert svc.statuses_for_entries([e, entry("Solo", ["1VR_ONLY"])])[e.entry_id].state == svc.NOT_YET_ASSESSED
    assert not env.cache.exists() and not _cache_files(env) and not _governed_files(env)


# ── 3/4. POST: generar, validar, cachear, CURRENT idempotente ───────────────
def test_generate_runs_engine_once_validates_and_caches_a_separate_envelope(env, spy):
    e = entry("Acme", ACME)
    st = svc.generate(e)
    assert st.state == svc.CURRENT and len(spy) == 1
    ma_profile.validate_assessment(st.profile)
    # el documento canónico NO lleva metadatos de caché/UI
    assert "envelope_version" not in st.profile and "generated_at" not in st.profile
    files = _governed_files(env)
    assert len(files) == 1 and files[0].relative_to(env.governed).parts[:2] == ("v1", "Acme") and not _cache_files(env)   # almacén DURABLE, no la caché efímera
    envelope = json.loads(files[0].read_text(encoding="utf-8"))
    for k in ("client", "terminal_am", "generated_at", "assessment_schema_version", "assessment_versions", "input_pool_signature",
              "assessment_artifact_signature", "status", "profile_json", "error_code"):
        assert k in envelope
    assert envelope["status"] == "OK" and envelope["terminal_am"] == "4AM_ACME" and envelope["error_code"] is None
    assert envelope["assessment_versions"] == svc.current_assessment_versions() == st.profile["versions"]
    assert envelope["input_pool_signature"] == st.profile["scope"]["pool_signature"]
    assert "envelope_version" not in envelope["profile_json"] and "generated_at" not in envelope["profile_json"]


def test_current_state_does_not_recompute_on_post_or_read(env, spy):
    e = entry("Acme", ACME)
    svc.generate(e)
    assert svc.status_for_entry(e).state == svc.CURRENT
    again = svc.generate(e)                                    # POST sobre CURRENT: idempotente
    assert again.state == svc.CURRENT and len(spy) == 1


# ── 5/6. STALE (firma del pool / versiones) ─────────────────────────────────
def test_stale_when_pool_signature_changes_and_regenerate_makes_it_current(env, spy):
    e = entry("Acme", ACME)
    old = svc.generate(e)
    f = env.inbound / "Acme/views/1VR_RAW.json"
    body = json.loads(f.read_text(encoding="utf-8"))
    body["definitions"]["1VR_RAW"]["elements"]["EXTRA"] = {"type": "cds.String"}
    f.write_text(json.dumps(body), encoding="utf-8")
    st = svc.status_for_entry(e)
    assert st.state == svc.STALE and st.profile is not None and st.profile == old.profile and st.can_generate     # el informe previo sigue consultable
    new = svc.generate(e)
    assert new.state == svc.CURRENT and len(spy) == 2 and new.profile["scope"]["pool_signature"] != old.profile["scope"]["pool_signature"]


def test_stale_when_assessment_versions_change(env, monkeypatch):
    e = entry("Acme", ACME)
    svc.generate(e)
    bumped = {**svc.current_assessment_versions(), "signals": "signal-rules/v999"}
    monkeypatch.setattr(svc, "current_assessment_versions", lambda: bumped)
    st = svc.status_for_entry(e)
    assert st.state == svc.STALE and st.profile is not None


def test_stale_never_a_false_current_when_a_shared_dependency_of_another_model_changes(env):
    # P1: la vigencia ya NO es la firma de TODO el cliente sino la de las dependencias del AM (ver test_assessment_dependency_currentness.py): un cambio en un objeto que el AM LEE
    # (aquí la vista compartida 3VF_P, tocada "desde" el otro modelo) sigue siendo STALE; un cambio que no lee conserva su vigencia.
    e = entry("Acme", ACME)
    svc.generate(e)
    f = env.inbound / "Acme/views/3VF_P.json"
    body = json.loads(f.read_text(encoding="utf-8"))
    body["definitions"]["3VF_P"]["elements"]["Z"] = {"type": "cds.String"}
    f.write_text(json.dumps(body), encoding="utf-8")
    assert svc.status_for_entry(e).state == svc.STALE


# ── 7/8. FAILED + reintento + último perfil retenido ────────────────────────
def test_failed_generation_is_stable_has_no_traceback_and_retry_recovers(env, monkeypatch, caplog):
    e = entry("Acme", ACME)
    real = svc._engine_assess

    def boom(*a):
        raise RuntimeError("fallo interno secreto /ruta/interna")
    monkeypatch.setattr(svc, "_engine_assess", boom)
    with caplog.at_level("ERROR", logger="danwor.assessment"):
        st = svc.generate(e)
    assert st.state == svc.FAILED and st.error_code == "GENERATION_ERROR" and st.profile is None
    assert "secreto" not in (st.reason or "") and "Traceback" not in (st.reason or "") and "RuntimeError" not in (st.reason or "")
    assert any("secreto" in (r.exc_text or "") for r in caplog.records)          # detalle SOLO en el log del servidor
    assert svc.status_for_entry(e).state == svc.FAILED                           # persistente (cabecera: "Assessment no disponible")
    monkeypatch.setattr(svc, "_engine_assess", real)
    assert svc.generate(e).state == svc.CURRENT                                  # reintentar


def test_failed_regeneration_keeps_the_last_valid_report_viewable(env, monkeypatch):
    e = entry("Acme", ACME)
    ok = svc.generate(e)
    f = env.inbound / "Acme/views/1VR_RAW.json"
    f.write_text(f.read_text(encoding="utf-8").replace('"A"', '"A2"', 1), encoding="utf-8")
    monkeypatch.setattr(svc, "_engine_assess", lambda *a: (_ for _ in ()).throw(RuntimeError("x")))
    st = svc.generate(e)
    assert st.state == svc.FAILED and st.profile == ok.profile and st.previous_profile_retained and st.viewable and st.summary is None


def test_engine_returning_indeterminate_is_a_stable_failure(env, monkeypatch):
    monkeypatch.setattr(svc, "_engine_assess", lambda *a: (ma_profile.build_indeterminate_assessment(client="Acme", requested_model="4AM_ACME", message="x"), {}))
    st = svc.generate(entry("Acme", ACME))
    assert st.state == svc.FAILED and st.error_code == "POOL_LOAD_ERROR" and st.profile is None


# ── 9. caché fuera de rutas persistidas (fail-closed) ───────────────────────
def test_default_cache_dir_is_outside_every_persisted_path():
    default = Path(config.BASE_DIR) / "assessment_cache"
    assert "assessment_cache" not in config.PERSIST_PATHS
    assert ma_paths.persisted_dir_containing(default) is None
    assert Path(config.ASSESSMENT_CACHE_DIR).name == "assessment_cache" or os.getenv("DANWOR_ASSESSMENT_CACHE_DIR")


@pytest.mark.parametrize("where", ["human-capital/x", "reports/a", "reporting_cache", "CATALOG_ENV/assessment"])
def test_cache_under_a_persisted_dir_is_refused_and_nothing_is_written(env, monkeypatch, where):
    # `catalog_local` se resuelve vía DANWOR_CATALOG_DIR (como remote_store): CATALOG_ENV = ese directorio
    bad = (env.tmp / "catalog" / "assessment") if where.startswith("CATALOG_ENV") else Path(config.BASE_DIR) / where
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", bad)
    assert svc.cache_dir() is None
    e = entry("Acme", ACME)
    st = svc.generate(e)                                        # sigue funcionando (solo memoria)
    assert st.state == svc.CURRENT and not bad.exists()


def test_cache_under_the_catalog_dir_is_refused_even_if_persist_paths_were_overridden(env, monkeypatch):
    monkeypatch.setattr(config, "PERSIST_PATHS", [])           # el entorno sacó catalog_local de SPACES_PERSIST_PATHS
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", env.tmp / "catalog" / "assessment")
    assert svc.cache_dir() is None


def test_cache_under_the_pool_is_refused(env, monkeypatch):
    monkeypatch.setattr(config, "ASSESSMENT_CACHE_DIR", env.inbound / "cache")
    assert svc.cache_dir() is None


# ── 10. almacén durable del assessment gobernado ────────────────────────────
def test_governed_assessment_survives_restart_and_is_not_marked_current_blindly(env, monkeypatch):
    e = entry("Acme", ACME)
    svc.generate(e)
    svc.reset_memory_cache()                                    # reinicio del proceso: se pierde la RAM
    _no_engine(monkeypatch)
    st = svc.status_for_entry(e)
    assert st.state == svc.CURRENT and st.profile is not None   # restaurado del almacén durable (sin motor)
    # la vigencia se sigue evaluando contra la firma del pool: si el pool cambió, es STALE (jamás CURRENT a ciegas)
    f = env.inbound / "Acme/views/1VR_RAW.json"
    f.write_text(f.read_text(encoding="utf-8").replace('"A"', '"A9"', 1), encoding="utf-8")
    svc.reset_memory_cache()
    assert svc.status_for_entry(e).state == svc.STALE


def test_losing_the_durable_store_itself_becomes_not_yet_assessed(env):
    e = entry("Acme", ACME)
    svc.generate(e)
    for p in _governed_files(env):
        p.unlink()
    svc.reset_memory_cache()                                    # sin RAM y sin almacén durable (p. ej. bucket vacío) = sin assessment
    assert svc.status_for_entry(e).state == svc.NOT_YET_ASSESSED


def test_corrupt_or_foreign_durable_file_fails_closed(env):
    e = entry("Acme", ACME)
    svc.generate(e)
    path = _governed_files(env)[0]
    for junk in ("no es json {", json.dumps({"otra": "cosa"}), json.dumps([1, 2])):
        path.write_text(junk, encoding="utf-8")
        svc.reset_memory_cache()
        assert svc.status_for_entry(e).state == svc.NOT_YET_ASSESSED


def test_artifact_signature_mismatch_on_disk_is_a_miss(env):
    e = entry("Acme", ACME)
    svc.generate(e)
    path = _governed_files(env)[0]
    data = json.loads(path.read_text(encoding="utf-8"))
    data["profile_json"]["summary"]["coverage"]["strict"] = 0.0123       # alteración fuera de banda
    path.write_text(json.dumps(data), encoding="utf-8")
    svc.reset_memory_cache()
    assert svc.status_for_entry(e).state == svc.NOT_YET_ASSESSED


# ── 11. single-flight ───────────────────────────────────────────────────────
def test_single_flight_two_concurrent_posts_run_the_engine_once(env, monkeypatch):
    e = entry("Acme", ACME)
    real, calls, started, gate = svc._engine_assess, [], threading.Event(), threading.Event()

    def slow(*a):
        calls.append(a)
        started.set()
        gate.wait(5)
        return real(*a)
    monkeypatch.setattr(svc, "_engine_assess", slow)
    out = []
    t1 = threading.Thread(target=lambda: out.append(svc.generate(e)))
    t1.start()
    assert started.wait(5)
    t2 = threading.Thread(target=lambda: out.append(svc.generate(e)))
    t2.start()
    time.sleep(0.2)                                             # t2 queda esperando el vuelo de t1
    gate.set()
    t1.join(10)
    t2.join(10)
    assert len(calls) == 1 and len(out) == 2 and {s.state for s in out} == {svc.CURRENT}


def test_single_flight_joiners_share_the_leaders_failure_instead_of_rerunning(env, monkeypatch):
    e = entry("Acme", ACME)
    calls, started, gate = [], threading.Event(), threading.Event()

    def failing(*a):
        calls.append(a)
        started.set()
        gate.wait(5)
        raise RuntimeError("falla")
    monkeypatch.setattr(svc, "_engine_assess", failing)
    out = []
    t1 = threading.Thread(target=lambda: out.append(svc.generate(e)))
    t1.start()
    assert started.wait(5)
    t2 = threading.Thread(target=lambda: out.append(svc.generate(e)))
    t2.start()
    time.sleep(0.2)
    gate.set()
    t1.join(10)
    t2.join(10)
    assert len(calls) == 1 and [s.state for s in out] == [svc.FAILED, svc.FAILED]


# ── 12. esquema desconocido / inválido ──────────────────────────────────────
def test_unknown_schema_is_incompatible_and_never_exposes_the_profile_for_rendering(env):
    e = entry("Acme", ACME)
    svc.generate(e)
    envelope = dict(svc._load_envelope("Acme", "4AM_ACME"))
    envelope["assessment_schema_version"] = "modeling_assessment.assessment/v0"
    svc._store_envelope(envelope)
    st = svc.status_for_entry(e)
    assert st.state == svc.INCOMPATIBLE_SCHEMA and st.profile is None and st.can_generate and not st.viewable
    assert svc.generate(e).state == svc.CURRENT                 # regenerar lo reemplaza


def test_structurally_invalid_profile_is_incompatible(env):
    e = entry("Acme", ACME)
    svc.generate(e)
    envelope = json.loads(json.dumps(svc._load_envelope("Acme", "4AM_ACME")))
    del envelope["profile_json"]["areas"]
    envelope["assessment_artifact_signature"] = svc._artifact_signature(envelope["profile_json"])
    svc._store_envelope(envelope)
    assert svc.status_for_entry(e).state == svc.INCOMPATIBLE_SCHEMA


# ── 14/15. exportaciones ────────────────────────────────────────────────────
def test_markdown_export_is_exactly_render_markdown_of_the_profile(env):
    st = svc.generate(entry("Acme", ACME))
    assert svc.export_markdown(st) == render_markdown(st.profile)
    assert svc.export_markdown(st).startswith("# Autoevaluación de Modelado")


def test_json_export_is_the_exact_canonical_profile_the_ui_uses(env):
    st = svc.generate(entry("Acme", ACME))
    engine_doc, _meta = shadow.assess(env.inbound, "Acme", "4AM_ACME", "General")
    assert svc.export_json(st) == ma_profile.canonical_assessment_json(engine_doc) == ma_profile.canonical_assessment_json(st.profile)
    assert json.loads(svc.export_json(st)) == st.profile
    assert svc.export_filename(st, "json") == "4AM_ACME.profile.json"


def test_invalidate_pool_index_forgets_fingerprints(env):
    svc.status_for_entry(entry("Acme", ACME))
    assert svc._index_memo
    svc.invalidate_pool_index()
    assert not svc._index_memo
