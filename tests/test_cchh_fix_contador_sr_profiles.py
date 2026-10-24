"""tools/human_capital/fix_contador_sr_profiles.py — saneamiento one-off `Contador SR` -> `Contador` (+ seniority `Senior` solo si está vacío). Datos 100 % sintéticos en un BASE_DIR temporal con Spaces simulado; nunca toca perfiles reales."""
from __future__ import annotations

import hashlib
import io
import re
from pathlib import Path

import pytest

import config
from tools.human_capital import fix_contador_sr_profiles as tool
from webapp.services import human_capital_service as svc, profile_document_repository as docs, profile_repository as repo
from tests.webapp.test_busqueda_solicitud_i1 import env, remote, seed_catalog  # noqa: F401  (env es un fixture autouse: persistencia aislada + Spaces simulado)

SOURCE = Path(tool.__file__)


@pytest.fixture(autouse=True)
def _governed_catalogs(remote):
    remote.enabled = True                                                       # modo remote con Spaces «configurado»; cada test decide cómo responde
    seed_catalog(seniority=["Junior", "Semi Senior", "Senior"], fuente=["LinkedIn", "Referido"], potencial=["Alto", "Medio"])


def _profile(first="Ana", last="Sintetica", skills="Contador SR", seniority=None, **extra) -> dict:
    data = {"first_name": first, "last_name": last, "email": f"{first}.{last}@sintetico.test".lower(), "phone": "+54 11 5555-0000", "profile_skills": skills, "seniority": seniority,
            "source": "LinkedIn", "tags": "historico, revisar", "notes": "nota interna", "potential_rating": 4, "potential_mark": "Alto", "location": "CABA",
            "import_origin": {"kind": "sharepoint_historical", "run_id": "run-1", "content_key": f"ck-{first}"}, **extra}
    return repo.create(data)


def _run(*argv):
    out = io.StringIO()
    code = tool.run(list(argv), out=out)
    return code, out.getvalue()


def _confirm_phrase() -> str:
    _code, text = _run()
    return re.search(r"--confirm (\S+)", text).group(1)


def _state() -> dict:
    return {p["profile_id"]: p for p in repo.list()}


def _meaningful(row: dict, ignore: set) -> dict:
    """El servicio explicita como `null` los campos de negocio ausentes: un `null` equivale a campo ausente."""
    return {k: v for k, v in row.items() if k not in ignore and v is not None}


def _digest() -> dict:
    d = Path(config.BASE_DIR) / "human-capital" / "profiles"
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(d.glob("*.json"))} if d.exists() else {}


# ═══════════════════ selección y transformación ═══════════════════
def test_the_token_is_matched_exactly_never_by_partial_text():
    for skills in ("Contador SRL", "Contador SR Impuestos", "Contador Senior", "Auditor Contador SR", "ContadorSR", "Contador", "Contador SR2"):
        assert not tool.has_target_token(skills), skills
    for skills in ("Contador SR", " Contador SR ", "Auditoría, Contador SR", "Contador SR,Impuestos", "A, Contador SR, B"):
        assert tool.has_target_token(skills), skills
    assert tool.has_ignored_variant("contador sr") and tool.has_ignored_variant("CONTADOR  SR") and not tool.has_ignored_variant("Contador SR")


@pytest.mark.parametrize("before,after", [("Contador SR", "Contador"), ("Auditoría, Contador SR", "Auditoría, Contador"), ("Contador SR, Impuestos", "Contador, Impuestos"),
                                          ("Impuestos,Contador SR,Costos", "Impuestos,Contador,Costos"), ("  Contador SR  ,Costos", "  Contador  ,Costos"),
                                          ("Contador, Contador SR", "Contador"), ("Contador SR, Contador", "Contador"), ("Contador SR, Contador SR", "Contador")])
def test_only_the_exact_token_is_replaced_keeping_the_other_specialties_and_never_duplicating(before, after):
    assert tool.replace_target_token(before) == after


