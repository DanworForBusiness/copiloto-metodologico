"""M7 — CLI + runner (`__main__.py`, `shadow.py`): salida explicita y fail-closed, validacion de argumentos, determinismo, manifiesto. Inbound CSN SINTETICO."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests._modeling_assessment_m7_fixtures import write_inbound
from tools.catalog.modeling_assessment import __main__ as cli
from tools.catalog.modeling_assessment import legacy_bridge as bridge
from tools.catalog.modeling_assessment import paths, shadow
from tools.catalog.modeling_assessment import profile as P

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "tools" / "catalog" / "modeling_assessment"


@pytest.fixture()
def inbound(tmp_path):
    return write_inbound(tmp_path / "inbound")


def run_cli(capsys, *argv):
    code = cli.main([str(a) for a in argv])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(root).as_posix().encode())
            h.update(p.read_bytes())
    return h.hexdigest()


@pytest.fixture()
def no_persisted(monkeypatch):
    monkeypatch.setattr(bridge, "persisted_dirs", lambda: [])


# ── corrida feliz ────────────────────────────────────────────────────────────
def test_cli_writes_json_markdown_and_manifest_for_one_analytic_model(inbound, tmp_path, capsys, no_persisted):
    out = tmp_path / "out"
    code, stdout, _err = run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", out)
    assert code == 0 and "evaluación: ASSESSED" in stdout
    d = out / "Acme"
    assert sorted(p.name for p in d.iterdir()) == ["4AM_ACME.profile.json", "4AM_ACME.profile.md", "run_manifest.json"]
    doc = json.loads((d / "4AM_ACME.profile.json").read_text(encoding="utf-8"))
    P.validate_assessment(doc)
    assert doc["scope"]["client"] == "Acme" and doc["scope"]["terminal_analytic_model"] == "4AM_ACME" and doc["scope"]["closure_size"] >= 4
    assert any(g["id"] == "DIRECT_RAW_TABLE_ACCESS" and g["occurrence_count"] == 1 for g in doc["observations"])
    md = (d / "4AM_ACME.profile.md").read_text(encoding="utf-8")
    assert md.startswith("# Autoevaluación de Modelado") and "ACCESO DIRECTO A TABLA FÍSICA — 1 ocurrencia" in md and "`RAW_T`" in md
    assert not re.search(r"\bflujo_", "".join(p.name for p in d.iterdir()))                 # nunca un prefijo que el catalogo cargaria


def test_json_and_markdown_describe_the_same_assessment(inbound, tmp_path, capsys, no_persisted):
    run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", tmp_path / "o")
    doc = json.loads((tmp_path / "o/Acme/4AM_ACME.profile.json").read_text(encoding="utf-8"))
    from tools.catalog.modeling_assessment import render_md
    assert render_md.render_markdown(doc) == (tmp_path / "o/Acme/4AM_ACME.profile.md").read_text(encoding="utf-8")


def test_aliases_and_canonical_names_give_identical_outputs(inbound, tmp_path, capsys, no_persisted):
    run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", tmp_path / "a")
    run_cli(capsys, "--inbound", inbound, "--client", "acme", "--analytic-model", "4AM_ACME", "--output", tmp_path / "b")
    for name in ("4AM_ACME.profile.json", "4AM_ACME.profile.md"):
        assert (tmp_path / "a/Acme" / name).read_bytes() == (tmp_path / "b/Acme" / name).read_bytes()     # el cliente se resuelve sin distinguir mayusculas


def test_execution_is_deterministic_and_the_manifest_only_differs_in_time(inbound, tmp_path, capsys, no_persisted):
    for o in ("x", "y"):
        assert run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", tmp_path / o)[0] == 0
    for name in ("4AM_ACME.profile.json", "4AM_ACME.profile.md"):
        assert (tmp_path / "x/Acme" / name).read_bytes() == (tmp_path / "y/Acme" / name).read_bytes()
    mx, my = (json.loads((tmp_path / o / "Acme/run_manifest.json").read_text(encoding="utf-8")) for o in ("x", "y"))
    for m_ in (mx, my):
        for run in m_["runs"].values():
            run.pop("timestamp_utc")
            run["inbound"] = "<inbound>"
    assert mx == my


def test_json_and_markdown_contain_no_timestamps_or_absolute_local_paths(inbound, tmp_path, capsys, no_persisted):
    run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", tmp_path / "o")
    for name in ("4AM_ACME.profile.json", "4AM_ACME.profile.md"):
        text = (tmp_path / "o/Acme" / name).read_text(encoding="utf-8")
        assert str(tmp_path) not in text and not re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", text)


# ── manifiesto ───────────────────────────────────────────────────────────────
def test_manifest_records_provenance_and_output_hashes(inbound, tmp_path, capsys, no_persisted):
    out = tmp_path / "o"
    run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", out)
    man = json.loads((out / "Acme/run_manifest.json").read_text(encoding="utf-8"))
    assert man["manifest_version"] == shadow.MANIFEST_VERSION and list(man["runs"]) == ["4AM_ACME"]
    run = man["runs"]["4AM_ACME"]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", run["timestamp_utc"]) and run["assessment_state"] == "ASSESSED" and run["errors"] == []
    assert run["git_sha"] is None or re.fullmatch(r"[0-9a-f]{40}", run["git_sha"])
    assert run["pool"]["pool_signature"] == json.loads((out / "Acme/4AM_ACME.profile.json").read_text(encoding="utf-8"))["scope"]["pool_signature"]
    assert run["versions"]["reason_codes"] == P.REASON_REGISTRY_VERSION and run["versions"]["assessment_schema"] == P.ASSESSMENT_SCHEMA_VERSION
    for kind, fname in (("json", "4AM_ACME.profile.json"), ("markdown", "4AM_ACME.profile.md")):
        assert run["outputs"][kind] == {"file": fname, "sha256": hashlib.sha256((out / "Acme" / fname).read_bytes()).hexdigest()}


def test_pool_signature_changes_when_any_pool_file_changes(inbound):
    key, group = shadow.select_pool(inbound, "Acme")
    files = [*group["jsons"], *group["txts"]]
    before = shadow.pool_signature(inbound, files)
    assert before == shadow.pool_signature(inbound, list(reversed(files)))
    files[0].write_text(files[0].read_text(encoding="utf-8") + " ", encoding="utf-8")
    assert shadow.pool_signature(inbound, files) != before


def test_second_model_run_is_added_to_the_manifest_instead_of_replacing_it(inbound, tmp_path, capsys, no_persisted):
    out = tmp_path / "o"
    run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", out)
    run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_OTHER", "--out", out)
    assert sorted(json.loads((out / "Acme/run_manifest.json").read_text(encoding="utf-8"))["runs"]) == ["4AM_ACME", "4AM_OTHER"]
    assert (out / "Acme/4AM_OTHER.profile.json").exists() and (out / "Acme/4AM_ACME.profile.json").exists()


# ── argumentos ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("argv", [["--client", "Acme"], ["--inbound", "x"], []])
def test_required_arguments_are_enforced(argv, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main(argv)
    assert e.value.code == 2 and "obligatorios" in capsys.readouterr().err


def test_there_is_no_default_output_dir_and_no_way_to_allow_persisted_paths():
    parser = cli.build_parser()
    assert parser.get_default("out") is None
    options = {o for a in parser._actions for o in a.option_strings}
    assert options == {"-h", "--help", "--inbound", "--client", "--domain", "--am", "--analytic-model", "--out", "--output", "--list-models"}
    assert not any("persist" in o or "force" in o or "unsafe" in o for o in options)
    for name in ("__main__.py", "shadow.py"):
        for n in ast.walk(ast.parse((PKG / name).read_text(encoding="utf-8"))):
            assert not (isinstance(n, ast.keyword) and n.arg == "allow_persisted"), name      # la excepcion de M0 no se expone ni se usa
            assert not (isinstance(n, ast.Constant) and n.value == "allow_persisted"), name


def test_missing_output_fails_closed_before_reading_the_pool(inbound, capsys, monkeypatch):
    monkeypatch.setattr(shadow, "assess", lambda *a, **k: pytest.fail("el pool no debe leerse sin un destino valido"))
    code, _o, err = run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME")
    assert code == 2 and "--out es obligatorio" in err
    code, _o, err = run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", "   ")
    assert code == 2 and "--out es obligatorio" in err


def test_output_inside_a_persisted_directory_is_rejected_even_with_mirroring_off(inbound, tmp_path, capsys, monkeypatch):
    persisted = tmp_path / "catalog_like" / "persisted"
    persisted.mkdir(parents=True)
    monkeypatch.setattr(bridge, "persisted_dirs", lambda: [persisted])
    monkeypatch.setattr(bridge, "persist_enabled", lambda: False)
    monkeypatch.setattr(shadow, "assess", lambda *a, **k: pytest.fail("el pool no debe leerse con un destino inseguro"))
    before = _tree_hash(tmp_path / "catalog_like")
    for target in (persisted, persisted / "sub" / "dir"):
        code, _o, err = run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", target)
        assert code == 2 and "persistido" in err
    assert _tree_hash(tmp_path / "catalog_like") == before and not (persisted / "sub").exists()


def test_every_real_persisted_directory_of_the_project_is_rejected_including_catalog_local(inbound, capsys):
    dirs = bridge.persisted_dirs()
    assert "catalog_local" in {d.name for d in dirs} and len(dirs) >= 5                # el proyecto declara los directorios espejados
    for d in dirs:
        target = d / "modeling_assessment_out"
        code, _o, err = run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", target)
        assert code == 2 and "persistido" in err and not target.exists(), d
        code, _o, err = run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", d)
        assert code == 2 and "persistido" in err, d


def test_missing_inbound_directory_is_a_usage_error(tmp_path, capsys, no_persisted):
    code, _o, err = run_cli(capsys, "--inbound", tmp_path / "nope", "--client", "Acme", "--am", "A", "--out", tmp_path / "o")
    assert code == 2 and "no es un directorio" in err and not (tmp_path / "o").exists()


def test_unknown_client_unknown_model_and_missing_model_selection(inbound, tmp_path, capsys, no_persisted):
    code, _o, err = run_cli(capsys, "--inbound", inbound, "--client", "Ghost", "--am", "4AM_ACME", "--out", tmp_path / "o")
    assert code == 2 and "Ghost" in err and "Acme" in err                          # lista los clientes con pool
    code, _o, err = run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_GHOST", "--out", tmp_path / "o")
    assert code == 2 and "4AM_GHOST" in err
    code, _o, err = run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--out", tmp_path / "o")     # hay 2 AM terminales: no se asume el cliente
    assert code == 2 and "4AM_ACME" in err and "4AM_OTHER" in err
    code, _o, err = run_cli(capsys, "--inbound", inbound, "--client", "Solo", "--am", "4AM_ACME", "--out", tmp_path / "o")
    assert code == 2
    assert not (tmp_path / "o").exists() or not list((tmp_path / "o").rglob("*.json"))


def test_ambiguous_client_domain_requires_domain(tmp_path, capsys, no_persisted):
    inbound = write_inbound(tmp_path / "in")
    (inbound / "Acme" / "DomX").mkdir()
    src = json.loads((inbound / "Acme/remote-tables/RAW_T.json").read_text(encoding="utf-8"))
    (inbound / "Acme/DomX/extra.json").write_text(json.dumps({"definitions": {"EXTRA": src["definitions"]["RAW_T"]}}), encoding="utf-8")
    code, _o, err = run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", tmp_path / "o")
    assert code == 2 and "--domain" in err


def test_list_models_prints_terminal_models_and_writes_nothing(inbound, tmp_path, capsys, no_persisted, monkeypatch):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    monkeypatch.chdir(cwd)                                                          # tampoco escribe en el directorio de trabajo
    before = _tree_hash(tmp_path)
    code, out, _e = run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--list-models")
    assert code == 0 and out.split() == ["4AM_ACME", "4AM_OTHER"] and _tree_hash(tmp_path) == before and not list(cwd.iterdir())
    code, out, _e = run_cli(capsys, "--inbound", inbound, "--client", "Solo", "--list-models")
    assert code == 0 and out.strip() == ""


# ── cliente sin AM terminal / pool no cargable ───────────────────────────────
def test_client_without_a_terminal_model_yields_a_not_applicable_assessment(inbound, tmp_path, capsys, no_persisted):
    out = tmp_path / "o"
    code, stdout, _e = run_cli(capsys, "--inbound", inbound, "--client", "Solo", "--out", out)
    assert code == 0 and "evaluación: NOT_APPLICABLE" in stdout
    doc = json.loads((out / "Solo" / f"{shadow.NO_TERMINAL_AM_LABEL}.profile.json").read_text(encoding="utf-8"))
    assert doc["assessment_state"] == "NOT_APPLICABLE" and doc["scope"]["reason"]["reason_code"] == "NO_TERMINAL_ANALYTIC_MODEL"
    assert "**Este cliente no tiene evaluación**" in (out / "Solo" / f"{shadow.NO_TERMINAL_AM_LABEL}.profile.md").read_text(encoding="utf-8")


def test_unloadable_pool_writes_an_indeterminate_document_and_exits_nonzero(inbound, tmp_path, capsys, no_persisted):
    out = tmp_path / "o"
    code, stdout, err = run_cli(capsys, "--inbound", inbound, "--client", "Clash", "--out", out)
    assert code == 3 and "evaluación: INDETERMINATE" in stdout and "error:" in err
    doc = json.loads((out / "Clash" / f"{shadow.NO_TERMINAL_AM_LABEL}.profile.json").read_text(encoding="utf-8"))
    assert doc["assessment_state"] == "INDETERMINATE" and doc["scope"]["reason"]["reason_code"] == "POOL_LOAD_ERROR"
    man = json.loads((out / "Clash/run_manifest.json").read_text(encoding="utf-8"))
    run = man["runs"][shadow.NO_TERMINAL_AM_LABEL]
    assert run["assessment_state"] == "INDETERMINATE" and run["errors"] and "DUP" in run["errors"][0]


# ── solo lectura ─────────────────────────────────────────────────────────────
def test_the_run_never_modifies_the_inbound_pool(inbound, tmp_path, capsys, no_persisted):
    before = _tree_hash(inbound)
    for client, extra in (("Acme", ["--am", "4AM_ACME"]), ("Solo", []), ("Clash", [])):
        run_cli(capsys, "--inbound", inbound, "--client", client, "--out", tmp_path / "o", *extra)
    assert _tree_hash(inbound) == before


def test_runner_never_writes_outside_the_explicit_output_directory(inbound, tmp_path, capsys, no_persisted, monkeypatch):
    written = []
    real = Path.write_text

    def spy(self, *a, **k):
        written.append(Path(self).resolve())
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "write_text", spy)
    out = (tmp_path / "o").resolve()
    run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", out)
    assert written and all(out in p.parents for p in written), [str(p) for p in written if out not in p.parents]


# ── fronteras del paquete ────────────────────────────────────────────────────
def test_cli_and_runner_only_touch_the_legacy_through_the_bridge():
    for name in ("shadow.py", "__main__.py"):
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if isinstance(n, ast.ImportFrom) and n.level == 0:
                assert (n.module or "").split(".")[0] not in {"tools", "config", "core", "webapp"}, (name, n.module)
            if isinstance(n, ast.Import):
                assert all(a.name.split(".")[0] not in {"tools", "config", "core", "webapp"} for a in n.names)
    assert "catalog_local" not in (PKG / "shadow.py").read_text(encoding="utf-8").replace('"""', "").split("def ")[1]


