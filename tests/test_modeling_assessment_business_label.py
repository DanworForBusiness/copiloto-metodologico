"""
Nombre de negocio del AM terminal en el perfil canónico (``scope.terminal_analytic_model_label``).

Contrato: la evidencia (@EndUserText.label del AM, con la resolución i18n existente) alimenta UN campo opcional de identidad descriptiva
del documento público; JSON, Markdown y HTML salen de ese mismo valor. Sin etiqueta en la evidencia no se inventa nada (campo null y
comportamiento previo). La etiqueta NO es insumo de ninguna regla: hechos, señales, cobertura y confianza no cambian.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from tests._modeling_assessment_m7_fixtures import write_inbound
from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import shadow as ma_shadow
from tools.catalog.modeling_assessment.render_md import render_markdown
from webapp.services import assessment_presenter as presenter
from webapp.services import assessment_service as asvc

LABEL = "Modelo analítico trazabilidad de Pagos - Factura - Compra"
AM = "4AM_ACME"


def _pool(tmp: Path, *, label=None, i18n=None) -> Path:
    root = write_inbound(tmp / "inbound")
    path = root / "Acme" / "analytic-models" / f"{AM}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    if label is not None:
        data["definitions"][AM]["@EndUserText.label"] = label
    if i18n is not None:
        data["i18n"] = i18n
    path.write_text(json.dumps(data), encoding="utf-8")
    return root


def _doc(root: Path) -> dict:
    doc, _meta = ma_shadow.assess(root, "Acme", AM, "General")
    return doc


def _adhoc_view(doc: dict) -> dict:
    status = asvc.AssessmentStatus(asvc.CURRENT, "x", "Acme", AM, generated_at="2026-10-03T00:00:00+00:00", profile=doc)
    return presenter.report_view(status, None, "datasphere_core", adhoc=presenter.AdhocReport("/b", "/j", "/m"))


# ── propagación fuente -> perfil ────────────────────────────────────────────
def test_label_declared_in_the_root_am_csn_reaches_the_canonical_profile(tmp_path):
    doc = _doc(_pool(tmp_path, label=LABEL))
    assert doc["scope"]["terminal_analytic_model_label"] == LABEL and doc["scope"]["terminal_analytic_model"] == AM
    ma_profile.validate_assessment(doc)


def test_i18n_placeholder_label_is_resolved_with_the_existing_resolver(tmp_path):
    root = _pool(tmp_path, label="{i18n>4AM_ACME@ENDUSERTEXT.LABEL}", i18n={"es": {"4AM_ACME@ENDUSERTEXT.LABEL": LABEL}})
    assert _doc(root)["scope"]["terminal_analytic_model_label"] == LABEL


def test_unresolvable_i18n_placeholder_or_missing_label_yields_null_not_a_derived_title(tmp_path):
    unresolved = _doc(_pool(tmp_path / "a", label="{i18n>NO_EXISTE}"))
    missing = _doc(_pool(tmp_path / "b"))
    for doc in (unresolved, missing):
        assert doc["scope"]["terminal_analytic_model_label"] is None and doc["scope"]["terminal_analytic_model"] == AM
        ma_profile.validate_assessment(doc)


def test_label_equal_to_the_technical_name_is_not_a_business_name(tmp_path):
    assert _doc(_pool(tmp_path, label=AM))["scope"]["terminal_analytic_model_label"] is None


# ── salidas canónicas alineadas ─────────────────────────────────────────────
def test_json_markdown_and_html_all_come_from_the_same_canonical_value(tmp_path):
    doc = _doc(_pool(tmp_path, label=LABEL))
    assert f'"terminal_analytic_model_label": "{LABEL}"' in ma_profile.canonical_assessment_json(doc)
    md = render_markdown(doc)
    assert f"- **Modelo analítico terminal:** {AM}" in md and f"- **Descripción:** {LABEL}" in md
    view = _adhoc_view(doc)
    assert view["title_desc"] == LABEL and view["title_id"] == AM


def test_without_a_label_outputs_keep_the_technical_id_only(tmp_path):
    doc = _doc(_pool(tmp_path))
    md = render_markdown(doc)
    assert "Descripción" not in md and "None" not in md.split("## Resumen")[0] and f"- **Modelo analítico terminal:** {AM}" in md
    view = _adhoc_view(doc)
    assert view["title_desc"] is None and view["title_id"] == AM


def test_generic_catalog_prefix_is_stripped_like_the_catalog_title(tmp_path):
    doc = _doc(_pool(tmp_path, label="Modelo analítico - " + LABEL))
    assert _adhoc_view(doc)["title_desc"] == LABEL


# ── semántica intacta ───────────────────────────────────────────────────────
def test_label_is_descriptive_only_all_assessment_content_is_unchanged(tmp_path):
    with_label = _doc(_pool(tmp_path / "a", label=LABEL))
    without = _doc(_pool(tmp_path / "b"))

    def strip(doc):
        d = copy.deepcopy(doc)
        d["scope"].pop("terminal_analytic_model_label")
        d["scope"].pop("pool_signature")                       # la firma del pool cambia con los BYTES del CSN, no con la semántica
        return d
    assert strip(with_label) == strip(without)                 # hechos, señales, cobertura, confianza, reglas: idénticos


def test_label_never_feeds_a_rule_or_fact():
    import re
    base = Path(__file__).resolve().parents[1] / "tools" / "catalog" / "modeling_assessment"
    users = [p.name for p in base.glob("*.py") if "terminal_analytic_model_label" in p.read_text(encoding="utf-8")]
    assert sorted(users) == ["profile.py", "render_md.py"]                                   # solo construcción del documento y su render
    assert not re.search(r"terminal_label", (base / "signals.py").read_text(encoding="utf-8"))


# ── esquema / compatibilidad ────────────────────────────────────────────────
def test_profiles_without_the_optional_field_remain_valid_and_schema_version_is_unchanged(tmp_path):
    doc = _doc(_pool(tmp_path, label=LABEL))
    old = copy.deepcopy(doc)
    old["scope"].pop("terminal_analytic_model_label")                                         # perfil cacheado antes del campo
    ma_profile.validate_assessment(old)
    ma_profile.validate_assessment(doc)
    assert doc["schema_version"] == ma_profile.ASSESSMENT_SCHEMA_VERSION == "modeling_assessment.assessment/v1"
    assert render_markdown(old).count("Descripción") == 0
    assert _adhoc_view(old)["title_desc"] is None                                            # el presenter tolera la clave ausente
    bad = copy.deepcopy(doc)
    bad["schema_version"] = "modeling_assessment.assessment/v0"
    try:
        ma_profile.validate_assessment(bad)
    except ValueError:
        pass
    else:                                                                                    # pragma: no cover
        raise AssertionError("una schema_version distinta debe seguir siendo incompatible")


def test_internal_facts_profile_schema_golden_is_untouched():
    from tools.catalog.modeling_assessment import schema as ma_schema
    assert ma_schema.SCHEMA_FILE.read_text(encoding="utf-8") == ma_schema.schema_text()                   # el golden generado desde el modelo sigue idéntico
    assert "terminal_analytic_model_label" not in ma_schema.SCHEMA_FILE.read_text(encoding="utf-8")      # es el perfil de HECHOS interno, no el documento público


def test_label_does_not_alter_currentness_inputs(tmp_path):
    versions = asvc.current_assessment_versions()
    doc = _doc(_pool(tmp_path, label=LABEL))
    assert doc["versions"] == versions                                                       # el bloque de versiones (vigencia) no cambia con el campo
    assert "terminal_analytic_model_label" not in json.dumps(versions)


# ── Catálogo: precedencia canónico > metadato del Catálogo > técnico ────────
def test_catalog_precedence_prefers_canonical_label_then_catalog_metadata_then_technical(tmp_path):
    class _Entry:
        entry_id, cliente, dominio, path, artefactos = "e1", "Acme", "Dom", "x", []
    with_label = _doc(_pool(tmp_path / "a", label=LABEL))
    without = _doc(_pool(tmp_path / "b"))
    s1 = asvc.AssessmentStatus(asvc.CURRENT, "e1", "Acme", AM, generated_at="2026-10-03T00:00:00+00:00", profile=with_label)
    s2 = asvc.AssessmentStatus(asvc.CURRENT, "e1", "Acme", AM, generated_at="2026-10-03T00:00:00+00:00", profile=without)
    v1 = presenter.report_view(s1, _Entry(), "datasphere_core")
    v2 = presenter.report_view(s2, _Entry(), "datasphere_core")
    assert v1["title_desc"] == LABEL and v1["title_id"] == AM                                # canónico gana
    expected = presenter._report_title(_Entry(), AM)                                         # sin canónico: comportamiento previo del Catálogo
    assert (v2["title_desc"], v2["title_id"]) == expected
    assert v1["adhoc"] is False and v2["adhoc"] is False
