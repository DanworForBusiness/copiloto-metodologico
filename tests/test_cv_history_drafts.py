"""Borradores por excepción de «Históricos sin validación» (Entrega B): servicio y persistencia. Lotes SINTÉTICOS (motor real de `build_plan`), disco temporal y Spaces simulado: nada contra DigitalOcean ni datos reales.
Un borrador nunca es un perfil: solo escribe `drafts/<content_key>.json` del lote; no toca perfiles, CVs, staging, manifiesto, libro ni auditoría."""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from pathlib import Path

import pytest

import config
from core import remote_store
from tests.test_cv_history_recoverability import RUN, World, _ext
from webapp.services import cv_history_drafts as drafts, cv_history_import_exec as ex, cv_history_manifest as mf, profile_repository

ACTOR = "owner-sintetico@danwor.com"
OTHER = "otra-persona@danwor.com"
PRIVATE = ("Zulema", "Draftona", "zulema.draftona@sintetico.test", "5491155559999", "observacion secreta")


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(config, "PERSIST_ENABLED", False)


def world(n: int = 2) -> list[str]:
    w = World()
    w.profile("Valeria", "Ibarra", email="ibarra.v@sintetico.test")
    keys = [w.doc(_ext(emails=[f"cv{i}@sintetico.test", f"alt{i}@sintetico.test"], alts=[(f"Persona{chr(97 + i)} Apellido{chr(97 + i)}", "UNANCHORED_AGREEMENT")], flags=["EMAIL_MULTIPLE", "NAME_UNCORROBORATED"])) for i in range(n)]
    w.build()
    return keys


def digest(skip: str = "drafts") -> dict:
    root = Path(config.BASE_DIR)
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.rglob("*")) if p.is_file() and f"/{skip}/" not in p.as_posix()}


def draft_files() -> list[Path]:
    return sorted((Path(config.BASE_DIR) / "human-capital" / "import-history" / RUN / "drafts").glob("*.json")) if (Path(config.BASE_DIR) / "human-capital" / "import-history" / RUN / "drafts").is_dir() else []


FIELDS = {"first_name": "Zulema", "last_name": "Draftona", "email": "zulema.draftona@sintetico.test", "phone": "+54 9 11 5555-9999", "notes": "observacion secreta"}


def save(key, raw=None, *, rev=0, actor=ACTOR):
    return drafts.save(RUN, key, FIELDS if raw is None else raw, actor=actor, expected_revision=rev)


# ═══════════════════ guardar, retomar, historial ═══════════════════
def test_the_first_save_creates_revision_1_with_author_dates_and_a_history_entry(monkeypatch):
    key, _ = world()
    monkeypatch.setattr(mf, "now_iso", lambda: "2026-10-26T10:00:00Z")
    out = save(key)
    doc = mf.get_draft(RUN, key)
    assert out["created"] and out["changed"] and doc == out["doc"]
    assert (doc["schema"], doc["run_id"], doc["content_key"], doc["revision"]) == (mf.DRAFT_SCHEMA, RUN, key, 1)
    assert (doc["created_by"], doc["updated_by"], doc["created_at"], doc["updated_at"]) == (ACTOR, ACTOR, "2026-10-26T10:00:00Z", "2026-10-26T10:00:00Z")
    assert doc["fields"] == FIELDS and doc["history"] == [{"revision": 1, "at": "2026-10-26T10:00:00Z", "by": ACTOR, "changed": list(drafts.FIELDS), "before": {f: "" for f in drafts.FIELDS}}]
    assert [p.name for p in draft_files()] == [f"{key}.json"]


def test_reopening_returns_exactly_what_was_saved_and_a_rectification_keeps_the_history(monkeypatch):
    key, _ = world()
    times = iter(["2026-10-26T10:00:00Z", "2026-10-26T11:30:00Z"])
    monkeypatch.setattr(mf, "now_iso", lambda: next(times))
    save(key)
    again = {**FIELDS, "last_name": "Draftoni", "phone": ""}
    out = save(key, again, rev=1, actor=OTHER)
    doc = drafts.get(RUN, key)
    assert out["changed"] and not out["created"] and doc["revision"] == 2 and doc["fields"] == drafts.clean(again)
    assert (doc["created_by"], doc["created_at"], doc["updated_by"], doc["updated_at"]) == (ACTOR, "2026-10-26T10:00:00Z", OTHER, "2026-10-26T11:30:00Z")
    last = doc["history"][-1]
    assert last["revision"] == 2 and last["by"] == OTHER and last["changed"] == ["last_name", "phone"] and last["before"] == {"last_name": "Draftona", "phone": "+54 9 11 5555-9999"}
    v = drafts.view(doc)
    assert v["fields"] == doc["fields"] and v["revision"] == 2 and v["history"][0]["revision"] == 2 and v["history"][0]["changed"] == ["apellido", "teléfono"] and v["updated_at"] == "2026-10-26 11:30 UTC"
    assert v["name"] == "Zulema Draftoni" and [h["revision"] for h in v["history"]] == [2, 1]


