"""Definición AUTORITATIVA de objetos con reemplazo de Space confirmado (manifiesto con `replaces`) en el generador del Catálogo.

Se acredita por `object_type` + `technical_name` (carpeta tipada real) y por el sha256 del manifiesto vigente; un homónimo en otra carpeta (paquete legacy
`VD_Material.json` en la raíz del pool, como en Biogénesis) se conserva como antecedente pero nunca sustituye ni se fusiona; el orden de recorrido no decide;
falta o integridad no acreditada ⇒ `CatalogAuthorityError` (la publicación hace rollback). Sin reemplazo confirmado, la regla de colisión no cambia."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.catalog import generate_catalog as gc
from tools.catalog.publication_txn import sha256_text_file

CLIENT = "Biogenesis"


def _defn(label: str, keys=("MATNR",)) -> dict:
    return {"kind": "entity", "@EndUserText.label": label,
            "elements": {k: {"type": "cds.String", "length": 18, "key": True} for k in keys} | {"MAKTX": {"type": "cds.String", "length": 40}}}


def _world(tmp_path: Path, *, replaces=True, typed=True, sha_override: str | None = None):
    catalog = tmp_path / "catalog_local"
    pool = catalog / ".csn_cache" / CLIENT
    (pool / "views").mkdir(parents=True)
    # paquete legacy HOMÓNIMO en la raíz del pool: define VD_Material con contenido de la era anterior (y otra entidad)
    (pool / "VD_Material.json").write_text(json.dumps({"_meta": {"creator": "CDS Compiler v1.19.2"},
                                                      "definitions": {"VD_Material": _defn("Material (PRODUCCION)", keys=("MATNR", "WERKS")),
                                                                      "VT_OTRA": _defn("Otra")}}), encoding="utf-8")
    typed_path = pool / "views" / "VD_Material.json"
    if typed:
        typed_path.write_text(json.dumps({"definitions": {"VD_Material": _defn("Material (CALIDAD)")}}), encoding="utf-8")
    manifest = {"space": "CALIDAD", "rootObject": "AM_X", "objects": [
        {"object_type": "views", "technical_name": "VD_Material", "sha256": sha_override or (sha256_text_file(typed_path) if typed else "0" * 64)}]}
    if replaces:
        manifest["replaces"] = {"space": "PRODUCCION", "tenantId": "", "manifest_sha256": "f" * 64}
    mdir = catalog / ".lineage_runs" / CLIENT / "CALIDAD" / "analytic-model" / "AM_X"
    mdir.mkdir(parents=True)
    (mdir / "lineage-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    files = [pool / "VD_Material.json"] + ([typed_path] if typed else [])
    return catalog, files


def test_homonymous_legacy_package_never_replaces_the_typed_authoritative_definition(tmp_path):
    catalog, files = _world(tmp_path)
    governed = gc._governed_names(catalog, CLIENT)
    assert governed == {"VD_Material": ("views", sha256_text_file(files[1]))}
    ents, _ = gc.load_folder(json_files=files, txt_files=[], governed_names=governed)
    vd = ents["VD_Material"]
    assert vd.label == "Material (CALIDAD)" and vd.file == "VD_Material.json" and vd.keys == ["MATNR"]   # sin fusión con la copia legacy (WERKS)
    assert ents["VT_OTRA"].label == "Otra"                                                          # el resto del paquete legacy sigue vigente


def test_reversed_traversal_order_gives_the_same_authority(tmp_path):
    catalog, files = _world(tmp_path)
    governed = gc._governed_names(catalog, CLIENT)
    a, _ = gc.load_folder(json_files=files, txt_files=[], governed_names=governed)
    b, _ = gc.load_folder(json_files=list(reversed(files)), txt_files=[], governed_names=governed)
    assert (a["VD_Material"].label, a["VD_Material"].keys) == (b["VD_Material"].label, b["VD_Material"].keys) == ("Material (CALIDAD)", ["MATNR"])


def test_divergent_hash_stops_generation_explicitly(tmp_path):
    catalog, files = _world(tmp_path, sha_override="a" * 64)
    with pytest.raises(gc.CatalogAuthorityError, match="sha256"):
        gc.load_folder(json_files=files, txt_files=[], governed_names=gc._governed_names(catalog, CLIENT))


def test_missing_typed_file_stops_generation_and_never_falls_back_to_the_homonym(tmp_path):
    catalog, files = _world(tmp_path, typed=False)
    with pytest.raises(gc.CatalogAuthorityError, match="views/VD_Material.json"):
        gc.load_folder(json_files=files, txt_files=[], governed_names=gc._governed_names(catalog, CLIENT))


def test_without_confirmed_replacement_the_collision_rule_is_unchanged(tmp_path):
    catalog, files = _world(tmp_path, replaces=False)
    assert gc._governed_names(catalog, CLIENT) == {}
    with pytest.raises(gc.CatalogCollisionError):
        gc.load_folder(json_files=files, txt_files=[], governed_names=gc._governed_names(catalog, CLIENT))
