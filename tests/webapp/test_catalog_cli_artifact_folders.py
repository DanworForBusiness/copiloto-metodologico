"""test_catalog_cli_artifact_folders.py — Ticket A: el generador del catálogo de
dominios consume el layout del export CLI de Datasphere
(<cliente>/<tipo>/*.json), tratando las carpetas de tipo como FÍSICAS (no
dominios) y resolviendo el linaje en un pool unificado a nivel (cliente, dominio).

Contrato (owner): FLAT y NESTED con el mismo contenido → salida semántica
equivalente; el linaje cruza carpetas-tipo; identidad semántica (dedup + colisión
determinística en mixed-mode); las carpetas-tipo nunca se infieren como dominio;
una carpeta desconocida sigue siendo un dominio."""
from __future__ import annotations

import copy as copy_module
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "catalog"))
import generate_catalog as G  # noqa: E402
import generate_space_catalog as GS  # noqa: E402

# Los 9 folders de tipo del export CLI de Datasphere (contrato owner).
_ALL_TYPE_DIRS = [
    "data-access-controls", "task-chains", "transformation-flows", "data-flows",
    "remote-tables", "analytic-models", "local-tables", "replication-flows", "views",
]


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


# ── firma semántica: ignora _meta.dependencies.folderAssignment ───────────────
# Forense producción 2026-09-30 (ver _handoffs/catalog-cli-production-
# regeneration-regression.md, CASE B): 28/28 colisiones reales mixed-mode de un
# cliente real resultaron ser ÚNICAMENTE este campo (bookkeeping de "carpeta del
# Data Builder" de SAP Datasphere, nunca leído por este generador) — el legacy
# export lo escribe, el export CLI no. Cero drift real de definición en los 28.

def _with_folder_assignment(defn_payload, name, folder_id):
    payload = copy_module.deepcopy(defn_payload)
    payload["definitions"][name]["_meta"] = {"dependencies": {"folderAssignment": folder_id}}
    return payload


def test_folder_assignment_present_vs_absent_dedups_no_collision(tmp_path):
    # Caso 1 (forense): legacy trae folderAssignment, CLI no lo trae, resto
    # idéntico → misma firma semántica, dedup silencioso, SIN colisión.
    _write(tmp_path / "ClienteX" / "TABLE.json",
           _with_folder_assignment(_table("TABLE"), "TABLE", "Folder_ABC"))
    _write(tmp_path / "ClienteX" / "local-tables" / "TABLE.json", _table("TABLE"))
    jsons = sorted(tmp_path.rglob("*.json"))
    entities, _ = G.load_folder(json_files=jsons, txt_files=[])
    assert list(entities) == ["TABLE"]


def test_folder_assignment_differing_value_still_dedups(tmp_path):
    # Caso 2 (forense): folderAssignment presente en AMBOS lados pero con un
    # valor DISTINTO (dos IDs de carpeta distintos) → sigue siendo equivalente,
    # SIN colisión — el valor de este campo puntual nunca importa.
    _write(tmp_path / "ClienteX" / "TABLE.json",
           _with_folder_assignment(_table("TABLE"), "TABLE", "Folder_ABC"))
    _write(tmp_path / "ClienteX" / "local-tables" / "TABLE.json",
           _with_folder_assignment(_table("TABLE"), "TABLE", "Folder_XYZ"))
    jsons = sorted(tmp_path.rglob("*.json"))
    entities, _ = G.load_folder(json_files=jsons, txt_files=[])
    assert list(entities) == ["TABLE"]


def test_other_meta_field_difference_still_raises_collision(tmp_path):
    # Caso 3: NO se ignora `_meta` entero, sólo `dependencies.folderAssignment`.
    # Una diferencia en CUALQUIER OTRA clave de `_meta` debe seguir colisionando.
    legacy = _table("TABLE")
    legacy["definitions"]["TABLE"]["_meta"] = {"someOtherField": "x"}
    nested = _table("TABLE")
    nested["definitions"]["TABLE"]["_meta"] = {"someOtherField": "y"}
    _write(tmp_path / "ClienteX" / "TABLE.json", legacy)
    _write(tmp_path / "ClienteX" / "local-tables" / "TABLE.json", nested)
    jsons = sorted(tmp_path.rglob("*.json"))
    with pytest.raises(G.CatalogCollisionError):
        G.load_folder(json_files=jsons, txt_files=[])