def test_execute_updates_only_the_selected_profiles_and_preserves_every_other_field_and_document():
    target = _profile("Ana", skills="Auditoría, Contador SR, Impuestos")
    set_sen = _profile("Bruno", skills="Contador SR")
    keep_sen = _profile("Carla", skills="Contador SR", seniority="Senior")
    untouched = _profile("Dario", skills="Contador, Impuestos")
    docs.create_with_explicit_id("doc-1", {"document_id": "doc-1", "profile_id": target["profile_id"], "provider": "sharepoint", "site_id": "s", "drive_id": "d", "item_id": "i", "filename": "cv.pdf",
                                           "content_type": "application/pdf", "size_bytes": 1, "uploaded_at": "2026-10-01T00:00:00+00:00", "uploaded_by": "x@test", "web_url": "https://example.invalid/x"})
    docs_before = {f.name: f.read_bytes() for f in (Path(config.BASE_DIR) / "human-capital" / "profile-documents").glob("*.json")}
    before = _state()
    code, text = _run("--execute", "--confirm", _confirm_phrase())
    assert code == tool.EXIT_OK and "modificado: 3" in text
    after = _state()
    changed = {"profile_skills", "seniority", "revision", "updated_at"}
    for pid, row in after.items():
        if pid == untouched["profile_id"]:
            assert row == before[pid]                                                                  # ni siquiera sube la revisión
            continue
        assert _meaningful(row, changed) == _meaningful(before[pid], changed)                              # todo lo demás intacto (tags, notas, import_origin, potencial int, fuente…)
        assert row["revision"] == before[pid]["revision"] + 1
    assert after[target["profile_id"]]["profile_skills"] == "Auditoría, Contador, Impuestos" and after[target["profile_id"]]["seniority"] == "Senior"
    assert after[set_sen["profile_id"]]["profile_skills"] == "Contador" and after[set_sen["profile_id"]]["seniority"] == "Senior"
    assert after[keep_sen["profile_id"]]["seniority"] == "Senior" and after[keep_sen["profile_id"]]["profile_skills"] == "Contador"
    assert after[target["profile_id"]]["potential_rating"] == 4 and after[target["profile_id"]]["import_origin"] == before[target["profile_id"]]["import_origin"]
    assert {f.name: f.read_bytes() for f in (Path(config.BASE_DIR) / "human-capital" / "profile-documents").glob("*.json")} == docs_before          # los documentos no se tocan


def test_a_different_seniority_is_a_conflict_and_the_whole_profile_is_left_untouched():
    conflict = _profile("Eva", skills="Auditoría, Contador SR", seniority="Junior")
    ok = _profile("Fede", skills="Contador SR")
    before = _state()[conflict["profile_id"]]
    code, text = _run("--execute", "--confirm", _confirm_phrase())
    assert code == tool.EXIT_OK and "conflictos_seniority: 1" in text and "CONFLICTO" in text and "modificado: 1" in text
    assert _state()[conflict["profile_id"]] == before                                                       # ni la especialidad ni el seniority
    assert _state()[ok["profile_id"]]["seniority"] == "Senior"
    assert "ya_corregido" not in text


def test_deleted_profiles_are_never_modified_and_lowercase_variants_are_only_reported():
    gone = _profile("Gaby", skills="Contador SR")
    repo.soft_delete(gone["profile_id"])
    variant = _profile("Hugo", skills="contador sr")
    live = _profile("Iris", skills="Contador SR")
    gone_before, variant_before = _state()[gone["profile_id"]], _state()[variant["profile_id"]]
    code, text = _run()
    assert "dados_de_baja_omitidos: 1" in text and "variantes_ignoradas: 1" in text and "a_modificar: 1" in text
    code, text = _run("--execute", "--confirm", _confirm_phrase())
    assert _state()[gone["profile_id"]] == gone_before and _state()[variant["profile_id"]] == variant_before and _state()[live["profile_id"]]["profile_skills"] == "Contador"


