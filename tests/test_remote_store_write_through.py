"""Persistencia explícita por evento (etapa 1): `remote_store.write_through` + productores `case_library` y `feedback_log`.

Contrato: cada evento de escritura deja el objeto en Spaces AL TERMINAR, sin `mirror_up` ni flush; un PUT fallido no rompe al productor, queda pendiente y se
reintenta en el siguiente evento; el restore recupera lo escrito; con la persistencia deshabilitada el comportamiento es el de siempre. Remoto = cliente S3 simulado
en memoria (sin red, sin credenciales, sin tocar archivos reales)."""
from __future__ import annotations

import datetime
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi.testclient import TestClient

import config
from core import remote_store as rs

REPO = Path(__file__).resolve().parents[1]


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.uploads: list[str] = []
        self.fail_uploads = 0                                   # n-ésimos PUT que fallan
        self.attempts = 0

    def upload_file(self, src, bucket, key):
        self.attempts += 1
        if self.fail_uploads > 0:
            self.fail_uploads -= 1
            raise OSError("S3 caído (simulado)")
        self.objects[key] = Path(src).read_bytes()
        self.uploads.append(key)

    def get_paginator(self, _):
        outer = self

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k, "Size": len(v), "ETag": '"x"', "LastModified": datetime.datetime.now()} for k, v in outer.objects.items() if k.startswith(Prefix)]}
        return P()

    def download_file(self, bucket, key, dest):
        Path(dest).write_bytes(self.objects[key])