def test_real_definition_difference_still_raises_collision_with_folder_assignment(tmp_path):
    # Caso 4: una divergencia REAL (columna distinta) sigue disparando
    # CatalogCollisionError normalmente, aun con folderAssignment de por medio
    # (no alcanza con que folderAssignment sea la única excusa para no comparar).
    legacy = _with_folder_assignment(_table("TABLE"), "TABLE", "Folder_ABC")
    nested = _table("TABLE")
    nested["definitions"]["TABLE"]["elements"]["y"] = {"type": "cds.Integer"}
    _write(tmp_path / "ClienteX" / "TABLE.json", legacy)
    _write(tmp_path / "ClienteX" / "local-tables" / "TABLE.json", nested)
    jsons = sorted(tmp_path.rglob("*.json"))
    with pytest.raises(G.CatalogCollisionError):
        G.load_folder(json_files=jsons, txt_files=[])


def test_whitespace_and_key_order_remain_equivalent_with_folder_assignment(tmp_path):
    # Caso 5: la canonicalización ya ignoraba key-order/whitespace (sort_keys +
    # JSON parseado) — confirmar que sigue así DESPUÉS del cambio, combinado con
    # folderAssignment presente de un solo lado.
    legacy = _with_folder_assignment(_table("TABLE"), "TABLE", "Folder_ABC")
    (tmp_path / "ClienteX").mkdir(parents=True, exist_ok=True)
    (tmp_path / "ClienteX" / "TABLE.json").write_text(
        json.dumps(legacy, indent=4, sort_keys=False), encoding="utf-8")
    nested = _table("TABLE")
    _write(tmp_path / "ClienteX" / "local-tables" / "TABLE.json", nested)
    jsons = sorted(tmp_path.rglob("*.json"))
    entities, _ = G.load_folder(json_files=jsons, txt_files=[])
    assert list(entities) == ["TABLE"]


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


# ══ GATES CONTRACTUALES (A1–A5) ════════════════════════════════════════════════

# A1 — 9-FOLDER MATRIX: cada carpeta-tipo se reconoce, nunca es dominio, y preserva
# cliente/dominio semántico (gate de interpretación de path, no de lineage).
@pytest.mark.parametrize("tdir", _ALL_TYPE_DIRS)
def test_a1_type_folder_recognized_and_never_a_domain(tdir):
    assert G._is_artifact_type_dir(tdir), tdir
    # bajo cliente (sin dominio) → General; bajo cliente/dominio → ese dominio.
    assert G._client_domain_for(("Cliente", tdir, "A.json")) == ("Cliente", "General")
    assert G._client_domain_for(("Cliente", "Ventas", tdir, "A.json")) == ("Cliente", "Ventas")
    # el nombre del tipo NUNCA queda como dominio.
    assert G._client_domain_for(("Cliente", tdir, "A.json"))[1] != tdir


@pytest.mark.parametrize("variant", ["VIEWS", "Analytic-Models", "local_tables", "Remote Tables"])
def test_a1_type_folder_recognition_is_casing_and_separator_insensitive(variant):
    # `_catalog_dir_kind` normaliza casing y -/_ → espacio; el reconocimiento es
    # robusto a cómo venga escrita la carpeta.
    assert G._is_artifact_type_dir(variant), variant


def test_a1_all_nine_types_in_one_client_collapse_to_general(tmp_path):
    for i, tdir in enumerate(_ALL_TYPE_DIRS):
        _write(tmp_path / "in" / "ClienteX" / tdir / "art.json", _table(f"ENT{i}"))
    G.generate_all(tmp_path / "in", tmp_path / "out")
    md = _md_tree(tmp_path / "out")
    # Todo cae en un único dominio General; ninguna carpeta-tipo aparece como dominio
    # (se inspeccionan los SEGMENTOS DE DIRECTORIO, no el nombre de archivo de vista).
    assert md, "debería generar al menos un flujo"
    assert all(k.startswith("ClienteX/General/") for k in md), md
    assert not any(G._is_artifact_type_dir(part) for k in md for part in Path(k).parts[:-1])


# A2 — DOMAIN DEPTH CONTRACT (+ profundidad adicional = comportamiento legacy real).
def test_a2_domain_depth_contract_and_deeper_levels():
    f = G._client_domain_for
    assert f(("Cliente", "A.json")) == ("Cliente", "General")
    assert f(("Cliente", "views", "A.json")) == ("Cliente", "General")
    assert f(("Cliente", "Ventas", "A.json")) == ("Cliente", "Ventas")
    assert f(("Cliente", "Ventas", "views", "A.json")) == ("Cliente", "Ventas")
    # Profundidad adicional: LEGACY real = cliente=parts[0], dominio=parts[1]; los
    # niveles más profundos NO crean dominios nuevos (se colapsan en el dominio de
    # nivel 2). El código legacy sólo miraba parts[0]/parts[1]; se preserva EXACTO.
    assert f(("Cliente", "Ventas", "Subcarpeta", "A.json")) == ("Cliente", "Ventas")
    assert f(("Cliente", "Ventas", "Subcarpeta", "views", "A.json")) == ("Cliente", "Ventas")