# ═══════════════════ dry-run, confirmación y datos personales ═══════════════════
def test_dry_run_is_the_default_writes_nothing_and_exposes_no_personal_data(remote):
    _profile("Julia", "Reservada", skills="Auditoría, Contador SR")
    _profile("Kevin", "Privado", skills="Contador SR", seniority="Junior")
    digest, pushes = _digest(), list(remote.pushes)
    for args in ((), ("--dry-run",), ("--json",)):
        code, text = _run(*args)
        assert code == tool.EXIT_OK and "Dry-run: no se escribió nada." in text and "a_modificar: 1" in text and "conflictos_seniority: 1" in text
        for private in ("Julia", "Reservada", "Kevin", "Privado", "sintetico.test", "5555-0000", "nota interna"):
            assert private not in text                                                                    # solo id corto, especialidades y seniority
        assert "'Auditoría, Contador SR' -> 'Auditoría, Contador'" in text and "(vacío) -> Senior" in text
    assert _digest() == digest and remote.pushes == pushes                                               # cero escrituras


def test_execute_requires_the_exact_confirmation_derived_from_the_environment_and_the_plan():
    p = _profile("Lola")
    digest = _digest()
    for confirm in ("", "SI", "SANEAR-CONTADOR-00000000-000000000000"):
        code, text = _run("--execute", "--confirm", confirm)
        assert code == tool.EXIT_NOT_CONFIRMED and "CONFIRMACIÓN FALTANTE O INVÁLIDA" in text
    assert _digest() == digest
    phrase = _confirm_phrase()
    repo.update(p["profile_id"], 1, {"notes": "cambió después del dry-run"})                              # el plan revisado ya no es el vigente (otra revisión)
    code, _ = _run("--execute", "--confirm", phrase)
    assert code == tool.EXIT_NOT_CONFIRMED and _state()[p["profile_id"]]["profile_skills"] == "Contador SR"
    assert _run("--execute", "--confirm", _confirm_phrase())[0] == tool.EXIT_OK


def test_dry_run_and_execute_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        _run("--dry-run", "--execute")


def test_seniority_must_exist_exactly_in_the_governed_catalog_or_nothing_runs():
    _profile("Mara")
    seed_catalog(seniority=["Junior", "Semi Senior", "SR"])                                               # sin «Senior» exacto
    digest = _digest()
    for args in ((), ("--execute", "--confirm", "x")):
        code, text = _run(*args)
        assert code == tool.EXIT_BLOCKED and "SENIORITY_NOT_IN_GOVERNED_CATALOG" in text and "Senior" in text
    assert _digest() == digest
    assert _run("--seniority", "SR")[0] == tool.EXIT_OK                                                    # otro valor exacto sí vigente es válido


def test_blocked_when_the_environment_cannot_write(remote):
    _profile("Nora")
    remote.enabled = False                                                                                 # modo remote sin Spaces configurado
    code, text = _run()
    assert code == tool.EXIT_BLOCKED and "REMOTE_NOT_CONFIGURED" in text


# ═══════════════════ idempotencia, concurrencia y fallos parciales ═══════════════════
def test_rerunning_is_idempotent_and_modifies_nothing_already_fixed(remote):
    ids = [_profile(n)["profile_id"] for n in ("Ana", "Beto")]
    assert _run("--execute", "--confirm", _confirm_phrase())[0] == tool.EXIT_OK
    state, digest, pushes = _state(), _digest(), list(remote.pushes)
    code, text = _run("--execute", "--confirm", "cualquier-cosa")
    assert code == tool.EXIT_OK and "Nada para modificar" in text and "idempotente" in text
    code, text = _run()
    assert code == tool.EXIT_OK and "a_modificar: 0" in text and "Para ejecutar" not in text
    assert _state() == state and _digest() == digest and remote.pushes == pushes and all(state[i]["revision"] == 2 for i in ids)