@pytest.fixture()
def s3(monkeypatch, tmp_path):
    fake = FakeS3()
    base = tmp_path / "app"
    base.mkdir()
    monkeypatch.setattr(config, "BASE_DIR", base, raising=False)
    monkeypatch.setattr(config, "PERSIST_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "SPACES_BUCKET", "bucket", raising=False)
    monkeypatch.setattr(rs, "_client", fake, raising=False)
    monkeypatch.setattr(rs, "_pending_push", {}, raising=False)
    monkeypatch.setattr(rs, "_signatures", {}, raising=False)

    def forbidden(*_a, **_k):
        raise AssertionError("el ciclo periódico (mirror_up) NO debe intervenir")
    monkeypatch.setattr(rs, "mirror_up", forbidden)               # el ciclo está «deshabilitado»: si algo lo invoca, el test falla
    fake.base = base
    return fake


# ── helper ─────────────────────────────────────────────────────────────────────────────
def test_write_through_persists_atomically_at_the_end_of_the_event_without_the_periodic_cycle(s3):
    path = s3.base / "feedback_log" / "x.yaml"
    assert rs.write_through(path, "a: 1\n") == rs.DURABLE_REMOTE
    assert s3.objects["feedback_log/x.yaml"].strip() == b"a: 1" and path.read_text() == "a: 1\n"           # (en Windows el modo texto traduce el salto de línea, igual que el write_text previo)
    assert [p.name for p in path.parent.iterdir()] == ["x.yaml"]                       # sin temporales residuales


def test_bytes_and_text_are_both_supported(s3):
    rs.write_through(s3.base / "case_library" / "b.bin", b"\x00\x01")
    assert s3.objects["case_library/b.bin"] == b"\x00\x01"


def test_with_persistence_disabled_it_is_a_plain_atomic_local_write(s3, monkeypatch):
    monkeypatch.setattr(config, "PERSIST_ENABLED", False, raising=False)
    path = s3.base / "feedback_log" / "x.yaml"
    assert rs.write_through(path, "a: 1\n") == rs.DURABLE_LOCAL
    assert path.read_text() == "a: 1\n" and s3.uploads == [] and rs._pending_push == {}


def test_a_path_outside_the_persisted_dirs_is_local_and_never_pending(s3, tmp_path):
    other = tmp_path / "elsewhere" / "x.yaml"
    assert rs.write_through(other, "a: 1\n") == rs.DURABLE_LOCAL
    assert s3.uploads == [] and rs._pending_push == {}


def test_a_failed_put_does_not_break_the_producer_stays_pending_and_is_retried_on_the_next_event(s3):
    a, b = s3.base / "feedback_log" / "a.yaml", s3.base / "feedback_log" / "b.yaml"
    s3.fail_uploads = 1
    assert rs.write_through(a, "n: a\n") == rs.DURABLE_LOCAL                              # PUT falló: archivo local válido, resultado honesto
    assert a.read_text() == "n: a\n" and "feedback_log/a.yaml" not in s3.objects and len(rs._pending_push) == 1
    assert rs.write_through(b, "n: b\n") == rs.DURABLE_REMOTE                             # el siguiente evento reintenta el pendiente y sube el nuevo
    assert s3.objects["feedback_log/a.yaml"].strip() == b"n: a" and s3.objects["feedback_log/b.yaml"].strip() == b"n: b" and rs._pending_push == {}


def test_the_retry_covers_every_pending_file_not_only_the_same_one(s3):
    paths = [s3.base / "feedback_log" / f"p{i}.yaml" for i in range(3)]
    s3.fail_uploads = 5                                                                    # ev1: 1 intento; ev2 y ev3: reintento + PUT propio (el reintento se corta en el primer fallo)
    assert [rs.write_through(p, f"i: {i}\n") for i, p in enumerate(paths)] == [rs.DURABLE_LOCAL] * 3 and len(rs._pending_push) == 3
    other = s3.base / "case_library" / "otro.yaml"
    assert rs.write_through(other, "x: 1\n") == rs.DURABLE_REMOTE                         # un evento de OTRO archivo recupera los tres pendientes
    assert sorted(s3.objects) == sorted(["feedback_log/p0.yaml", "feedback_log/p1.yaml", "feedback_log/p2.yaml", "case_library/otro.yaml"]) and rs._pending_push == {}


def test_with_spaces_down_each_event_costs_at_most_two_put_attempts_however_many_are_pending(s3):
    s3.fail_uploads = 10**6
    per_event = []
    for i in range(8):
        before = s3.attempts
        assert rs.write_through(s3.base / "feedback_log" / f"e{i}.yaml", "k: v\n") == rs.DURABLE_LOCAL
        per_event.append(s3.attempts - before)
    assert max(per_event) <= 2 and len(rs._pending_push) == 8                              # sin amplificación de latencia


def test_a_failed_put_leaves_the_file_unsigned_so_the_existing_mirror_up_backstop_still_covers_it(s3):
    path = s3.base / "feedback_log" / "a.yaml"
    s3.fail_uploads = 1
    assert rs.write_through(path, "n: a\n") == rs.DURABLE_LOCAL
    assert "feedback_log/a.yaml" not in rs._signatures                                      # mirror_up (ciclo/flush) lo subirá mientras exista
    assert rs.write_through(s3.base / "feedback_log" / "b.yaml", "n: b\n") == rs.DURABLE_REMOTE
    assert "feedback_log/b.yaml" in rs._signatures and "feedback_log/a.yaml" in rs._signatures


def test_the_pending_list_is_bounded(s3, monkeypatch):
    monkeypatch.setattr(rs, "_PENDING_MAX", 3)
    s3.fail_uploads = 10**6
    for i in range(6):
        rs.write_through(s3.base / "feedback_log" / f"e{i}.yaml", "k: v\n")
    assert len(rs._pending_push) == 3 and all((s3.base / "feedback_log" / f"e{i}.yaml").is_file() for i in range(6))   # los archivos siguen en disco


def test_local_io_errors_propagate_to_the_caller(s3):
    blocker = s3.base / "feedback_log"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("soy un archivo, no un directorio")
    with pytest.raises(OSError):
        rs.write_through(blocker / "x.yaml", "a: 1\n")
    assert s3.uploads == []


def test_what_was_written_is_recoverable_by_restore_after_losing_the_local_disk(s3):
    path = s3.base / "case_library" / "case_1.yaml"
    rs.write_through(path, "case_id: case_1\n")
    path.unlink()                                                                       # contenedor efímero reiniciado
    assert rs.restore() >= 1 and path.read_text() == "case_id: case_1\n"


# ── case_library ──────────────────────────────────────────────────────────────────────
def _run():
    return SimpleNamespace(id="abcdef1234567890", leaf={"decision": "ok", "post_actions": {"mode": "resolved"}, "notes": []}, case_id=None, current="leaf1", started_at="2026-10-12T00:00:00+00:00",
                           tree={"id": "t1", "title": "T", "nodes": {"n1": {"question": "¿q?", "options": ["a", "b"]}}, "links": {}}, history=[("n1", "a")])


def test_case_library_persists_each_case_event_when_it_completes(s3):
    from webapp.services.case_library import CaseLibraryRepository
    repo = CaseLibraryRepository(root=s3.base / "case_library")
    run = _run()
    case_id = repo.create_for_run(run)
    key = f"case_library/{case_id}.yaml"
    assert yaml.safe_load(s3.objects[key])["case_id"] == case_id                           # creado => ya en Spaces
    repo.update_feedback(run, rating="up", outcome="anduvo", lesson="L")
    remote = yaml.safe_load(s3.objects[key])
    assert remote["rating"] == "up" and remote["reviewed"] is True and remote["lessons"] == ["L"]   # la actualización también
    assert s3.uploads.count(key) == 2


def test_case_library_keeps_working_when_the_put_fails_and_recovers_on_the_next_case(s3):
    from webapp.services.case_library import CaseLibraryRepository
    repo = CaseLibraryRepository(root=s3.base / "case_library")
    s3.fail_uploads = 1
    first = repo.create_for_run(_run())
    assert (s3.base / "case_library" / f"{first}.yaml").is_file() and f"case_library/{first}.yaml" not in s3.objects
    run2 = _run()
    run2.id = "zzzzzz9999999999"
    second = repo.create_for_run(run2)
    assert f"case_library/{first}.yaml" in s3.objects and f"case_library/{second}.yaml" in s3.objects


# ── feedback_log ──────────────────────────────────────────────────────────────────────
def test_shared_feedback_store_persists_each_record_when_it_completes(s3, monkeypatch):
    from webapp.services import feedback_store
    monkeypatch.setattr(feedback_store, "FEEDBACK_LOG_DIR", s3.base / "feedback_log")
    feedback_store.write_feedback_record({"type": "reporting", "rating": "up"}, prefix="reporting_feedback", stamp="20261012_100000")
    (key,) = s3.objects
    assert re.fullmatch(r"feedback_log/reporting_feedback_20261012_100000_[0-9a-f]{8}\.yaml", key) and yaml.safe_load(s3.objects[key])["rating"] == "up"


def test_feedback_store_stays_best_effort_when_the_local_write_fails(s3, monkeypatch):
    from webapp.services import feedback_store
    monkeypatch.setattr(feedback_store, "FEEDBACK_LOG_DIR", s3.base / "feedback_log")
    monkeypatch.setattr(rs, "write_through", lambda *a, **k: (_ for _ in ()).throw(OSError("disco lleno")))
    feedback_store.write_feedback_record({"type": "x"}, prefix="p", stamp="s")             # no levanta: el feedback nunca rompe la experiencia
    assert s3.objects == {}


def test_assistant_feedback_repository_persists_when_it_completes(s3):
    from webapp.services.assistant_service import AssistantFeedbackRepository
    reply = SimpleNamespace(id="r1", module="m", topic="t", action="a", question="¿?", conversation_id="c1")
    path = AssistantFeedbackRepository(root=s3.base / "feedback_log").save(reply, "up")
    assert yaml.safe_load(s3.objects[f"feedback_log/{path.name}"])["type"] == "assistant_response"


def test_catalog_feedback_endpoint_persists_the_event_with_the_cycle_disabled(s3, monkeypatch):
    """Ruta real de la app (TestClient, sin lifespan ⇒ sin sync_loop): el POST deja el YAML en Spaces antes de responder."""
    from webapp.main import app
    from webapp.routers import feedback as feedback_router
    from webapp.services import auth_service
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", s3.base / "feedback_log")
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "qa@danwor.com", "name": "QA", "first_name": "QA", "family_name": "U"}))
    r = c.post("/catalog/feedback", data={"rating": "up", "catalog_kind": "flow", "catalog_ref": "x", "pack": "datasphere_core"})
    assert r.status_code == 200
    keys = [k for k in s3.objects if k.startswith("feedback_log/catalog_feedback_")]
    assert len(keys) == 1 and yaml.safe_load(s3.objects[keys[0]])["rating"] == "up"


