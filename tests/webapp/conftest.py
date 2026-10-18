"""Fixtures compartidas de tests/webapp."""
from __future__ import annotations

import pytest

# Pruebas de la migracion de EVIDENCIA ``evidence_leaf_materialization_v4`` (cerrada en produccion): simulan un mundo PREVIO con perfiles de evidencia v3 cuyo Juicio vigente era judgment-rules/v3.
# Su objeto es la mecanica de migracion (plan, aprobacion, respaldo, olas, restauracion), no el ruleset: se fija el contrato de Juicio historico para aislarlas del cambio de ruleset (B5.5, v4).
_PINNED_TO_JUDGMENT_V3 = frozenset({
    "test_assessment_leaf_materialization_migration", "test_durable_backup", "test_migration_duties_modes", "test_migration_secured_apply", "test_transition_compat",
    "test_transition_end_to_end", "test_v4_dryrun_report", "test_v4_remote_backup_probe",
})


@pytest.fixture(autouse=True)
def _pin_historical_judgment_contract_for_the_closed_evidence_migration(request, monkeypatch):
    if request.module.__name__.rsplit(".", 1)[-1] not in _PINNED_TO_JUDGMENT_V3:
        yield
        return
    from tools.catalog.methodological_judgment import RULESET_V3
    from webapp.services import assessment_service as svc
    from webapp.services import judgment_decisions as jd
    monkeypatch.setattr(svc, "CURRENT_RULESET_VERSION", RULESET_V3)
    monkeypatch.setattr(svc, "JUDGMENT_CONTRACT_VERSIONS", (svc.JUDGMENT_CONTRACT_VERSIONS[0], RULESET_V3))
    monkeypatch.setattr(jd, "CURRENT_RULESET_VERSION", RULESET_V3)
    yield
