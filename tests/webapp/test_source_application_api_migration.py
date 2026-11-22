"""Mecánica de la migración gobernada `evidence_source_application_api_v6` (SOURCE/SAPSF): registro/seguridad, transicionalidad (CURRENT/STALE),
y el invariante del postcheck (`_without_source_application_api`: SOLO puede cambiar la clase de contrato de fuente SAPSF + su referencia en el linaje publicado).
Pruebas puras (sin pool/motor). El ND→BUENA de Workforce/EducationPro y la invariancia del resto se evidencian con el dry-run del lote sobre el pool real."""
import copy

from webapp.services import assessment_service as svc
from webapp.services import assessment_backfill as bf
from tools.catalog import assessment_currentness as cur

MIG = "evidence_source_application_api_v6"


# ───────────────────────────── registro y seguridad ─────────────────────────────
def test_migration_is_registered_transitional_secured_and_rederives_judgment():
    assert MIG in cur.EVIDENCE_MIGRATIONS
    m = cur.EVIDENCE_MIGRATIONS[MIG]
    assert m.target_keys == ("canonical_evidence",) and m.eligible_old["canonical_evidence"] == frozenset({"canonical-evidence/v5"})
    assert m.closure_policy == cur.CLOSURE_EQUAL and m.transitional is True
    assert svc.SOURCE_APPLICATION_API_MIGRATION == MIG
    assert MIG in svc.JUDGMENT_REDERIVING_MIGRATIONS                 # re-deriva el Juicio (puede cambiar nivel), con replay de ratificaciones
    assert MIG not in svc.JUDGMENT_REVALIDATING_MIGRATIONS
    assert MIG in bf.SECURED_MIGRATIONS and MIG in bf.CONFIRMATION_REQUIRED   # --apply fail-closed


# ───────────────────────────── currentness: un v5 queda CURRENT/pending (sin STALE masivo) ─────────────────────────────
def test_v5_envelope_is_current_pending_this_migration():
    versions = svc.current_assessment_versions()
    old = dict(versions, canonical_evidence="canonical-evidence/v5")
    assert cur.targeted_migration(old, versions) == MIG
    c = cur.classify_currentness("sig", old, "sig", versions)
    assert c.state == cur.CURRENT and c.pending_migration == MIG     # transicional: visible, sin STALE
    # discrepancias AJENAS no las absorbe la compatibilidad (siguen STALE)
    other = cur.classify_currentness("sig", dict(old, signals="signal-rules/v0"), "sig", versions)
    assert other.state == cur.STALE


# ───────────────────────────── invariante del postcheck ─────────────────────────────
def _profile(sapsf_class, *, pfl_leaf_class, extra_surface=None):
    return {
        "canonical_evidence": {
            "version": "canonical-evidence/v6",
            "source_contract_classes": {"leaves": [{"leaf": "1TR_SSF_FOCompany", "contract_class": sapsf_class}], "by_class": [{"contract_class": sapsf_class, "count": 1}]},
            "published_field_lineage": {"fields": [{"technical_name": "X", "label": "X", "frontier_leaves": [{"leaf": "1TR_SSF_FOCompany", "source_contract_class": pfl_leaf_class}]}]},
            "closure": {"plane_context": None},
        },
        "versions": {"canonical_evidence": "canonical-evidence/v6"},
        "surfaces": ([extra_surface] if extra_surface else []),
    }


def test_without_source_application_api_ignores_only_the_sapsf_class_and_lineage_ref():
    v5 = _profile("EXTERNAL_CONTRACT_UNDETERMINED", pfl_leaf_class="EXTERNAL_CONTRACT_UNDETERMINED")
    v5["canonical_evidence"]["version"] = "canonical-evidence/v5"
    v5["versions"]["canonical_evidence"] = "canonical-evidence/v5"
    v6 = _profile("GOVERNED_SAP_APPLICATION_API", pfl_leaf_class="GOVERNED_SAP_APPLICATION_API")
    # Solo cambió la clase SAPSF (+ versión + referencia en el linaje) → el normalizado es IGUAL
    assert svc._without_source_application_api(v6) == svc._without_source_application_api(v5)


def test_without_source_application_api_detects_foreign_changes():
    v5 = _profile("EXTERNAL_CONTRACT_UNDETERMINED", pfl_leaf_class="EXTERNAL_CONTRACT_UNDETERMINED")
    v6 = _profile("GOVERNED_SAP_APPLICATION_API", pfl_leaf_class="GOVERNED_SAP_APPLICATION_API", extra_surface={"id": "otra", "x": 1})
    # Cambió algo AJENO (una superficie no-LAYER_BOUNDARY) → el normalizado DIFIERE (el postcheck abortaría con NON_SOURCE_APPLICATION_API_CONTENT_CHANGED)
    assert svc._without_source_application_api(v6) != svc._without_source_application_api(v5)


def _rich(sapsf_class, *, areas=None, surfaces=None, findings=None, summary=None):
    p = _profile(sapsf_class, pfl_leaf_class=sapsf_class)
    p["areas"] = areas if areas is not None else [{"id": "SOURCE", "finding_groups": ["G1"]}]
    p["surfaces"] = surfaces if surfaces is not None else [{"surface_id": "rule.x", "rule_result": "OK"}]
    p["findings"] = findings if findings is not None else [{"id": "F1"}]
    p["summary"] = summary if summary is not None else {"coverage": {"assessed": 10}}
    return p


def test_postcheck_rejects_foreign_changes_outside_source_and_layer_boundary():
    """La tolerancia es ESPECÍFICA de LAYER_BOUNDARY_REVIEW: cualquier cambio AJENO (áreas, superficies, findings, summary no-LAYER_BOUNDARY) sigue DIFERENCIANDO el normalizado (postcheck lo rechaza)."""
    base = _rich("GOVERNED_SAP_APPLICATION_API")
    for mut in ({"areas": [{"id": "SOURCE", "finding_groups": ["G1", "G2_NUEVO"]}]},   # grupo NO-LAYER_BOUNDARY
                {"surfaces": [{"surface_id": "rule.x", "rule_result": "CHANGED"}]},    # superficie NO-LAYER_BOUNDARY
                {"findings": [{"id": "F1"}, {"id": "F2_NUEVO"}]},
                {"summary": {"coverage": {"assessed": 99}}}):
        other = _rich("GOVERNED_SAP_APPLICATION_API", **mut)
        assert svc._without_source_application_api(base) != svc._without_source_application_api(other), f"cambio ajeno no detectado: {list(mut)}"


def test_without_source_application_api_tolerates_the_layer_boundary_cascade():
    # Reconocer la fuente como contrato gobernado SUPRIME LAYER_BOUNDARY_REVIEW (fuente gobernada ⇒ sin revisión de armonización faltante): esa cascada NO debe romper el postcheck.
    v5 = _profile("EXTERNAL_CONTRACT_UNDETERMINED", pfl_leaf_class="EXTERNAL_CONTRACT_UNDETERMINED")
    v5["rule_results"] = [{"rule": "LAYER_BOUNDARY_REVIEW", "result": "PATTERN_FOUND"}]        # vigente: la revisión existía
    v5["review_required"] = [{"id": "LAYER_BOUNDARY_REVIEW"}]
    v6 = _profile("GOVERNED_SAP_APPLICATION_API", pfl_leaf_class="GOVERNED_SAP_APPLICATION_API")
    v6["rule_results"] = []                                                                     # candidato: suprimida por la fuente gobernada
    v6["review_required"] = []
    assert svc._without_source_application_api(v6) == svc._without_source_application_api(v5)