# A3 — ESPACIOS / CONEXIONES: reconocimiento case-insensitive por su lógica especial,
# y NUNCA como dominios genéricos.
def test_a3_find_subdir_is_case_insensitive(tmp_path):
    (tmp_path / "Espacios").mkdir()
    (tmp_path / "conexiones").mkdir()
    assert GS._find_subdir(tmp_path, "Espacios") == tmp_path / "Espacios"
    assert GS._find_subdir(tmp_path, "espacios") == tmp_path / "Espacios"     # minúscula ↔ Capitalizado
    assert GS._find_subdir(tmp_path, "Conexiones") == tmp_path / "conexiones"  # Capitalizado ↔ minúscula
    assert GS._find_subdir(tmp_path, "nope") is None


def test_a3_discover_client_roots_both_casings(tmp_path):
    (tmp_path / "ClienteCap" / "Espacios").mkdir(parents=True)
    (tmp_path / "ClienteLow" / "conexiones").mkdir(parents=True)
    roots = dict(GS._discover_client_roots(tmp_path))
    assert "ClienteCap" in roots and "ClienteLow" in roots


def test_a3_espacios_conexiones_never_become_domains(tmp_path):
    out = tmp_path / "out"
    _write(tmp_path / "in" / "ClienteX" / "espacios" / "space.json", _table("S"))
    _write(tmp_path / "in" / "ClienteX" / "Conexiones" / "conn_space_x.json", _table("C"))
    _write(tmp_path / "in" / "ClienteX" / "views" / "VIEW.json", _view("VIEW", "TABLE"))
    _write(tmp_path / "in" / "ClienteX" / "local-tables" / "TABLE.json", _table("TABLE"))
    G.generate_all(tmp_path / "in", out)
    md = _md_tree(out)
    assert not any("espacios" in k.lower() or "conexiones" in k.lower() for k in md)
    assert any(k.startswith("ClienteX/General/") for k in md)


# A4 — DATA PRODUCTS CONTROL: el generador de dominios NO ingiere carpetas DP_
# (las maneja generate_data_products_catalog, cross-folder, sin tocar acá); no hay
# double-count y las carpetas-tipo no se interpretan como semántica funcional.
def test_a4_dp_folders_excluded_from_domain_generator(tmp_path):
    out = tmp_path / "out"
    _write(tmp_path / "in" / "DP_Ventas" / "analytic-models" / "AM.json", _view("AM", "VIEW"))
    _write(tmp_path / "in" / "ClienteX" / "views" / "VIEW.json", _view("VIEW", "TABLE"))
    _write(tmp_path / "in" / "ClienteX" / "local-tables" / "TABLE.json", _table("TABLE"))
    G.generate_all(tmp_path / "in", out)
    md = _md_tree(out)
    assert not any("DP_" in k for k in md)                      # DP_ nunca es dominio
    assert any(k.startswith("ClienteX/General/") for k in md)   # el resto sí


# A5 — DEDUP/COLLISION CANONICALIZATION: la equivalencia de contenido se decide por
# el definition PARSEADO y serializado con sort_keys (json.dumps sort_keys=True) —
# NO depende de whitespace (el parseo lo elimina), NI del orden de keys, NI del path.
def test_a5_dedup_is_canonical_not_raw_key_order(tmp_path):
    t1 = _table("TABLE")
    d = t1["definitions"]["TABLE"]
    # Mismo contenido semántico, ORDEN DE KEYS distinto.
    reordered = {"elements": d["elements"], "@EndUserText.label": d["@EndUserText.label"],
                 "@DataWarehouse.repository": d["@DataWarehouse.repository"], "kind": d["kind"]}
    t2 = {"definitions": {"TABLE": reordered}}
    _write(tmp_path / "ClienteX" / "TABLE.json", t1)
    _write(tmp_path / "ClienteX" / "local-tables" / "TABLE.json", t2)
    entities, _ = G.load_folder(json_files=sorted(tmp_path.rglob("*.json")), txt_files=[])
    assert list(entities) == ["TABLE"]   # dedup determinístico, SIN colisión


def test_a5_divergent_relevant_content_is_a_collision(tmp_path):
    _write(tmp_path / "ClienteX" / "TABLE.json", _table("TABLE"))
    diff = _table("TABLE")
    diff["definitions"]["TABLE"]["elements"]["y"] = {"type": "cds.Integer"}
    _write(tmp_path / "ClienteX" / "local-tables" / "TABLE.json", diff)
    with pytest.raises(G.CatalogCollisionError):
        G.load_folder(json_files=sorted(tmp_path.rglob("*.json")), txt_files=[])
