"""Resolución de linaje cross-DP (contrato base compartido) sin duplicar archivos.

Un data product B construye sobre una base común (capa 1-2) que vive en `_shared` (o en
otro DP). El generador arma el linaje sobre la UNIÓN de archivos y la reachability desde
los anchors de B selecciona su subgrafo: la base compartida entra resuelta por nombre
(marcada `shared`), sin duplicar; los artefactos propios de OTROS DP quedan afuera.
"""
import json
import sys
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent.parent / "tools" / "catalog"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

import generate_data_products_catalog as g  # noqa: E402

_ENTITY = {"kind": "entity", "elements": {"X": {"type": "cds.String", "length": 1}}}


def _art(name, monetary=False):
    els = {"K": {"type": "cds.String", "length": 5, "key": True}}
    if monetary:
        els["NetAmount"] = {"type": "cds.Decimal", "precision": 38, "scale": 6,
                            "@EndUserText.label": "Net amount"}
    return {"definitions": {name: {"kind": "entity", "elements": els}}}


def _ft(*names):
    return {"definitions": {n: dict(_ENTITY) for n in names}}


def _seed(root: Path):
    """DP_B (propios: 3TF_B + su FT) + _shared (contrato base 1TR/2TF + su FT)."""
    wrapper = root / "Data Products"
    dpb = wrapper / "DP_B"
    shared = wrapper / "_shared"
    dpb.mkdir(parents=True)
    shared.mkdir(parents=True)
    (dpb / "3TF_B.json").write_text(json.dumps(_art("3TF_B", monetary=True)), encoding="utf-8")
    (dpb / "FT_2TF_3TF.json").write_text(json.dumps(_ft("2TF_SHARED", "3TF_B")), encoding="utf-8")
    (shared / "1TR_SHARED.json").write_text(json.dumps(_art("1TR_SHARED")), encoding="utf-8")
    (shared / "2TF_SHARED.json").write_text(json.dumps(_art("2TF_SHARED", monetary=True)), encoding="utf-8")
    (shared / "FT_1TR_2TF.json").write_text(json.dumps(_ft("1TR_SHARED", "2TF_SHARED")), encoding="utf-8")
    return dpb, shared


_PUBLISHED = [{"name": "3TF_B", "display": "3TF_B", "kind": "entity"}]


def test_linaje_incompleto_sin_indice(tmp_path):
    dpb, _shared = _seed(tmp_path)
    nodes = g._resolve_lineage(dpb, _PUBLISHED, index=None)
    names = {n["technical_name"] for n in nodes}
    assert "3TF_B" in names
    assert "1TR_SHARED" not in names   # la base compartida no está en la carpeta propia


def test_linaje_completo_con_indice_cross_dp(tmp_path):
    dpb, shared = _seed(tmp_path)
    index = g._dp_artifact_index([dpb, shared])
    nodes = g._resolve_lineage(dpb, _PUBLISHED, index)
    by_name = {n["technical_name"]: n for n in nodes}
    # el contrato base compartido entra resuelto por nombre, sin duplicar
    assert {"3TF_B", "2TF_SHARED", "1TR_SHARED"} <= set(by_name)
    # marcados como compartidos/heredados (vienen de _shared, no de DP_B)
    assert by_name["2TF_SHARED"]["shared"] and by_name["2TF_SHARED"]["shared_from"] == "_shared"
    assert by_name["1TR_SHARED"]["shared"]
    # lo propio del DP NO es compartido
    assert not by_name["3TF_B"]["shared"]


def test_shared_folder_no_es_un_producto(tmp_path):
    _seed(tmp_path)
    prods = {p.name for p in g._product_folders(tmp_path)}
    shared = {p.name for p in g._shared_folders(tmp_path)}
    assert prods == {"DP_B"} and shared == {"_shared"}


def test_gobierno_detecta_drift_y_base_atrapada(tmp_path):
    # Mismo artefacto en dos carpetas con contenido DISTINTO → drift; usado por 2 DPs y
    # sin copia en _shared → base atrapada.
    wrapper = tmp_path / "Data Products"
    (wrapper / "DP_A").mkdir(parents=True)
    (wrapper / "DP_B").mkdir(parents=True)
    (wrapper / "DP_A" / "2TF_SHARED.json").write_text(json.dumps(_art("2TF_SHARED")), encoding="utf-8")
    (wrapper / "DP_B" / "2TF_SHARED.json").write_text(
        json.dumps(_art("2TF_SHARED", monetary=True)), encoding="utf-8")   # contenido distinto
    index = g._dp_artifact_index([wrapper / "DP_A", wrapper / "DP_B"])
    usage = {"2TF_SHARED": {"DP_A", "DP_B"}}
    line = g._shared_governance(index, usage, shared_folder_names=set(), out_root=tmp_path / "out")
    assert "drift" in line and "atrapada" in line
