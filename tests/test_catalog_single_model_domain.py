"""Dominio de un MODELO ÚNICO en carpeta plana: el generador lo nombra por su cubo (misma regla que con ≥2 modelos) en vez de dejarlo en el rótulo genérico
'General', y la curaduría (`manual:`) del 'General' previo se hereda al dominio que lo reemplaza sin pisar la del destino.

Caso real: Contreras (`AM_Turnover`, label «HCM: Turnover») e Illolay (`4MA_PEDIDOS_ENTREGAS`) publicaban un solo modelo y quedaban fuera de «Modelos».
Generación REAL sobre pools mínimos en tmp (sin red, sin catalog_local real)."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from tools.catalog import catalog_service as cs
from tools.catalog import generate_catalog as gc
from webapp.services import catalog_navigation as nav


def _table(name):
    return {"kind": "entity", "@ObjectModel.modelingPattern": {"#": "DATA_STRUCTURE"}, "elements": {"id": {"type": "cds.String", "key": True}}}


def _view(src, label=None, cube=False):
    d = {"kind": "entity", "elements": {"id": {"type": "cds.String"}}, "query": {"SELECT": {"from": {"ref": [src]}, "columns": [{"ref": ["id"]}]}}}
    if label:
        d["@EndUserText.label"] = label
    if cube:
        d["@Analytics.dataCategory"] = {"#": "CUBE"}
    return d


def _pool(tmp_path: Path, defs: dict) -> list[Path]:
    f = tmp_path / "inbound" / "pool.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"definitions": defs}), encoding="utf-8")
    return [f]


def _run(tmp_path, defs, cliente="Cli"):
    out = tmp_path / "out"
    gc.process_folder(tmp_path / "inbound", out, cliente, "General", json_files=_pool(tmp_path, defs), txt_files=[])
    return out / cliente


def _fm(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    return yaml.safe_load(text.split("---", 2)[1])


def _legacy(cli_dir: Path, flow_manual: dict, vistas: dict[str, dict]) -> None:
    """Salida 'General' de una generación anterior, con curaduría del senior."""
    old = cli_dir / "General"
    (old / "vistas").mkdir(parents=True)
    (old / "flujo_General.md").write_text(gc._fm({"tipo": "flujo", "dominio": "General"}, flow_manual), encoding="utf-8")
    for name, manual in vistas.items():
        (old / "vistas" / f"{name}.md").write_text(gc._fm({}, manual), encoding="utf-8")


ONE_HR_MODEL = {"1TR_EMP": _table("1TR_EMP"), "2VF_EMP": _view("1TR_EMP"), "AM_Turnover": _view("2VF_EMP", "HCM: Turnover", cube=True),
                "1TR_ORPHAN": _table("1TR_ORPHAN")}


def test_single_model_without_vocabulary_is_named_by_its_cube_and_keeps_the_whole_pool(tmp_path):
    cli = _run(tmp_path, ONE_HR_MODEL)
    assert sorted(p.name for p in cli.iterdir()) == ["HCM_Turnover"]                            # nunca 'General'
    auto = _fm(cli / "HCM_Turnover" / "flujo_HCM_Turnover.md")["auto"]
    assert auto["dominio"] == "HCM: Turnover"
    assert "1TR_ORPHAN" in auto["artefactos"]                                                    # el pool no se recorta al subgrafo del cubo


def test_single_model_label_equal_to_name_uses_the_technical_name(tmp_path):
    cli = _run(tmp_path, {"1TR_A": _table("1TR_A"), "4MA_PEDIDOS_ENTREGAS": _view("1TR_A", cube=True)})
    assert [p.name for p in cli.iterdir()] == ["4MA_PEDIDOS_ENTREGAS"]


def test_single_model_with_vocabulary_keeps_todays_domain(tmp_path):
    cli = _run(tmp_path, {"1TR_A": _table("1TR_A"), "4MA_STOCK": _view("1TR_A", "Modelo Analítico - Stock", cube=True)})
    assert [p.name for p in cli.iterdir()] == ["Stock_Inventario"]


def test_no_analytic_model_and_no_vocabulary_stays_general(tmp_path):
    cli = _run(tmp_path, {"1TR_A": _table("1TR_A"), "2VF_A": _view("1TR_A")})
    assert [p.name for p in cli.iterdir()] == ["General"]


def test_two_models_segmentation_is_unchanged(tmp_path):
    cli = _run(tmp_path, {"1TR_A": _table("1TR_A"), "AM_X": _view("1TR_A", "Modelo - Despachos", cube=True),
                          "AM_Y": _view("1TR_A", "Modelo - Exportaciones", cube=True)})
    assert sorted(p.name for p in cli.iterdir()) == ["Despachos", "Exportaciones"]


def test_previous_general_curation_is_inherited_and_general_is_removed(tmp_path):
    cli = tmp_path / "out" / "Cli"
    _legacy(cli, {"cliente": "Cli", "proyecto": "Cli · General", "linea_de_negocio": "HCM", "compliance_veredicto_confirmado": "buena",
                  "notas_senior": "revisado"},
            {"AM_Turnover": {"notas": "cubo curado"}, "1TR_GONE": {"notas": "ya no está en el pool"}})
    _run(tmp_path, ONE_HR_MODEL)
    assert sorted(p.name for p in cli.iterdir()) == ["HCM_Turnover"]
    manual = _fm(cli / "HCM_Turnover" / "flujo_HCM_Turnover.md")["manual"]
    assert manual["linea_de_negocio"] == "HCM" and manual["compliance_veredicto_confirmado"] == "buena" and manual["notas_senior"] == "revisado"
    assert manual["proyecto"] == "Cli · HCM: Turnover"                                           # el rótulo viejo no se hereda
    assert _fm(cli / "HCM_Turnover" / "vistas" / "AM_Turnover.md")["manual"]["notas"] == "cubo curado"
    assert not (cli / "HCM_Turnover" / "vistas" / "1TR_GONE.md").exists()                        # no se siembran vistas de entidades ausentes


def test_destination_curation_wins_over_the_previous_general(tmp_path):
    cli = _run(tmp_path, ONE_HR_MODEL)
    fp = cli / "HCM_Turnover" / "flujo_HCM_Turnover.md"
    fm = _fm(fp)
    fm["manual"]["notas_senior"] = "curaduría del destino"
    fp.write_text(gc._fm(fm["auto"], fm["manual"]), encoding="utf-8")
    _legacy(cli, {"notas_senior": "curaduría vieja de General"}, {})                            # caso prod: General resucitado junto al destino
    _run(tmp_path, ONE_HR_MODEL)
    assert sorted(p.name for p in cli.iterdir()) == ["HCM_Turnover"]
    assert _fm(fp)["manual"]["notas_senior"] == "curaduría del destino"


def test_two_models_inherit_view_curation_but_not_the_ambiguous_flow_curation(tmp_path):
    cli = tmp_path / "out" / "Cli"
    _legacy(cli, {"notas_senior": "flujo mezclado"}, {"1TR_A": {"notas": "tabla curada"}})
    _run(tmp_path, {"1TR_A": _table("1TR_A"), "AM_X": _view("1TR_A", "Modelo - Despachos", cube=True),
                    "AM_Y": _view("1TR_A", "Modelo - Exportaciones", cube=True)})
    for dom in ("Despachos", "Exportaciones"):
        assert _fm(cli / dom / "vistas" / "1TR_A.md")["manual"]["notas"] == "tabla curada"
        assert _fm(cli / dom / f"flujo_{dom}.md")["manual"]["notas_senior"] == ""


def test_the_single_model_appears_in_models_without_any_line_of_business(tmp_path):
    _run(tmp_path, ONE_HR_MODEL, "Contreras")
    entries = cs.load_catalog(str(tmp_path / "out"))
    assert not any((_fm(Path(e.path))["manual"].get("linea_de_negocio")) for e in entries)       # sin clasificación de línea de negocio
    assert nav.model_domains(cs, entries) == ["HCM: Turnover"]