def test_saving_the_same_values_changes_nothing_and_keeps_the_revision(monkeypatch):
    key, _ = world()
    save(key)
    before = draft_files()[0].read_bytes()
    out = save(key, dict(FIELDS), rev=1)
    assert not out["changed"] and out["doc"]["revision"] == 1 and draft_files()[0].read_bytes() == before                  # ni siquiera se reescribe el archivo
    out = save(key, {**FIELDS, "first_name": "  Zulema   "}, rev=1)                                                           # los espacios sobrantes no son un cambio
    assert not out["changed"] and drafts.get(RUN, key)["revision"] == 1


def test_clean_normalizes_whitespace_and_validation_rejects_bad_values_without_writing():
    assert drafts.clean({"first_name": "  Ana   María ", "notes": " línea 1  \r\n\r\nlínea 2   \n"}) == {"first_name": "Ana María", "last_name": "", "email": "", "phone": "", "notes": "línea 1\n\nlínea 2"}
    key, _ = world()
    before = digest()
    for field, value, message in (("email", "no-es-un-email", "email no parece válido"), ("email", "a b@c.com", "email no parece válido"), ("phone", "abc", "al menos 6 dígitos"), ("phone", "123", "al menos 6 dígitos"),
                                  ("first_name", "x" * 61, "supera los 60"), ("notes", "y" * 2001, "supera los 2000"), ("last_name", "Ana\x00", "caracteres no válidos")):
        with pytest.raises(drafts.DraftError, match=message):
            save(key, {**FIELDS, field: value})
    assert draft_files() == [] and digest() == before


def test_an_empty_draft_cannot_be_created_but_an_existing_one_can_be_cleared():
    key, _ = world()
    with pytest.raises(drafts.DraftError, match="nada para guardar"):
        save(key, {})
    assert draft_files() == []
    save(key)
    out = save(key, {}, rev=1)
    assert out["changed"] and drafts.get(RUN, key)["fields"] == {f: "" for f in drafts.FIELDS} and drafts.get(RUN, key)["revision"] == 2


def test_incomplete_drafts_are_allowed_partial_work_is_the_point():
    key, _ = world()
    save(key, {"first_name": "Zulema"})
    assert drafts.get(RUN, key)["fields"]["first_name"] == "Zulema" and drafts.get(RUN, key)["fields"]["email"] == ""


def test_the_history_is_capped_and_counts_what_was_dropped():
    key, _ = world()
    for rev in range(55):
        save(key, {"first_name": f"Nombre{rev}"}, rev=rev)
    doc = drafts.get(RUN, key)
    assert doc["revision"] == 55 and len(doc["history"]) == drafts.MAX_HISTORY and doc["history_dropped"] == 5 and doc["history"][0]["revision"] == 6 and doc["history"][-1]["revision"] == 55


# ═══════════════════ control de revisión y concurrencia ═══════════════════
def test_a_stale_revision_is_a_conflict_that_writes_nothing_and_shows_what_is_saved():
    key, _ = world()
    save(key)
    save(key, {**FIELDS, "last_name": "Draftoni"}, rev=1, actor=OTHER)                                                          # otra persona se adelantó (rev 2)
    before = draft_files()[0].read_bytes()
    with pytest.raises(drafts.DraftConflict) as conflict:
        save(key, {**FIELDS, "first_name": "Zulema Ana"}, rev=1)                                                                # el formulario cargó la revisión 1
    assert conflict.value.current["revision"] == 2 and draft_files()[0].read_bytes() == before
    view = drafts.conflict_view(conflict.value.current, drafts.clean({**FIELDS, "first_name": "Zulema Ana"}))
    assert view["revision"] == 2 and view["by"] == OTHER and ("nombre", "Zulema") in view["differs"] and ("apellido", "Draftoni") in view["differs"] and not view["deleted"]
    assert save(key, {**FIELDS, "first_name": "Zulema Ana"}, rev=2)["doc"]["revision"] == 3                                       # tras ver lo vigente, guarda sabiendo


