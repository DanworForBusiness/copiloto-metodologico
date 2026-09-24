"""
test_catalog_type_inheritance_alias.py — Herencia de tipo/longitud cuando el FROM
del cube aliasa la fuente (`from.as` != id real).

Root cause (TYPE_INHERITANCE_RESOLUTION_BUG): las medidas de un Analytic Model son
proyecciones (`ref`) cuyo tipo efectivo vive en el fact upstream. Si el `from` del
query aliasa la fuente (p. ej. Datasphere trunca el alias de una entidad
parametrizada), el token del ref (`ref[0]`) es el ALIAS, distinto del id real, y la
herencia `_inherit_typelen` fallaba porque `raw_elems` se indexa por id real.

Contrato genérico: resolver `alias -> id real` para CUALQUIER FROM con alias, tenga
`args` (parametrizado) o no. Fixtures sintéticas — sin catalog_local ni nombres reales.
"""
import json
import sys
from pathlib import Path

_TOOLS = str(Path(__file__).resolve().parents[2] / "tools" / "catalog")
if _TOOLS not in sys.path:
    sys.path.insert(0, _TOOLS)
import generate_catalog as g  # noqa: E402


def _csn(path: Path, definitions: dict):
    path.write_text(json.dumps({"definitions": definitions}), encoding="utf-8")


def _fact(measure_type="cds.Decimal", precision=13, scale=3):
    return {"3VF_X": {"kind": "entity", "elements": {
        "M": {"type": measure_type, "precision": precision, "scale": scale},
    }}}


def _cube(from_ref, from_as, extra_measure_elem=None):
    measure_elem = extra_measure_elem or {
        "@AnalyticsDetails.measureType": {"#": "AGGREGATED"},
        "@EndUserText.label": "Measure",
    }
    return {"4AM_A": {
        "kind": "entity",
        "@ObjectModel.modelingPattern": {"#": "ANALYTICAL_CUBE"},
        "@Analytics.dataCategory": {"#": "CUBE"},
        "elements": {"M": measure_elem},
        "query": {"SELECT": {
            "from": {"ref": from_ref, "as": from_as},
            "columns": [{"ref": [from_as, "M"], "as": "M"}],
        }},
    }}


def _measure_type(folder: Path):
    entities, _ = g.load_folder(folder)
    cube = entities["4AM_A"]
    f = next(fld for fld in cube.fields if fld["campo"] == "M")
    return f["tipo"], f["longitud"]


def test_A_parametrized_fact_alias_resolves(tmp_path):
    d = tmp_path / "A"; d.mkdir()
    _csn(d / "3VF_X.json", _fact())
    _csn(d / "4AM_A.json", _cube(
        from_ref=[{"id": "3VF_X", "args": {"P": {"param": True, "ref": ["P"]}}}],
        from_as="FACT_ALIAS"))
    assert _measure_type(d) == ("Decimal", "13,3")


def test_B_plain_alias_differs_from_id_resolves(tmp_path):
    d = tmp_path / "B"; d.mkdir()
    _csn(d / "3VF_X.json", _fact())
    _csn(d / "4AM_A.json", _cube(from_ref=["3VF_X"], from_as="FACT_ALIAS"))
    assert _measure_type(d) == ("Decimal", "13,3")


def test_C_alias_equals_id_preserved(tmp_path):
    d = tmp_path / "C"; d.mkdir()
    _csn(d / "3VF_X.json", _fact())
    _csn(d / "4AM_A.json", _cube(from_ref=["3VF_X"], from_as="3VF_X"))
    assert _measure_type(d) == ("Decimal", "13,3")


def test_D_inline_type_has_precedence(tmp_path):
    # La medida del cube trae tipo inline → NO se pisa con la herencia del fact.
    d = tmp_path / "D"; d.mkdir()
    _csn(d / "3VF_X.json", _fact())  # fact dice Decimal 13,3
    _csn(d / "4AM_A.json", _cube(
        from_ref=[{"id": "3VF_X", "args": {}}], from_as="FACT_ALIAS",
        extra_measure_elem={"type": "cds.Integer",
                            "@AnalyticsDetails.measureType": {"#": "AGGREGATED"}}))
    tipo, _ = _measure_type(d)
    assert tipo == "Integer"  # inline gana; no lo pisa el Decimal del fact


def test_E_unresolvable_alias_keeps_absence(tmp_path):
    # Alias apunta a un fact ausente → no se inventa metadata; tipo sigue "—".
    d = tmp_path / "E"; d.mkdir()
    _csn(d / "4AM_A.json", _cube(
        from_ref=[{"id": "MISSING_FACT", "args": {}}], from_as="GHOST"))
    assert _measure_type(d) == ("—", "")
