"""Integracion de punta a punta (Spaces falso, copias aisladas): v3 pendiente CURRENT en las superficies -> apply con respaldo durable -> restauracion autorizada desde el run remoto -> el v3
vuelve a ser CURRENT pendiente; y los demas motivos de STALE siguen visibles en las mismas superficies."""
from __future__ import annotations

import json
import shutil

from tests.webapp.test_assessment_enrichment import ALWAYS  # noqa: F401
from tests.webapp.test_assessment_leaf_materialization_migration import MIG, _env, _make_v3, _path, _rewrite, kworld  # noqa: F401
from tests.webapp.test_durable_backup import _authorizer
from tests.webapp.test_governed_assessment_durability import files, restart  # noqa: F401
from tests.webapp.test_migration_secured_apply import KEYS, _plan, _publish_all, _restore_approval, _secured, remote  # noqa: F401
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as svc
from webapp.services import client_assessment_portfolio as portfolio
from webapp.services import durable_backup as dbk
from webapp.services import migration_approval as ma

PACK = "datasphere_core"


def _surfaces(w, entry) -> dict:
    st = svc.status_for_entry(entry)
    rep = presenter.report_view(st, entry, PACK)
    row = portfolio._row(entry, st, PACK)
    return {"state": st.state, "pending": st.pending_migration, "report_state": rep["state"], "report_note": rep["state_note"], "report_body": rep["state_body"], "row_state": row["state"],
            "row_ref": row["assessment_ref"] is not None}


def test_a_pending_v3_is_presented_as_current_everywhere_while_other_stale_causes_stay_visible(kworld):
    _make_v3(kworld, kworld.k, kworld.k2)
    _rewrite(_path(kworld, "Kensing2"), lambda e: e.update(input_pool_signature="0" * 64))                    # k2: ademas cambio la entrada
    restart()
    pend, stale = _surfaces(kworld, kworld.k), _surfaces(kworld, kworld.k2)
    assert pend == {"state": svc.CURRENT, "pending": MIG, "report_state": svc.CURRENT, "report_note": None, "report_body": None, "row_state": svc.CURRENT, "row_ref": True}
    assert stale["state"] == svc.STALE and stale["pending"] is None and stale["report_state"] == svc.STALE and stale["row_state"] == svc.STALE
    assert stale["report_note"] and stale["report_body"]                                                         # banner de desactualizado intacto para los demas motivos
    assert not svc.status_for_entry(kworld.k).can_generate and svc.status_for_entry(kworld.k2).can_generate


def test_full_cycle_pending_to_migrated_to_restored_from_the_durable_run(kworld, remote):
    originals = _make_v3(kworld, kworld.k)
    _publish_all(kworld)
    before_bytes = {p.name: p.read_bytes() for p in files(kworld)}
    plan = _plan(kworld, kworld.k)
    out = _secured(kworld, kworld.k, authorizer=_authorizer(kworld, plan), allow_local_only=False, run_id="run-e2e")
    assert out["outcome"] == bf.APPLIED and out["plan"]["digest"] == plan["digest"]
    after = _surfaces(kworld, kworld.k)
    assert after["state"] == svc.CURRENT and after["pending"] is None
    env = _env(_path(kworld, "Kensing"))
    assert env["assessment_versions"]["canonical_evidence"] != originals["Kensing"]["assessment_versions"]["canonical_evidence"]
    assert env["judgment"]["ruleset_version"] == originals["Kensing"]["judgment"]["ruleset_version"]            # misma metodologia del Juicio (solo se re-liga la firma del perfil)
    assert out["records"][0]["delta"]["judgment"]["judgment_action"] == "judgment_signature_rebound"
    # el contenedor pierde su respaldo local: se restaura desde el run durable, con autorizacion explicita
    seal = out["secured_apply"]["backup"]["manifest_sha256"]
    stg = kworld.tmp / "stg"
    dbk.fetch(MIG, "run-e2e", stg)
    approval = _restore_approval(kworld, stg)
    shutil.rmtree(stg)
    shutil.rmtree(kworld.tmp / "bk")
    res = bf.restore_from_remote_run(MIG, "run-e2e", stg, apply=True, confirm=seal[:12], authorizer=ma.authorizer_from_file(approval, "bob", approver_keys=KEYS))
    assert res["outcome"] == "DONE" and res["counts"]["RESTORED"] == 1
    restart()
    back = _surfaces(kworld, kworld.k)
    assert back["state"] == svc.CURRENT and back["pending"] == MIG                                              # vuelve a v3 CURRENT-pendiente
    assert {p.name: p.read_bytes() for p in files(kworld)} == before_bytes
    again = bf.migrate_stale_evidence([kworld.k], MIG, apply=False, governed_state_present=ALWAYS)
    assert again["plan"]["digest"] == plan["digest"] and again["counts"]["pending_migration_compat"] == 1        # elegible otra vez: el ciclo es repetible
