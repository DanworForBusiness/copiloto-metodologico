"""Data Products regenera cuando cambia el criterio del linaje publicado.

El cache incremental de Data Products firma cada carpeta con `GENERATOR_VERSION` + sus CSN (`_sha_folder`), no con el código del
generador. Como el linaje publicado cambió (fuente conductora: `generate_catalog.published_lineage`, que Data Products consume), la
versión sube a `data-products-v16`: sin eso, una carpeta sin cambios de CSN conservaría la clasificación anterior y quedaría
inconsistente con el Catálogo de Dominios."""
from __future__ import annotations

from tools.catalog import generate_data_products_catalog as dp


def test_generator_version_reflects_the_published_lineage_criterion():
    assert dp.GENERATOR_VERSION == "data-products-v16"


def test_folder_signature_changes_with_the_generator_version(tmp_path, monkeypatch):
    """Misma carpeta y mismos CSN: la firma cambia al cambiar la versión → el cache incremental regenera las fichas existentes."""
    (tmp_path / "3VF_X.json").write_text('{"definitions": {}}', encoding="utf-8")
    current = dp._sha_folder(tmp_path)
    assert current == dp._sha_folder(tmp_path)                                   # determinística
    monkeypatch.setattr(dp, "GENERATOR_VERSION", "data-products-v15")
    assert dp._sha_folder(tmp_path) != current


def test_data_products_consume_the_published_lineage():
    assert dp._published_lineage is not None
    from tools.catalog import generate_catalog as gc
    assert dp._published_lineage is gc.published_lineage
