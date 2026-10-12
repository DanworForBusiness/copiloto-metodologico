"""Linaje de Data Flows del export CLI + encabezado del detalle del flujo.

Causa que fija: el export CLI trae los Data Flows en `data-flows/DF_*.json` con la sección `dataflows` (`sources`/`targets`) y SIN `definitions`; el loader sólo
leía `definitions`, así que la tabla local que un DF produce quedaba sin proveedores y el Linaje se cortaba en ella (Biogenesis · Ocupación Almacenes:
`1TR_LAGP + 1TR_LQUA → 2VR_… → DF_… → 2TR_… → 3VF_… → AM_…` mostraba el linaje desde `2TR_…`).

Contrato:
  * el catálogo une target ← sources SOLO con lo que el propio DF declara y cuando ambos extremos existen en el pool (nunca por convención de nombres);
  * el assessment NO ve ese linaje (`dataflow_lineage=False`): su cierre trata la tabla producida por un flujo como HOJA y el flujo como procedencia;
  * el encabezado del detalle del flujo es el nombre PROPIO del modelo (cuando hay uno solo con etiqueta), y el dominio —clasificación compartida— pasa al eyebrow.
Fixtures SINTÉTICAS."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "catalog"))
import generate_catalog as G  # noqa: E402

from tools.catalog import catalog_service as cs  # noqa: E402
from tools.catalog.modeling_assessment import shadow  # noqa: E402
from webapp.services import assessment_presenter as presenter  # noqa: E402


def _write(path: Path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _view(name, *from_refs, label=None):
    frm = {"ref": [from_refs[0]]} if len(from_refs) == 1 else {"join": [{"ref": [r]} for r in from_refs]}
    return {"definitions": {name: {"kind": "entity", "@EndUserText.label": label or name,
                                   "query": {"SELECT": {"from": frm, "columns": [{"ref": ["x"]}]}},
                                   "elements": {"x": {"type": "cds.String", "length": 10}}}}}


def _table(name):
    return {"definitions": {name: {"kind": "entity", "@EndUserText.label": name,
                                   "@DataWarehouse.repository": {"technicalType": {"#": "DWC_LOCAL_TABLE"}},
                                   "elements": {"x": {"type": "cds.String", "length": 10}}}}}


def _df(name, sources, targets):
    return {"dataflows": {name: {"kind": "sap.dis.dataflow", "contents": {"processes": {}},
                                 "sources": {s: {"elements": {}} for s in sources}, "targets": {t: {"elements": {}} for t in targets}}}}


def _pool(root: Path, *, with_df=True, source_present=True):
    """AM ← 3VF ← 2TR (producida por DF desde 2VR ← 1TR_A + 1TR_B)."""
    c = root / "ClienteX"
    _write(c / "analytic-models" / "AM_OCUPACION.json", _view("AM_OCUPACION", "3VF_OCUPACION", label="Modelo analítico - Ocupación Almacenes"))
    _write(c / "views" / "3VF_OCUPACION.json", _view("3VF_OCUPACION", "2TR_OCUPACION"))
    _write(c / "local-tables" / "2TR_OCUPACION.json", _table("2TR_OCUPACION"))
    if source_present:
        _write(c / "views" / "2VR_OCUPACION.json", _view("2VR_OCUPACION", "1TR_A", "1TR_B"))
        _write(c / "local-tables" / "1TR_A.json", _table("1TR_A"))
        _write(c / "local-tables" / "1TR_B.json", _table("1TR_B"))
    if with_df:
        _write(c / "data-flows" / "DF_OCUPACION.json", _df("DF_OCUPACION", ["2VR_OCUPACION"], ["2TR_OCUPACION"]))
    return sorted(root.rglob("*.json"))


def _load(root, **kw):
    entities, _ = G.load_folder(json_files=_pool(root, **{k: v for k, v in kw.items() if k in ("with_df", "source_present")}), txt_files=[],
                                **({"dataflow_lineage": kw["dataflow_lineage"]} if "dataflow_lineage" in kw else {}))
    return entities


# ── loader ───────────────────────────────────────────────────────────────────────────────────
def test_the_table_produced_by_an_export_data_flow_inherits_the_flow_sources_and_the_chain_reaches_the_raw_tables(tmp_path):
    e = _load(tmp_path)
    assert e["2TR_OCUPACION"].upstreams == ["2VR_OCUPACION"]
    assert e["2VR_OCUPACION"].upstreams == ["1TR_A", "1TR_B"]
    assert e["3VF_OCUPACION"].upstreams == ["2TR_OCUPACION"] and e["AM_OCUPACION"].upstreams == ["3VF_OCUPACION"]


def test_without_a_data_flow_in_the_export_nothing_is_invented_from_the_2TR_naming(tmp_path):
    e = _load(tmp_path, with_df=False)
    assert e["2TR_OCUPACION"].upstreams == []                                                             # la convención 2TR_* sólo interpreta responsabilidad, nunca crea dependencias


def test_a_data_flow_source_that_is_not_in_the_pool_is_not_fabricated(tmp_path):
    e = _load(tmp_path, source_present=False)
    assert e["2TR_OCUPACION"].upstreams == []                                                             # el DF declara 2VR_OCUPACION pero el pool no lo trae: dependencia sin resolver


def test_the_assessment_loader_does_not_receive_the_flow_lineage(tmp_path):
    e = _load(tmp_path, dataflow_lineage=False)
    assert e["2TR_OCUPACION"].upstreams == []                                                             # hoja del cierre: sus fuentes son procedencia, no linaje semántico


def test_the_assessment_pool_loader_opts_out_of_the_flow_lineage(tmp_path):
    jsons = _pool(tmp_path)
    entities = shadow.load_pool({"jsons": jsons, "txts": []})
    assert entities["2TR_OCUPACION"].upstreams == [] and entities["3VF_OCUPACION"].upstreams == ["2TR_OCUPACION"]


def test_a_target_that_already_has_its_own_lineage_keeps_it(tmp_path):
    c = tmp_path / "ClienteX"
    _write(c / "views" / "V_SRC.json", _view("V_SRC", "T0"))
    _write(c / "local-tables" / "T0.json", _table("T0"))
    _write(c / "views" / "V_TGT.json", _view("V_TGT", "T0"))                                              # el target es una VISTA con query propia
    _write(c / "data-flows" / "DF_X.json", _df("DF_X", ["V_SRC"], ["V_TGT"]))
    e, _ = G.load_folder(json_files=sorted(tmp_path.rglob("*.json")), txt_files=[])
    assert e["V_TGT"].upstreams == ["T0"]


def test_a_flow_with_no_declared_sources_adds_nothing(tmp_path):
    c = tmp_path / "ClienteX"
    _write(c / "local-tables" / "T_CURR.json", _table("T_CURR"))
    _write(c / "data-flows" / "DF_CURR.json", _df("DF_CURR", [], ["T_CURR"]))
    e, _ = G.load_folder(json_files=sorted(tmp_path.rglob("*.json")), txt_files=[])
    assert e["T_CURR"].upstreams == []


def test_legacy_df_files_keep_their_behaviour(tmp_path):
    # layout legacy: DF_*.json con `definitions` (target sin query + source de capa 1) — la regla previa sigue vigente.
    _write(tmp_path / "ClienteX" / "DF_L.json", {"definitions": {
        "1TR_SRC": {"kind": "entity", "@DataWarehouse.remote.entity": "SRC", "elements": {"x": {"type": "cds.String", "length": 1}}},
        "2TR_L": {"kind": "entity", "elements": {"x": {"type": "cds.String", "length": 1}}}}})
    e, _ = G.load_folder(json_files=sorted(tmp_path.rglob("*.json")), txt_files=[])
    assert e["2TR_L"].upstreams == ["1TR_SRC"] and e["2TR_L"].is_dataflow


# ── generador: el Linaje del flujo continúa hasta las tablas crudas ──────────────────────────────
def test_the_generated_lineage_continues_through_the_flow_to_the_raw_tables(tmp_path):
    inbound, out = tmp_path / "in", tmp_path / "out"
    _pool(inbound)
    G.generate_all(inbound, out)
    import yaml
    text = next(out.rglob("flujo_*.md")).read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))
    linaje = yaml.safe_load(text.split("---" + chr(10), 2)[1])["auto"]["linaje"]
    assert linaje["2TR_OCUPACION"] == ["2VR_OCUPACION"] and linaje["2VR_OCUPACION"] == ["1TR_A", "1TR_B"]
    assert linaje["3VF_OCUPACION"] == ["2TR_OCUPACION"] and linaje["AM_OCUPACION"] == ["3VF_OCUPACION"]


# ── encabezado del detalle del flujo ─────────────────────────────────────────────────────────────
def _entry(tmp_path, dominio, artefactos, labels):
    d = tmp_path / "ClienteX" / "Dom"
    (d / "vistas").mkdir(parents=True)
    for name, label in labels.items():
        (d / "vistas" / f"{name}.md").write_text(f"---\nauto:\n  label: {label}\n---\n", encoding="utf-8")
    arts = "".join(f"  - {a}\n" for a in artefactos)
    (d / "flujo_Dom.md").write_text(f"---\nauto:\n  tipo: flujo\n  dominio: {dominio}\n  artefactos:\n{arts}manual:\n  cliente: ClienteX\n---\nCuerpo.\n", encoding="utf-8")
    (entry,) = cs.load_catalog(str(tmp_path))
    return entry


def test_the_flow_heading_is_the_model_name_not_the_shared_business_classification(tmp_path):
    entry = _entry(tmp_path, "Stock / Inventario", ["3VF_OCUPACION", "AM_OCUPACION_ALMACENES"], {"AM_OCUPACION_ALMACENES": "Modelo analítico - Ocupación de Almacenes"})
    assert presenter.flow_heading(entry) == ("Ocupación de Almacenes", "AM_OCUPACION_ALMACENES")


def test_the_flow_heading_uses_the_readable_technical_name_when_there_is_no_business_name(tmp_path):
    no_label = _entry(tmp_path / "a", "General", ["4MA_PEDIDOS_ENTREGAS"], {})
    assert presenter.flow_heading(no_label) == ("Pedidos Entregas", "4MA_PEDIDOS_ENTREGAS")                  # nunca «General»


def test_the_flow_heading_keeps_an_honest_fallback_without_an_identifiable_terminal_model(tmp_path):
    none = _entry(tmp_path / "b", "Stock / Inventario", ["3VF_X"], {})
    assert presenter.flow_heading(none) == (presenter.NO_TERMINAL_TITLE, None) == ("Flujo sin modelo terminal", None)         # neutro: nunca el dominio legacy
    two = _entry(tmp_path / "c", "Stock / Inventario", ["AM_X", "AM_Y"], {"AM_X": "X", "AM_Y": "Y"})
    assert presenter.flow_heading(two) == (presenter.NO_TERMINAL_TITLE, None)


def test_terminal_titles_resolve_canonical_then_catalog_then_readable_name(tmp_path):
    from types import SimpleNamespace as NS
    entry = _entry(tmp_path, "General", ["AM_A", "AM_B"], {"AM_A": "A", "AM_B": "B"})
    doc = {"scope": {"root_label": "Modelo analítico - Nombre canónico B"}}
    assert presenter.terminal_titles(NS(terminal_am="AM_B"), entry, doc) == ("Nombre canónico B", "AM_B")                 # 1. negocio (perfil canónico)
    assert presenter.terminal_titles(NS(terminal_am="AM_B"), entry, None) == ("B", "AM_B")                                # 1. negocio (Catálogo)
    assert presenter.terminal_titles(NS(terminal_am="AM_Z_SIN_VISTA"), entry, None) == ("Z Sin Vista", "AM_Z_SIN_VISTA")  # 2. técnico legible
    assert presenter.terminal_titles(NS(terminal_am=None), entry, None) == (presenter.NO_TERMINAL_TITLE, None)            # sin modelo terminal: neutro
    assert presenter.terminal_titles(NS(terminal_am="AM_B"), entry, None, adhoc=True) == (None, "AM_B")                   # ad-hoc: sin cambios


def test_the_flow_heading_reuses_the_title_resolved_for_the_assessment_header(tmp_path):
    entry = _entry(tmp_path, "General", ["AM_B"], {"AM_B": "B"})
    assert presenter.flow_heading(entry, {"terminal_am": "AM_B", "title_desc": "Título del informe", "title_id": "AM_B"}) == ("Título del informe", "AM_B")
    assert presenter.flow_heading(entry, {"terminal_am": None, "title_desc": presenter.NO_TERMINAL_TITLE, "title_id": None}) == (presenter.NO_TERMINAL_TITLE, None)


@pytest.mark.parametrize("technical,expected", [("4MA_PEDIDOS_ENTREGAS", "Pedidos Entregas"), ("AM_Lista_Precios", "Lista Precios"), ("AM_RESERVA_MP", "Reserva MP"),
                                                 ("AM_DescuentosComerciales", "Descuentos Comerciales"), ("AM_TEST_VENTA_2", "Test Venta 2"), ("4MA_STOCK", "Stock"), ("", "")])
def test_the_readable_technical_name_only_strips_the_layer_prefix_and_formats_words(technical, expected):
    assert presenter.readable_model_name(technical) == expected
