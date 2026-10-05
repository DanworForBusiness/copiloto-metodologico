"""Pool CSN sintetico del evidence release (S1-S6): replica la ESTRUCTURA del caso Illolay sin datos reales.

    4AM_X (AM, cubo) <- 3VF_X (SQL) <- V_A (SQL)  <- 2TR_VBAK  (tabla local producida por el Data Flow DF_VBAK)
                                    <- V_B (SQL)  <- 2TR_VBAK  (solo VBELN, AUDAT; re-deriva el mes en su WHERE)
                      + asociaciones: 2VS_DIM (dimension de proyecto, 11/12 columnas identidad + 1 derivada), SAP.TIME.VIEW_DIMENSION_DAY (plataforma)
    DF_VBAK: VBAK_HIST (vista) -> 2TR_VBAK   (modo append + upsert; la vista filtra ERDAT >= '20250101')
    VBAK_HIST <- 1TR_VBAK (ABAPTABLES.VBAK, tabla fisica) + 1TR_TVAKT (ABAPTABLES.TVAKT)

Dos empaquetados del MISMO hecho semantico (una tabla local producida por un DF y leida por Modeling):
  bundled_df=True   estilo Illolay/Loma: ``DF_VBAK.json`` trae ``definitions`` (target, fuentes, vista) + ``dataflows`` (el loader marca el target ``is_dataflow``);
  bundled_df=False  estilo Biogenesis: ``data-flows/DF_VBAK.json`` solo trae ``dataflows``; las entidades viven en sus propios archivos.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

AM = "4AM_X"
CODES = ["AUART", "BSTNK", "AUGRU", "VBELN", "AUDAT", "VBTYP", "KUNNR", "VKORG", "VTWEG", "VKBUR", "LIFSK", "LFSTK"]       # 12 columnas con nombre de la fuente
SAP_LABEL = {c: f"SAP {c} label" for c in CODES}
SAP_LABEL.update({"ERDAT": "Created on", "BEZEI": "Description"})
OWN_NAME_LABEL = {"AUART", "BSTNK", "AUGRU", "VBELN"}                                       # en 2TR_VBAK la etiqueta == el nombre tecnico
MES = {"func": "CONCAT", "args": [
    {"func": "EXTRACT", "args": [{"xpr": ["year", "from", {"ref": ["V_A", "AUDAT"]}]}]},
    {"func": "LPAD", "args": [{"func": "EXTRACT", "args": [{"xpr": ["month", "from", {"ref": ["V_A", "AUDAT"]}]}]}, {"val": 2}, {"val": "0"}]}]}


def _w(root: Path, rel: str, body) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")


def el(label: str | None = None, **kw) -> dict:
    d = {"type": "cds.String", "length": 10, **kw}
    if label is not None:
        d["@EndUserText.label"] = label
    return d


def remote_table(name: str, entity: str, cols: dict[str, dict], connection: str = "S4_PRD") -> dict:
    return {name: {"kind": "entity", "elements": cols, "@DataWarehouse.remote.connection": connection, "@DataWarehouse.remote.entity": entity,
                   "@ObjectModel.modelingPattern": {"#": "DATA_STRUCTURE"}}}


def raw_vbak_cols() -> dict:
    return {**{c: el(SAP_LABEL[c]) for c in CODES}, "ERDAT": el("Created on", type="cds.Date")}


def raw_tvakt_cols() -> dict:
    return {"AUART": el("Doc type"), "SPRAS": el("Language"), "BEZEI": el("Description")}


def vbak_hist_def() -> dict:
    cols = [{"ref": ["1TR_VBAK", c]} for c in CODES] + [{"ref": ["1TR_TVAKT", "BEZEI"], "as": "BEZEI_DOC_TYPE"}, {"ref": ["1TR_VBAK", "ERDAT"]}]
    elements = {**{c: el(SAP_LABEL[c]) for c in CODES}, "BEZEI_DOC_TYPE": el("Description"), "ERDAT": el("Created on", type="cds.Date")}
    return {"VBAK_HIST": {"kind": "entity", "elements": elements, "@ObjectModel.modelingPattern": {"#": "DATA_STRUCTURE"},
                          "query": {"SELECT": {"from": {"join": "left", "args": [{"ref": ["1TR_VBAK"]}, {"ref": ["1TR_TVAKT"]}],
                                               "on": [{"ref": ["1TR_TVAKT", "AUART"]}, "=", {"ref": ["1TR_VBAK", "AUART"]}]},
                                     "columns": cols, "where": [{"ref": ["1TR_VBAK", "ERDAT"]}, ">=", {"val": "20250101"}]}}}}


def target_def(name: str = "2TR_VBAK") -> dict:
    """14 campos: 12 con el nombre de la fuente + BEZEI_DOC_TYPE (identidad) + BEZEI (renombrado desde BEZEI_REASON en el DF). ERDAT se descarta."""
    elements = {c: el(c if c in OWN_NAME_LABEL else SAP_LABEL[c]) for c in CODES}
    elements["BEZEI_DOC_TYPE"] = el("BEZEI_DOC_TYPE")
    elements["BEZEI"] = el("BEZEI_REASON")
    return {name: {"kind": "entity", "elements": elements, "@ObjectModel.modelingPattern": {"#": "DATA_STRUCTURE"}}}


def dataflow(name: str, source: str, target: str, *, mode: str = "append", upsert: bool = True, with_mappings: bool = True) -> dict:
    maps = [{"expression": f'"{c}"', "target": c} for c in CODES] + [{"expression": '"BEZEI_DOC_TYPE"', "target": "BEZEI_DOC_TYPE"}, {"expression": '"BEZEI_REASON"', "target": "BEZEI"}]
    producer = {"component": "com.sap.database.table.producer", "metadata": {"label": target, "config": {
        "service": "HANA", "dwcEntity": target, "remoteObjectType": "TABLE", "mode": mode, "upsert": upsert, "batchSize": 1000,
        **({"attributeMappings": maps} if with_mappings else {})}}}
    consumer = {"component": "com.sap.database.table.consumer", "metadata": {"label": source, "config": {
        "service": "HANA", "dwcEntity": source, "remoteObjectType": "VIEW", "mode": "append"}}}
    return {"dataflows": {name: {"kind": "sap.dis.dataflow", "contents": {"processes": {"source1": consumer, "target2": producer}},
                                 "sources": {source: {"elements": {}}}, "targets": {target: {"elements": {}}}}}}


def _view(name: str, cols: list, frm: dict, elements: dict, *, pattern: str = "ANALYTICAL_FACT", where=None, group_by=None, having=None) -> dict:
    sel = {"from": frm, "columns": cols}
    if where is not None:
        sel["where"] = where
    if group_by is not None:
        sel["groupBy"] = group_by
    if having is not None:
        sel["having"] = having
    return {name: {"kind": "entity", "elements": elements, "@ObjectModel.modelingPattern": {"#": pattern}, "@DataWarehouse.sqlEditor.query": "SELECT 1", "query": {"SELECT": sel}}}


def write_pool(*, bundled_df: bool = True, orphan: bool = False, mode: str = "append", upsert: bool = True, root: Path | None = None, target_file: bool = True,
               peso_type: str = "Double") -> Path:
    root = root or Path(tempfile.mkdtemp(prefix="evrel_"))
    # --- fuentes remotas y vista de origen del flujo
    raw = {**remote_table("1TR_VBAK", "ABAPTABLES.VBAK", raw_vbak_cols()), **remote_table("1TR_TVAKT", "ABAPTABLES.TVAKT", raw_tvakt_cols())}
    flow = dataflow("DF_VBAK", "VBAK_HIST", "2TR_VBAK", mode=mode, upsert=upsert)
    if bundled_df:
        _w(root, "Acme/DF_VBAK.json", {"definitions": {**target_def(), **raw, **vbak_hist_def()}, **flow})
        if target_file:
            _w(root, "Acme/local-tables/2TR_VBAK.json", {"definitions": target_def()})              # como Illolay: el target tambien tiene su propio archivo
    else:
        _w(root, "Acme/remote-tables/1TR_VBAK.json", {"definitions": remote_table("1TR_VBAK", "ABAPTABLES.VBAK", raw_vbak_cols())})
        _w(root, "Acme/remote-tables/1TR_TVAKT.json", {"definitions": remote_table("1TR_TVAKT", "ABAPTABLES.TVAKT", raw_tvakt_cols())})
        _w(root, "Acme/views/VBAK_HIST.json", {"definitions": vbak_hist_def()})
        _w(root, "Acme/local-tables/2TR_VBAK.json", {"definitions": target_def()})
        _w(root, "Acme/data-flows/DF_VBAK.json", flow)
    # --- vistas de Modeling que LEEN la tabla producida por el flujo
    a_cols = [{"ref": ["2TR_VBAK", c]} for c in CODES] + [{**MES, "as": "Mes"}]
    a_cols[-1]["args"][0]["args"][0]["xpr"][2] = {"ref": ["2TR_VBAK", "AUDAT"]}
    a_cols[-1]["args"][1]["args"][0]["args"][0]["xpr"][2] = {"ref": ["2TR_VBAK", "AUDAT"]}
    a_el = {**{c: el(SAP_LABEL[c]) for c in CODES}, "Mes": el("Mes")}
    _w(root, "Acme/views/V_A.json", {"definitions": _view("V_A", a_cols, {"ref": ["2TR_VBAK"]}, a_el)})
    mes_where = ["V_B_SRC", "between", {"func": "TO_DATE", "args": [{"func": "concat", "args": [MES, {"val": "01"}]}, {"val": "YYYYMMDD"}]}]
    b_where = [{"ref": ["2TR_VBAK", "AUDAT"]}, "between", {"func": "TO_DATE", "args": [{"func": "concat", "args": [
        {"func": "CONCAT", "args": [{"func": "EXTRACT", "args": [{"xpr": ["year", "from", {"ref": ["2TR_VBAK", "AUDAT"]}]}]},
                                    {"func": "LPAD", "args": [{"func": "EXTRACT", "args": [{"xpr": ["month", "from", {"ref": ["2TR_VBAK", "AUDAT"]}]}]}, {"val": 2}, {"val": "0"}]}]},
        {"val": "01"}]}, {"val": "YYYYMMDD"}]}]
    _ = mes_where
    b_cols = [{"ref": ["2TR_VBAK", "VBELN"]}, {"ref": ["2TR_VBAK", "AUDAT"], "as": "FechaPedido"}]
    _w(root, "Acme/views/V_B.json", {"definitions": _view("V_B", b_cols, {"ref": ["2TR_VBAK"]}, {"VBELN": el("VBELN"), "FechaPedido": el("FechaPedido")}, where=b_where)})
    # --- dimension de proyecto con 11 columnas identidad + 1 derivada (como 2VS_KNA1)
    dim_cols = [{"ref": ["1TR_KNA1", f"C{i}"]} for i in range(11)] + [{"func": "LEFT", "args": [{"ref": ["1TR_KNA1", "KUNNR"]}, {"val": 4}], "as": "Cliente_Cod"}]
    kna1 = {f"C{i}": el(f"Customer C{i}") for i in range(11)}
    kna1["KUNNR"] = el("Customer Number")
    _w(root, "Acme/remote-tables/1TR_KNA1.json", {"definitions": remote_table("1TR_KNA1", "ABAPTABLES.KNA1", kna1)})
    dim_el = {**{f"C{i}": el(f"Customer C{i}") for i in range(11)}, "Cliente_Cod": el("Cliente_Cod")}
    _w(root, "Acme/views/2VS_DIM.json", {"definitions": _view("2VS_DIM", dim_cols, {"ref": ["1TR_KNA1"]}, dim_el, pattern="ANALYTICAL_DIMENSION")})
    # --- composicion y modelo analitico
    j_cols = [{"ref": ["V_A", c]} for c in CODES] + [{"ref": ["V_A", "Mes"]}, {"ref": ["V_B", "FechaPedido"]}]
    j_el = {**{c: el(SAP_LABEL[c]) for c in CODES}, "Mes": el("Mes"), "FechaPedido": el("FechaPedido")}
    j_from = {"join": "left", "args": [{"ref": ["V_A"]}, {"ref": ["V_B"]}], "on": [{"ref": ["V_A", "VBELN"]}, "=", {"ref": ["V_B", "VBELN"]}]}
    _w(root, "Acme/views/3VF_X.json", {"definitions": _view("3VF_X", j_cols, j_from, j_el, group_by=[{"ref": ["V_A", "VBELN"]}, {**_mes_on("V_A")}])})
    am_cols = [{"ref": ["3VF_X", c], "as": c} for c in CODES] + [{"ref": ["3VF_X", "Mes"], "as": "Mes"}]
    am_el = {**{c: el(SAP_LABEL[c]) for c in CODES}, "Mes": el("Mes"),
             "Cantidad": {"type": "cds.Decimal", "precision": 20, "scale": 3, "@EndUserText.label": "Cantidad", "@AnalyticsDetails.measureType": {"#": "BASE"}},
             "Importe": {"type": "cds.Decimal", "precision": 35, "scale": 3, "@EndUserText.label": "Importe", "@AnalyticsDetails.measureType": {"#": "BASE"}},
             "Peso": ({"type": "cds.Double"} if peso_type == "Double" else {"type": "cds.Decimal", "precision": 20, "scale": 3}) | {"@EndUserText.label": "Peso", "@AnalyticsDetails.measureType": {"#": "BASE"}},
             "_Dim": {"type": "cds.Association", "target": "2VS_DIM", "on": [{"ref": ["VBELN"]}, "=", {"ref": ["_Dim", "C0"]}], "@EndUserText.label": "Cliente"},
             "_Fecha": {"type": "cds.Association", "target": "SAP.TIME.VIEW_DIMENSION_DAY", "on": [{"ref": ["Mes"]}, "=", {"ref": ["_Fecha", "DATE_SQL"]}], "@EndUserText.label": "Fecha"}}
    am_cols += [{"ref": ["3VF_X", "Mes"], "as": "Cantidad"}, {"ref": ["3VF_X", "Mes"], "as": "Importe"}, {"ref": ["3VF_X", "Mes"], "as": "Peso"}, {"ref": ["_Dim"]}, {"ref": ["_Fecha"]}]
    _w(root, f"Acme/analytic-models/{AM}.json", {"definitions": {AM: {"kind": "entity", "elements": am_el, "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"},
        "@Analytics.dataCategory": {"#": "CUBE"}, "query": {"SELECT": {"from": {"ref": ["3VF_X"]}, "columns": am_cols}}}}})
    if orphan:                                                                                  # una tabla producida por OTRO flujo que ningun artefacto de Modeling lee
        _w(root, "Acme/DF_ORPHAN.json", {"definitions": {"2TR_ORPHAN": {"kind": "entity", "elements": {"X": el("X")}}},
                                          "dataflows": {"DF_ORPHAN": {"kind": "sap.dis.dataflow", "contents": {}, "sources": {}, "targets": {"2TR_ORPHAN": {}}}}})
    return root


def _mes_on(provider: str) -> dict:
    return {"func": "CONCAT", "args": [
        {"func": "EXTRACT", "args": [{"xpr": ["year", "from", {"ref": [provider, "AUDAT"]}]}]},
        {"func": "LPAD", "args": [{"func": "EXTRACT", "args": [{"xpr": ["month", "from", {"ref": [provider, "AUDAT"]}]}]}, {"val": 2}, {"val": "0"}]}]}
