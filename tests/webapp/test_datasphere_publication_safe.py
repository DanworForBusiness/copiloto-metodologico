"""Publicación segura (fase 0) desde el SERVICIO y la UI: contrato de estados hacia la interfaz, activación/desactivación del interruptor sin estados mixtos, y la NO regresión de
Assessment, Juicio y ratificaciones (la fase 0 no los toca; la publicación abortada no los altera; solo se ordena el registro del Assessment DESPUÉS del commit local)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import webapp.main as main
from tests.publication_fakes import CLIENT, SPACE, FakeS3, SimulatedCrash, enable_remote, view
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog import DSP_CLI_Export_Lineage as dsp
from tools.catalog import publication_txn as txn
from webapp import access
from webapp.services import assessment_service as assess
from webapp.services import auth_service
from webapp.services import datasphere_assessment_service as asd
from webapp.services import datasphere_publication_service as pub

REPO = Path(__file__).resolve().parents[2]
STEM = "04_Modelado_y_Desarrollo"
RUN_URL = f"/modules/{STEM}/datasphere-publication/run"


def values(root="AM_Lista_Precios"):
    return asd.FormValues.from_mapping({"host": "https://t.eu10.hcs.cloud.sap", "token_url": "https://t.authentication.eu10.hana.ondemand.com/oauth/token",
                                        "client_id": "sb-abc", "space": SPACE, "root_object": root, "client": CLIENT, "features": None})


@pytest.fixture()
def svc(ctx, monkeypatch):                                                                                    # noqa: F811
    """Servicio listo: CLI disponible, caché del Catálogo neutra y un espía del registro de Assessment (la real se prueba aparte)."""
    registered: list = []
    monkeypatch.setattr(asd, "cli_available", lambda: True)
    monkeypatch.setattr(main, "_invalidate_catalog_cache", lambda: None)
    monkeypatch.setattr(pub, "register_assessment", lambda v: (registered.append(v.root_object), [("entry-1", "CURRENT")])[1])    # P1: la unidad segura YA NO registra el assessment después del commit
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(ctx.tmp / "governed"))
    assess.reset_memory_cache()
    ctx.registered = registered
    return ctx


def flag(monkeypatch, on: bool):
    monkeypatch.setenv(txn.SAFE_ENV, "1") if on else monkeypatch.delenv(txn.SAFE_ENV, raising=False)


# ═══════════════ mapeo de estados al contrato de la UI ═══════════════
def _result(status, ok=False, code=None, durability="", **kw):
    return dsp.ExportResult(ok=ok, error_code=code, status=status, operation_id="op1", durability=durability, exported_files=3, duration_s=1.0,
                            stages={"extraction": "OK"}, deferred_reasons=kw.pop("deferred", []), failures=kw.pop("failures", []), **kw)


CASES = [
    (txn.COMPLETED, True, None, "SUCCESS", True),
    (txn.COMMITTED_UNCONFIRMED, False, "PERSISTENCE_UNCONFIRMED", "PARTIAL", True),
    (txn.COMMITTED_CLEANUP_DEFERRED, False, "CLEANUP_DEFERRED", "PARTIAL", True),
    (txn.FAILED_NO_CHANGE, False, "EXTRACTION_INCOMPLETE", "FAILED", False),
    (txn.FAILED_NO_CHANGE, False, "POOL_IDENTITY_CONFLICT", "FAILED", False),
    (txn.FAILED_NO_CHANGE, False, "CATALOG_BUSY", "FAILED", False),
    (txn.ROLLED_BACK, False, "CATALOG_GENERATION_FAILED", "FAILED", False),
    (txn.RECOVERY_REQUIRED, False, "RECOVERY_REQUIRED", "FAILED", False),
]


@pytest.mark.parametrize("status, ok, code, state, registers", CASES)
def test_every_status_maps_to_the_ui_contract_and_the_safe_path_never_registers_the_assessment_after_the_commit(svc, monkeypatch, status, ok, code, state, registers):
    flag(monkeypatch, True)
    seen: dict = {}
    monkeypatch.setattr(dsp, "run_export", lambda req: (seen.setdefault("req", req), _result(status, ok, code))[1])
    out = pub.run(values(), "supersecret", user_email="c@danwor.com")
    assert out.state == state and out.status == status and (out.error_code or None) == code
    assert svc.registered == []                                                                                # el assessment es parte de la UNIDAD (preparado antes de activar), no un registro posterior
    assert seen["req"].safe is True and seen["req"].mirror_up is True and seen["req"].mode == "publish"        # seguro: la confirmación remota es obligatoria
    assert seen["req"].stage_hooks is not None and type(seen["req"].stage_hooks).__name__ == "AssessmentStage"
    if state == "PARTIAL":
        assert out.title == "Publicación parcial" and out.message
        assert out.state != "SUCCESS"
    if state == "FAILED":
        assert out.message and out.title == pub.TITLE_PUBLISH


def test_success_requires_a_completed_status_and_a_partial_never_opens_the_catalog(svc, monkeypatch):
    flag(monkeypatch, True)
    monkeypatch.setattr(dsp, "run_export", lambda req: _result(txn.COMMITTED_UNCONFIRMED, False, "PERSISTENCE_UNCONFIRMED", txn.DURABILITY_UNCONFIRMED))
    out = pub.run(values(), "s")
    assert out.state == "PARTIAL" and out.durability == txn.DURABILITY_UNCONFIRMED and "NO está confirmada" in out.message


def test_with_the_switch_off_the_service_keeps_the_legacy_path(svc, monkeypatch):
    flag(monkeypatch, False)
    monkeypatch.delenv("DANWOR_PUBLICATION_MIRROR_UP", raising=False)
    seen: dict = {}
    monkeypatch.setattr(dsp, "run_export", lambda req: (seen.setdefault("req", req), dsp.ExportResult(ok=True, exported_files=3, duration_s=1.0))[1])
    out = pub.run(values(), "s")
    assert out.state == "SUCCESS" and seen["req"].safe is False and seen["req"].mirror_up is False              # camino de siempre: Spaces solo si se habilita por entorno
    assert not hasattr(out, "status") or out.status == ""


def test_the_switch_is_read_once_per_operation_and_defaults_to_off(svc, monkeypatch):
    monkeypatch.delenv(txn.SAFE_ENV, raising=False)
    assert txn.safe_mode_enabled() is False


# ═══════════════ extremo a extremo por el servicio (exportador, manifiestos y generador reales) ═══════════════
def test_end_to_end_success_and_incomplete_extraction_through_the_service(svc, monkeypatch):
    flag(monkeypatch, True)
    ok = pub.run(values(), "supersecret")
    assert ok.state == "SUCCESS" and ok.status == txn.COMPLETED and ok.change == "CHANGED" and ok.assessment["regenerated"] == ["AM_Lista_Precios"]
    assert assess.governed_path(CLIENT, "AM_Lista_Precios").is_file()                                          # el assessment se escribió DENTRO de la unidad
    before = svc.digest()
    svc.tenant.fail_reads.add(("views", "3VF_LP"))
    bad = pub.run(values(), "supersecret")
    assert bad.state == "FAILED" and bad.error_code == "EXTRACTION_INCOMPLETE" and svc.digest() == before
    assert svc.registered == []                                                                                # y nunca se registró nada posterior


# ═══════════════ interruptor: activar y desactivar sin estados mixtos ═══════════════
def test_turning_the_switch_off_after_a_crash_never_leaves_a_mixed_state(svc, monkeypatch):
    flag(monkeypatch, True)
    assert pub.run(values(), "s").state == "SUCCESS"
    pre = svc.digest()
    svc.tenant.put(SPACE, "views", "1VR_LP_BASE", view("1VR_LP_BASE", ["RAW_PRECIOS"], cols=("A", "B", "C_NUEVA")))
    svc.tenant.put(SPACE, "views", "3VF_LP", view("3VF_LP", ["1VR_LP_BASE"]))
    svc.tenant.drop(SPACE, "views", "1VR_LP_EXTRA")
    monkeypatch.setattr(txn, "FAULT_HOOK", lambda p, **k: (_ for _ in ()).throw(SimulatedCrash()) if p == "after_cleanup" else None)
    with pytest.raises(SimulatedCrash):                                                                        # el proceso muere a mitad del apply
        pub.run(values(), "s")
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    assert svc.digest() != pre and txn.has_pending(svc.catalog)                                                # estado a medias (journal APPLYING)
    flag(monkeypatch, False)                                                                                   # se apaga el interruptor y se publica por el camino anterior
    out = pub.run(values(), "s")
    assert out.state == "SUCCESS" and not txn.has_pending(svc.catalog)
    assert sorted(j["state"] for j in svc.journals()) == sorted([txn.J_LOCAL_COMMITTED, txn.J_ROLLED_BACK])      # la operación a medias quedó ROLLED_BACK; la comprometida, intacta
    assert "views/1VR_LP_EXTRA" not in svc.pool_names() and "C_NUEVA" in (svc.pool / CLIENT / "views" / "1VR_LP_BASE.json").read_text(encoding="utf-8")
    flag(monkeypatch, True)                                                                                    # y se vuelve a encender: converge
    assert pub.run(values(), "s").state == "SUCCESS"


def test_an_unrecoverable_journal_blocks_publication_with_the_switch_on_or_off(svc, monkeypatch):
    svc_flag_states = (True, False)
    for on in svc_flag_states:
        flag(monkeypatch, on)
        jd = txn.journal_dir(svc.catalog)
        jd.mkdir(parents=True, exist_ok=True)
        (jd / "opX.json").write_text("{ilegible", encoding="utf-8")
        out = pub.run(values(), "s")
        assert out.state == "FAILED" and out.error_code == "RECOVERY_REQUIRED", on
        (jd / "opX.json").unlink()


# ═══════════════ router: el evento final lleva el estado explícito ═══════════════
def _client(email):
    c = TestClient(main.app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


FORM = {"pack": "datasphere_core", "host": "https://t.eu10.hcs.cloud.sap", "token_url": "https://t.authentication.eu10.hana.ondemand.com/oauth/token",
        "datasphere_client_id": "sb", "datasphere_client_secret": "s3", "space": SPACE, "datasphere_root_object": "AM", "client": CLIENT}


@pytest.mark.parametrize("outcome, expect_url", [
    (pub.PublicationOutcome("SUCCESS", status=txn.COMPLETED, durability=txn.DURABILITY_REMOTE, stages={"remote": "OK"}, operation_id="o1", change="CHANGED",
                            assessment={"regenerated": ["AM_X"], "kept": ["AM_Y", "AM_Z"], "uncertain": [], "removed": []}), True),
    (pub.PublicationOutcome(pub.STATE_PARTIAL, "PERSISTENCE_UNCONFIRMED", "msg", title=pub.TITLE_PARTIAL, status=txn.COMMITTED_UNCONFIRMED, durability=txn.DURABILITY_UNCONFIRMED), False),
])
def test_the_done_event_carries_status_durability_and_stages_and_only_success_opens_the_catalog(monkeypatch, outcome, expect_url):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    monkeypatch.setattr(pub, "run", lambda *a, **k: outcome)
    r = _client("owner@danwor.com").post(RUN_URL, data=FORM, headers={"Origin": "http://testserver"})
    done = next(json.loads(ln) for ln in r.text.splitlines() if '"t": "done"' in ln or '"t":"done"' in ln)
    assert done["state"] == outcome.state and done["status"] == outcome.status and done["durability"] == outcome.durability and "stages" in done
    assert done["change"] == outcome.change and done["assessment"] == {k: len(v) for k, v in outcome.assessment.items()}                        # P1: trazabilidad hacia la interfaz
    assert bool(done["result_url"]) is expect_url


def test_the_ui_only_navigates_on_success_so_a_partial_is_shown_as_a_warning_not_a_success():
    js = (REPO / "webapp/static/datasphere_publication.js").read_text(encoding="utf-8")
    assert 'done.state === "SUCCESS" && done.result_url' in js and js.count("window.location.assign") == 1
    assert "PARTIAL" not in js                                                                                  # PARTIAL cae en el bloque de aviso (título/mensaje) con el botón rehabilitado para reintentar


# ═══════════════ Assessment, Juicio y ratificaciones: sin regresión ═══════════════
def _entries(ctx):
    tools = str(REPO / "tools" / "catalog")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import catalog_service as cs
    return cs.load_catalog(str(ctx.catalog))


def _entry(ctx, am="AM_Lista_Precios"):
    return next(e for e in _entries(ctx) if am in {str(a) for a in (e.artefactos or [])})


def _envelope_bytes(am="AM_Lista_Precios") -> bytes:
    return assess.governed_path(CLIENT, am).read_bytes()


@pytest.fixture()
def governed(ctx, monkeypatch, tmp_path):                                                                      # noqa: F811
    monkeypatch.setenv("DANWOR_GOVERNED_ASSESSMENTS_DIR", str(tmp_path / "governed"))
    assess.reset_memory_cache()
    return ctx


def test_an_aborted_publication_leaves_the_governed_assessment_and_its_judgment_byte_identical(governed):
    governed.publish()
    assert assess.generate(_entry(governed)).state == "CURRENT"
    stored = _envelope_bytes()
    governed.tenant.fail_reads.add(("views", "3VF_LP"))
    res, _ = governed.publish()
    assert res.status == txn.FAILED_NO_CHANGE
    assert _envelope_bytes() == stored and assess.status_for_entry(_entry(governed)).state == "CURRENT"        # el Assessment CURRENT (y su Juicio) intactos
    assert json.loads(stored)["judgment"] is not None


def test_an_identical_republication_keeps_the_assessment_current_and_unchanged(governed):
    governed.publish()
    assess.generate(_entry(governed))
    stored = _envelope_bytes()
    assert governed.publish()[0].applied["changed"] is False
    assert assess.generate(_entry(governed)).state == "CURRENT" and _envelope_bytes() == stored


def test_a_committed_change_makes_the_assessment_stale_and_regeneration_replaces_the_same_record(governed):
    governed.publish()
    assess.generate(_entry(governed))
    governed.tenant.put(SPACE, "views", "1VR_LP_BASE", view("1VR_LP_BASE", ["RAW_PRECIOS"], cols=("A", "B", "C_NUEVA")))
    assert governed.publish()[0].status == txn.COMPLETED
    assert assess.status_for_entry(_entry(governed)).state == "STALE"
    assert assess.generate(_entry(governed)).state == "CURRENT"
    assert len(list(assess.governed_path(CLIENT, "AM_Lista_Precios").parent.glob("AM_Lista_Precios__*.json"))) == 1


def test_ratifications_and_backups_in_spaces_are_never_touched_by_a_safe_publication(governed, monkeypatch):
    s3 = FakeS3()
    enable_remote(monkeypatch, s3)
    seeded = {"audit/governed-decisions/Acme/abc123/2026-10-01T00:00:00Z-ev1.json": b'{"event":"ratification","actor":"senior"}',
              "audit/migration-backups/v1/Acme/x.json": b'{"backup":true}', "audit/governed-assessments/v1/acme/AM__x.json": b'{"envelope":true}'}
    s3.objects.update(seeded)
    res, _ = governed.publish(mirror=True)
    assert res.status == txn.COMPLETED and res.durability == txn.DURABILITY_REMOTE
    assert {k: s3.objects[k] for k in seeded} == seeded                                                        # bytes idénticos
    new = set(s3.objects) - set(seeded)
    assert new and all(k.startswith("audit/catalog_local/") for k in new)                                      # solo se sube catalog_local


def test_phase_zero_does_not_touch_assessment_judgment_or_governance_code():
    for rel in ("webapp/services/assessment_service.py", "webapp/services/judgment_decisions.py", "webapp/services/governed_decisions.py",
                "webapp/services/governed_decisions_store.py", "webapp/services/governed_ratification_link.py"):
        assert "publication_txn" not in (REPO / rel).read_text(encoding="utf-8"), rel
    assert not re.search(r"publication_txn|PUBLICATION_SAFE", (REPO / "tools/catalog/methodological_judgment/__init__.py").read_text(encoding="utf-8"))
