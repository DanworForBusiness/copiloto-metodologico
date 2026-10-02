"""`legacy_bridge` (Fase 1, hito M0): unico punto de acceso al legacy.

  * los simbolos re-exportados SON los objetos del legacy (un renombre legacy falla aca, claro);
  * `classify_extraction` es el objeto unico del criterio (guardia complementaria a
    tests/test_compliance_rubric.py);
  * el snapshot de compatibilidad de `_eligible` / agrupacion de pool coincide con
    `generate_all` sobre un arbol sintetico que cubre todas las ramas de elegibilidad.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from tools.catalog import generate_catalog as gc
from tools.catalog.modeling_assessment import legacy_bridge as bridge


# ── identidad de simbolos ──────────────────────────────────────────────────
@pytest.mark.parametrize("public,legacy", [
    ("load_folder", "load_folder"), ("Entity", "Entity"), ("CatalogCollisionError", "CatalogCollisionError"),
    ("is_cube", "_is_cube"), ("cube_subgraph", "_cube_subgraph"), ("capa_of", "_capa_of"),
    ("parse_name", "parse_name"), ("typed_artifact", "_typed_artifact"), ("is_sapi_remote", "_is_sapi_remote"),
    ("CAPA", "CAPA"), ("OTROS", "OTROS"), ("client_domain_for", "_client_domain_for"),
    ("is_artifact_type_dir", "_is_artifact_type_dir"), ("catalog_dir_kind", "_catalog_dir_kind"),
    ("CATALOG_IGNORED_DIRS", "CATALOG_IGNORED_DIRS"), ("CATALOG_SOURCE_EXTENSIONS", "CATALOG_SOURCE_EXTENSIONS"),
    ("file_sha256", "_catalog_sha256"),
])
def test_bridge_symbols_are_the_legacy_objects(public, legacy):
    assert getattr(bridge, public) is getattr(gc, legacy)


def test_classify_extraction_is_the_single_shared_criterion_object():
    from tools.catalog.compliance_criteria import classify_extraction as shared
    assert bridge.classify_extraction is shared is gc.classify_extraction


def test_bridge_keeps_the_legacy_compatibility_contract_documented():
    src = inspect.getsource(bridge)
    assert "LEGACY COMPATIBILITY LOGIC" in src
    assert "test_pool_groups_parity_with_generate_all" in src
    # la logica de elegibilidad vive SOLO en el bridge
    pkg = Path(bridge.__file__).parent
    others = [p.name for p in pkg.glob("*.py") if p.name != "legacy_bridge.py" and "_eligible" in p.read_text(encoding="utf-8")]
    assert others == []


# ── paridad del pool con generate_all ──────────────────────────────────────
def _touch(root: Path, rel: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{}" if rel.endswith(".json") else "-- sql", encoding="utf-8")


# Un caso por rama de elegibilidad / agrupacion de `generate_all`.
_TREE = [
    "ClientA/flat.json",                      # legacy plano → (ClientA, General)
    "ClientA/flat.txt",                       # txt del mismo grupo
    "ClientB/views/v.json",                   # carpeta-tipo removida → (ClientB, General)
    "ClientB/analytic-models/am.json",
    "ClientB/local-tables/t.json",
    "ClientB/replication-flows/rf.json",
    "ClientB/views/v.txt",
    "ClientC/DomX/views/c.json",              # (ClientC, DomX) con carpeta-tipo
    "ClientC/DomX/d.json",
    "ClientC/DomY/e.json",
    "ClientC/DomY/e.txt",
    "ClientD/Espacios/ignored.json",          # carpeta ignorada (espacios)
    "ClientD/ok.json",
    "ClientE/api/ignored.json",               # carpeta ignorada (api)
    "ClientE/APIs/ignored2.json",
    "DP_Foo/dp.json",                         # prefijo DP_ excluido
    "dp_bar/dp2.json",                        # DP_ en minuscula tambien
    "ClientF/02. Data Products/dpx.json",     # carpeta ignorada con prefijo numerico
    "ClientF/keep.json",
    "ClientG/unknown-type/j.json",            # carpeta desconocida = nivel de dominio
    "top.json",                               # sin cliente → None
    "ClientH/Data Flows/df.json",             # carpeta-tipo con espacio
    "ClientH/Remote-Tables/rt.json",          # carpeta-tipo con guion y mayuscula
    "orphan_txt/only.txt",                    # txt sin json → sin grupo
]


def test_pool_groups_parity_with_generate_all(tmp_path, monkeypatch):
    inbound = tmp_path / "inbound"
    for rel in _TREE:
        _touch(inbound, rel)

    calls = []

    def fake_process_folder(src, out_root, cliente=None, dominio=None, *, json_files=None, txt_files=None):
        rel = Path(src).relative_to(inbound).parts          # generate_all pasa src = inbound/cliente_dir/dominio_dir
        calls.append(((rel[0], rel[1]), sorted(json_files), sorted(txt_files)))
        return "ok"

    monkeypatch.setattr(gc, "process_folder", fake_process_folder)
    monkeypatch.setattr(gc, "_sync_apis", lambda *_a, **_k: 0)
    gc.generate_all(inbound, tmp_path / "out")

    legacy = {key: (jsons, txts) for key, jsons, txts in calls}
    mine = {key: (g["jsons"], g["txts"]) for key, g in bridge.legacy_pool_groups(inbound).items()}
    assert mine == legacy
    # sanity: el arbol ejercita ramas reales (no es una comparacion vacia)
    assert len(mine) >= 6
    assert ("ClientB", "General") in mine and ("ClientC", "DomX") in mine and ("ClientG", "unknown-type") in mine
    for excluded in ("Espacios", "api", "APIs", "Data Products"):
        assert not any(excluded in str(p) for jsons, _ in mine.values() for p in jsons)
    assert not any("DP_" in c.upper() or "DP_" in d.upper() for c, d in mine)


def test_pool_groups_returns_sorted_lists(tmp_path):
    for rel in ["C/views/b.json", "C/views/a.json", "C/analytic-models/z.json"]:
        _touch(tmp_path, rel)
    g = bridge.legacy_pool_groups(tmp_path)[("C", "General")]
    assert g["jsons"] == sorted(g["jsons"])
