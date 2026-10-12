"""Persistencia explícita por evento (etapa 2): al completar «Generar catálogo» el catálogo legacy se confirma en Spaces SIN depender del ciclo de `mirror_up`.

Contrato: el evento sube solo lo que cambió (firma local ≠ última confirmada) bajo `catalog_local`/`reports`; el manifest (marcador de completitud) va AL FINAL y solo con todo
lo demás confirmado; una regeneración incompleta no se publica; un PUT fallido deja el marcador retenido y se completa en el próximo evento; el restore recupera el
catálogo sin haber corrido nunca el ciclo; los archivos manuales (`manual:`) se conservan. Generación REAL sobre un inbound mínimo + S3 simulado en memoria (sin red,
sin credenciales, sin tocar el repo ni Spaces)."""
from __future__ import annotations

import datetime
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from core import remote_store as rs
from webapp import access
from webapp.main import app
from webapp.services import admin_tools, auth_service

REAL_BASE_DIR = config.BASE_DIR             # el generador se importa desde el código real (conftest redirige BASE_DIR a un tmp en cada test)
MANIFEST = "catalog_local/.catalog_manifest.json"


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.uploads: list[str] = []
        self.fail_on: set[int] = set()                          # n-ésimos intentos de PUT (1-based) que fallan
        self.attempts = 0

    def upload_file(self, src, bucket, key):
        self.attempts += 1
        if self.attempts in self.fail_on:
            raise OSError("S3 caído (simulado)")
        self.objects[key] = Path(src).read_bytes()
        self.uploads.append(key)

    def get_paginator(self, _):
        outer = self

        class P:
            def paginate(self, Bucket, Prefix):
                yield {"Contents": [{"Key": k, "Size": len(v), "ETag": '"' + hashlib.md5(v).hexdigest() + '"', "LastModified": datetime.datetime.now()}
                                    for k, v in outer.objects.items() if k.startswith(Prefix)]}
        return P()

    def download_file(self, bucket, key, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(self.objects[key])


def _table(name: str, extra: str = "") -> dict:
    return {"definitions": {name: {
        "kind": "entity", "@EndUserText.label": name + extra,
        "@DataWarehouse.repository": {"technicalType": {"#": "DWC_LOCAL_TABLE"}},
        "elements": {"x": {"type": "cds.String", "length": 10}},
    }}}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


@pytest.fixture()
def world(monkeypatch, tmp_path):
    fake = FakeS3()
    inbound, out = tmp_path / "inbound", tmp_path / "app" / "catalog_local"
    _write(inbound / "ClienteA" / "TABLE_A.json", _table("TABLE_A"))
    _write(inbound / "ClienteB" / "TABLE_B.json", _table("TABLE_B"))
    monkeypatch.setenv("DANWOR_CATALOG_INBOUND_DIR", str(inbound))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(out))
    monkeypatch.setattr(config, "BASE_DIR", REAL_BASE_DIR, raising=False)               # el generador vive en el repo real
    monkeypatch.setattr(config, "PERSIST_PATHS", ["catalog_local"], raising=False)       # solo el catálogo de la prueba (nunca el `reports/` real)
    monkeypatch.setattr(config, "PERSIST_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "SPACES_BUCKET", "bucket", raising=False)
    monkeypatch.setattr(config, "SPACES_PREFIX", "", raising=False)
    monkeypatch.setattr(rs, "_client", fake, raising=False)
    monkeypatch.setattr(rs, "_pending_push", {}, raising=False)
    monkeypatch.setattr(rs, "_signatures", {}, raising=False)
    monkeypatch.setattr(rs, "_digests", {}, raising=False)
    monkeypatch.setattr(rs, "_held", {}, raising=False)
    monkeypatch.setattr(rs, "_withheld", {}, raising=False)
    monkeypatch.setattr(rs, "_unreconciled", set(), raising=False)
    monkeypatch.setattr(admin_tools, "_reconciliation_line", lambda _out: "[reconciliación] (omitida en test)")      # escribirían en el `reports/` real
    monkeypatch.setattr(admin_tools, "_portability_line", lambda _out: "[portabilidad BDC] (omitida en test)")

    fake.real_mirror = rs.mirror_up                                                         # el ciclo periódico REAL, para las pruebas de convivencia

    def forbidden(*_a, **_k):
        raise AssertionError("el ciclo periódico (mirror_up) NO debe intervenir")
    monkeypatch.setattr(rs, "mirror_up", forbidden)
    fake.inbound, fake.out = inbound, out
    return fake


def _local_keys(out: Path) -> set[str]:
    return {f"catalog_local/{p.relative_to(out).as_posix()}" for p in out.rglob("*") if p.is_file()}


def test_a_complete_regeneration_is_confirmed_in_spaces_without_the_periodic_cycle(world):
    text = admin_tools.run_catalog_generation()
    assert "[ERROR]" not in text and "[persistencia] Spaces:" in text and "confirmado(s)" in text
    local = _local_keys(world.out)
    assert local and set(world.objects) == local                                           # todo lo generado quedó en Spaces
    for key in local:
        assert world.objects[key] == (world.out.parent / key).read_bytes()                  # bytes exactos
    assert world.uploads[-1] == MANIFEST                                                    # el manifest va AL FINAL


def test_only_what_changed_is_uploaded_on_the_next_regeneration(world):
    admin_tools.run_catalog_generation()
    world.uploads.clear()
    text = admin_tools.run_catalog_generation()                                             # sin cambios en los crudos
    assert "sin archivos modificados por confirmar" in text and world.uploads == []
    _write(world.inbound / "ClienteB" / "TABLE_B.json", _table("TABLE_B", extra=" v2"))      # cambia SOLO el dominio B
    text = admin_tools.run_catalog_generation()
    assert "[persistencia] Spaces:" in text
    assert world.uploads and all("ClienteB" in k or k == MANIFEST for k in world.uploads) and world.uploads[-1] == MANIFEST


def test_files_only_touched_with_identical_content_are_not_uploaded_again(world):
    import os
    admin_tools.run_catalog_generation()
    world.uploads.clear()
    for path in world.out.rglob("*.md"):
        os.utime(path, ns=(1_000_000_000, 1_000_000_000))                                   # un generador que reescribe lo mismo cambia solo la firma
    assert rs.persist_changed(["catalog_local"])["uploaded"] == 0 and world.uploads == []
    victim = next(world.out.rglob("*.md"))
    victim.write_text(victim.read_text(encoding="utf-8") + " CAMBIO", encoding="utf-8")
    assert rs.persist_changed(["catalog_local"])["uploaded"] == 1 and world.uploads == [f"catalog_local/{victim.relative_to(world.out).as_posix()}"]


def test_a_failed_put_withholds_the_manifest_and_the_next_event_completes_the_publication(world):
    world.fail_on = {2}
    text = admin_tools.run_catalog_generation()
    assert "ATENCIÓN" in text and "manifest NO se publicó" in text
    assert MANIFEST not in world.objects                                                    # Spaces no refleja una regeneración completa
    assert rs._pending_push                                                                 # lo no confirmado queda para el próximo evento
    world.fail_on = set()
    text = admin_tools.run_catalog_generation()                                             # siguiente evento: completa lo pendiente
    assert "[persistencia] Spaces:" in text and set(world.objects) == _local_keys(world.out) and world.uploads[-1] == MANIFEST
    assert not rs._pending_push


def test_with_spaces_down_the_event_stops_at_the_first_failure_and_does_not_multiply_attempts(world):
    world.fail_on = set(range(1, 10_000))
    text = admin_tools.run_catalog_generation()
    assert "ATENCIÓN" in text and world.objects == {}
    assert world.attempts <= 8 + 1                                                          # a lo sumo los PUT en vuelo del pool + 0 reintentos; nunca uno por archivo


def test_an_incomplete_regeneration_is_not_published(world, monkeypatch):
    import importlib
    import generate_space_catalog as space_gen
    monkeypatch.setattr(importlib, "reload", lambda module: module)                         # sin recargar, para poder simular el fallo de un generador
    monkeypatch.setattr(space_gen, "generate_all", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    text = admin_tools.run_catalog_generation()
    assert "[WARN] Catálogo de espacios/conexiones no generado" in text and "NO publicado en Spaces" in text
    assert world.objects == {} and world.attempts == 0                                      # ni un PUT: un parcial no se confirma como completo
    assert _local_keys(world.out)                                                           # el resultado local sigue ahí


def test_a_top_level_generator_error_is_not_published(world):
    _write(world.inbound / "ClienteA" / "local-tables" / "TABLE_A.json", {"definitions": {"TABLE_A": {"kind": "entity", "elements": {"y": {"type": "cds.Integer"}}}}})
    text = admin_tools.run_catalog_generation()
    assert "[ERROR]" in text and "NO publicado en Spaces" in text and world.objects == {}


def test_the_catalog_is_restored_from_spaces_without_the_cycle_ever_running(world):
    admin_tools.run_catalog_generation()
    expected = {k: v for k, v in world.objects.items()}
    shutil.rmtree(world.out)                                                                # FS efímero vacío (redeploy)
    rs._signatures.clear()
    assert rs.restore() == len(expected)
    for key, data in expected.items():
        assert (world.out.parent / key).read_bytes() == data
    assert (world.out / ".catalog_manifest.json").is_file()


def test_manual_blocks_are_preserved_and_published(world):
    admin_tools.run_catalog_generation()
    flow = next(world.out.rglob("flujo_*.md"))
    text = flow.read_text(encoding="utf-8")
    flow.write_text(text.replace("manual:", "manual:\n  nota_senior: a mano", 1), encoding="utf-8")
    _write(world.inbound / "ClienteA" / "TABLE_A.json", _table("TABLE_A", extra=" v2"))      # fuerza la regeneración de ese flujo
    admin_tools.run_catalog_generation()
    assert "nota_senior: a mano" in flow.read_text(encoding="utf-8")
    key = f"catalog_local/{flow.relative_to(world.out).as_posix()}"
    assert b"nota_senior: a mano" in world.objects[key]


def test_restore_in_progress_or_incomplete_blocks_the_publication(world, monkeypatch):
    monkeypatch.setattr(rs, "_restore_degraded", True, raising=False)
    text = admin_tools.run_catalog_generation()
    assert "no se publicó en Spaces" in text and world.objects == {} and world.attempts == 0


def test_unreconciled_files_are_never_overwritten_and_withhold_the_manifest(world):
    admin_tools.run_catalog_generation()
    victim = next(k for k in world.objects if k.endswith(".md"))
    _write(world.inbound / "ClienteA" / "TABLE_A.json", _table("TABLE_A", extra=" v3"))
    _write(world.inbound / "ClienteB" / "TABLE_B.json", _table("TABLE_B", extra=" v3"))
    rs._unreconciled.add(victim)
    before, n = world.objects[victim], len(world.uploads)
    text = admin_tools.run_catalog_generation()
    assert "sin reconciliar" in text and world.objects[victim] == before
    assert MANIFEST not in world.uploads[n:] and "manifest NO se publicó" in text            # el marcador queda retenido: Spaces no refleja una regeneración completa


def test_only_the_requested_persisted_directories_are_swept(world, monkeypatch):
    monkeypatch.setattr(config, "PERSIST_PATHS", ["catalog_local", "case_library"], raising=False)
    other = world.out.parent / "case_library"
    monkeypatch.setattr(config, "BASE_DIR", world.out.parent, raising=False)               # case_library cuelga de BASE_DIR
    _write(other / "otro.json", {"a": 1})
    _write(world.out / "x.md", {"b": 2})
    result = rs.persist_changed(["catalog_local"])
    assert result["status"] == "remote" and "case_library/otro.json" not in world.objects and "catalog_local/x.md" in world.objects


def test_with_persistence_disabled_nothing_changes(world, monkeypatch):
    monkeypatch.setattr(config, "PERSIST_ENABLED", False, raising=False)
    text = admin_tools.run_catalog_generation()
    assert "[persistencia]" not in text and "[ERROR]" not in text and world.attempts == 0 and world.objects == {}


# ── rutas HTTP («Generar catálogo» y panel admin) ─────────────────────────────────────────────────
access.APP_OWNER = "test@danwor.com"
auth_service.ALLOWED_USERS = "ALL"


def _client(diagnostic: bool = False) -> TestClient:
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": "test@danwor.com", "name": "T", "first_name": "T", "family_name": "U"}))
    if diagnostic:
        c.cookies.set("dw-diagnostic", "on")
    return c


def test_the_self_service_button_route_reports_the_persistence_outcome(world):
    body = _client().post("/catalog/regenerate", data={"graph_token": ""}).json()
    assert body["ok"] is True and "[persistencia] Spaces:" in body["message"]
    assert set(world.objects) == _local_keys(world.out)


def test_the_admin_panel_route_persists_too(world):
    response = _client(diagnostic=True).post("/admin/catalog/generate", data={"graph_token": ""})
    assert response.status_code == 200 and "[persistencia] Spaces:" in response.text and MANIFEST in world.objects


# ═══ convivencia con el ciclo periódico (mirror_up): el ciclo REAL corre en medio / después de la regeneración ═══════════════════════════════════════
def _catalog_uploads(world):
    return [k for k in world.uploads if k.startswith("catalog_local/")]


def test_the_periodic_cycle_publishes_nothing_of_catalog_local_while_it_is_being_regenerated(world, monkeypatch):
    during = []
    monkeypatch.setattr(admin_tools, "_reconciliation_line", lambda _o: (during.append((world.real_mirror(), list(world.uploads))), "[reconciliación] (test)")[1])   # el ciclo corre con los generadores ya escritos
    text = admin_tools.run_catalog_generation()
    assert during == [(0, [])]                                                                              # ni un archivo subido por el ciclo en plena regeneración
    assert "[persistencia] Spaces:" in text and set(world.objects) == _local_keys(world.out) and world.uploads[-1] == MANIFEST     # y la regeneración exitosa persiste explícito, manifest al final
    assert rs._held == {} and rs._withheld == {}                                                            # sin retención residual


def test_a_failed_regeneration_is_never_published_by_the_periodic_cycle(world, monkeypatch):
    import importlib
    import generate_space_catalog as space_gen
    monkeypatch.setattr(importlib, "reload", lambda module: module)
    monkeypatch.setattr(space_gen, "generate_all", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    text = admin_tools.run_catalog_generation()
    assert "NO publicado en Spaces" in text and "retenido" in text and _local_keys(world.out)
    assert world.real_mirror() == 0 and world.objects == {}                                                 # el ciclo posterior no publica el resultado parcial
    assert world.real_mirror() == 0 and world.attempts == 0                                                 # ni en el ciclo siguiente
    (world.out / "otro_productor.md").write_text("de otro productor", encoding="utf-8")                      # otro archivo NUEVO (no es parte de lo retenido) sí sincroniza
    assert world.real_mirror() == 1 and list(world.objects) == ["catalog_local/otro_productor.md"]


def test_a_put_failure_never_lets_the_periodic_cycle_publish_an_incomplete_state(world):
    world.fail_on = {2}
    text = admin_tools.run_catalog_generation()
    assert "ATENCIÓN" in text and "NO lo completa por su cuenta" in text and MANIFEST not in world.objects
    world.fail_on = set()                                                                                   # Spaces se recupera
    before = dict(world.objects)
    assert world.real_mirror() == 0 and world.objects == before and MANIFEST not in world.objects          # el ciclo no completa la publicación ni sube el manifest
    text = admin_tools.run_catalog_generation()                                                             # la siguiente regeneración completa lo pendiente, manifest al final
    assert "[persistencia] Spaces:" in text and set(world.objects) == _local_keys(world.out) and world.uploads[-1] == MANIFEST
    assert rs._withheld == {} and rs._held == {}                                                            # y la retención se levanta sola


def test_the_protection_is_never_permanent_after_an_error_or_a_restart(world, monkeypatch):
    with pytest.raises(RuntimeError):
        with rs.hold_publication(["catalog_local"]):
            raise RuntimeError("falla del productor")
    assert rs._held == {}                                                                                   # la excepción no deja el directorio retenido en curso
    admin_tools.run_catalog_generation()                                                                    # regeneración sana (persiste todo)
    assert world.real_mirror() == 0 and rs._withheld == {}
    # regeneración fallida → retenido; «reinicio» = estado de proceso nuevo y vacío
    import importlib
    import generate_space_catalog as space_gen
    _write(world.inbound / "ClienteA" / "TABLE_A.json", _table("TABLE_A", extra=" v9"))
    monkeypatch.setattr(importlib, "reload", lambda module: module)
    monkeypatch.setattr(space_gen, "generate_all", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    admin_tools.run_catalog_generation()
    assert rs._withheld and world.real_mirror() == 0
    monkeypatch.setattr(rs, "_held", {}, raising=False)
    monkeypatch.setattr(rs, "_withheld", {}, raising=False)
    monkeypatch.setattr(rs, "_signatures", {}, raising=False)
    assert world.real_mirror() > 0                                                                          # tras un reinicio el ciclo vuelve a sincronizar con normalidad


def test_a_withheld_file_is_released_as_soon_as_another_producer_changes_it(world, monkeypatch):
    import importlib
    import generate_space_catalog as space_gen
    monkeypatch.setattr(importlib, "reload", lambda module: module)
    monkeypatch.setattr(space_gen, "generate_all", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")))
    admin_tools.run_catalog_generation()
    victim = next(world.out.rglob("flujo_*.md"))
    victim.write_text(victim.read_text(encoding="utf-8") + " editado por un humano", encoding="utf-8")        # cambio posterior, ajeno a la regeneración fallida
    assert world.real_mirror() == 1                                                                         # esa edición sí se publica (la retención era sobre el contenido parcial)


# ═══ restore: las firmas/ETag existentes evitan re-subir lo idéntico en la primera regeneración tras un deploy ═════════════════════════════════
def test_after_a_deploy_the_first_regeneration_does_not_re_upload_what_is_already_identical_in_spaces(world):
    admin_tools.run_catalog_generation()
    n_local = len(_local_keys(world.out))
    shutil.rmtree(world.out)                                                                                # redeploy: FS efímero vacío y estado de proceso nuevo
    for name in ("_signatures", "_digests", "_pending_push"):
        getattr(rs, name).clear()
    assert rs.restore() == n_local
    assert len(rs._digests) == n_local                                                                      # sembrados con el ETag (md5 simple) que el restore ya recibe
    world.uploads.clear()
    text = admin_tools.run_catalog_generation()                                                             # los generadores reescriben archivos idénticos (mtime nuevo)
    assert world.uploads == [] and "sin archivos modificados por confirmar" in text                         # 0 PUT redundantes
    _write(world.inbound / "ClienteB" / "TABLE_B.json", _table("TABLE_B", extra=" v2"))
    admin_tools.run_catalog_generation()
    assert world.uploads and all("ClienteB" in k or k == MANIFEST for k in world.uploads)                   # solo lo que realmente cambió


def test_a_non_md5_or_multipart_etag_seeds_nothing_and_stays_safe(world):
    rs._seed_digest("catalog_local/x.md", '"abc-3"')
    rs._seed_digest("catalog_local/y.md", None)
    rs._seed_digest("catalog_local/z.md", '"' + "0" * 31 + '"')
    assert rs._digests == {}                                                                                # sin md5 simple no se afirma nada: se sube como siempre


# ═══ otros productores y directorios no cambian ═══════════════════════════════════════════════════════════════════════════════════════════════
@pytest.fixture()
def store(monkeypatch, tmp_path):
    fake = FakeS3()
    base = tmp_path / "app"
    base.mkdir()
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(base / "catalog_local"))
    monkeypatch.setattr(config, "BASE_DIR", base, raising=False)
    monkeypatch.setattr(config, "PERSIST_PATHS", ["catalog_local", "case_library", "feedback_log", "reports"], raising=False)
    monkeypatch.setattr(config, "PERSIST_ENABLED", True, raising=False)
    monkeypatch.setattr(config, "SPACES_BUCKET", "bucket", raising=False)
    monkeypatch.setattr(config, "SPACES_PREFIX", "", raising=False)
    for name, value in (("_client", fake), ("_pending_push", {}), ("_signatures", {}), ("_digests", {}), ("_held", {}), ("_withheld", {}), ("_unreconciled", set())):
        monkeypatch.setattr(rs, name, value, raising=False)
    fake.base = base
    return fake


def test_other_directories_and_producers_keep_working_while_catalog_local_is_held(store):
    (store.base / "catalog_local").mkdir()
    (store.base / "catalog_local" / "a.md").write_text("catálogo", encoding="utf-8")
    (store.base / "reports").mkdir()
    (store.base / "reports" / "r.txt").write_text("reporte", encoding="utf-8")
    with rs.hold_publication(["catalog_local"]):
        assert rs.write_through(store.base / "case_library" / "c1.yaml", "a: 1\n") == rs.DURABLE_REMOTE      # Case Library: write-through normal
        assert rs.write_through(store.base / "feedback_log" / "f1.yaml", "b: 2\n") == rs.DURABLE_REMOTE      # Feedback: idem
        assert rs.mirror_up() == 1 and "reports/r.txt" in store.objects                                     # `reports` y demás directorios siguen en el ciclo
        assert "catalog_local/a.md" not in store.objects                                                    # y catalog_local, retenido en plena regeneración
    assert rs.mirror_up() == 1 and "catalog_local/a.md" in store.objects                                   # fin del hold sano: el ciclo vuelve a incluirlo


def test_a_failed_hold_withholds_only_catalog_local_and_a_complete_persist_releases_it(store):
    (store.base / "catalog_local").mkdir()
    (store.base / "catalog_local" / "a.md").write_text("parcial", encoding="utf-8")
    (store.base / "reports").mkdir()
    (store.base / "reports" / "r.txt").write_text("reporte", encoding="utf-8")
    with rs.hold_publication(["catalog_local"]) as hold:
        hold.fail()
    assert rs.mirror_up() == 1 and list(store.objects) == ["reports/r.txt"]                                # solo `reports` sincroniza; el parcial de catalog_local no
    assert rs.persist_changed(["catalog_local"])["status"] == "remote" and "catalog_local/a.md" in store.objects
    assert rs._withheld == {}
