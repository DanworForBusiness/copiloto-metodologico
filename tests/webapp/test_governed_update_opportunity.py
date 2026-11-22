"""Detección ad hoc → gobernado (`governed_update_opportunity`): DETECTA y EXPLICA una oportunidad de revisión supervisada; NUNCA persiste.
Distingue detección / comparabilidad / elegibilidad; `space=None` no es coincidencia automática."""
from webapp.services import assessment_service as svc


def _env(level, status, sapsf_class, *, space=None):
    leaves = [{"leaf": f"1TR_SSF_{i}", "contract_class": sapsf_class, "remote_connection": "SSFF"} for i in range(15)]
    return {"client": "Contreras", "terminal_am": "AM_Workforce", "judgment": {"status": status, "level": level},
            "profile_json": {"scope": ({"space": space} if space else {}), "canonical_evidence": {"source_contract_classes": {"leaves": leaves}}}}


def _cand_profile(sapsf_class):
    leaves = [{"leaf": f"1TR_SSF_{i}", "contract_class": sapsf_class, "remote_connection": "SSFF"} for i in range(15)]
    return {"canonical_evidence": {"source_contract_classes": {"leaves": leaves}}}


def _patch_env(monkeypatch, env):
    monkeypatch.setattr(svc, "_load_envelope", lambda c, a: env)


def test_no_governed_envelope_no_opportunity(monkeypatch):
    _patch_env(monkeypatch, None)
    assert svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API")) is None


def test_no_relevant_difference_no_opportunity(monkeypatch):
    env = _env("BUENA", "DETERMINED", "GOVERNED_SAP_APPLICATION_API")
    _patch_env(monkeypatch, env)
    # mismo nivel y mismos contratos → sin oportunidad
    assert svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API")) is None


def test_level_change_nd_to_buena_is_an_opportunity(monkeypatch):
    env = _env(None, "NOT_DETERMINABLE", "EXTERNAL_CONTRACT_UNDETERMINED")        # vigente ND
    _patch_env(monkeypatch, env)
    opp = svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API"))
    assert opp and opp["level_changed"] and opp["contracts_changed"]
    assert opp["vigente"]["level"] is None and opp["candidato"]["level"] == "BUENA"
    assert opp["comparable"] is True                                             # sin espacios explícitos → comparable (detección), procedencia a validar
    assert opp["provenance_validated"] is False


def test_space_mismatch_is_not_comparable(monkeypatch):
    env = _env(None, "NOT_DETERMINABLE", "EXTERNAL_CONTRACT_UNDETERMINED", space="PRD")
    _patch_env(monkeypatch, env)
    opp = svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API"), space="DEV")
    assert opp and opp["comparable"] is False and opp["provenance_validated"] is False   # espacios DIFIEREN explícitamente → no comparable (no reemplazo directo)


def test_same_space_validates_provenance(monkeypatch):
    env = _env(None, "NOT_DETERMINABLE", "EXTERNAL_CONTRACT_UNDETERMINED", space="DEV")
    _patch_env(monkeypatch, env)
    opp = svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API"), space="DEV")
    assert opp and opp["comparable"] is True and opp["provenance_validated"] is True


def test_presenter_view_is_compact_and_explains(monkeypatch):
    from webapp.services import assessment_presenter as pres
    env = _env(None, "NOT_DETERMINABLE", "EXTERNAL_CONTRACT_UNDETERMINED", space="PRD")
    _patch_env(monkeypatch, env)
    opp = svc.governed_update_opportunity("Contreras", "AM_Workforce", {"status": "DETERMINED", "level": "BUENA"}, _cand_profile("GOVERNED_SAP_APPLICATION_API"), space="DEV")
    v = pres._update_opportunity_view(opp)
    assert v["vigente_level"] == "NO DETERMINABLE" and v["candidato_level"] == "BUENA" and v["level_changed"]
    assert "GOVERNED_SAP_APPLICATION_API" in v["source_classes_changed"] and v["provenance_validated"] is False and v["motive"]
    assert pres._update_opportunity_view(None) is None