def test_a_form_that_loaded_no_draft_conflicts_with_one_created_in_the_meantime_and_a_wrong_revision_is_invalid():
    key, _ = world()
    save(key)
    with pytest.raises(drafts.DraftConflict):
        save(key, {**FIELDS, "notes": "otra"}, rev=0)
    for bad in ("abc", "-1", "1.5", "9" * 12):
        with pytest.raises(drafts.DraftError, match="revisión del formulario"):
            save(key, FIELDS, rev=bad)
    assert drafts.conflict_view(None, {})["deleted"] is True


def test_concurrent_saves_with_the_same_revision_let_exactly_one_win():
    key, _ = world()
    barrier, results = threading.Barrier(8), []

    def worker(n):
        barrier.wait()
        try:
            save(key, {"first_name": f"Hilo{n}"}, rev=0, actor=f"actor{n}@danwor.com")
            results.append("ok")
        except drafts.DraftConflict:
            results.append("conflict")
    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["conflict"] * 7 + ["ok"] and drafts.get(RUN, key)["revision"] == 1 and len(drafts.get(RUN, key)["history"]) == 1


def test_a_write_that_another_process_overwrote_is_reported_not_swallowed(monkeypatch):
    key, _ = world()
    real = mf._write

    def clobbering(path, payload):
        real(path, payload)
        real(path, {**payload, "revision": 99, "updated_by": "otro-proceso"})
    monkeypatch.setattr(mf, "_write", clobbering)
    with pytest.raises(mf.ManifestError, match="cambió durante el guardado"):
        save(key)


# ═══════════════════ persistencia: errores parciales, solo lectura y aislamiento ═══════════════════
def _remote_like(monkeypatch, outcome):
    """Spaces simulado: el archivo local se escribe siempre (como `write_through` real) y el resultado de la durabilidad lo decide `outcome()`."""
    def fake(path, text, **kw):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text if isinstance(text, str) else text.decode("utf-8"), encoding="utf-8")
        return outcome()
    monkeypatch.setattr(remote_store, "write_through", fake)
    monkeypatch.setattr(remote_store, "local_only", lambda: False)
    monkeypatch.setattr(remote_store, "enabled", lambda: False)


def test_when_spaces_does_not_confirm_nothing_is_saved_the_previous_draft_is_restored_and_a_retry_works(monkeypatch):
    key, _ = world()
    state = {"result": remote_store.DURABLE_REMOTE}
    _remote_like(monkeypatch, lambda: state["result"])
    save(key)
    previous = draft_files()[0].read_bytes()
    state["result"] = remote_store.DURABLE_LOCAL                                                                                 # Spaces no confirma
    with pytest.raises(mf.ManifestDurabilityError):
        save(key, {**FIELDS, "last_name": "Draftoni"}, rev=1)
    assert draft_files()[0].read_bytes() == previous and drafts.get(RUN, key)["revision"] == 1                                  # local y remoto no divergen: sigue la revisión 1
    state["result"] = remote_store.DURABLE_REMOTE
    out = save(key, {**FIELDS, "last_name": "Draftoni"}, rev=1)                                                                  # el formulario conserva la revisión: reintentar funciona
    assert out["doc"]["revision"] == 2 and drafts.get(RUN, key)["fields"]["last_name"] == "Draftoni"


def test_a_first_save_that_spaces_does_not_confirm_leaves_no_file_behind(monkeypatch):
    key, _ = world()
    _remote_like(monkeypatch, lambda: remote_store.DURABLE_LOCAL)
    with pytest.raises(mf.ManifestDurabilityError):
        save(key)
    assert draft_files() == [] and drafts.count(RUN) == 0


def test_read_only_mode_rejects_the_save_before_touching_the_disk(monkeypatch):
    key, _ = world()
    monkeypatch.setattr(remote_store, "read_only_mode", lambda: True)
    before = digest(skip="__none__")
    with pytest.raises(mf.ManifestDurabilityError, match="solo lectura"):
        save(key)
    assert digest(skip="__none__") == before and draft_files() == []


