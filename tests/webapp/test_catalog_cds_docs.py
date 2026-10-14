"""Resolución de la documentación CDS («Abrir CDS View»): por identidad explícita del documento y, si no, por ``@AbapCatalog.sqlViewName`` declarado por UN solo documento.
Sin reglas por prefijo (`DSP_`), sin alias por modelo, sin enlaces a documentos inexistentes, sin asociaciones ambiguas."""
from __future__ import annotations

import os

import pytest

import webapp.main as main
from webapp.services import catalog_cds_docs as cds


def _doc(apis, stem, sql_view=None, entity=True):
    head = f"@AbapCatalog.sqlViewName: '{sql_view}'\n" if sql_view else ""
    (apis / f"{stem}.md").write_text(f"---\ncds_view: {stem}\ntabla_base: X\nextraccion: FULL\n---\n# API\n## Código\n```abap\n{head}define view {stem} as select from x\n```\n", encoding="utf-8")


@pytest.fixture()
def apis(tmp_path, monkeypatch):
    d = tmp_path / "APIs"
    d.mkdir()
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path))
    cds._memo.clear()
    return d


def test_control_positive_the_entity_named_document_resolves_by_its_explicit_identity(apis):
    _doc(apis, "ZCDS_DSP_DIM_ADRC")                                                   # define view entity: sin sqlViewName (caso que ya funcionaba)
    assert cds.resolve(apis, "ZCDS_DSP_DIM_ADRC") == "ZCDS_DSP_DIM_ADRC"
    assert main._catalog_cds_link("ABAP_CDS.ZCDS_DSP_DIM_ADRC$E") == "ZCDS_DSP_DIM_ADRC"


def test_the_sql_view_name_declared_by_exactly_one_document_resolves_the_odp_import_name(apis):
    _doc(apis, "ZCDS_DSP_BILLING", sql_view="ZCDS_BILLING")                           # tabla remota ABAP_CDS.ZCDS_BILLING$F ↔ documento ZCDS_DSP_BILLING.md
    assert cds.resolve(apis, "ZCDS_BILLING") == "ZCDS_DSP_BILLING" and cds.resolve(apis, "zcds_billing") == "ZCDS_DSP_BILLING"
    assert main._catalog_cds_link("ABAP_CDS.ZCDS_BILLING$F") == "ZCDS_DSP_BILLING"
    assert (apis / f"{main._catalog_cds_link('ABAP_CDS.ZCDS_BILLING$F')}.md").exists()      # el destino existe de verdad


def test_no_generic_prefix_rule_a_similar_name_without_declared_metadata_does_not_resolve(apis):
    _doc(apis, "ZCDS_DSP_BILLING")                                                    # existe el documento, pero NO declara sqlViewName
    assert cds.resolve(apis, "ZCDS_BILLING") == "" and main._catalog_cds_link("ABAP_CDS.ZCDS_BILLING$F") == ""
    _doc(apis, "ZCDS_DSP_OTHER", sql_view="ZCDS_OTHER")
    assert cds.resolve(apis, "ZCDS_BILLING") == ""                                    # y otro documento con otra vista SQL tampoco lo habilita


def test_an_ambiguous_sql_view_name_is_not_resolved(apis):
    _doc(apis, "ZCDS_A", sql_view="ZSHARED")
    _doc(apis, "ZCDS_B", sql_view="ZSHARED")
    assert cds.resolve(apis, "ZSHARED") == "" and main._catalog_cds_link("ABAP_CDS.ZSHARED$F") == ""


def test_a_direct_name_match_does_not_prevail_over_contradicting_explicit_metadata(apis):
    _doc(apis, "ZX", sql_view="Y")                                                    # ZX.md declara que su vista SQL es Y …
    _doc(apis, "Y")                                                                   # … y existe un archivo Y.md: «Y» lo reclaman dos documentos distintos
    assert cds.resolve(apis, "Y") == ""                                               # contradicción 1: otro documento declara «Y» como su sqlViewName
    _doc(apis, "ZENT", sql_view="ZOTHER")
    assert cds.resolve(apis, "ZENT") == ""                                            # contradicción 2: el propio documento declara OTRA vista SQL (ODP lo importa como ZOTHER)
    assert cds.resolve(apis, "ZOTHER") == "ZENT"                                      # y ese otro nombre sí apunta a él