def test_a_concurrent_edit_is_reevaluated_once_and_preserved(monkeypatch):
    p = _profile("Olga", skills="Contador SR")
    real, calls = svc.update_profile, {"n": 0}

    def racing(profile_id, expected_revision, fields, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            repo.update(profile_id, expected_revision, {"notes": "edición concurrente"})                   # otra persona edita justo antes
        return real(profile_id, expected_revision, fields, **kw)
    monkeypatch.setattr(svc, "update_profile", racing)
    code, text = _run("--execute", "--confirm", _confirm_phrase())
    row = _state()[p["profile_id"]]
    assert code == tool.EXIT_OK and calls["n"] == 2 and row["profile_skills"] == "Contador" and row["notes"] == "edición concurrente" and row["revision"] == 3      # re-leyó y conservó el cambio ajeno


def test_a_persistent_concurrent_edit_is_reported_and_the_run_continues(monkeypatch):
    stuck, fine = _profile("Pablo", skills="Contador SR"), _profile("Quino", skills="Contador SR")
    real = svc.update_profile

    def racing(profile_id, expected_revision, fields, **kw):
        if profile_id == stuck["profile_id"]:
            repo.update(profile_id, expected_revision, {"notes": "otra vez"})
        return real(profile_id, expected_revision, fields, **kw)
    monkeypatch.setattr(svc, "update_profile", racing)
    code, text = _run("--execute", "--confirm", _confirm_phrase())
    assert code == tool.EXIT_INCOMPLETE and "modificacion_concurrente: 1" in text and "modificado: 1" in text
    assert _state()[stuck["profile_id"]]["profile_skills"] == "Contador SR" and _state()[fine["profile_id"]]["profile_skills"] == "Contador"
    monkeypatch.setattr(svc, "update_profile", real)                                                       # reanudar: solo lo pendiente
    assert _run("--execute", "--confirm", _confirm_phrase())[0] == tool.EXIT_OK and _state()[stuck["profile_id"]]["profile_skills"] == "Contador"


@pytest.mark.parametrize("script,error", [([True, False, False], "ProfileRemoteOutcomeUnknownError"), ([True, False, True], "ProfileStorageError")], ids=["resultado-remoto-desconocido", "rollback-confirmado"])
def test_a_partial_spaces_failure_stops_the_run_and_a_rerun_finishes_only_what_is_missing(remote, script, error):
    ids = [_profile(n)["profile_id"] for n in ("Ana", "Beto", "Carla")]
    remote.push_script = list(script)                                                                      # 1.º perfil OK; el 2.º no se confirma (y su compensación según el caso)
    code, text = _run("--execute", "--confirm", _confirm_phrase())
    assert code == tool.EXIT_FAILED and f"DETENIDO por {error}" in text and "fallo_persistencia: 1" in text and "pending: 1" in text and "modificado: 1" in text
    state = _state()
    done = [i for i in ids if state[i]["profile_skills"] == "Contador"]
    assert len(done) == 1 and sorted(state[i]["profile_skills"] for i in ids if i not in done) == ["Contador SR", "Contador SR"]       # lo no confirmado quedó como estaba (local restaurado)
    assert all(state[i]["revision"] == 1 for i in ids if i not in done)
    remote.push_script = []                                                                                # Spaces vuelve
    done_revision = state[done[0]]["revision"]
    code, text = _run("--execute", "--confirm", _confirm_phrase())
    assert code == tool.EXIT_OK and "modificado: 2" in text                                                # procesa SOLO lo pendiente
    final = _state()
    assert all(final[i]["profile_skills"] == "Contador" and final[i]["seniority"] == "Senior" for i in ids) and final[done[0]]["revision"] == done_revision


# ═══════════════════ sin escrituras directas ═══════════════════
def test_the_tool_never_writes_files_or_spaces_objects_directly():
    source = SOURCE.read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", source, flags=re.S)
    for forbidden in ("json.dump(", ".write_text(", ".write_bytes(", "_write_atomic", "push_path", "delete_path", "open(", "os.replace", "unlink", "shutil"):
        assert forbidden not in code, forbidden
    assert "service.update_profile(" in code and "repo.get(" in code and '["repo"].list()' in code               # toda escritura pasa por el servicio (que usa el repositorio)
