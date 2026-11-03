"""Visibilidad de un MODELO ÚNICO en carpeta plana: el generador lo nombra por su cubo (misma regla que con ≥2 modelos) en vez de dejarlo en el rótulo genérico
'General' (que la pestaña «Modelos» excluye), y MIGRA la curaduría (`manual:`) del 'General' previo al dominio renombrado sin pisar la del destino.

Fixtures con curaduría histórica REAL (no vacía). La rama de ≥2 cubos y el dominio real de 2 niveles se comparan contra el generador de ``origin/main``
(golden de TODOS los .md generados). Casos de referencia: Contreras (`AM_Turnover`, «HCM: Turnover») e Illolay (`4MA_PEDIDOS_ENTREGAS`), sin reglas por cliente.
Generación REAL sobre pools mínimos en tmp (sin red, sin catalog_local real)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from tools.catalog import catalog_service as cs
from tools.catalog import generate_catalog as gc
from webapp.services import catalog_navigation as nav
from webapp.services import domain_classification_service as dcs

REPO = Path(__file__).resolve().parents[1]


def _table():
    return {"kind": "entity", "@ObjectModel.modelingPattern": {"#": "DATA_STRUCTURE"}, "elements": {"id": {"type": "cds.String", "key": True}}}


def _view(src, label=None, cube=False):
    d = {"kind": "entity", "elements": {"id": {"type": "cds.String"}}, "query": {"SELECT": {"from": {"ref": [src]}, "columns": [{"ref": ["id"]}]}}}
    if label:
        d["@EndUserText.label"] = label
    if cube:
        d["@Analytics.dataCategory"] = {"#": "CUBE"}
    return d


CONTRERAS_LIKE = {"1TR_EMP": _table(), "2VF_EMP": _view("1TR_EMP"), "AM_Turnover": _view("2VF_EMP", "HCM: Turnover", cube=True), "1TR_ORPHAN": _table()}
ILLOLAY_LIKE = {"1TR_A": _table(), "4MA_PEDIDOS_ENTREGAS": _view("1TR_A", cube=True)}
TWO_MODELS = {"1TR_A": _table(), "AM_X": _view("1TR_A", "Modelo - Despachos", cube=True), "AM_Y": _view("1TR_A", "Modelo - Exportaciones", cube=True)}

FLOW_CURATION = {"cliente": "Cli", "proyecto": "Cli · General", "dominio": "Rotación de personal", "linea_de_negocio": "HUMAN_RESOURCES",
                 "intent_negocio": "Medir la rotación mensual", "extraccion_corporativa": "", "compliance_veredicto_confirmado": "buena",
                 "notas_senior": "Revisado con el cliente en 2026-09"}
VIEW_CURATION = {"AM_Turnover": {"cliente": "Cli", "proyecto": "Cli · General", "notas": "cubo curado", "descripcion_negocio": "Bajas y altas"},
                 "1TR_EMP": {"cliente": "Cli", "proyecto": "Cli · General", "notas": "fuente SuccessFactors"},
                 "1TR_GONE": {"notas": "ya no está en el pool"}}


def _pool(root: Path, defs: dict) -> list[Path]:
    f = root / "inbound" / "pool.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"definitions": defs}), encoding="utf-8")
    return [f]


def _run(root: Path, defs: dict, cliente="Cli", dominio="General", mod=gc) -> Path:
    mod.process_folder(root / "inbound", root / "out", cliente, dominio, json_files=_pool(root, defs), txt_files=[])
    return root / "out" / cliente


def _fm(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])


def _legacy(cli_dir: Path, flow_manual: dict, vistas: dict[str, dict], folder: str = "General") -> None:
    """Salida de una generación ANTERIOR con curaduría del senior."""
    old = cli_dir / folder
    (old / "vistas").mkdir(parents=True, exist_ok=True)
    (old / f"flujo_{folder}.md").write_text(gc._fm({"tipo": "flujo", "dominio": folder}, flow_manual), encoding="utf-8")
    for name, manual in vistas.items():
        (old / "vistas" / f"{name}.md").write_text(gc._fm({}, manual), encoding="utf-8")


def _snapshot(cli_dir: Path) -> dict[str, str]:
    return {p.relative_to(cli_dir).as_posix(): p.read_text(encoding="utf-8") for p in sorted(cli_dir.rglob("*.md"))}


@pytest.fixture(scope="module")
def base_generator(tmp_path_factory):
    """El generador de ``origin/main`` (o ``main``), para comparar golden. Sin esa referencia en el checkout (p. ej. CI superficial): skip."""
    for ref in ("origin/main", "main"):
        out = subprocess.run(["git", "show", f"{ref}:tools/catalog/generate_catalog.py"], cwd=REPO, capture_output=True, encoding="utf-8")
        if out.returncode == 0:
            path = tmp_path_factory.mktemp("base") / "generate_catalog_base.py"
            path.write_text(out.stdout, encoding="utf-8")
            spec = importlib.util.spec_from_file_location("generate_catalog_base", path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = mod                                                         # los @dataclass del módulo lo resuelven por nombre
            spec.loader.exec_module(mod)
            return mod
    pytest.skip("sin rama main para comparar")


# ── (e) visibilidad: Contreras-like e Illolay-like sin reglas por cliente ──
def test_single_model_without_vocabulary_is_named_by_its_cube_and_keeps_the_whole_pool(tmp_path):
    cli = _run(tmp_path, CONTRERAS_LIKE)
    assert sorted(p.name for p in cli.iterdir()) == ["HCM_Turnover"]
    auto = _fm(cli / "HCM_Turnover" / "flujo_HCM_Turnover.md")["auto"]
    assert auto["dominio"] == "HCM: Turnover"
    assert "1TR_ORPHAN" in auto["artefactos"]                                                    # el pool no se recorta al subgrafo del cubo


def test_single_model_label_equal_to_name_uses_the_technical_name(tmp_path):
    assert [p.name for p in _run(tmp_path, ILLOLAY_LIKE).iterdir()] == ["4MA_PEDIDOS_ENTREGAS"]


def test_the_rule_has_no_client_specific_branches():
    import inspect
    src = inspect.getsource(gc.process_folder) + inspect.getsource(gc._legacy_general_manual) + inspect.getsource(gc._seed_legacy_manual)
    for name in ("Contreras", "Illolay", "Turnover", "PEDIDOS", "Biogenesis", "Kensing"):
        assert name not in src, name


@pytest.mark.parametrize("cliente, defs, dominio", [("Contreras", CONTRERAS_LIKE, "HCM: Turnover"), ("Illolay", ILLOLAY_LIKE, "4MA_PEDIDOS_ENTREGAS")])
def test_the_single_model_appears_in_models_without_any_line_of_business(tmp_path, cliente, defs, dominio):
    _run(tmp_path, defs, cliente)
    entries = cs.load_catalog(str(tmp_path / "out"))
    assert [dcs.manual_lob(e) for e in entries] == [None]                                        # sin clasificación de línea de negocio
    assert nav.model_domains(cs, entries) == [dominio]


def test_single_model_with_vocabulary_keeps_todays_domain(tmp_path):
    assert [p.name for p in _run(tmp_path, {"1TR_A": _table(), "4MA_STOCK": _view("1TR_A", "Modelo Analítico - Stock", cube=True)}).iterdir()] == ["Stock_Inventario"]


def test_no_analytic_model_and_no_vocabulary_stays_general(tmp_path):
    assert [p.name for p in _run(tmp_path, {"1TR_A": _table(), "2VF_A": _view("1TR_A")}).iterdir()] == ["General"]


# ── (a) curaduría histórica REAL migrada al dominio renombrado ──
def test_historical_general_curation_is_migrated_exactly_and_general_disappears(tmp_path):
    pristine = _fm(_run(tmp_path / "pristine", CONTRERAS_LIKE) / "HCM_Turnover" / "flujo_HCM_Turnover.md")
    cli = tmp_path / "out" / "Cli"
    _legacy(cli, FLOW_CURATION, VIEW_CURATION)
    _run(tmp_path, CONTRERAS_LIKE)
    assert sorted(p.name for p in cli.iterdir()) == ["HCM_Turnover"]                            # 'General' desaparece
    fm = _fm(cli / "HCM_Turnover" / "flujo_HCM_Turnover.md")
    for key in ("cliente", "dominio", "linea_de_negocio", "intent_negocio", "extraccion_corporativa", "compliance_veredicto_confirmado", "notas_senior"):
        assert fm["manual"][key] == FLOW_CURATION[key], key                                     # EXACTAMENTE los valores históricos
    assert fm["manual"]["proyecto"] == "Cli · HCM: Turnover"                                     # el rótulo viejo no se hereda: se rearma
    assert fm["auto"] == pristine["auto"]                                                        # el veredicto legado (auto) no cambia por migrar
    for name in ("AM_Turnover", "1TR_EMP"):
        manual = _fm(cli / "HCM_Turnover" / "vistas" / f"{name}.md")["manual"]
        assert all(manual[k] == v for k, v in VIEW_CURATION[name].items() if k != "proyecto"), (name, manual)
    assert not (cli / "HCM_Turnover" / "vistas" / "1TR_GONE.md").exists()                        # no se siembran vistas de entidades ausentes
    entry, = cs.load_catalog(str(tmp_path / "out"))
    assert dcs.manual_lob(entry) == "HUMAN_RESOURCES"                                            # la línea de negocio manual se lee igual tras migrar
    assert entry.dominio == "Rotación de personal" and entry.veredicto == "buena"                # override del senior y veredicto confirmado intactos


# ── (b) la curaduría propia del destino gana ──
def test_destination_curation_wins_over_the_previous_general(tmp_path):
    cli = tmp_path / "out" / "Cli"
    _legacy(cli, {"notas_senior": "curaduría del destino", "linea_de_negocio": "FINANCE"}, {"AM_Turnover": {"notas": "vista del destino"}}, folder="HCM_Turnover")
    _legacy(cli, FLOW_CURATION, VIEW_CURATION)                                                   # caso prod: General resucitado junto al destino
    _run(tmp_path, CONTRERAS_LIKE)
    assert sorted(p.name for p in cli.iterdir()) == ["HCM_Turnover"]
    manual = _fm(cli / "HCM_Turnover" / "flujo_HCM_Turnover.md")["manual"]
    assert manual["notas_senior"] == "curaduría del destino" and manual["linea_de_negocio"] == "FINANCE" and manual["intent_negocio"] == ""
    assert _fm(cli / "HCM_Turnover" / "vistas" / "AM_Turnover.md")["manual"]["notas"] == "vista del destino"
    assert _fm(cli / "HCM_Turnover" / "vistas" / "1TR_EMP.md")["manual"]["notas"] == "fuente SuccessFactors"   # vista sin curaduría propia: hereda


# ── (c) ≥2 cubos y (d) dominio real de 2 niveles: salida IDÉNTICA a origin/main ──
@pytest.mark.parametrize("case", ["two_models_with_curated_general", "two_level_domain_with_general_sibling", "two_level_single_model"])
def test_untouched_paths_produce_the_same_output_as_main(tmp_path, base_generator, case):
    defs, dominio = {"two_models_with_curated_general": (TWO_MODELS, "General"),
                     "two_level_domain_with_general_sibling": (CONTRERAS_LIKE, "Recursos Humanos"),
                     "two_level_single_model": (ILLOLAY_LIKE, "Logistica")}[case]
    snaps = []
    for name, mod in (("base", base_generator), ("new", gc)):
        root = tmp_path / name
        _legacy(root / "out" / "Cli", FLOW_CURATION, {"1TR_A": {"notas": "tabla curada"}, **VIEW_CURATION})
        _run(root, defs, dominio=dominio, mod=mod)
        snaps.append(_snapshot(root / "out" / "Cli"))
    assert snaps[0] == snaps[1]
    assert snaps[1]                                                                              # generó algo