def test_module_entry_point_runs_as_python_dash_m(inbound, tmp_path):
    env_out = tmp_path / "o"
    r = subprocess.run([sys.executable, "-m", "tools.catalog.modeling_assessment", "--inbound", str(inbound), "--client", "Acme", "--am", "4AM_ACME",
                        "--out", str(env_out)], cwd=REPO, capture_output=True, text=True, timeout=180)
    assert r.returncode == 0, r.stderr
    assert "evaluación: ASSESSED" in r.stdout and (env_out / "Acme/4AM_ACME.profile.md").exists()
    r = subprocess.run([sys.executable, "-m", "tools.catalog.modeling_assessment", "--inbound", str(inbound), "--client", "Acme", "--am", "4AM_ACME"],
                       cwd=REPO, capture_output=True, text=True, timeout=180)
    assert r.returncode == 2 and "--out es obligatorio" in r.stderr


def test_cli_help_and_runner_strings_use_no_score_or_band_vocabulary():
    banned = re.compile(r"(score|scoring|band|weights?|penalt(?:y|ies)|PLENO|BUENO|PARCIAL|rating|grade|ranking)", re.I)
    assert not banned.search(cli.build_parser().format_help())
    for name in ("shadow.py", "__main__.py", "profile.py"):
        tree = ast.parse((PKG / name).read_text(encoding="utf-8"))
        docs = {id(n.body[0].value) for n in ast.walk(tree) if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef)) and n.body
                and isinstance(n.body[0], ast.Expr) and isinstance(getattr(n.body[0], "value", None), ast.Constant)}
        bad = [c.value for c in ast.walk(tree) if isinstance(c, ast.Constant) and isinstance(c.value, str) and id(c) not in docs and banned.search(c.value)]
        assert bad == [], (name, bad)