def test_a_direct_match_is_kept_when_the_document_agrees_or_declares_nothing(apis):
    _doc(apis, "ZSELF", sql_view="ZSELF")                                             # declara como sqlViewName su propio nombre: consistente
    _doc(apis, "ZCDS_DSP_DIM_ADRC")                                                   # view entity: no declara nada
    assert cds.resolve(apis, "ZSELF") == "ZSELF"
    assert cds.resolve(apis, "ZCDS_DSP_DIM_ADRC") == "ZCDS_DSP_DIM_ADRC"              # control positivo (ADRC) sin regresión


def test_unknown_unsafe_or_missing_inputs_never_produce_a_link(apis, tmp_path):
    _doc(apis, "ZCDS_DSP_BILLING", sql_view="ZCDS_BILLING")
    for bad in ("", "  ", "../x", "a/b", "a\\b", ".hidden", "NO_EXISTE"):
        assert cds.resolve(apis, bad) == ""
    assert cds.resolve(tmp_path / "no-dir", "ZCDS_BILLING") == ""
    assert main._catalog_cds_link("") == "" and main._catalog_cds_link("ABAPTABLES.T001") == "" and main._catalog_cds_link("ABAP_CDS.NO_EXISTE$F") == ""


def test_the_index_follows_the_content_of_the_folder(apis):
    assert cds.resolve(apis, "ZCDS_BILLING") == ""
    _doc(apis, "ZCDS_DSP_BILLING", sql_view="ZCDS_BILLING")                           # un documento importado después (APIS_ONLY) pasa a resolverse sin reiniciar
    assert cds.resolve(apis, "ZCDS_BILLING") == "ZCDS_DSP_BILLING"
    os.remove(apis / "ZCDS_DSP_BILLING.md")
    assert cds.resolve(apis, "ZCDS_BILLING") == ""


def test_data_product_injection_uses_the_same_resolver(apis):
    _doc(apis, "ZCDS_DSP_BILLING", sql_view="ZCDS_BILLING")
    body = "## {dw-gold}ZCDS_DSP_X\n\ntexto\n"
    out = main._catalog_dp_inject_cds_buttons(body, [{"display": "ZCDS_DSP_X", "origen_cds": "ZCDS_BILLING"}], "datasphere_core")
    assert "Abrir CDS View" in out and "name=ZCDS_DSP_BILLING" in out
    assert "Abrir CDS View" not in main._catalog_dp_inject_cds_buttons(body, [{"display": "ZCDS_DSP_X", "origen_cds": "NO_EXISTE"}], "datasphere_core")


@pytest.mark.parametrize("origin,doc,sql_view", [
    ("ABAP_CDS.ZCDS_BILLING$F", "ZCDS_DSP_BILLING", "ZCDS_BILLING"),
    ("ABAP_CDS.ZDSP_KONP$F", "ZCDS_DSP_FACT_KONP", "ZDSP_KONP"),
    ("ABAP_CDS.ZCDS_DIM_TSPAT$E", "ZCDS_DSP_TSPAT", "ZCDS_DIM_TSPAT"),
    ("ABAP_CDS.ZCDS_DIM_KNA1$E", "ZCDS_DSP_DIM_KNA1", "ZCDS_DIM_KNA1"),
])
def test_real_ingestion_origins_resolve_to_the_document_that_declares_their_sql_view_name(apis, origin, doc, sql_view):
    _doc(apis, "ZCDS_DSP_DIM_ADRC")                                                   # control: coincidencia directa, sin sqlViewName
    _doc(apis, doc, sql_view=sql_view)
    assert main._catalog_cds_link(origin) == doc and main._catalog_cds_link("ABAP_CDS.ZCDS_DSP_DIM_ADRC$E") == "ZCDS_DSP_DIM_ADRC"
    assert (apis / f"{doc}.md").exists()
