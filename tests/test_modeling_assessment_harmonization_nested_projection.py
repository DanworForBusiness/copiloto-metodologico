"""Contrato de Armonizacion (canonical-evidence/v5): una columna del SELECT externo SIN proveedor determinado (su fuente es un subselect: las vistas graficas compilan a SELECT anidados) toma
su procedencia del resolvedor canonico de linaje de campo (``field_lineage.resolve_query``) SOLO si es UN origen inequivoco IDENTITY/RENAME/CAST. Datos SINTETICOS; motor real.

Fija: (1) la vista anidada `Plant -> WERKS` queda RENAME con su proveedor y etiquetas observables; (2) coincide con ``resolve_query``; (3) ambiguedad, derivacion y proveedor fuera de la
evidencia conservan UNKNOWN; (4) resolver el proveedor NO aporta etiquetas (un proveedor sin etiquetas nunca da EQUALS_PROVIDER_LABEL); (5) las proyecciones directas no cambian;
(6) paridad Catalogo / ad hoc; (7) version v5, migracion gobernada registrada (transicional, confirmacion asegurada) y su postcheck/gancho del Juicio.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.catalog import assessment_currentness as cur
from tools.catalog.modeling_assessment import evidence_contract as EC
from tools.catalog.modeling_assessment import shadow
from tools.catalog.modeling_assessment.facts_harmonization_contract import nested_projection_origin
from tools.catalog.modeling_assessment.field_lineage import Origin, extract_query_ir, resolve_query
from tests._modeling_assessment_m7_fixtures import _view
from webapp.services import assessment_backfill as bf
from webapp.services import assessment_service as asvc

CLIENT, AM = "Acme", "4AM_N"
LABELS = {"Plant": "Centro", "StorageLocation": "Almacen", "StorageLocationName": "Denominacion de almacen", "Qty": "Cantidad"}


def _local(name: str, labels: dict[str, str] | None) -> dict:
    els = {f: ({"type": "cds.String", "@EndUserText.label": lab} if labels is not None else {"type": "cds.String"}) for f, lab in LABELS.items()}
    return {"definitions": {name: {"kind": "entity", "@DataWarehouse.repository.technicalType": {"#": "DWC_LOCAL_TABLE"}, "elements": els}}}


def _nested_dimension(name: str, inner_from: dict, inner_cols: list[dict]) -> dict:
    """Vista GRAFICA tipo VD_T001L: SELECT externo sobre un subselect («Calculated Columns 1») que renombra desde la tabla."""
    outs = [c["as"] for c in inner_cols]
    return {"definitions": {name: {
        "kind": "entity", "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_DIMENSION"},
        "elements": {o: {"type": "cds.String", "@EndUserText.label": {"WERKS": "Centro", "LGORT": "Almacen", "LGOBE": "Denominacion de almacen",
                                                                       "KEY_LGORT": "Clave - Almacen", "M": "Cantidad"}.get(o, o)} for o in outs},
        "query": {"SELECT": {"from": {"SELECT": {"from": inner_from, "columns": inner_cols}, "as": "Calculated Columns 1"},
                             "columns": [{"ref": [o]} for o in outs]}}}}}


def _direct_dimension(name: str) -> dict:
    """Vista tipo VT_T001W: proyeccion DIRECTA desde la tabla (sin subselect)."""
    cols = [("Plant", "WERKS"), ("StorageLocation", "LGORT"), ("Qty", "M")]
    return {"definitions": {name: {
        "kind": "entity", "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_DIMENSION"},
        "elements": {o: {"type": "cds.String", "@EndUserText.label": LABELS[s]} for s, o in cols},
        "query": {"SELECT": {"from": {"ref": ["LT_SRC"]}, "columns": [{"ref": [s], "as": o} for s, o in cols]}}}}}


RENAMES = [{"ref": ["LT_SRC", "Plant"], "as": "WERKS"}, {"ref": ["LT_SRC", "StorageLocation"], "as": "LGORT"},
           {"ref": ["LT_SRC", "StorageLocationName"], "as": "LGOBE"}, {"ref": ["LT_SRC", "StorageLocation"], "as": "KEY_LGORT"},
           {"ref": ["LT_SRC", "Qty"], "as": "M"}]


def _write(root: Path, rel: str, body: dict) -> None:
    p = root / CLIENT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body), encoding="utf-8")


def _pool(tmp_path: Path, dim: dict, *, src_labels: dict | None = LABELS, with_src: bool = True, extra: dict | None = None) -> Path:
    root = tmp_path / "pool"
    if with_src:
        _write(root, "local-tables/LT_SRC.json", _local("LT_SRC", src_labels))
    _write(root, "local-tables/LT_OTHER.json", _local("LT_OTHER", LABELS))
    for rel, body in (extra or {}).items():
        _write(root, rel, body)
    _write(root, "views/VD_N.json", dim)
    _write(root, "views/3VF_N.json", _view("3VF_N", "VD_N", ("WERKS", "LGORT"), pattern="ANALYTICAL_FACT", measures=("M",)))
    _write(root, "analytic-models/4AM_N.json", _view(AM, "3VF_N", ("WERKS", "LGORT"), pattern="ANALYTICAL_CUBE", category="CUBE", measures=("M",)))
    return root


def _harm(root: Path, art: str = "VD_N") -> dict:
    doc, _ = shadow.assess(root, CLIENT, AM, "General")
    assert doc["assessment_state"] == "ASSESSED", doc.get("assessment_state")
    return next(a for a in doc["canonical_evidence"]["harmonization_contract"]["artifacts"] if a["artifact"] == art), doc


# ── 1-2. vista anidada: RENAME con proveedor, etiquetas observables y consistencia con resolve_query ─────────────────
def test_nested_graphical_view_resolves_provider_and_labels(tmp_path):
    root = _pool(tmp_path, _nested_dimension("VD_N", {"ref": ["LT_SRC"]}, RENAMES))
    a, doc = _harm(root)
    assert a["mapping_counts"]["RENAME"] == 5 and a["mapping_counts"]["IDENTITY"] == 0 and a["mapping_counts"]["UNKNOWN"] == 0
    assert {r["target"]: r["source_field"] for r in a["renamed_fields"]} == {"WERKS": "Plant", "LGORT": "StorageLocation", "LGOBE": "StorageLocationName",
                                                                             "KEY_LGORT": "StorageLocation", "M": "Qty"}
    assert a["retained_source_fields"] == [{"source": "LT_SRC", "source_field_count": 4, "retained_source_fields": 4, "ratio": 1.0}]
    assert a["labels_determinable"] is True and a["label_origin_counts"]["UNKNOWN_PROVIDER_LABEL"] == 0
    assert a["label_origin_counts"]["EQUALS_PROVIDER_LABEL"] == 4 and a["label_origin_counts"]["DIFFERENT_FROM_PROVIDER"] == 1     # «Clave - Almacen» es propia
    assert a["character"]["value"] != "UNKNOWN"
    assert doc["canonical_evidence"]["version"] == "canonical-evidence/v5" == doc["versions"]["canonical_evidence"]


def test_harmonization_agrees_with_the_canonical_field_lineage_resolver(tmp_path):
    dim = _nested_dimension("VD_N", {"ref": ["LT_SRC"]}, RENAMES)
    a, _ = _harm(_pool(tmp_path, dim))
    origins = resolve_query(extract_query_ir(dim["definitions"]["VD_N"]), lambda e: tuple(LABELS) if e == "LT_SRC" else None)
    expected = {o: (origins[o][0].provider, origins[o][0].field) for o in ("WERKS", "LGORT", "LGOBE", "KEY_LGORT", "M")}
    assert {r["target"]: ("LT_SRC", r["source_field"]) for r in a["renamed_fields"]} == expected


# ── 3. ambiguedad / derivacion / proveedor fuera de la evidencia: UNKNOWN se conserva ────────────────────────────────
def test_ambiguous_unqualified_reference_stays_unknown(tmp_path):
    inner_from = {"join": "inner", "args": [{"ref": ["LT_SRC"], "as": "A"}, {"ref": ["LT_OTHER"], "as": "B"}],
                  "on": [{"ref": ["A", "Plant"]}, "=", {"ref": ["B", "Plant"]}]}
    cols = [{"ref": ["Plant"], "as": "WERKS"}]                                      # sin calificar y ambos lo exponen: no se elige
    a, _ = _harm(_pool(tmp_path, _nested_dimension("VD_N", inner_from, cols)))
    assert a["label_origin_counts"]["UNKNOWN_PROVIDER_LABEL"] >= 1 and a["labels_determinable"] is False
    assert a["character"]["value"] == "UNKNOWN"


def test_derived_column_in_the_subselect_is_not_compatible(tmp_path):
    cols = [{"func": "concat", "args": [{"ref": ["LT_SRC", "Plant"]}, {"ref": ["LT_SRC", "StorageLocation"]}], "as": "WERKS"}]
    a, _ = _harm(_pool(tmp_path, _nested_dimension("VD_N", {"ref": ["LT_SRC"]}, cols)))
    assert a["renamed_fields"] == [] and a["label_origin_counts"]["UNKNOWN_PROVIDER_LABEL"] == 1


def test_provider_outside_the_evidence_keeps_labels_unknown(tmp_path):
    a, _ = _harm(_pool(tmp_path, _nested_dimension("VD_N", {"ref": ["LT_SRC"]}, RENAMES), with_src=False))
    assert a["labels_determinable"] is False and a["label_origin_counts"]["UNKNOWN_PROVIDER_LABEL"] == 5       # sin la entidad no hay etiquetas que observar


# ── 4. resolver el proveedor no inventa etiquetas ────────────────────────────────────────────────────────────────
def test_resolving_the_provider_never_invents_its_labels(tmp_path):
    a, _ = _harm(_pool(tmp_path, _nested_dimension("VD_N", {"ref": ["LT_SRC"]}, RENAMES), src_labels=None))
    assert a["label_origin_counts"]["EQUALS_PROVIDER_LABEL"] == 0                   # el proveedor no tiene etiquetas: ninguna "coincide"
    assert a["labels_determinable"] is True and a["label_origin_counts"]["DIFFERENT_FROM_PROVIDER"] == 5       # conocido sin etiqueta: las propias son del autor


# ── 5. proyecciones directas: sin regresión ──────────────────────────────────────────────────────────────────────
def test_direct_projection_is_unchanged(tmp_path, monkeypatch):
    root = _pool(tmp_path, _direct_dimension("VD_N"))
    a, _ = _harm(root)
    from tools.catalog.modeling_assessment import facts_harmonization_contract as H
    monkeypatch.setattr(H, "resolve_query", lambda *x, **k: (_ for _ in ()).throw(AssertionError("una proyeccion directa no usa la resolucion complementaria")))
    b, _ = _harm(root)
    assert a == b and a["mapping_counts"]["RENAME"] == 3 and a["label_origin_counts"]["EQUALS_PROVIDER_LABEL"] == 3


@pytest.mark.parametrize("origins,expected", [
    ({"X": [Origin("T", "f", "RENAME")]}, ("RENAME", "T", "f")),
    ({"X": [Origin("T", "f", "CAST")]}, ("CAST", "T", "f")),
    ({"X": [Origin("T", "f", "DERIVED")]}, None),
    ({"X": [Origin("T", "f", "EXPRESSION")]}, None),
    ({"X": [Origin(None, None, "UNKNOWN", "UNQUALIFIED_MULTI_SOURCE")]}, None),
    ({"X": [Origin("T", "f", "RENAME"), Origin("U", "f", "RENAME")]}, None),           # dos origenes (union): no se elige
    ({}, None),
])
def test_only_a_single_unambiguous_compatible_origin_is_accepted(origins, expected):
    assert nested_projection_origin(origins, "X") == expected


# ── 6. paridad Catálogo / ad hoc ────────────────────────────────────────────────────────────────────────────────
def test_catalog_and_adhoc_paths_agree(tmp_path):
    root = _pool(tmp_path, _nested_dimension("VD_N", {"ref": ["LT_SRC"]}, RENAMES))
    catalog, _ = asvc._engine_assess(root, CLIENT, AM, "General")
    adhoc, code = asvc.assess_evidence_root(root, CLIENT, AM, "General", root_type="analytic-model")
    assert code is None and catalog == adhoc


# ── 7. versionado y tratamiento gobernado de los assessments anteriores ──────────────────────────────────────────
def test_version_v5_keeps_v4_readable():
    assert EC.CANONICAL_EVIDENCE_VERSION == "canonical-evidence/v5" and "canonical-evidence/v4" in EC.SUPPORTED_VERSIONS


def test_a_v4_profile_is_covered_by_the_governed_migration_and_stays_current_until_migrated():
    versions = asvc.current_assessment_versions()
    old = {**versions, "canonical_evidence": "canonical-evidence/v4"}
    assert cur.targeted_migration(old, versions) == asvc.HARMONIZATION_MIGRATION
    c = cur.classify_currentness("sig", old, "sig", versions)
    assert c.state == cur.CURRENT and c.pending_migration == asvc.HARMONIZATION_MIGRATION      # transicional: sin STALE masivo; se migra explicitamente
    assert asvc.HARMONIZATION_MIGRATION in bf.CONFIRMATION_REQUIRED and asvc.HARMONIZATION_MIGRATION in bf.SECURED_MIGRATIONS


def test_postcheck_allows_only_the_harmonization_section_to_change(tmp_path):
    _a, new = _harm(_pool(tmp_path, _nested_dimension("VD_N", {"ref": ["LT_SRC"]}, RENAMES)))
    old = copy.deepcopy(new)
    old["versions"]["canonical_evidence"] = old["canonical_evidence"]["version"] = "canonical-evidence/v4"
    old["canonical_evidence"]["harmonization_contract"]["artifacts"][0]["labels_determinable"] = False     # el v4 no la observaba
    versions = asvc.current_assessment_versions()
    asvc.migration_postcheck(asvc.HARMONIZATION_MIGRATION, new, {"profile_json": old}, versions)            # solo Armonización difiere: OK
    tampered = copy.deepcopy(old)
    tampered["canonical_evidence"]["leaf_provenance"] = []
    with pytest.raises(asvc.EnrichmentError) as e:
        asvc.migration_postcheck(asvc.HARMONIZATION_MIGRATION, new, {"profile_json": tampered}, versions)
    assert e.value.code in ("NON_HARMONIZATION_CONTENT_CHANGED", "CLOSURE_DRIFT", "CLOSURE_NOT_VERIFIABLE")


def test_judgment_is_rederived_replaying_the_same_ratifications(tmp_path, monkeypatch):
    _a, doc = _harm(_pool(tmp_path, _nested_dimension("VD_N", {"ref": ["LT_SRC"]}, RENAMES)))
    prev_j = asvc._derive_judgment(doc)
    overrides, notes = asvc._harmonization_envelope_hook(doc, {"judgment": prev_j, "domain_classification": {"d": 1}, "business_classification": {"b": 2}})
    assert overrides["judgment"]["output_signature"] == prev_j["output_signature"] and notes["judgment_action"].startswith("judgment_current:")
    assert overrides["domain_classification"] == {"d": 1} and overrides["business_classification"] == {"b": 2}
    from webapp.services import judgment_decisions as jd
    monkeypatch.setattr(jd, "lost_decisions", lambda *a, **k: [{"event_id": "e1"}])                     # una ratificación que ya no se re-aplica
    with pytest.raises(asvc.EnrichmentError) as e:
        asvc._harmonization_envelope_hook(doc, {"judgment": prev_j})
    assert e.value.code == "RATIFICATION_NOT_REAPPLIED"


def test_judgment_ruleset_is_untouched():
    from tools.catalog.methodological_judgment import CURRENT_RULESET_VERSION
    assert CURRENT_RULESET_VERSION == "judgment-rules/v4"


# ── consumo de un candidato de Armonización: linaje O dimensión publicada por asociación ─────────────────────────────
def test_a_business_dimension_published_by_association_counts_as_consumed():
    from tools.catalog.methodological_judgment.profile_access import ProfileEvidence
    ev = ProfileEvidence({"canonical_evidence": {
        "view_complexity": {"views": [{"artifact": "3VF", "provider_names": ["2TR"]}, {"artifact": "VD_DIM", "provider_names": ["T"]},
                                          {"artifact": "VD_ORPHAN", "provider_names": ["T"]}]},
        "published_contract": {"associations": {"associations": [{"target": "VD_DIM", "category": "BUSINESS_DIMENSION"},
                                                                 {"target": "VD_TEXT", "category": "TEXT"}]}}}})
    assert ev.consumed_downstream("2TR")                       # por linaje (3VF lo lee)
    assert ev.consumed_downstream("VD_DIM") and not ev.descendants("VD_DIM")     # por asociación publicada, sin FROM aguas abajo
    assert not ev.consumed_downstream("VD_ORPHAN") and not ev.consumed_downstream("VD_TEXT")   # ni linaje ni dimensión de negocio publicada


def test_the_projected_judgment_change_enters_the_signed_plan_item(tmp_path):
    """Mismo ruleset, otra interpretacion (p. ej. ND -> ADECUADA): el resultado proyectado viaja en ``judgment_action`` y por eso en el digest que firma el aprobador."""
    _a, doc = _harm(_pool(tmp_path, _nested_dimension("VD_N", {"ref": ["LT_SRC"]}, RENAMES)))
    fresh = asvc._derive_judgment(doc)
    stale = {**fresh, "status": "NOT_DETERMINABLE", "level": None, "level_rule": "LVL-06", "output_signature": "sha256:otra-interpretacion"}
    _over, notes = asvc._harmonization_envelope_hook(doc, {"judgment": stale})
    assert notes["judgment_action"] == f"judgment_rederived:ND/LVL-06->{fresh['level'] or 'ND'}/{fresh['level_rule']}"
    cand = type("C", (), {"client": "Acme", "terminal_am": AM, "previous_signature": "x", "envelope": {}, "notes": notes})()
    assert bf._plan_item(cand)["judgment_action"] == notes["judgment_action"]               # entra al ítem firmado
