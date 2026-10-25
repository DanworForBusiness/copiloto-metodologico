"""CLI `cv_exceptions_recoverability`: proceso INDEPENDIENTE (disco vacío) que restaura el lote y los perfiles desde Spaces antes de leer, bloquea ante inconsistencias y es estrictamente de solo lectura (la única escritura posible es el
archivo local explícito de `--detail-out`). Spaces simulado con las funciones reales de `remote_store`; datos sintéticos."""
from __future__ import annotations

import io
import json
import re
from pathlib import Path

import pytest

import config
from tools.human_capital import cv_exceptions_recoverability as cli
from tests.test_cchh_fix_contador_sr_standalone import FakeS3, s3  # noqa: F401  (fixture del proceso independiente)
from tests.test_cv_history_recoverability import PII, RUN, World, _corroborated_world, _ext
from webapp.services import cv_history_recoverability as rec

TOOL = Path(cli.__file__)


def _seed_remote(fake, monkeypatch, tmp_path) -> World:
    """El lado «app web»: construye el lote y los perfiles en OTRO disco (modo local) y los sube al Spaces simulado; el CLI arranca con el disco vacío."""
    web = tmp_path / "web"
    web.mkdir()
    with monkeypatch.context() as m:
        m.setattr(config, "BASE_DIR", web)
        m.setattr(config, "PERSISTENCE_MODE", "local")
        m.setattr(config, "PERSIST_ENABLED", False)
        w = World()
        w.profile("Valeria", "Ibarra", email="ibarra.v@sintetico.test", phone="5491155550001")
        w.doc(_ext(failure="NO_TEXT", rule="R6", state="MISSING"))
        w.doc(_ext(emails=["ibarra.v@sintetico.test", "otro@sintetico.test"], phone="5491155550001", alts=[("Valeria Ibarra", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"]))
        w.doc(_ext(emails=["laura.mendez@sintetico.test"], alts=[("Laura Mendez", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
        w.doc(_ext(phone="5491155550002", alts=[("Rosa Vega", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]))
        w.doc(_ext(phone="5491155550002", alts=[("Rosa Vega", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MISSING", "NAME_UNCORROBORATED"]))
        w.build()
    for path in sorted((web / "human-capital").rglob("*.json")):
        fake.objects[path.relative_to(web).as_posix()] = path.read_bytes()
    return w


def _run(*argv):
    out = io.StringIO()
    code = cli.run(list(argv), out=out)
    return code, out.getvalue()


def test_a_standalone_process_restores_the_batch_and_the_bank_then_reports_without_writing(s3, monkeypatch, tmp_path):
    _seed_remote(s3, monkeypatch, tmp_path)
    remote_before = dict(s3.objects)
    code, text = _run("--run", RUN)
    assert code == cli.EXIT_OK
    assert re.search(r"Lote en Spaces: \d+ · locales antes: 0 · descargados: \d+", text) and "Perfiles en Spaces: 1 · locales antes: 0 · descargados: 1" in text
    assert "excepciones analizadas: 5" in text and "abiertas según el manifiesto: 5 · clasificadas: 5 · diferencia: 0" in text
    assert "POSIBLE_VINCULACION: 1" in text and "ALTA_ASISTIDA: 3" in text and "PROBLEMA_EXTRACCION: 1" in text and "no son identidades verificadas" in text
    for private in PII + ("Laura", "Mendez", "Rosa", "Vega", "laura.mendez", "5491155550002"):
        assert private not in text, private                                                                    # el informe agregado: sin nombres, emails ni teléfonos
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text)                                                     # ... ni profile_id
    assert s3.uploads == [] and s3.objects == remote_before                                                      # solo lectura sobre Spaces
    assert config.PERSIST_PATHS == ["catalog_local", "case_library", "human-capital", "reports", "governed-assessments"]


def test_the_protected_detail_goes_only_to_an_explicit_new_local_file_with_opaque_ids_and_no_personal_data(s3, monkeypatch, tmp_path):
    _seed_remote(s3, monkeypatch, tmp_path)
    target = tmp_path.parent / f"detalle_{tmp_path.name}.json"
    code, text = _run("--run", RUN, "--detail-out", str(target))
    assert code == cli.EXIT_OK and target.exists() and f"Detalle protegido escrito en {target}" in text
    raw = target.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["lote"] == RUN and len(data["detalle"]) == 5 and all(re.fullmatch(r"[0-9a-f]{16}", d["content_key"]) for d in data["detalle"])
    assert any(d["perfiles"] and re.fullmatch(r"[0-9a-f-]{36}", d["perfiles"][0]["profile_id"]) for d in data["detalle"]) and "DETALLE PROTEGIDO" in data["aviso"]
    for private in PII + ("Laura", "Mendez", "Rosa", "Vega", "laura.mendez", "5491155550002"):
        assert private not in raw, private
    assert s3.uploads == []
    again = _run("--run", RUN, "--detail-out", str(target))                                                     # nunca sobrescribe
    assert again[0] == cli.EXIT_USAGE and "ya existe" in again[1]


@pytest.mark.parametrize("where", ["inside_human_capital", "inside_base_dir", "missing_parent"])
def test_the_detail_path_is_refused_when_it_is_inside_persisted_data_or_invalid(s3, monkeypatch, tmp_path, where):
    _seed_remote(s3, monkeypatch, tmp_path)
    path = {"inside_human_capital": Path(config.BASE_DIR) / "human-capital" / "detalle.json", "inside_base_dir": Path(config.BASE_DIR) / "reports" / "detalle.json",
            "missing_parent": tmp_path.parent / "no_existe_la_carpeta" / "detalle.json"}[where]
    path.parent.mkdir(parents=True, exist_ok=True) if where != "missing_parent" else None
    code, text = _run("--run", RUN, "--detail-out", str(path))
    assert code == cli.EXIT_USAGE and "USO INVÁLIDO" in text and not path.exists() and "excepciones analizadas" not in text and s3.uploads == []


def test_an_incomplete_remote_restore_blocks_before_any_report(s3, monkeypatch, tmp_path):
    w = _seed_remote(s3, monkeypatch, tmp_path)
    s3.fail_download = {f"human-capital/import-history/{RUN}/staging/{w.docs[2][0]}.json"}
    code, text = _run("--run", RUN)
    assert code == cli.EXIT_BLOCKED and "RESTORE_INCOMPLETO" in text and "excepciones analizadas" not in text and s3.uploads == []


def test_a_failed_listing_blocks(s3, monkeypatch, tmp_path):
    _seed_remote(s3, monkeypatch, tmp_path)
    s3.listing_error = True
    code, text = _run("--run", RUN)
    assert code == cli.EXIT_BLOCKED and "RESTORE_LISTADO_REMOTO_FALLIDO" in text and "excepciones analizadas" not in text


def test_a_local_copy_that_differs_from_spaces_blocks_and_is_preserved(s3, monkeypatch, tmp_path):
    w = _seed_remote(s3, monkeypatch, tmp_path)
    local = Path(config.BASE_DIR) / "human-capital" / "import-history" / RUN / "ledger"
    local.mkdir(parents=True)
    stale = local / f"{w.docs[1][0]}.json"
    stale.write_text('{"state": "DONE", "obsoleto": true}', encoding="utf-8")
    remote_ledger = f"human-capital/import-history/{RUN}/ledger/{w.docs[1][0]}.json"
    s3.objects[remote_ledger] = b'{"state": "BLOCKED"}'
    code, text = _run("--run", RUN)
    assert code == cli.EXIT_BLOCKED and "RESTORE_LOCAL_DISTINTO_DEL_REMOTO" in text and stale.read_text(encoding="utf-8") == '{"state": "DONE", "obsoleto": true}' and s3.uploads == []


def test_an_unknown_or_invalid_run_is_blocked_with_a_code(s3, monkeypatch, tmp_path):
    _seed_remote(s3, monkeypatch, tmp_path)
    code, text = _run("--run", "ch-20250101T000000Z-aaaa")
    assert code == cli.EXIT_BLOCKED and "LOTE_INEXISTENTE" in text
    code, text = _run("--run", "../../etc")
    assert code == cli.EXIT_BLOCKED and "RUN_ID_INVALIDO" in text


def test_it_works_in_read_only_persistence_mode_because_it_never_needs_write_capability(s3, monkeypatch, tmp_path):
    _seed_remote(s3, monkeypatch, tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "read-only")
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)
    monkeypatch.setattr(config, "PERSIST_READ_ONLY", True)
    code, text = _run("--run", RUN, "--json")
    assert code == cli.EXIT_OK and "excepciones_analizadas" in text and s3.uploads == []


def test_the_cli_source_has_no_write_path_other_than_the_explicit_exclusive_detail_file():
    source = TOOL.read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", source, flags=re.S)
    assert code.count("open(") == 1 and 'open(detail_path, "x"' in code
    for forbidden in ("push_path", "delete_path", "put_persisted_object", "write_through", "mirror_up", "update_profile", "complete_identity", "link_existing", "dismiss", "retry(", "reopen(", "put_group", "put_ledger", "put_plan", ".write_text", "unlink",
                      "download"):
        assert forbidden not in code, forbidden


# ═══════════════════ opción --corroboradas ═══════════════════
def _seed_corroborated(fake, monkeypatch, tmp_path):
    """Lote sintético con las 24 altas corroboradas (9 A · 6 B · 5 C · 4 D) más una alta no corroborada, subido a un Spaces simulado."""
    web = tmp_path / "web"
    web.mkdir()
    with monkeypatch.context() as m:
        m.setattr(config, "BASE_DIR", web)
        m.setattr(config, "PERSISTENCE_MODE", "local")
        m.setattr(config, "PERSIST_ENABLED", False)
        w, private = _corroborated_world()
        w.doc(_ext(emails=["laura.mendez@sintetico.test"], alts=[("Laura Mendez", "UNANCHORED_AGREEMENT")], flags=["NAME_UNCORROBORATED"]))
        w.build()
    for path in sorted((web / "human-capital").rglob("*.json")):
        fake.objects[path.relative_to(web).as_posix()] = path.read_bytes()
    return private


def test_corroboradas_option_reports_the_four_levels_and_reconciles_through_the_standalone_restore(s3, monkeypatch, tmp_path):
    private = _seed_corroborated(s3, monkeypatch, tmp_path)
    remote_before = dict(s3.objects)
    code, text = _run("--run", RUN, "--corroboradas")
    assert code == cli.EXIT_OK and "ALTAS ASISTIDAS CON IDENTIDAD CORROBORADA" in text
    assert "altas asistidas: 25 · corroboradas: 24 · según el informe: 24 · diferencia: 0 · suma de niveles: 24 · ejes que no suman: ninguno" in text
    for line in ("A: 9 — ", "B: 6 — ", "C: 5 — ", "D: 4 — "):
        assert line in text, line
    assert "CRUCE motivo original" in text and "evidencia_del_ancla: {" in text and "dato_a_confirmar: {" in text and "Propuestas NO verificadas" in text
    for item in private + list(PII) + ["Laura", "Mendez", "sintetico.test", "@"]:
        assert item not in text, item
    assert not re.search(r"[0-9a-f]{16}", text.replace(RUN, "")) and not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", text)
    assert s3.uploads == [] and s3.objects == remote_before


def test_without_the_option_the_output_is_exactly_the_previous_one(s3, monkeypatch, tmp_path):
    _seed_corroborated(s3, monkeypatch, tmp_path)
    plain = _run("--run", RUN)
    with_option = _run("--run", RUN, "--corroboradas")
    assert plain[0] == with_option[0] == cli.EXIT_OK and "IDENTIDAD CORROBORADA" not in plain[1] and "NIVEL DE PRECARGA" not in plain[1]
    def report_only(text):                                                                                       # sin las líneas de restauración (la segunda corrida del mismo proceso encuentra el disco ya restaurado)
        return "\n".join(line for line in text.split("\n") if not line.startswith(("Lote en Spaces", "Perfiles en Spaces")))

    base = report_only(plain[1])
    assert report_only(with_option[1]).startswith(base) and "ALTAS ASISTIDAS CON IDENTIDAD CORROBORADA" in report_only(with_option[1])[len(base):]       # solo AGREGA una sección al final


def test_json_adds_the_corroborated_block_only_when_the_option_is_used(s3, monkeypatch, tmp_path):
    _seed_corroborated(s3, monkeypatch, tmp_path)

    def payload(*argv):
        text = _run(*argv)[1]
        return json.loads(text[text.index("{\n  "):])                                                         # el JSON empieza en la primera llave de nivel superior con sangría

    assert "altas_corroboradas" not in payload("--run", RUN, "--json")
    block = payload("--run", RUN, "--json", "--corroboradas")["altas_corroboradas"]
    assert block["corroboradas"] == 24 and block["niveles"] == {"A": 9, "B": 6, "C": 5, "D": 4} and block["conciliacion"]["diferencia"] == 0



def test_a_corroborated_breakdown_that_does_not_reconcile_exits_with_the_reconciliation_code(s3, monkeypatch, tmp_path):
    _seed_corroborated(s3, monkeypatch, tmp_path)
    real = rec.corroborated_breakdown

    def broken(inputs, report):
        b = real(inputs, report)
        b["conciliacion"] = {**b["conciliacion"], "diferencia": 1}
        return b

    monkeypatch.setattr(rec, "corroborated_breakdown", broken)
    code, text = _run("--run", RUN, "--corroboradas")
    assert code == cli.EXIT_NO_RECONCILE and "el desglose de altas corroboradas NO concilia" in text


def test_a_blocked_restore_prints_no_corroborated_breakdown(s3, monkeypatch, tmp_path):
    _seed_corroborated(s3, monkeypatch, tmp_path)
    s3.listing_error = True
    code, text = _run("--run", RUN, "--corroboradas")
    assert code == cli.EXIT_BLOCKED and "RESTORE_LISTADO_REMOTO_FALLIDO" in text and "NIVEL DE PRECARGA" not in text
