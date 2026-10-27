"""P1 · identidad del cliente: una sola clave canónica por cliente, sin abrir un segundo catálogo por una diferencia de mayúsculas.

El Assessment ya trata al cliente en minúsculas; el directorio del pool/Catálogo respetaba lo tipeado, así que en un sistema de archivos que distingue mayúsculas (Linux, App Platform)
«Acme» y «ACME» eran dos catálogos con UNA sola identidad de assessment. Estas pruebas fijan la resolución (pura) y su integración con la publicación segura.
"""
from __future__ import annotations

import os

import pytest

from tests.publication_fakes import CLIENT
from tests.publication_fixtures import ctx  # noqa: F401
from tools.catalog import publication_txn as txn


@pytest.mark.parametrize("requested, existing, expected", [
    ("Acme", [], "Acme"),                                  # sin variantes previas: el nombre pedido
    ("Acme", ["Acme"], "Acme"),
    ("ACME", ["Acme"], "Acme"),                            # otra capitalización: se reusa el directorio existente
    ("acme", ["Acme", "Otro"], "Acme"),
    ("Acme", ["Otro", "Tercero"], "Acme"),                 # los demás clientes no influyen
    ("Pampa", ["Pampas", "pampa_x"], "Pampa"),             # solo coincide la igualdad completa sin distinguir mayúsculas
])
def test_canonical_client_dir_reuses_the_single_existing_variant(requested, existing, expected):
    assert txn.canonical_client_dir(requested, existing) == expected


@pytest.mark.parametrize("requested", ["Acme", "ACME", "acme", "aCmE"])
def test_two_existing_case_variants_are_a_conflict_whatever_is_requested(requested):
    with pytest.raises(txn.ClientKeyConflict) as exc:
        txn.canonical_client_dir(requested, ["Acme", "ACME", "Otro"])
    assert exc.value.variants == ["ACME", "Acme"]


def test_existing_client_dirs_reads_pool_lineage_runs_and_catalog_but_not_hidden_dirs(tmp_path):
    catalog, pool = tmp_path / "catalog_local", tmp_path / "catalog_local" / ".csn_cache"
    (pool / "Acme").mkdir(parents=True)
    (catalog / ".lineage_runs" / "Beta").mkdir(parents=True)
    (catalog / "Gamma").mkdir()
    (catalog / ".publication_state").mkdir()
    (catalog / "index.md").write_text("x", encoding="utf-8")
    assert txn.existing_client_dirs(catalog, pool) == {"Acme", "Beta", "Gamma"}
    assert txn.existing_client_dirs(tmp_path / "missing", tmp_path / "missing" / "pool") == set()


def test_publishing_with_another_capitalization_reuses_the_existing_client_catalog(ctx):          # noqa: F811
    first, _ = ctx.publish(client=CLIENT)
    assert first.status == txn.COMPLETED and first.client_dir == CLIENT
    names_before = ctx.pool_names()
    res, _ = ctx.publish(client=CLIENT.upper())
    assert res.status == txn.COMPLETED and res.client_dir == CLIENT                              # mismo directorio: NO se abrió un segundo catálogo
    assert sorted(p.name for p in ctx.pool.iterdir() if p.is_dir()) == [CLIENT]
    assert ctx.pool_names() == names_before
    assert [p.name for p in (ctx.catalog / ".lineage_runs").iterdir()] == [CLIENT]


@pytest.mark.skipif(os.path.normcase("A") == os.path.normcase("a"), reason="el sistema de archivos no distingue mayúsculas: no puede existir la duplicación")
def test_an_existing_split_identity_is_refused_without_touching_anything(ctx):                     # noqa: F811
    ctx.publish(client=CLIENT)
    (ctx.pool / CLIENT.upper()).mkdir()                                                           # duplicación heredada: dos directorios del mismo cliente
    before = ctx.digest()
    res, _ = ctx.publish(client=CLIENT)
    assert (res.ok, res.status, res.error_code) == (False, txn.FAILED_NO_CHANGE, "CLIENT_KEY_CONFLICT")
    assert sorted(f["technical_name"] for f in res.failures) == sorted([CLIENT, CLIENT.upper()])
    assert ctx.digest() == before and not txn.has_pending(ctx.catalog)


def test_the_legacy_path_keeps_the_typed_capitalization(ctx):                                    # noqa: F811
    """Con el interruptor apagado el comportamiento es el de siempre (la canonicalización es parte de la publicación segura)."""
    res, _ = ctx.publish(client=CLIENT, safe=False)
    assert res.ok and [d.name for d in ctx.pool.iterdir() if d.is_dir()] == [CLIENT]
    res2, _ = ctx.publish(client=CLIENT.upper(), safe=False)
    assert res2.ok and sorted(d.name for d in ctx.pool.iterdir() if d.is_dir()) in (sorted([CLIENT, CLIENT.upper()]), [CLIENT])   # sin cambios de comportamiento (según el sistema de archivos)
