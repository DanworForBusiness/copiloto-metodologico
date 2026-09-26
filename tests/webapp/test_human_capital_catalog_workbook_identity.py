"""
test_human_capital_catalog_workbook_identity.py — CCHH Catalog Foundation,
config governance corrective: `Catalogos_CCHH.xlsx` vive en el MISMO
site/drive ya configurado para el resto de Capital Humano
(`HC_SHAREPOINT_SITE_ID`/`HC_SHAREPOINT_DRIVE_ID`, config.py) -- nunca una
segunda variable de drive redundante (`HC_CATALOG_DRIVE_ID`, removida). La
única configuración nueva del catálogo es `HC_CATALOG_ITEM_ID`.
`CATALOG_WORKBOOK_PATH` es documentación/discovery pura, nunca identidad
de runtime -- ver `workbook_source()`."""
from __future__ import annotations

import pytest

import config
from webapp.services import human_capital_catalog_repository as repo


def test_workbook_source_uses_the_existing_sharepoint_drive_id(monkeypatch):
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "drive-existente")
    monkeypatch.setattr(config, "HC_CATALOG_ITEM_ID", "item-catalogo")
    source = repo.workbook_source()
    assert source["drive_id"] == "drive-existente"


def test_workbook_source_returns_both_ids_when_configured(monkeypatch):
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "drive-existente")
    monkeypatch.setattr(config, "HC_CATALOG_ITEM_ID", "item-catalogo")
    assert repo.workbook_source() == {"drive_id": "drive-existente", "item_id": "item-catalogo"}


def test_workbook_source_raises_when_sharepoint_drive_id_missing(monkeypatch):
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "")
    monkeypatch.setattr(config, "HC_CATALOG_ITEM_ID", "item-catalogo")
    with pytest.raises(repo.CatalogConfigIncompleteError):
        repo.workbook_source()


def test_workbook_source_raises_when_catalog_item_id_missing(monkeypatch):
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "drive-existente")
    monkeypatch.setattr(config, "HC_CATALOG_ITEM_ID", "")
    with pytest.raises(repo.CatalogConfigIncompleteError):
        repo.workbook_source()


def test_hc_catalog_drive_id_no_longer_exists_as_a_config_var():
    # Gobernanza de config: eliminada por completo, nunca "vacía por
    # default" -- una segunda variable de drive redundante sería
    # exactamente la duplicación de autoridad que este fix corrige.
    assert not hasattr(config, "HC_CATALOG_DRIVE_ID")


def test_hc_catalog_path_no_longer_exists_as_a_config_var():
    # El path es documentación (`CATALOG_WORKBOOK_PATH`, en el propio
    # repository), nunca una env var de runtime.
    assert not hasattr(config, "HC_CATALOG_PATH")


def test_catalog_workbook_path_constant_is_documentation_only():
    # Existe como referencia humana...
    assert repo.CATALOG_WORKBOOK_PATH == "CCHH HUB/Configuracion/Catalogos_CCHH.xlsx"


def test_no_runtime_consumer_reads_catalog_workbook_path(monkeypatch):
    # ...pero NINGÚN consumer de runtime la lee para resolver nada: mutarla
    # no puede cambiar el comportamiento de `workbook_source()`/
    # `fetch_workbook_bytes()`. Se prueba por comportamiento, no por
    # inspección de código: si algo empezara a depender de ella, este test
    # lo detectaría (el resultado no cambiaría con el valor mutado).
    monkeypatch.setattr(config, "HC_SHAREPOINT_DRIVE_ID", "drive-existente")
    monkeypatch.setattr(config, "HC_CATALOG_ITEM_ID", "item-catalogo")
    before = repo.workbook_source()
    monkeypatch.setattr(repo, "CATALOG_WORKBOOK_PATH", "cualquier/otra/ruta.xlsx")
    after = repo.workbook_source()
    assert before == after