def test_real_cli_help_is_spanish_positive_and_free_of_unpublished_vocabulary():
    """Ejercita la ruta PUBLICA real (`python -m ... --help`) y la compara con el parser en proceso: lo que ve el usuario es lo que se verifica."""
    banned = re.compile(r"(score|scoring|band|weights?|penalt(?:y|ies)|PLENO|BUENO|PARCIAL|rating|grade|ranking|maturity|puntaje|puntuaci[oó]n|banda|"
                        r"penalizaci[oó]n|penalidad|ponderaci[oó]n|madurez)", re.I)
    r = subprocess.run([sys.executable, "-m", "tools.catalog.modeling_assessment", "--help"], cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, "PYTHONIOENCODING": "utf-8"}, timeout=180)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert not banned.search(out), banned.search(out).group(0)
    flat = " ".join(out.split())                                                                    # la ayuda envuelve lineas: se compara sin saltos
    assert "uso:" in flat and "opciones:" in flat and "muestra esta ayuda y termina" in flat and "perfil de evidencia, cobertura y confianza" in flat
    assert "usage:" not in flat and "options:" not in flat and "show this help" not in flat
    assert flat == " ".join(cli.build_parser().format_help().split())              # la ayuda en proceso es la misma que ve el usuario


def test_cli_runtime_messages_are_spanish(inbound, tmp_path, capsys, no_persisted):
    code, stdout, _e = run_cli(capsys, "--inbound", inbound, "--client", "Acme", "--am", "4AM_ACME", "--out", tmp_path / "o")
    assert code == 0 and [ln.split(":")[0] for ln in stdout.splitlines()] == ["evaluación", "json", "markdown", "manifiesto"]
    with pytest.raises(SystemExit) as e:
        cli.main(["--client", "Acme"])
    err = capsys.readouterr().err
    assert e.value.code == 2 and "faltan argumentos obligatorios: --inbound" in err and "uso:" in err and "the following" not in err and "usage:" not in err
    with pytest.raises(SystemExit):
        cli.main(["--inbound", str(inbound), "--client", "Acme", "--bogus"])
    assert "argumentos no reconocidos: --bogus" in capsys.readouterr().err


def test_m7_sources_and_tests_contain_no_stray_control_characters():
    """Una barra invertida + b mal escapada deja un backspace literal dentro de una regex y la vuelve imposible de cumplir (test vacuo): se vigila en todo M7."""
    files = [*PKG.glob("*.py"), *(REPO / "tests").glob("test_modeling_assessment_*.py"), *(REPO / "tests").glob("_modeling_assessment_m*_fixtures.py")]
    assert len(files) > 20
    allowed = {"\n", "\r", "\t"}
    bad = []
    for f in files:
        text = f.read_text(encoding="utf-8")
        bad += [f"{f.name}:{text.count(chr(10), 0, i) + 1}" for i, ch in enumerate(text) if ord(ch) < 32 and ch not in allowed]
    assert bad == [], bad