def test_saving_a_draft_writes_only_its_own_file_and_never_touches_the_bank_the_ledger_staging_or_the_manifest(monkeypatch):
    key, other = world()
    mf.put_ledger(RUN, other, {"source": "manual", "profile_id": None, "document_id": None, "attempts": 0, "history": [], "state": "BLOCKED", "reason": "X"})
    profiles_before = profile_repository.list()
    before = digest()
    for fn in ("complete_identity", "link_existing", "dismiss", "reopen", "retry", "execute_batch", "start_execution"):
        monkeypatch.setattr(ex, fn, lambda *a, **k: (_ for _ in ()).throw(AssertionError("guardar un borrador no puede llamar a las acciones de importación")))
    monkeypatch.setattr(profile_repository, "create", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no se crean perfiles")))
    monkeypatch.setattr(profile_repository, "update", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no se modifican perfiles")))
    save(key)
    save(key, {**FIELDS, "notes": "otra nota"}, rev=1)
    assert digest() == before                                                                                                    # run.json, plan, índice, staging, libro, auditoría y perfiles: byte a byte iguales
    assert profile_repository.list() == profiles_before and [p.relative_to(config.BASE_DIR).as_posix() for p in draft_files()] == [f"human-capital/import-history/{RUN}/drafts/{key}.json"]
    assert not (Path(config.BASE_DIR) / "human-capital" / "profiles" / f"{key}.json").exists() and len(mf.list_ledgers(RUN)) == 1


def test_a_draft_is_not_a_profile_so_every_bank_listing_ignores_it():
    key, _ = world()
    n = len(profile_repository.list())
    save(key)
    assert len(profile_repository.list()) == n and all("Zulema" not in json.dumps(p, ensure_ascii=False) for p in profile_repository.list())
    profiles_dir = Path(config.BASE_DIR) / "human-capital" / "profiles"
    assert all("Zulema" not in f.read_text(encoding="utf-8") for f in profiles_dir.rglob("*.json"))


def test_discarding_the_temporary_area_does_not_delete_drafts_there_is_no_automatic_deletion():
    key, _ = world()
    save(key)
    removed = mf.delete_staging_files(RUN)
    assert removed > 0 and mf.staging_files_present(RUN) == 0
    assert drafts.get(RUN, key)["fields"] == drafts.clean(FIELDS) and drafts.count(RUN) == 1                                      # la retención está pendiente de definición: nada los borra solo


def test_drafts_only_exist_for_open_exceptions():
    key, _ = world()
    with pytest.raises(drafts.DraftError, match="ya no es una excepción"):
        save("f" * 16)                                                                                                          # contenido que no es una excepción del lote
    with pytest.raises(mf.ManifestNotFoundError):
        drafts.save("ch-20200101T000000Z-aaaa", key, FIELDS, actor=ACTOR, expected_revision=0)
    mf.put_ledger(RUN, key, {"source": "manual", "action": "MANUAL_CREATE", "profile_id": "p", "document_id": "d", "attempts": 0, "history": [], "state": "DONE"})
    assert ex.exception_row(RUN, key)["status"] == "resolved"
    with pytest.raises(drafts.DraftError, match="ya no es una excepción por resolver"):
        save(key)


def test_listing_and_counting_ignore_stray_files_and_attach_survives_an_unreadable_draft():
    k1, k2 = world()
    save(k1)
    directory = Path(config.BASE_DIR) / "human-capital" / "import-history" / RUN / "drafts"
    (directory / "README.txt").write_text("x", encoding="utf-8")
    (directory / "no-es-una-clave.json").write_text("{}", encoding="utf-8")
    assert drafts.count(RUN) == 1 and list(mf.list_drafts(RUN)) == [k1]
    (directory / f"{k2}.json").write_text("{ no es json", encoding="utf-8")
    rows = [{"content_key": k1}, {"content_key": k2}, None]
    drafts.attach(RUN, rows)
    assert rows[0]["draft"]["revision"] == 1 and rows[1]["draft"] is None and rows[2] is None


def test_nothing_personal_reaches_the_logs(caplog):
    key, _ = world()
    caplog.set_level(logging.DEBUG)
    save(key)
    save(key, {**FIELDS, "last_name": "Draftoni"}, rev=1)
    with pytest.raises(drafts.DraftConflict):
        save(key, FIELDS, rev=1)
    with pytest.raises(drafts.DraftError):
        save(key, {**FIELDS, "email": "malo"}, rev=2)
    text = caplog.text
    for secret in (*PRIVATE, "Draftoni", ACTOR):
        assert secret not in text, secret
    assert any(r.name == drafts.__name__ and "revision=1" in r.getMessage() for r in caplog.records)


def test_the_module_never_calls_import_actions_profile_writers_or_microsoft_graph():
    import re
    source = Path(drafts.__file__).read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", source, flags=re.S)
    for forbidden in ("complete_identity", "link_existing", "dismiss(", "retry(", "reopen(", "execute_batch", "profile_repository", "microsoft365", "graph_", "put_ledger", "put_group", "put_plan", "update_run", "append_audit", "delete_",
                      "unlink", "open(", ".write_text"):
        assert forbidden not in code, forbidden
