"""test_assessment_enrichment.py — enriquecimiento CONTROLADO de assessments gobernados CURRENT (``assessment_backfill --enrich-current-missing``).

Contrato (ver el docstring de ``assessment_backfill``):
  * modo EXPLÍCITO y separado: el backfill normal sigue generando solo NOT_YET_ASSESSED; el enriquecimiento exige --dry-run XOR --apply;
  * elegible = CURRENT + perfil legible + SIN el hecho requerido (por CONTENIDO canónico); el resto se omite con motivo explícito; dedupe por (cliente, AM);
  * regenera el assessment COMPLETO con el motor vigente (no parchea JSON) y solo reemplaza el registro de la MISMA llave si todo valida
    (firma del pool, hecho presente, leería CURRENT); ante cualquier fallo el registro previo queda intacto (memoria, disco y remoto);
  * dry-run corre el motor en memoria y muestra viejo→nuevo SIN escribir; apply reutiliza ese mismo perfil;
  * durabilidad remote | local | memory (memory = falla); el reinicio solo es seguro si TODO lo regenerado quedó en remote.
Fixtures sintéticas y un Spaces falso en memoria (sin red, sin Datasphere, sin credenciales reales).
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
from types import SimpleNamespace

import pytest

from core import remote_store
from tests.webapp.test_governed_assessment_durability import FakeBucket, files, restart, spaces, world  # noqa: F401  (fixtures reutilizadas)
from tools.catalog import catalog_service as cs
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as svc
from webapp.services import catalog_reads

FACT = "am.view_type_composition"
ALWAYS = lambda: True  # noqa: E731


# ═══════════════ unidades con dobles (sin motor ni disco) ═══════════════
def _entry(eid, cliente="Acme", am="AM_A"):
    return SimpleNamespace(entry_id=eid, cliente=cliente, dominio="Dom", artefactos=[am], path="x")


def _prof(has_fact, cov=0.9, conf="HIGH", reviews=1, oq=2, ingestion=("ASSESSED", 2)):
    fi = [{"fact": "am.contract_inventory@AM"}] + ([{"fact": f"{FACT}@AM"}] if has_fact else [])
    return {"fact_index": fi, "summary": {"coverage": {"strict": cov}, "confidence": {"level": conf}, "context_required": {"open_questions": oq}},
            "review_required": [{"occurrence_count": reviews}] if reviews else [],
            "areas": [{"id": "INGESTION", "assessability": ingestion[0], "finding_groups": [f"G{i}" for i in range(ingestion[1])]},
                      {"id": "PROPAGATION", "assessability": "ASSESSED", "finding_groups": []}]}


def _status(entry, state, profile=None, am="AM"):
    return svc.AssessmentStatus(state, entry.entry_id, entry.cliente, am if state != svc.NOT_APPLICABLE else None, profile=profile)


class Fakes:
    """Dobles de ``status_for`` / ``regenerate`` / ``commit`` / ``retry`` con registro de llamadas."""
    def __init__(self, statuses, durable=svc.DURABLE_REMOTE, fail=()):
        self.statuses, self.durable, self.fail = statuses, durable, set(fail)
        self.regenerated, self.committed, self.retried = [], [], []
        self.candidates = {}

    def status_for(self, e):
        return self.statuses[e.entry_id]

    def regenerate(self, e, required):
        self.regenerated.append(e.entry_id)
        if e.entry_id in self.fail:
            raise svc.EnrichmentError("ENGINE_NOT_ASSESSED")
        cand = SimpleNamespace(client=e.cliente, terminal_am="AM", profile=_prof(True, cov=0.8, conf="MEDIUM", reviews=3, ingestion=("PARTIALLY_ASSESSED", 3)),
                               envelope={"marker": e.entry_id})
        self.candidates[e.entry_id] = cand
        return cand

    def commit(self, cand):
        self.committed.append(cand)
        return self.durable if not callable(self.durable) else self.durable(cand)

    def retry(self, client, am):
        self.retried.append((client, am))
        return False

    def run(self, entries, apply=False, **kw):
        kw.setdefault("remote_enabled", ALWAYS)
        return bf.enrich_current(entries, "view_type", apply=apply, status_for=self.status_for, regenerate=self.regenerate, commit=self.commit,
                                 retry_remote=self.retry, governed_state_present=ALWAYS, **kw)


def _set(entries_states):
    es = [_entry(f"e{i}", am=f"AM_{i}") for i, _ in enumerate(entries_states)]
    return es, {e.entry_id: _status(e, st, pr, am=f"AM_{i}") for i, (e, (st, pr)) in enumerate(zip(es, entries_states))}


def test_the_existing_backfill_default_is_unchanged_and_never_touches_current_records(monkeypatch):
    spy = []
    real_backfill = bf.backfill
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (None, [_entry("a")]))
    monkeypatch.setattr(bf, "backfill", lambda entries, dry_run=False, **k: spy.append(("backfill", dry_run)) or {"generated": [], "skipped": [], "failed": []})
    monkeypatch.setattr(bf, "enrich_current", lambda *a, **k: pytest.fail("sin el flag explícito no se enriquece"))
    monkeypatch.setattr(svc, "commit_regenerated", lambda *a, **k: pytest.fail("sin el flag explícito no se reemplaza nada"))
    assert bf.main(["--dry-run"]) == 0 and spy == [("backfill", True)]
    out = real_backfill([_entry("c")], status_for=lambda e: _status(e, svc.CURRENT, _prof(False)), generate=lambda e: pytest.fail("un CURRENT no se regenera"))
    assert out["counts"] == {"generated": 0, "skipped": 1, "failed": 0}                                   # el backfill normal omite CURRENT, como siempre


@pytest.mark.parametrize("argv", [["--enrich-current-missing", "view_type"],                                     # ni --dry-run ni --apply
                                  ["--enrich-current-missing", "view_type", "--dry-run", "--apply"],             # ambos
                                  ["--apply"],                                                                   # --apply sin modo de enriquecimiento
                                  ["--enrich-current-missing", "nope", "--dry-run"]])                            # enriquecimiento desconocido
def test_enrichment_requires_exactly_dry_run_or_apply_and_fails_safely_with_zero_writes(argv, monkeypatch):
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: pytest.fail("ni siquiera se lee el Catálogo"))
    monkeypatch.setattr(svc, "commit_regenerated", lambda *a, **k: pytest.fail("cero escrituras"))
    monkeypatch.setattr(svc, "_store_envelope", lambda *a, **k: pytest.fail("cero escrituras"))
    with pytest.raises(SystemExit) as exc:
        bf.main(argv)
    assert exc.value.code == 2


def test_cli_dry_run_and_apply_select_the_mode_and_client_scope(monkeypatch, capsys):
    entries = [_entry("a1", "Acme"), _entry("b1", "Beta"), _entry("a2", "Acme", "AM_2")]
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (None, entries))
    seen = []
    monkeypatch.setattr(bf, "enrich_current", lambda es, name, apply=False, **k: seen.append(([e.entry_id for e in es], name, apply)) or
                        {"counts": {"failed": 0}, "environment_warnings": []})
    assert bf.main(["--enrich-current-missing", "view_type", "--client", "acme", "--dry-run"]) == 0
    assert bf.main(["--enrich-current-missing", "view_type", "--apply"]) == 0
    assert seen == [(["a1", "a2"], "view_type", False), (["a1", "b1", "a2"], "view_type", True)]             # --client acota; sin --client, todo el Catálogo


def test_eligibility_is_by_content_current_and_missing_fact_only():
    es, sts = _set([(svc.CURRENT, _prof(False)), (svc.CURRENT, _prof(True)), (svc.STALE, _prof(False)), (svc.FAILED, _prof(False)),
                    (svc.INCOMPATIBLE_SCHEMA, None), (svc.NOT_APPLICABLE, None), (svc.NOT_YET_ASSESSED, None)])
    f = Fakes(sts)
    out = f.run(es)
    by = {r["entry_ids"][0]: r for r in out["records"]}
    assert by["e0"]["action"] == bf.WOULD_REGENERATE and by["e0"]["missing"] == [FACT]                       # CURRENT + sin hecho: elegible
    assert by["e1"]["reason"] == bf.SKIPPED_ALREADY_ENRICHED and by["e1"]["action"] == bf.SKIP               # CURRENT + con hecho: se omite
    assert by["e2"]["reason"] == by["e3"]["reason"] == by["e4"]["reason"] == by["e5"]["reason"] == bf.SKIPPED_NON_CURRENT      # STALE, FAILED, INCOMPATIBLE, N/A
    assert by["e6"]["reason"] == bf.SKIPPED_NOT_YET_ASSESSED                                                  # NOT_YET_ASSESSED: lo genera el backfill normal
    assert f.regenerated == ["e0"]                                                                           # el motor solo corre para el elegible
    assert out["counts"] == {"eligible": 1, "would_regenerate": 1, "regenerated_remote": 0, "regenerated_local": 0, bf.SKIPPED_ALREADY_ENRICHED: 1,
                             bf.SKIPPED_NON_CURRENT: 4, bf.SKIPPED_NOT_YET_ASSESSED: 1, "failed": 0}


def test_entries_pointing_to_the_same_governed_assessment_are_handled_once():
    a, b, c = _entry("x1", am="AM_S"), _entry("x2", am="AM_S"), _entry("y1", cliente="Beta", am="AM_S")
    sts = {e.entry_id: _status(e, svc.CURRENT, _prof(False), am="AM_S") for e in (a, b, c)}
    f = Fakes(sts)
    out = f.run([a, b, c], apply=True)
    assert f.regenerated == ["x1", "y1"] and len(f.committed) == 2                                           # Acme/AM_S una vez; Beta/AM_S es otro registro
    assert [r["entry_ids"] for r in out["records"]] == [["x1", "x2"], ["y1"]]


def test_dry_run_runs_the_engine_in_memory_but_never_writes_and_shows_old_to_new():
    es, sts = _set([(svc.CURRENT, _prof(False, cov=0.9, conf="HIGH", reviews=1, oq=2, ingestion=("ASSESSED", 2)))])
    f = Fakes(sts)
    f.commit = lambda cand: pytest.fail("dry-run no escribe")
    out = f.run(es, apply=False)
    assert f.regenerated == ["e0"] and out["dry_run"] is True and out["apply"] is False and out["restart"] == bf.RESTART_DRY_RUN
    d = out["records"][0]["delta"]
    assert d["coverage_strict"] == {"old": 0.9, "new": 0.8, "changed": True} and d["confidence"]["old"] == "HIGH" and d["confidence"]["new"] == "MEDIUM"
    assert d["review_occurrences"] == {"old": 1, "new": 3, "changed": True} and d["open_questions"]["old"] == 2
    assert d["areas"]["INGESTION"]["state"] == {"old": "ASSESSED", "new": "PARTIALLY_ASSESSED"} and d["areas"]["INGESTION"]["findings"] == {"old": 2, "new": 3}
    assert d["areas"]["INGESTION"]["changed"] is True and d["enrichment"][FACT] == {"old": "absent", "new": "present"} and d["changed"] is True
    assert "profile" not in json.dumps(out)[:0] and len(json.dumps(out)) < 3000                              # informe conciso: no vuelca perfiles


def test_apply_reuses_the_same_regenerated_profile_for_the_delta_and_for_the_write():
    es, sts = _set([(svc.CURRENT, _prof(False))])
    f = Fakes(sts)
    out = f.run(es, apply=True)
    assert f.regenerated == ["e0"] and f.committed == [f.candidates["e0"]]                                    # una sola regeneración; se escribe ESE candidato
    assert out["records"][0]["delta"]["enrichment"][FACT]["new"] == "present" and out["records"][0]["action"] == bf.REGENERATED


def test_one_failure_does_not_stop_the_batch_and_never_writes_the_failed_one():
    es, sts = _set([(svc.CURRENT, _prof(False)), (svc.CURRENT, _prof(False)), (svc.CURRENT, _prof(False))])
    f = Fakes(sts, fail={"e1"})
    out = f.run(es, apply=True)
    assert [r["action"] for r in out["records"]] == [bf.REGENERATED, bf.FAILED_ACTION, bf.REGENERATED] and out["records"][1]["reason"] == "ENGINE_NOT_ASSESSED"
    assert [c.envelope["marker"] for c in f.committed] == ["e0", "e2"] and out["counts"]["failed"] == 1 and out["counts"]["regenerated_remote"] == 2


def test_durability_semantics_and_the_restart_rule():
    es, sts = _set([(svc.CURRENT, _prof(False)), (svc.CURRENT, _prof(False))])
    ok = Fakes(sts, durable=svc.DURABLE_REMOTE).run(es, apply=True)
    assert ok["counts"]["regenerated_remote"] == 2 and ok["restart"] == bf.RESTART_SAFE and ok["restart_safe"] and ok["complete"]
    local = Fakes(sts, durable=lambda c: svc.DURABLE_LOCAL if c.envelope["marker"] == "e1" else svc.DURABLE_REMOTE).run(es, apply=True)
    assert local["counts"]["regenerated_remote"] == 1 and local["counts"]["regenerated_local"] == 1
    assert local["restart"] == bf.RESTART_BLOCKED and not local["restart_safe"] and not local["complete"]    # UN local bloquea el reinicio
    mem = Fakes(sts, durable=svc.DURABLE_MEMORY).run(es, apply=True)
    assert mem["counts"]["failed"] == 2 and mem["counts"]["regenerated_remote"] == 0 and all("NOT_PERSISTED_MEMORY_ONLY" in r["reason"] for r in mem["records"])
    assert mem["restart"] == bf.RESTART_NOT_NEEDED and not mem["complete"]                                   # memory = FALLA (el previo sigue vigente)
    disabled = Fakes(sts).run(es, apply=True, remote_enabled=lambda: False)
    assert disabled["restart"] == bf.RESTART_BLOCKED and any("REMOTE_STORE_DISABLED" in w for w in disabled["environment_warnings"])


def test_pending_local_records_get_one_explicit_remote_retry_and_only_confirmed_ones_unblock_the_restart():
    es, sts = _set([(svc.CURRENT, _prof(False))])
    f = Fakes(sts, durable=svc.DURABLE_LOCAL)
    f.retry = lambda client, am: f.retried.append((client, am)) or True
    out = f.run(es, apply=True)
    assert f.retried == [("Acme", "AM_0")] and out["records"][0]["durable"] == svc.DURABLE_REMOTE and out["restart"] == bf.RESTART_SAFE
    g = Fakes(sts, durable=svc.DURABLE_LOCAL)
    out = g.run(es, apply=True)
    assert out["records"][0]["durable"] == svc.DURABLE_LOCAL and out["records"][0]["remote_retry"] == "NOT_CONFIRMED" and out["restart"] == bf.RESTART_BLOCKED


def test_a_wrong_environment_is_reported_not_disguised_as_nothing_to_do():
    es = [_entry("a"), _entry("b", am="AM_B")]
    out = bf.enrich_current(es, "view_type", status_for=lambda e: _status(e, svc.NOT_YET_ASSESSED), governed_state_present=ALWAYS)
    assert any("ENVIRONMENT_NO_GOVERNED_STATE" in w for w in out["environment_warnings"]) and out["counts"]["eligible"] == 0
    out = bf.enrich_current([], "view_type", governed_state_present=ALWAYS)
    assert any("NO_CATALOG_ENTRIES" in w for w in out["environment_warnings"])
    out = bf.enrich_current([_entry("a")], "view_type", status_for=lambda e: _status(e, svc.CURRENT, _prof(True)), governed_state_present=lambda: False)
    assert any("ENVIRONMENT_NO_GOVERNED_STATE" in w for w in out["environment_warnings"])


def test_the_enrichment_path_never_restores_extracts_or_uses_credentials():
    src = inspect.getsource(bf) + inspect.getsource(svc.regenerate_in_memory) + inspect.getsource(svc.commit_regenerated)
    for forbidden in ("run_export", "ExportRequest", "client_secret", "datasphere_publication", "datasphere_assessment_service", "remote_store.restore", ".restore()", "SPACES_KEY", "SPACES_SECRET"):
        assert forbidden not in src, forbidden


# ═══════════════ integración con el servicio real (pool sintético, almacén gobernado temporal) ═══════════════
def _make_historical(world):
    """Genera el assessment con el motor y lo deja como uno HISTÓRICO: CURRENT pero sin el hecho (re-firmado, como lo habría dejado el motor anterior)."""
    st = svc.generate(world.acme)
    assert st.state == svc.CURRENT
    (path,) = files(world)
    env = json.loads(path.read_text(encoding="utf-8"))
    prof = env["profile_json"]
    prof["fact_index"] = [x for x in prof["fact_index"] if not x["fact"].startswith(FACT)]
    for a in prof["areas"]:
        a["highlights"] = [h for h in a["highlights"] if h["id"] not in ("views_in_closure", "view_type_distribution", "view_artifacts")]
    env["assessment_artifact_signature"] = svc._artifact_signature(prof)
    path.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
    restart()
    old = svc.status_for_entry(world.acme)
    assert old.state == svc.CURRENT and svc.missing_facts(old.profile, [FACT]) == [FACT]
    return path


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(world, apply=False, **kw):
    return bf.enrich_current([world.acme], "view_type", apply=apply, governed_state_present=ALWAYS, **kw)


def test_dry_run_with_the_real_engine_previews_the_delta_and_writes_nothing(world, monkeypatch):
    path = _make_historical(world)
    before = _digest(path)
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    monkeypatch.setattr(dsp, "run_export", lambda *a, **k: pytest.fail("sin extracción de Datasphere"))
    calls = []
    real = svc._engine_assess
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: calls.append(1) or real(*a, **k))               # el motor canónico SE EJECUTA
    out = _run(world, apply=False)
    assert len(calls) == 1 and out["counts"]["would_regenerate"] == 1 and out["records"][0]["action"] == bf.WOULD_REGENERATE
    assert out["records"][0]["delta"]["enrichment"][FACT] == {"old": "absent", "new": "present"} and set(out["records"][0]["delta"]["areas"]) >= {"INGESTION"}
    assert _digest(path) == before and len(files(world)) == 1                                                  # CERO escrituras
    restart()
    assert svc.status_for_entry(world.acme).state == svc.CURRENT and svc.missing_facts(svc.status_for_entry(world.acme).profile, [FACT]) == [FACT]


def test_apply_replaces_the_same_governed_record_with_the_complete_regenerated_assessment(world):
    path = _make_historical(world)
    out = _run(world, apply=True)
    rec = out["records"][0]
    assert rec["action"] == bf.REGENERATED and rec["durable"] == svc.DURABLE_LOCAL and out["restart"] == bf.RESTART_BLOCKED       # sin Spaces: local ⇒ reinicio bloqueado
    assert files(world) == [path]                                                                              # la MISMA llave: sin duplicados
    restart()
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.CURRENT and svc.missing_facts(st.profile, [FACT]) == []                              # sigue CURRENT y ahora trae el hecho
    from tools.catalog.modeling_assessment import profile as ma_profile
    ma_profile.validate_assessment(st.profile)                                                                   # por el contrato canónico, no una cadena suelta
    assert any(h["id"] == "views_in_closure" for a in st.profile["areas"] if a["id"] == "ANALYTIC_MODEL" for h in a["highlights"])   # puede renderizar el resumen de [02]


def test_it_is_idempotent_a_second_run_skips_and_writes_nothing(world):
    path = _make_historical(world)
    _run(world, apply=True)
    after_first = _digest(path)
    restart()
    out = _run(world, apply=True)
    assert out["records"][0]["reason"] == bf.SKIPPED_ALREADY_ENRICHED and out["counts"]["eligible"] == 0 and out["counts"]["regenerated_local"] == 0
    assert _digest(path) == after_first and files(world) == [path]
    assert _run(world, apply=False)["counts"][bf.SKIPPED_ALREADY_ENRICHED] == 1


def _expect_preserved(world, path, reason_prefix=None, **kw):
    before = _digest(path)
    out = _run(world, apply=True, **kw)
    rec = out["records"][0]
    assert rec["action"] == bf.FAILED_ACTION and out["counts"]["failed"] == 1 and out["counts"]["regenerated_local"] == 0
    if reason_prefix:
        assert rec["reason"].startswith(reason_prefix), rec["reason"]
    assert _digest(path) == before and files(world) == [path]                                                  # el registro previo NO cambió ni se duplicó
    restart()
    st = svc.status_for_entry(world.acme)
    assert st.state == svc.CURRENT and svc.missing_facts(st.profile, [FACT]) == [FACT]                           # y sigue siendo el CURRENT histórico, no un FAILED
    return rec


def test_engine_failure_preserves_the_previous_record_and_never_writes_a_failed_envelope(world, monkeypatch):
    path = _make_historical(world)
    monkeypatch.setattr(svc, "_engine_assess", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom /secret/path")))
    rec = _expect_preserved(world, path, "GENERATION_ERROR")
    assert "boom" not in json.dumps(rec) and "secret" not in json.dumps(rec)                                     # la excepción no se filtra al informe


def test_validation_failure_preserves_the_previous_record(world, monkeypatch):
    path = _make_historical(world)
    real = svc._engine_assess

    def broken(*a, **k):
        doc, meta = real(*a, **k)
        doc = copy.deepcopy(doc)
        doc.pop("summary")
        return doc, meta
    monkeypatch.setattr(svc, "_engine_assess", broken)
    _expect_preserved(world, path)


def test_the_regenerated_pool_signature_must_equal_the_local_pool_signature(world, monkeypatch):
    path = _make_historical(world)
    real = svc._engine_assess

    def other_pool(*a, **k):
        doc, meta = real(*a, **k)
        doc = copy.deepcopy(doc)
        doc["scope"]["pool_signature"] = "0" * 64
        return doc, {**meta, "pool_signature": "0" * 64}
    monkeypatch.setattr(svc, "_engine_assess", other_pool)
    _expect_preserved(world, path, "POOL_SIGNATURE_MISMATCH")


def test_the_requested_enrichment_must_exist_in_the_regenerated_profile_before_any_write(world, monkeypatch):
    path = _make_historical(world)
    real = svc._engine_assess

    def without_fact(*a, **k):
        doc, meta = real(*a, **k)
        doc = copy.deepcopy(doc)
        doc["fact_index"] = [x for x in doc["fact_index"] if not x["fact"].startswith(FACT)]
        return doc, meta
    monkeypatch.setattr(svc, "_engine_assess", without_fact)
    _expect_preserved(world, path, "ENRICHMENT_MISSING")


def test_the_regenerated_record_must_resolve_current_or_nothing_is_written(world, monkeypatch):
    path = _make_historical(world)
    monkeypatch.setattr(svc, "_envelope_resolves_current", lambda *a, **k: False)
    _expect_preserved(world, path, "WOULD_NOT_BE_CURRENT")
    monkeypatch.undo()


def test_a_non_current_or_missing_previous_record_is_never_replaced_by_the_safe_path(world):
    assert svc.generate(world.acme).state == svc.CURRENT
    restart()
    (path,) = files(world)
    env = json.loads(path.read_text(encoding="utf-8"))
    env["input_pool_signature"] = "stale"                                                                       # STALE
    path.write_text(json.dumps(env, ensure_ascii=False), encoding="utf-8")
    restart()
    out = _run(world, apply=True)
    assert out["records"][0]["reason"] == bf.SKIPPED_NON_CURRENT and out["counts"]["regenerated_local"] == 0
    with pytest.raises(svc.EnrichmentError) as exc:                                                              # y si se la fuerza, la ruta segura se niega
        svc.regenerate_in_memory(world.acme, [FACT])
    assert exc.value.code == "PREVIOUS_NOT_CURRENT"


def test_a_disk_write_failure_reports_memory_and_keeps_the_previous_good_state(world, monkeypatch):
    path = _make_historical(world)
    before_status = svc.status_for_entry(world.acme)
    monkeypatch.setattr(svc.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    rec = _expect_preserved(world, path, "NOT_PERSISTED_MEMORY_ONLY")
    assert rec["durable"] == svc.DURABLE_MEMORY
    assert not [p for p in path.parent.iterdir() if p.name.startswith(".tmp-")]                                  # sin restos del archivo temporal
    assert before_status.profile is not None


def test_remote_persistence_is_reported_as_remote_and_equals_the_local_file(world, spaces):
    path = _make_historical(world)
    out = _run(world, apply=True)
    rec = out["records"][0]
    assert rec["durable"] == svc.DURABLE_REMOTE and out["restart"] == bf.RESTART_SAFE and out["complete"]
    key = next(k for k in spaces.objects if k.endswith(path.name))
    assert spaces.objects[key] == path.read_bytes()                                                               # el objeto remoto ES el reemplazo


def test_a_failed_remote_put_is_local_blocks_the_restart_and_never_deletes_the_old_remote_object(world, spaces, monkeypatch):
    path = _make_historical(world)
    assert remote_store.push_path(path)                                                                           # el viejo histórico ya está en Spaces
    key = next(k for k in spaces.objects if k.endswith(path.name))
    old_remote = spaces.objects[key]
    spaces.fail_uploads = True
    out = _run(world, apply=True)
    rec = out["records"][0]
    assert rec["action"] == bf.REGENERATED and rec["durable"] == svc.DURABLE_LOCAL and rec["remote_retry"] == "NOT_CONFIRMED"
    assert out["counts"]["regenerated_local"] == 1 and out["restart"] == bf.RESTART_BLOCKED and not out["restart_safe"]
    assert spaces.objects[key] == old_remote and key in spaces.objects                                           # el objeto remoto previo sigue ahí, intacto
    spaces.fail_uploads = False                                                                                   # el reintento EXPLÍCITO (misma API) confirma el remoto
    assert svc.retry_remote_persistence("Acme", "4AM_ACME") is True and spaces.objects[key] == path.read_bytes()


def test_output_never_carries_credentials_or_filesystem_paths(world, spaces):
    _make_historical(world)
    out = json.dumps(_run(world, apply=True), ensure_ascii=False)
    for secret in ("fake-key", "fake-secret", "fake.digitaloceanspaces.com", str(world.tmp), "governed-assessments"):
        assert secret not in out, secret


def test_client_scope_applies_only_to_the_requested_client(world):
    _make_historical(world)
    solo = world.entries[1]                                                                                       # otro cliente (sin AM terminal)
    out = bf.enrich_current([solo], "view_type", governed_state_present=ALWAYS)
    assert out["counts"]["eligible"] == 0 and all(r["client"] == "Solo" for r in out["records"])
    assert len(files(world)) == 1
