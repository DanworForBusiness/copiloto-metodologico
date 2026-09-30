"""test_catalog_cli_artifact_folders.py — Ticket A: el generador del catálogo de
dominios consume el layout del export CLI de Datasphere
(<cliente>/<tipo>/*.json), tratando las carpetas de tipo como FÍSICAS (no
dominios) y resolviendo el linaje en un pool unificado a nivel (cliente, dominio).

Contrato (owner): FLAT y NESTED con el mismo contenido → salida semántica
equivalente; el linaje cruza carpetas-tipo; identidad semántica (dedup + colisión
determinística en mixed-mode); las carpetas-tipo nunca se infieren como dominio;
una carpeta desconocida sigue siendo un dominio."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "catalog"))
import generate_catalog as G  # noqa: E402


# ── fixtures CSN mínimos ───────────────────────────────────────────────────────

def _view(name, from_ref):
    return {"definitions": {name: {
        "kind": "entity", "@EndUserText.label": name,
        "query": {"SELECT": {"from": {"ref": [from_ref]}, "columns": [{"ref": ["x"]}]}},
        "elements": {"x": {"type": "cds.String", "length": 10}},
    }}}


def _table(name):
    return {"definitions": {name: {
        "kind": "entity", "@EndUserText.label": name,
        "@DataWarehouse.repository": {"technicalType": {"#": "DWC_LOCAL_TABLE"}},
        "elements": {"x": {"type": "cds.String", "length": 10}},
    }}}


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _md_tree(out: Path):
    return {p.relative_to(out).as_posix(): p.read_text(encoding="utf-8")
            for p in sorted(out.rglob("*.md"))}


# ── equivalence FLAT vs NESTED ────────────────────────────────────────────────

def test_flat_and_nested_produce_equivalent_catalog(tmp_path):
    flat_in, flat_out = tmp_path / "flat_in", tmp_path / "flat_out"
    nest_in, nest_out = tmp_path / "nest_in", tmp_path / "nest_out"
    # FLAT
    _write(flat_in / "ClienteX" / "AM.json", _view("AM", "VIEW"))
    _write(flat_in / "ClienteX" / "VIEW.json", _view("VIEW", "TABLE"))
    _write(flat_in / "ClienteX" / "TABLE.json", _table("TABLE"))
    # NESTED (mismo contenido, carpetas por tipo)
    _write(nest_in / "ClienteX" / "analytic-models" / "AM.json", _view("AM", "VIEW"))
    _write(nest_in / "ClienteX" / "views" / "VIEW.json", _view("VIEW", "TABLE"))
    _write(nest_in / "ClienteX" / "local-tables" / "TABLE.json", _table("TABLE"))

    G.generate_all(flat_in, flat_out)
    G.generate_all(nest_in, nest_out)

    flat_md, nest_md = _md_tree(flat_out), _md_tree(nest_out)
    # Mismo set de archivos (cliente/dominio/vistas) y MISMO contenido semántico
    # (el md rendea dominio, artefactos, counts, upstreams, asociaciones, types/
    # length, naming y flow-identity — la igualdad byte cubre las 10 dimensiones).
    assert set(flat_md) == set(nest_md)
    assert flat_md == nest_md
    # La carpeta-tipo NUNCA es dominio: nada bajo .../views/ ni .../analytic-models/.
    assert not any("/views/" in k or "/analytic-models/" in k or "/local-tables/" in k
                   for k in nest_md)


def test_cross_folder_lineage_resolves_in_unified_pool(tmp_path):
    # analytic-models → views → local/remote tables, cada uno en su carpeta.
    d = tmp_path / "ClienteX"
    _write(d / "analytic-models" / "AM.json", _view("AM", "VIEW"))
    _write(d / "views" / "VIEW.json", _view("VIEW", "TABLE"))
    _write(d / "local-tables" / "TABLE.json", _table("TABLE"))
    jsons = sorted((tmp_path).rglob("*.json"))
    entities, _ = G.load_folder(json_files=jsons, txt_files=[])
    assert set(entities) == {"AM", "VIEW", "TABLE"}
    # El upstream directo de una entidad en una carpeta se resuelve contra otra.
    assert "VIEW" in entities["AM"].upstreams
    assert "TABLE" in entities["VIEW"].upstreams


# ── dedup / colisión mixed-mode ────────────────────────────────────────────────

def test_mixed_mode_identical_content_dedups_no_double_count(tmp_path):
    # Mismo artefacto plano y anidado, contenido idéntico → UNA entidad.
    _write(tmp_path / "ClienteX" / "TABLE.json", _table("TABLE"))
    _write(tmp_path / "ClienteX" / "local-tables" / "TABLE.json", _table("TABLE"))
    jsons = sorted(tmp_path.rglob("*.json"))
    entities, _ = G.load_folder(json_files=jsons, txt_files=[])
    assert list(entities) == ["TABLE"]   # sin doble conteo


def test_mixed_mode_divergent_content_raises_deterministic_collision(tmp_path):
    # Misma identidad, contenido DISTINTO, carpetas físicas distintas → colisión.
    _write(tmp_path / "ClienteX" / "TABLE.json", _table("TABLE"))
    diff = _table("TABLE"); diff["definitions"]["TABLE"]["elements"]["y"] = {"type": "cds.Integer"}
    _write(tmp_path / "ClienteX" / "local-tables" / "TABLE.json", diff)
    jsons = sorted(tmp_path.rglob("*.json"))
    with pytest.raises(G.CatalogCollisionError):
        G.load_folder(json_files=jsons, txt_files=[])


def test_same_folder_multiple_files_never_a_collision(tmp_path):
    # Dos archivos distintos en la MISMA carpeta (legacy) nunca son colisión, aun
    # con nombres de entidad diferentes — comportamiento legacy intacto.
    _write(tmp_path / "ClienteX" / "A.json", _table("A"))
    _write(tmp_path / "ClienteX" / "B.json", _table("B"))
    entities, _ = G.load_folder(tmp_path / "ClienteX")
    assert set(entities) == {"A", "B"}


# ── carpeta desconocida = dominio; nested bajo dominio real ────────────────────

def test_unknown_folder_stays_a_domain_and_type_folder_is_stripped():
    # Directo sobre el helper de resolución (cliente, dominio).
    f = G._client_domain_for
    assert f(("ClienteX", "A.json")) == ("ClienteX", "General")
    assert f(("ClienteX", "views", "A.json")) == ("ClienteX", "General")
    assert f(("ClienteX", "Ventas", "A.json")) == ("ClienteX", "Ventas")
    assert f(("ClienteX", "Ventas", "views", "A.json")) == ("ClienteX", "Ventas")
    assert f(("A.json",)) is None


def test_domain_from_real_domain_folder_with_nested_types(tmp_path):
    out = tmp_path / "out"
    _write(tmp_path / "in" / "ClienteX" / "Ventas" / "views" / "VIEW.json", _view("VIEW", "TABLE"))
    _write(tmp_path / "in" / "ClienteX" / "Ventas" / "local-tables" / "TABLE.json", _table("TABLE"))
    G.generate_all(tmp_path / "in", out)
    md = _md_tree(out)
    assert any(k.startswith("ClienteX/Ventas/") for k in md)          # dominio real preservado
    assert not any("/views/" in k or "/local-tables/" in k for k in md)  # tipo no es dominio