# ── candado: ningún escritor de estas rutas vuelve al patrón «tmp + replace sin push» ───────────────
WRITERS = {"webapp/routers/academy.py": 1, "webapp/routers/commercial_strategy.py": 1, "webapp/routers/feedback.py": 4, "webapp/routers/wizard.py": 1,
           "webapp/services/assistant_service.py": 1, "webapp/services/feedback_store.py": 1, "webapp/services/case_library.py": 1}


@pytest.mark.parametrize("rel,n", sorted(WRITERS.items()))
def test_feedback_and_case_writers_use_write_through(rel, n):
    src = (REPO / rel).read_text(encoding="utf-8")
    assert src.count("remote_store.write_through(") == n and '.with_suffix(".yaml.tmp")' not in src


def test_no_other_module_writes_into_feedback_log_or_case_library_without_the_helper():
    allow = set(WRITERS) | {"webapp/services/admin_tools.py", "webapp/services/activity_analytics.py"}      # purga (delete_path) y lectura
    offenders = []
    for p in list((REPO / "webapp").rglob("*.py")):
        rel = p.relative_to(REPO).as_posix()
        text = p.read_text(encoding="utf-8")
        if rel in allow or not re.search(r"FEEDBACK_LOG_DIR|CASE_LIBRARY_DIR|feedback_log|case_library", text):
            continue
        if re.search(r"(FEEDBACK_LOG_DIR|CASE_LIBRARY_DIR)[^\n]*\n?[^\n]*\.(write_text|open\()", text) and "write_through(" not in text:
            offenders.append(rel)
    assert offenders == []
