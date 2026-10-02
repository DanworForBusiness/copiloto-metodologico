"""Fixtures SINTETICAS de M7 (CLI / documento publico / Markdown). Nombres genericos; sin datos de clientes reales.

  * `write_inbound(root)` escribe un inbound CSN REAL con la forma del export CLI (`<cliente>/<carpeta-tipo>/*.json`) para ejercitar el CLI de punta a punta:
      Acme     un AM terminal `4AM_ACME` <- `3VF_P` <- `1VR_RAW` <- `RAW_T` (tabla fisica remota con filtro) + otro AM `4AM_OTHER` en el mismo pool
      Solo     un cliente SIN modelo analitico terminal (solo una vista)
      Clash    la misma entidad definida con contenido distinto en dos carpetas fisicas (colision -> el pool no carga)
  * `scenario_pools()` agrupa pools de entidades ya construidos (M2-M6) que juntos disparan TODAS las senales del registro.
"""
from __future__ import annotations

import json
from pathlib import Path

from tests._modeling_assessment_m2_fixtures import WHERE_LANG, _remote_table, m2_entities, write_pool_files
from tests._modeling_assessment_m3_fixtures import m3_entities
from tests._modeling_assessment_m4_fixtures import m4_entities
from tests._modeling_assessment_m4_fixtures import f4
from tests.test_modeling_assessment_signals import _flat, _lb, _opaque, _pt, _pt_internal, _redo, _rl


def _view(name, source, columns, *, pattern=None, category=None, measures=()):
    body = {"kind": "entity", "elements": {c: {"type": "cds.String", "@EndUserText.label": c.title()} for c in columns},
            "query": {"SELECT": {"from": {"ref": [source]}, "columns": [{"ref": [source, c], "as": c} for c in columns]}}}
    for m_ in measures:
        body["elements"][m_] = {"type": "cds.Decimal", "precision": 13, "scale": 3, "@AnalyticsDetails.measureType": {"#": "AGGREGATED"},
                                "@EndUserText.label": m_.title()}
        body["query"]["SELECT"]["columns"].append({"ref": [source, m_], "as": m_})
    if pattern:
        body["@ObjectModel.modelingPattern"] = {"#": pattern}
    if category:
        body["@Analytics.dataCategory"] = {"#": category}
    return {"definitions": {name: body}}


def _write(root: Path, rel: str, body) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")
    return p


def write_inbound(root: Path) -> Path:
    raw = _remote_table("RAW_T", "S4PREM", "ABAPTABLES.TAB1", where=WHERE_LANG)
    raw["definitions"]["RAW_T"]["elements"] = {c: {"type": "cds.String"} for c in ("A", "B", "M")}
    _write(root, "Acme/remote-tables/RAW_T.json", raw)
    _write(root, "Acme/views/1VR_RAW.json", _view("1VR_RAW", "RAW_T", ("A", "B"), measures=("M",)))
    _write(root, "Acme/views/3VF_P.json", _view("3VF_P", "1VR_RAW", ("A", "B"), pattern="ANALYTICAL_FACT", measures=("M",)))
    _write(root, "Acme/analytic-models/4AM_ACME.json", _view("4AM_ACME", "3VF_P", ("A", "B"), pattern="ANALYTICAL_CUBE", category="CUBE", measures=("M",)))
    _write(root, "Acme/analytic-models/4AM_OTHER.json", _view("4AM_OTHER", "3VF_P", ("A",), pattern="ANALYTICAL_CUBE", category="CUBE", measures=("M",)))
    _write(root, "Solo/views/1VR_ONLY.json", _view("1VR_ONLY", "LOCAL_X", ("A",)))
    _write(root, "Solo/local-tables/LOCAL_X.json", {"definitions": {"LOCAL_X": {"kind": "entity", "elements": {"A": {"type": "cds.String"}}}}})
    _write(root, "Clash/views/DUP.json", _view("DUP", "SRC", ("A",)))
    _write(root, "Clash/local-tables/DUP.json", {"definitions": {"DUP": {"kind": "entity", "elements": {"Z": {"type": "cds.String"}}}}})
    return root


_OPAQUE_FIELDS = [f4("ZED_VETNOSIS", "EBIT"), f4("ZED_NIVEL_1", "Id Nivel 1"), f4("KSTAR", "Clase de coste")]


def scenario_pools(tmp_path: Path):
    """[(entidades, am, evidencia_suplementaria|None)] cuya union dispara todas las senales del registro."""
    ev = SupplementaryEvidence_from(tmp_path)
    return [(m2_entities(), "AM_P", ev), (m3_entities(), "AM_M", None), (m4_entities(), "AM_X", None), (_pt(), "AM_PT", None), (_pt_internal(), "AM_PT", None),
            (_rl(), "4AM_RL", None), (_lb(), "AM_LB", None), (_flat(4), "AM_FL", None), (_redo(), "AM_RD", None),
            (_opaque({"AM_OP": _OPAQUE_FIELDS, "VF_OP": _OPAQUE_FIELDS, "SRC_OP": _OPAQUE_FIELDS}), "AM_OP", None)]


def SupplementaryEvidence_from(tmp_path: Path):
    from tools.catalog.modeling_assessment.readers import SupplementaryEvidence
    return SupplementaryEvidence.from_files(write_pool_files(tmp_path), root=tmp_path)
