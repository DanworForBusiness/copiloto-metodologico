"""Herramienta de purga controlada de datos de PRUEBA de Capital Humano (`tools/human_capital/purge_test_data.py`), probada SOLO con datos sintéticos y un Spaces simulado en memoria:
dry-run por defecto, inventario local/Spaces y huérfanos, respaldo sellado (incluye solo-remotos y versiones remotas distintas), ejecución desde el manifiesto sellado (reanudable; deriva y concurrencia bloquean),
borrado por objeto verificado, restauración protegida sin sobrescritura, verificación posterior a la sincronización, preservación de catálogos/import-history/desconocidos y ausencia de datos personales en la salida."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from hashlib import md5
from pathlib import Path

import pytest

import config
from core import remote_store
from tools.human_capital import purge_test_data as purge
from webapp.services import durable_backup as dbk

T0 = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
OLD = T0 - timedelta(days=2)
OLD_ISO = OLD.strftime("%Y-%m-%dT%H:%M:%SZ")
PII = ["Zoraida", "Quintanilla", "zoraida.q", "Evaristo", "Bermúdez", "evaristo.b", "+54 9 11 5555-0000"]


class Clock:
    def __init__(self, start):
        self.t = start

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.t += timedelta(seconds=seconds)


class FakeSpaces:
    """Spaces en memoria: directorio persistido `human-capital` + namespace de respaldos. Registra los borrados y permite inyectar fallos."""
    BACKUP_NAMESPACE = remote_store.BACKUP_NAMESPACE
    RemoteReadError = remote_store.RemoteReadError
    RemoteBackupExists = remote_store.RemoteBackupExists

    def __init__(self, base: Path):
        self.base = Path(base) / "human-capital"
        self.objects: dict[str, bytes] = {}
        self.lm: dict[str, str] = {}
        self.backup: dict[str, bytes] = {}
        self.delete_log: list[str] = []
        self.fail_delete: set[str] = set()
        self.ghost_after_delete: set[str] = set()
        self.fail_push: set[str] = set()
        self.fail_list = False
        self.on_delete = None
        self.collision = None

    def _rel(self, path):
        return Path(path).resolve().relative_to(self.base.resolve()).as_posix()

    read_enabled = write_enabled = staticmethod(lambda: True)

    def backup_namespace_collision(self):
        return self.collision

    def list_persisted_objects(self, name):
        if self.fail_list:
            raise remote_store.RemoteReadError("listado fallo")
        return {rel: {"size": len(b), "etag": md5(b).hexdigest(), "last_modified": self.lm.get(rel, OLD_ISO)} for rel, b in sorted(self.objects.items())}

    def read_object_bytes(self, path):
        return self.objects.get(self._rel(path))

    def delete_path(self, path):
        rel = self._rel(path)
        self.delete_log.append(rel)
        if self.on_delete:
            self.on_delete(rel)
        if rel in self.fail_delete:
            return False
        if rel not in self.ghost_after_delete:
            self.objects.pop(rel, None)
        return True

    def persisted_object_exists(self, path):
        return self._rel(path) in self.objects

    def push_path(self, path):
        rel = self._rel(path)
        if rel in self.fail_push:
            return False
        self.objects[rel] = Path(path).read_bytes()
        self.lm[rel] = OLD_ISO
        return True

    def put_backup_object(self, rel, data):
        if rel in self.backup:
            raise remote_store.RemoteBackupExists(rel)
        self.backup[rel] = bytes(data)

    def get_backup_object(self, rel):
        return self.backup.get(rel)

    def list_backup_keys(self, prefix):
        return sorted(k for k in self.backup if k.startswith(prefix.rstrip("/") + "/"))


class World:
    def __init__(self, tmp, monkeypatch):
        self.home = tmp / "app"
        self.home.mkdir()
        monkeypatch.setattr(config, "BASE_DIR", self.home)
        monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")
        monkeypatch.setattr(config, "SPACES_BUCKET", "bkt-test")
        monkeypatch.setattr(config, "SPACES_PREFIX", "prod")
        monkeypatch.setattr(config, "SPACES_ENDPOINT", "https://atl1.example-spaces.test")
        monkeypatch.setattr(config, "PERSIST_PATHS", ["catalog_local", "human-capital", "activity_log"])
        self.spaces = FakeSpaces(self.home)
        self.clock = Clock(T0)
        monkeypatch.setattr(dbk, "remote_store", self.spaces)
        monkeypatch.setattr(dbk, "new_run_id", lambda digest, now=None: f"{self.clock.now().strftime('%Y%m%dT%H%M%SZ')}-{digest[:12]}")
        self.deps = purge._load_deps()
        self.deps.update(remote_store=self.spaces, now=self.clock.now, sleep=self.clock.sleep)
        self.root = self.home / "human-capital"

    def put(self, rel, data, *, local=True, remote=True, remote_data=None, old=True):
        data = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode("utf-8")
        if local:
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            if old:
                ts = OLD.timestamp()
                os.utime(path, (ts, ts))
        if remote:
            self.spaces.objects[rel] = remote_data if remote_data is not None else data
            self.spaces.lm[rel] = OLD_ISO if old else T0.strftime("%Y-%m-%dT%H:%M:%SZ")

    def local_files(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in sorted(self.root.rglob("*")) if p.is_file()} if self.root.is_dir() else {}

    # ── llamadas ──
    def dry(self):
        return purge.dry_run(self.deps)

    def backup(self, confirm=None):
        rep, code = self.dry()
        return purge.do_backup(self.deps, confirm if confirm is not None else rep["confirmations"]["backup"])

    def execute(self, run_id, *, confirm=None, ack=None, window=None, digest12=None):
        env = purge.environment(self.deps)
        d12 = digest12 or run_id.rsplit("-", 1)[-1]
        ph = purge.phrases(env["env8"], d12)
        until = window if window is not None else (self.clock.now() + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
        ns = argparse.Namespace(confirm=ph["purge"] if confirm is None else confirm, owner_ack=ph["owner_ack"] if ack is None else ack, backup_run=run_id, window_until=until)
        return purge.do_execute(self.deps, ns)

    def verify(self, run_id=None):
        return purge.do_verify(self.deps, run_id)

    def restore(self, run_id, *, confirm=None, digest12=None):
        env = purge.environment(self.deps)
        ph = purge.phrases(env["env8"], digest12 or run_id.rsplit("-", 1)[-1])
        ns = argparse.Namespace(confirm=ph["restore"] if confirm is True else confirm, backup_run=run_id)
        return purge.do_restore(self.deps, ns)


@pytest.fixture()
def w(tmp_path, monkeypatch):
    return World(tmp_path, monkeypatch)


def seed(w: World):
    """Datos SINTÉTICOS con todas las clases de caso: válidos, huérfanos, solo-local, solo-remoto, versión remota distinta, temporales, preservados y desconocidos."""
    prof = lambda pid, **k: {"profile_id": pid, "first_name": "Zoraida", "last_name": "Quintanilla", "email": "zoraida.q@example.test", "phone": "+54 9 11 5555-0000", **k}      # noqa: E731
    w.put("profiles/p1.json", prof("p1"))
    w.put("profiles/p2.json", prof("p2", first_name="Evaristo", last_name="Bermúdez", email="evaristo.b@example.test", deleted_at="2026-01-01T00:00:00Z"))
    w.put("profiles/p3.json", prof("p3"), remote=False)                                                                                  # solo local
    w.put("profiles/p4.json", prof("p4"), local=False)                                                                                   # SOLO remoto
    w.put("profiles/p5.json", prof("p5"), remote_data=json.dumps(prof("p5", notes="versión remota distinta")).encode())                    # difiere local/remoto
    w.put("busquedas/b1.json", {"busqueda_id": "b1", "solicitud": {"estado": "en_gestion"}, "candidatos": [{"application_id": "a1", "profile_id": "p1"}, {"application_id": "a2", "profile_id": "p2"}]})
    w.put("busquedas/b2.json", {"busqueda_id": "b2", "candidatos": []})
    w.put("entrevistas/e1.json", {"profile_id": "p1", "busqueda_id": "b1", "application_id": "a1"})
    w.put("entrevistas/e2.json", {"profile_id": "p1", "busqueda_id": "bX", "application_id": "aX"})                                        # huérfana: búsqueda inexistente
    w.put("onboarding/a1.json", {"application_id": "a1", "profile_id": "p1", "busqueda_id": "b1"})
    w.put("onboarding/a9.json", {"application_id": "a9", "profile_id": "p1", "busqueda_id": "b1"})                                        # huérfano: postulación inexistente
    w.put("scoring-grids/b1.json", {"x": 1})
    w.put("scoring-grids/bZ.json", {"x": 1})                                                                                             # huérfana
    w.put("scoring-evaluations/b1/ev1.json", {"application_id": "a1"})
    w.put("scoring-evaluations/bQ/ev2.json", {"application_id": "aQ"})                                                                   # huérfana
    w.put("profile-documents/d1.json", {"profile_id": "p1", "item_id": "sp-1"})
    w.put("profile-documents/d2.json", {"profile_id": "pNONE"})                                                                          # huérfano
    w.put("publicaciones/pub1.json", {"busqueda_id": "b1"})
    w.put("publicaciones/pub2.json", {"busqueda_id": "bMISSING"})
    w.put("profiles/p1.json.77.88.tmp", b"{parcial", remote=False)                                                                       # temporal de escritura
    # preservados y desconocidos
    w.put("catalogs/catalogos.json", {"area": ["A"]})
    w.put("import-history/ch-20261023T000000Z-ab12/run.json", {"kind": "pilot"})
    w.put("import-history/ch-20261023T000000Z-ab12/staging/aaaaaaaaaaaaaaaa.json", {"pii": "Zoraida Quintanilla"})
    w.put("misc/leeme.txt", b"desconocido local", remote=False)
    w.put("raro/objeto.bin", b"desconocido remoto", local=False)


PURGE_RELS = {"profiles/p1.json", "profiles/p2.json", "profiles/p3.json", "profiles/p4.json", "profiles/p5.json", "busquedas/b1.json", "busquedas/b2.json", "entrevistas/e1.json", "entrevistas/e2.json",
              "onboarding/a1.json", "onboarding/a9.json", "scoring-grids/b1.json", "scoring-grids/bZ.json", "scoring-evaluations/b1/ev1.json", "scoring-evaluations/bQ/ev2.json",
              "profile-documents/d1.json", "profile-documents/d2.json", "publicaciones/pub1.json", "publicaciones/pub2.json", "profiles/p1.json.77.88.tmp"}
PRESERVED_RELS = {"catalogs/catalogos.json", "import-history/ch-20261023T000000Z-ab12/run.json", "import-history/ch-20261023T000000Z-ab12/staging/aaaaaaaaaaaaaaaa.json", "misc/leeme.txt"}


def snapshot(w: World):
    return dict(w.local_files()), dict(w.spaces.objects)


def no_pii(report) -> list[str]:
    blob = json.dumps(report, ensure_ascii=False)
    return [p for p in PII if p in blob]


def full_cycle(w: World):
    """dry-run → backup → (devuelve run_id)."""
    rep, code = w.backup()
    assert code == 0, rep
    return rep["run_id"]


# ═══════════════════ clasificación (lista blanca) ═══════════════════
@pytest.mark.parametrize("rel, expected", [
    ("profiles/abc-123_X.json", ("purge", "profiles")), ("busquedas/b.json", ("purge", "busquedas")), ("scoring-evaluations/b1/e1.json", ("purge", "scoring-evaluations")),
    ("profiles/x.json.1.2.tmp", ("temp", "profiles")), ("scoring-evaluations/b1/.tmp-abc", ("temp", "scoring-evaluations")),
    ("catalogs/catalogos.json", ("preserved", None)), ("import-history/ch-1/run.json", ("preserved", None)), ("catalogs/x.tmp", ("preserved", None)),
    ("profiles/../catalogs/x.json", ("unknown", None)), ("profiles/sub/x.json", ("unknown", None)), ("profiles/x.JSON", ("unknown", None)), ("profiles/x.txt", ("unknown", None)),
    ("scoring-evaluations/x.json", ("unknown", None)), ("scoring-evaluations/b/c/d.json", ("unknown", None)), ("otra/cosa.json", ("unknown", None)), ("profiles/" + "a" * 65 + ".json", ("unknown", None)),
    ("profiles.json", ("unknown", None)), ("Profiles/x.json", ("unknown", None)),
])
def test_only_the_whitelist_is_ever_in_scope(rel, expected):
    assert purge.classify(rel) == expected


# ═══════════════════ dry-run ═══════════════════
def test_dry_run_is_the_default_writes_nothing_and_reports_inventory_inconsistencies_orphans_and_unknowns(w, capsys):
    seed(w)
    before = snapshot(w)
    code = purge.main([], w.deps)
    rep = json.loads(capsys.readouterr().out)
    assert code == 0 and rep["mode"] == "dry_run" and rep["writes_performed"] == 0 and snapshot(w) == before and w.spaces.backup == {} and w.spaces.delete_log == []
    c = rep["counts"]
    assert c["profiles"] == {"total": 5, "both": 3, "local_only": 1, "remote_only": 1, "differ": 1, "bytes": c["profiles"]["bytes"]} and c["busquedas"]["total"] == 2 and c["scoring-evaluations"]["total"] == 2
    assert c["temporales"] == {"total": 1, "local": 1, "remote": 0}
    assert rep["inconsistencies"] == {"local_only": 2, "remote_only": 1, "differ": 1}
    assert rep["orphans"] == {"entrevistas": 1, "onboarding": 1, "scoring_evaluations": 1, "scoring_grids": 1, "profile_documents": 1, "publicaciones_with_missing_busqueda": 1,
                              "applications_with_missing_or_deleted_profile": 1, "profiles_soft_deleted": 1}
    assert rep["unknown_files"]["local"] == 1 and rep["unknown_files"]["remote"] == 1 and set(rep["unknown_files"]["top_level"]) == {"misc", "raro"}
    assert rep["preserved"]["catalogs_local"] == 1 and rep["preserved"]["import_history_local"] == 2
    assert rep["retention"] == {"backup_recommended_days": 14, "automatic_backup_deletion": False} and "no puede impedir" in rep["isolation_note"]
    assert rep["scope"]["graph_operations"] == 0 and rep["scope"]["sharepoint_deletes"] == 0
    assert not no_pii(rep)


def test_confirmations_are_bound_to_the_environment_and_the_inventory(w):
    seed(w)
    rep, _ = w.dry()
    env8, d12 = rep["environment"]["env8"], rep["digest12"]
    assert rep["confirmations"] == {"backup": f"BACKUP-CCHH-{env8}-{d12}", "purge": f"PURGE-CCHH-{env8}-{d12}", "owner_ack": f"NO-REAL-DATA-{d12}", "restore": f"RESTORE-CCHH-{env8}-{d12}"}
    w.put("profiles/p3.json", {"profile_id": "p3", "cambio": True}, remote=False)
    assert w.dry()[0]["digest12"] != d12                                                                           # cualquier cambio de contenido cambia el digest
    config.SPACES_PREFIX = "otro-ambiente"
    assert w.dry()[0]["environment"]["env8"] != env8                                                                # y otro bucket/prefijo cambia la huella del entorno


def test_dry_run_blocks_on_unsafe_environments_and_listing_failures(w, monkeypatch):
    seed(w)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    assert w.dry()[1] == 2 and "MODE_NOT_REMOTE" in w.dry()[0]["blockers"]
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")
    monkeypatch.setattr(config, "PERSIST_PATHS", ["catalog_local"])
    assert "HC_NOT_IN_PERSIST_PATHS" in w.dry()[0]["blockers"]
    monkeypatch.setattr(config, "PERSIST_PATHS", ["human-capital"])
    w.spaces.collision = "colisión"
    assert "NAMESPACE_COLLISION" in w.dry()[0]["blockers"] and w.dry()[0]["environment"]["bucket"] == "bkt-test"
    w.spaces.collision = None
    monkeypatch.setattr(config, "SPACES_BUCKET", "")
    assert "BUCKET_NOT_SET" in w.dry()[0]["blockers"]
    monkeypatch.setattr(config, "SPACES_BUCKET", "bkt-test")
    w.spaces.fail_list = True
    rep, code = w.dry()
    assert code == 2 and rep["blockers"][-1].startswith("LISTING_FAILED") and "counts" not in rep                    # un listado parcial jamás se presenta como completo


# ═══════════════════ backup ═══════════════════
def test_backup_requires_the_exact_confirmation_and_does_nothing_otherwise(w):
    seed(w)
    before = snapshot(w)
    for bad in (None, "", "BACKUP", "backup-cchh-x"):
        rep, code = purge.do_backup(w.deps, bad)
        assert code == 4 and w.spaces.backup == {}, rep
    stale = purge.phrases(purge.environment(w.deps)["env8"], "0" * 12)["backup"]                                     # frase bien formada pero de OTRO inventario
    rep, code = purge.do_backup(w.deps, stale)
    assert code == 6 and rep["error"] == "INVENTORY_CHANGED_SINCE_DRY_RUN" and w.spaces.backup == {}
    assert snapshot(w) == before


def test_backup_publishes_a_sealed_verified_run_with_remote_only_objects_remote_versions_and_retention(w):
    seed(w)
    before = snapshot(w)
    temp_before = set(Path(tempfile.gettempdir()).glob("cchh-reset-*"))
    rep, code = w.backup()
    assert code == 0 and rep["published"]["sealed"] is True and rep["verification"]["manifest_matches_inventory"] is True and rep["verification"]["verify_problems"] == 0
    assert rep["verification"]["remote_only_objects_included"] == 1 and rep["verification"]["differing_remote_versions_included"] == 1 and rep["published"]["objects"] == len(PURGE_RELS)
    assert rep["retention"]["backup_recommended_days"] == 14 and rep["retention"]["automatic_backup_deletion"] is False
    base = f"{remote_store.BACKUP_NAMESPACE}/{purge.MIGRATION}/{rep['run_id']}/"
    keys = [k for k in w.spaces.backup if k.startswith(base)]
    assert all(k.startswith(f"{remote_store.BACKUP_NAMESPACE}/") for k in w.spaces.backup) and base + "sealed.json" in keys and base + "approval.json" in keys
    assert {k[len(base + "objects/"):] for k in keys if "/objects/" in k} == PURGE_RELS                                # TODO lo que se va a borrar está respaldado
    assert any("/objects_remote/profiles/p5.json" in k for k in keys)                                                   # la versión remota distinta también
    assert w.spaces.backup[base + "objects/profiles/p4.json"] == w.spaces.objects["profiles/p4.json"]                    # el contenido SOLO remoto, por bytes
    approval = json.loads(w.spaces.backup[base + "approval.json"])
    assert approval["retention_days"] == 14 and approval["automatic_backup_deletion"] is False and set(approval["objects"]) == PURGE_RELS and approval["unknown"] == {"local": 1, "remote": 1}
    assert set(approval["preserved"]) == {"catalogs/catalogos.json", "import-history/ch-20261023T000000Z-ab12/run.json", "import-history/ch-20261023T000000Z-ab12/staging/aaaaaaaaaaaaaaaa.json"}
    assert snapshot(w) == before                                                                                         # el backup NO borra ni modifica nada
    assert set(Path(tempfile.gettempdir()).glob("cchh-reset-*")) == temp_before                                          # el staging temporal local (con datos) se elimina siempre
    assert not no_pii(rep)


def test_backup_refuses_when_there_are_recent_writes_or_the_metadata_changes_between_snapshots(w):
    seed(w)
    w.put("profiles/p3.json", {"profile_id": "p3"}, remote=False, old=False)
    os.utime(w.root / "profiles/p3.json", (T0.timestamp() - 60, T0.timestamp() - 60))                                   # modificado hace 1 minuto
    rep, code = w.backup()
    assert code == 6 and rep["error"] == "RECENT_WRITES" and rep["recent_writes_last_10_min"] >= 1 and w.spaces.backup == {}
    ts = OLD.timestamp()
    os.utime(w.root / "profiles/p3.json", (ts, ts))
    w.spaces.lm.pop("profiles/p3.json", None)

    def writer(seconds):                                                                                                 # otro proceso escribe DURANTE los 20 s entre inventarios
        w.clock.t += timedelta(seconds=seconds)
        w.put("busquedas/b3.json", {"busqueda_id": "b3"})
    w.deps["sleep"] = writer
    rep, code = w.backup()
    assert code == 6 and rep["error"] == "CONCURRENT_WRITES_DETECTED" and rep["snapshots_equal"] is False and w.spaces.backup == {}


def test_backup_with_nothing_to_purge_is_blocked_and_content_changes_during_staging_are_drift(w):
    rep, code = purge.do_backup(w.deps, purge.phrases(purge.environment(w.deps)["env8"], purge.plan_digest(purge.environment(w.deps)["env8"], {})[:12])["backup"])
    assert code == 2 and rep["error"] == "NOTHING_TO_BACK_UP"
    seed(w)
    real = purge.take_inventory
    calls = {"n": 0}

    def tamper(deps, *, with_content=True):
        inv = real(deps, with_content=with_content)
        calls["n"] += 1
        if calls["n"] == 3:                                                                                              # después del inventario, antes del staging: cambia un archivo
            (w.root / "profiles/p3.json").write_bytes(b'{"cambiado": 1}')
        return inv
    purge.take_inventory = tamper
    try:
        rep, code = w.backup()
    finally:
        purge.take_inventory = real
    assert code == 6 and rep["error"] == "OBJECT_CHANGED_DURING_BACKUP" and w.spaces.backup == {}


# ═══════════════════ ejecución ═══════════════════
def test_execute_needs_every_confirmation_a_valid_window_and_a_sealed_backup(w):
    seed(w)
    run = full_cycle(w)
    before = snapshot(w)
    env8 = purge.environment(w.deps)["env8"]
    for kw in ({"confirm": ""}, {"confirm": "PURGE"}, {"ack": ""}, {"ack": "SI"}, {"digest12": "f" * 12, "confirm": purge.phrases(env8, "f" * 12)["purge"]}):
        rep, code = w.execute(run, **kw)
        assert code == 4, (kw, rep)
    past = (w.clock.now() - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    far = (w.clock.now() + timedelta(minutes=61)).strftime("%Y-%m-%dT%H:%M:%SZ")
    for bad, error in ((past, "WINDOW_INVALID"), (far, "WINDOW_INVALID"), ("mañana", "CONFIRMATION_REQUIRED"), ("2026-10-24 13:00", "CONFIRMATION_REQUIRED")):
        rep, code = w.execute(run, window=bad)
        assert code == 4 and rep["error"] == error, (bad, rep)
    assert snapshot(w) == before and w.spaces.delete_log == []                                                          # ninguna confirmación inválida cambió nada
    unknown_run = "20261024T000000Z-" + "a" * 12
    rep, code = w.execute(unknown_run, window=(w.clock.now() + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert code == 4 and rep["error"].startswith("BACKUP_NOT_SEALED_OR_INVALID") and w.spaces.delete_log == []
    rep, code = w.execute(run, window=(w.clock.now() + timedelta(minutes=60)).strftime("%Y-%m-%dT%H:%M:%SZ"))             # 60 minutos exactos: el máximo permitido
    assert code == 0 and rep["deleted_now"] == len(PURGE_RELS)


def test_execute_deletes_only_the_whitelist_remote_first_per_object_and_preserves_everything_else(w):
    seed(w)
    run = full_cycle(w)
    rep, code = w.execute(run)
    assert code == 0 and rep["deleted_now"] == len(PURGE_RELS) and rep["state_after"] == {"done": len(PURGE_RELS), "pending": 0, "drift": 0, "new": 0}
    left_local, left_remote = w.local_files(), set(w.spaces.objects)
    assert set(left_local) == PRESERVED_RELS - {"raro/objeto.bin"} and left_remote == {"catalogs/catalogos.json", "import-history/ch-20261023T000000Z-ab12/run.json",
                                                                                  "import-history/ch-20261023T000000Z-ab12/staging/aaaaaaaaaaaaaaaa.json", "raro/objeto.bin"}
    assert left_local["catalogs/catalogos.json"] == json.dumps({"area": ["A"]}).encode() and left_local["misc/leeme.txt"] == b"desconocido local"      # catálogos/import-history/desconocidos intactos
    assert (w.root / "profiles").is_dir() and (w.root / "scoring-evaluations").is_dir() and not list((w.root / "scoring-evaluations").iterdir())      # solo archivos; los directorios de entidad quedan
    order = [purge.classify(r)[1] for r in w.spaces.delete_log if purge.classify(r)[0] == "purge"]
    seen = [e for i, e in enumerate(order) if e not in order[:i]]
    assert seen == [e for e in purge.ENTITY_ORDER if e in seen]                                                          # dependientes primero
    assert all(purge.classify(r)[0] in ("purge", "temp") for r in w.spaces.delete_log) and set(w.spaces.delete_log) <= PURGE_RELS            # nunca algo fuera de la lista blanca
    assert len(w.spaces.delete_log) == len(PURGE_RELS) - 2                                                                # los 2 objetos solo-locales (p3 y el temporal) no tocan Spaces
    assert rep["finished_at"] and rep["verify_not_before"] == (datetime.strptime(rep["finished_at"], "%Y-%m-%dT%H:%M:%SZ") + timedelta(seconds=150)).strftime("%Y-%m-%dT%H:%M:%SZ")
    events = [json.loads(v) for k, v in w.spaces.backup.items() if "/journal/" in k]
    assert [e["event"] for e in sorted(events, key=lambda e: e["seq"])] == ["execute_started", "execute_finished"] and not no_pii(events)
    assert not no_pii(rep) and rep["scope"]["graph_operations"] == 0


def test_a_second_execute_is_idempotent(w):
    seed(w)
    run = full_cycle(w)
    assert w.execute(run)[1] == 0
    log = list(w.spaces.delete_log)
    rep, code = w.execute(run)
    assert code == 0 and rep["deleted_now"] == 0 and rep["state_before"] == {"done": len(PURGE_RELS), "pending": 0, "drift": 0, "new": 0} and w.spaces.delete_log == log


def test_partial_failures_resume_from_the_sealed_manifest_without_invalidating_the_plan(w):
    seed(w)
    run = full_cycle(w)
    w.spaces.fail_delete = {"entrevistas/e1.json"}
    rep, code = w.execute(run)
    assert code == 5 and rep["resumable"] is True and (w.root / "entrevistas/e1.json").is_file()                        # el fallo remoto no toca el disco de ese objeto
    still = (set(w.local_files()) | set(w.spaces.objects)) & PURGE_RELS
    assert 0 < rep["deleted_now"] == len(PURGE_RELS) - len(still)                                                       # el avance informado coincide EXACTAMENTE con la realidad
    w.spaces.fail_delete.clear()
    again, code2 = w.execute(run)                                                                                       # el inventario ya difiere del original, pero el manifiesto sellado manda
    assert code2 == 0 and again["state_before"]["done"] == rep["deleted_now"] and again["state_before"]["drift"] == 0 and again["state_before"]["new"] == 0
    assert again["state_after"]["pending"] == 0 and not ((set(w.local_files()) | set(w.spaces.objects)) & PURGE_RELS)


def test_one_side_already_deleted_counts_as_progress_and_the_other_side_must_still_match(w):
    seed(w)
    run = full_cycle(w)
    (w.root / "busquedas/b1.json").unlink()                                                                              # el disco ya se borró, Spaces todavía lo tiene (un corte entre ambos)
    assert w.execute(run)[1] == 0 and "busquedas/b1.json" not in w.spaces.objects


def test_modified_or_new_objects_block_the_execution_and_nothing_is_deleted(w):
    seed(w)
    run = full_cycle(w)
    original = (w.root / "profiles/p1.json").read_bytes()
    w.put("profiles/p1.json", {"profile_id": "p1", "editado": True})                                                      # modificado después del backup (local y remoto)
    rep, code = w.execute(run)
    assert code == 6 and rep["error"] == "DRIFT_FROM_APPROVED_MANIFEST" and rep["state_before"]["drift"] == 1 and w.spaces.delete_log == []
    w.put("profiles/p1.json", original)                                                                                   # revertido: ya no hay deriva…
    w.put("profiles/p9.json", {"profile_id": "p9"})                                                                       # …pero aparece un objeto NUEVO que el backup no cubre
    rep, code = w.execute(run)
    assert code == 6 and rep["state_before"]["new"] == 1 and rep["state_before"]["drift"] == 0 and w.spaces.delete_log == []
    assert (w.root / "profiles/p9.json").is_file() and "profiles/p9.json" in w.spaces.objects


def test_a_side_that_appears_again_after_the_backup_is_drift_not_progress(w):
    seed(w)
    run = full_cycle(w)
    w.put("profiles/p3.json", json.loads((w.root / "profiles/p3.json").read_text()), remote=True)                         # p3 era SOLO local: ahora también está en Spaces
    rep, code = w.execute(run)
    assert code == 6 and rep["state_before"]["drift"] == 1 and w.spaces.delete_log == []


def test_changes_during_the_run_stop_it_immediately(w):
    seed(w)
    run = full_cycle(w)
    state = {"done": False}

    def meddle(rel):
        if not state["done"]:
            state["done"] = True
            w.spaces.objects["busquedas/b2.json"] = b'{"otro": "proceso"}'                                              # otro proceso modifica un objeto pendiente
    w.spaces.on_delete = meddle
    rep, code = w.execute(run)
    assert code == 6 and rep["error"] == "REMOTE_CHANGED" and rep["deleted_now"] >= 1 and "busquedas/b2.json" in w.spaces.objects and "busquedas/b2.json" in w.local_files()


def test_objects_appearing_during_the_run_are_reported_and_never_deleted(w):
    seed(w)
    run = full_cycle(w)
    last = {}

    def late(rel):
        last["rel"] = rel
        if rel.startswith("profiles/") and "late" not in last:
            last["late"] = True
            w.put("publicaciones/pub-nueva.json", {"busqueda_id": "b1"})                                                # escritura concurrente de la app
    w.spaces.on_delete = late
    rep, code = w.execute(run)
    assert code == 6 and rep["error"] == "OBJECTS_APPEARED_OR_CHANGED_DURING_PURGE" and rep["state_after"]["new"] == 1 and "publicaciones/pub-nueva.json" in w.local_files()


def test_window_expiry_and_unconfirmed_remote_deletes_are_handled(w):
    seed(w)
    run = full_cycle(w)

    def slow(rel):
        w.clock.t += timedelta(minutes=20)                                                                               # cada borrado «tarda» 20 min: la ventana de 30 min vence
    w.spaces.on_delete = slow
    rep, code = w.execute(run)
    assert code == 5 and rep["error"] == "WINDOW_EXPIRED" and rep["deleted_now"] == 2
    w.spaces.on_delete = None
    assert w.execute(run)[1] == 0                                                                                        # con una ventana nueva se reanuda y termina
    w.put("profiles/q1.json", {"profile_id": "q1"})                                                                      # borrado remoto no confirmado: el objeto SIGUE existiendo en Spaces
    run2 = w.backup()[0]["run_id"]
    w.spaces.ghost_after_delete = {"profiles/q1.json"}
    w.clock.sleep(1)
    rep, code = w.execute(run2)
    assert code == 5 and rep["error"] == "REMOTE_DELETE_NOT_CONFIRMED" and (w.root / "profiles/q1.json").is_file()        # el disco NO se toca si Spaces no confirmó


def test_execute_refuses_recent_writes_and_a_backup_from_another_environment(w):
    seed(w)
    run = full_cycle(w)
    os.utime(w.root / "profiles/p3.json", (T0.timestamp() - 30, T0.timestamp() - 30))
    rep, code = w.execute(run)
    assert code == 6 and rep["error"] == "RECENT_WRITES" and w.spaces.delete_log == []
    ts = OLD.timestamp()
    os.utime(w.root / "profiles/p3.json", (ts, ts))
    config.SPACES_PREFIX = "otro"
    rep, code = w.execute(run)
    assert code == 4 and rep["error"] == "ENV_MISMATCH" and w.spaces.delete_log == []


def test_an_unsealed_or_tampered_backup_cannot_authorize_a_purge(w):
    seed(w)
    run = full_cycle(w)
    base = f"{remote_store.BACKUP_NAMESPACE}/{purge.MIGRATION}/{run}/"
    sealed = w.spaces.backup.pop(base + "sealed.json")
    assert w.execute(run)[1] == 4 and w.spaces.delete_log == []
    w.spaces.backup[base + "sealed.json"] = sealed
    key = next(k for k in w.spaces.backup if "/objects/profiles/p1.json" in k)
    w.spaces.backup[key] = b"corrupto"
    rep, code = w.execute(run)
    assert code == 4 and "BACKUP_NOT_SEALED_OR_INVALID" in rep["error"] and w.spaces.delete_log == []


def test_execute_never_imports_or_calls_graph_and_logs_no_secrets(w, capsys):
    code = subprocess.run([sys.executable, "-c", "import sys, tools.human_capital.purge_test_data;"
                           "bad=[m for m in ('core.microsoft365','webapp.services.profile_document_service','webapp.services.sharepoint_graph_diagnostics','openai') if m in sys.modules]; print(bad)"],
                          capture_output=True, text=True, cwd=Path(__file__).resolve().parents[1])
    assert code.returncode == 0 and code.stdout.strip() == "[]", code.stderr
    src = (Path(__file__).resolve().parents[1] / "tools/human_capital/purge_test_data.py").read_text(encoding="utf-8")
    assert "microsoft365" not in src.split('"""', 2)[2] and "graph_delete" not in src and "rmtree(" in src and src.count("rmtree(work, ignore_errors=True)") == src.count("rmtree(") == 4          # solo el directorio temporal LOCAL de cada modo (backup/execute/verify/restore)
    seed(w)
    run = full_cycle(w)
    w.execute(run)
    assert capsys.readouterr().out == ""                                                                                 # las funciones no imprimen: solo `main` emite el JSON, sin credenciales


# ═══════════════════ verificación ═══════════════════
def test_verify_waits_for_the_sync_window_and_then_confirms_empty_filesystem_and_spaces(w):
    seed(w)
    run = full_cycle(w)
    assert w.execute(run)[1] == 0
    rep, code = w.verify(run)
    assert code == 3 and rep["error"] == "PENDING_SYNC_WINDOW" and rep["sync_window"]["seconds_remaining"] == 150 and rep["filesystem_empty"] and rep["spaces_empty"]
    w.clock.sleep(149)
    assert w.verify(run)[1] == 3
    w.clock.sleep(1)
    rep, code = w.verify(run)
    assert code == 0 and rep["verdict"] == "OK" and rep["repository_reads"] == {k: "ok" for k in ("profiles", "busquedas", "entrevistas", "onboarding", "publicaciones")}
    assert rep["preserved"] == {"missing": 0, "changed": 0, "checked": 3} and rep["unknown_files"]["missing_since_backup"] == 0 and not no_pii(rep)


def test_verify_detects_reappearance_residue_and_missing_preserved_files(w):
    seed(w)
    run = full_cycle(w)
    w.execute(run)
    w.clock.sleep(200)
    w.spaces.objects["profiles/p1.json"] = b"{}"                                                                         # la app volvió a subir un objeto (reaparición)
    rep, code = w.verify(run)
    assert code == 7 and rep["error"] == "RESIDUE_OR_PRESERVED_MISSING" and rep["spaces_empty"] is False
    del w.spaces.objects["profiles/p1.json"]
    (w.root / "profiles").mkdir(exist_ok=True)
    (w.root / "profiles/zz.json").write_text("{}")
    assert w.verify(run)[1] == 7
    (w.root / "profiles/zz.json").unlink()
    (w.root / "catalogs/catalogos.json").unlink()                                                                         # faltante de un preservado: falla
    assert w.verify(run)[1] == 7
    w.put("catalogs/catalogos.json", {"area": ["cambiado legítimamente"]})
    rep, code = w.verify(run)
    assert code == 0 and rep["preserved"]["changed"] == 1                                                               # cambiado = advertencia, no falla
    (w.root / "misc/leeme.txt").unlink()
    assert w.verify(run)[1] == 7 and w.verify(run)[0]["unknown_files"]["missing_since_backup"] == 1                       # un desconocido que desapareció también falla


def test_verify_fails_when_a_repository_cannot_be_read(w, monkeypatch):
    seed(w)
    run = full_cycle(w)
    w.execute(run)
    w.clock.sleep(200)
    from webapp.services import profile_repository
    real = profile_repository.list
    monkeypatch.setattr(profile_repository, "list", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    rep, code = w.verify(run)
    assert code == 7 and rep["error"] == "REPOSITORY_READ_FAILED" and rep["repository_reads"]["profiles"] == "RuntimeError"
    monkeypatch.setattr(profile_repository, "list", real)
    assert w.verify(run)[1] == 0


def test_verify_without_the_backup_run_checks_residue_only(w):
    rep, code = w.verify(None)
    assert code == 0 and "sync_window" not in rep and rep["filesystem_empty"] is True and rep["spaces_empty"] is True
    seed(w)
    assert w.verify(None)[1] == 7                                                                                         # con datos sin purgar, la verificación falla


# ═══════════════════ restauración ═══════════════════
def test_restore_without_confirmation_only_reports_and_changes_nothing(w):
    seed(w)
    run = full_cycle(w)
    w.execute(run)
    before = snapshot(w)
    rep, code = w.restore(run, confirm=None)
    assert code == 0 and rep["required_confirmation"].startswith("RESTORE-CCHH-") and rep["would_restore"]["restore_local"] == len(PURGE_RELS) and snapshot(w) == before and rep["rollback_performed"] is False


def test_restore_rebuilds_local_and_spaces_without_overwriting_and_reports_what_it_cannot_restore(w):
    seed(w)
    originals_local = {rel: (w.root / rel).read_bytes() for rel in PURGE_RELS if (w.root / rel).is_file()}
    originals_remote = dict(w.spaces.objects)
    run = full_cycle(w)
    w.execute(run)
    rep, code = w.restore(run, confirm=True)
    assert code == 0 and rep["restore"]["failed"] == 0 and rep["restore"]["conflicts"] == 0
    for rel, data in originals_local.items():
        assert (w.root / rel).read_bytes() == data, rel                                                                   # el disco vuelve a ser idéntico
    for rel, data in originals_remote.items():
        if rel in PURGE_RELS and rel != "profiles/p5.json":
            assert w.spaces.objects[rel] == data, rel                                                                     # Spaces también, byte a byte
    assert (w.root / "profiles/p4.json").read_bytes() == originals_remote["profiles/p4.json"] and w.spaces.objects["profiles/p4.json"] == originals_remote["profiles/p4.json"]       # solo-remoto: se repone en ambos
    assert rep["restore"]["remote_version_not_restored"] == 1 and "profiles/p5.json" not in w.spaces.objects and (w.root / "profiles/p5.json").read_bytes() == originals_local["profiles/p5.json"]   # la versión remota distinta queda SOLO en el respaldo (`objects_remote/`); no se inventa otra
    again, code2 = w.restore(run, confirm=True)
    assert code2 == 0 and again["restore"]["restore_local"] == 0 and again["restore"]["restore_remote"] == 0                 # idempotente


def test_restore_never_overwrites_existing_data_and_never_rolls_back(w):
    seed(w)
    run = full_cycle(w)
    w.execute(run)
    w.put("profiles/p1.json", {"profile_id": "p1", "datos": "nuevos y distintos"})                                         # alguien ya creó algo con ese nombre
    w.spaces.fail_push = {"entrevistas/e1.json"}
    rep, code = w.restore(run, confirm=True)
    assert code == 5 and rep["error"] == "RESTORE_INCOMPLETE" and rep["rollback_performed"] is False
    assert json.loads((w.root / "profiles/p1.json").read_text())["datos"] == "nuevos y distintos" and json.loads(w.spaces.objects["profiles/p1.json"].decode())["datos"] == "nuevos y distintos"
    assert rep["restore"]["conflicts"] >= 1 and rep["restore"]["failed"] == 1
    assert (w.root / "entrevistas/e1.json").is_file() and (w.root / "profiles/p2.json").is_file()                          # lo restaurado antes del fallo NO se revierte
    assert w.spaces.delete_log and all(r in PURGE_RELS for r in w.spaces.delete_log)                                       # (los borrados son solo los de la ejecución previa; restaurar no borra)
    n = len(w.spaces.delete_log)
    w.restore(run, confirm=True)
    assert len(w.spaces.delete_log) == n


def test_restore_is_protected_by_environment_confirmation_and_a_sealed_backup(w):
    seed(w)
    run = full_cycle(w)
    w.execute(run)
    before = snapshot(w)
    for bad in ("RESTORE", purge.phrases(purge.environment(w.deps)["env8"], "f" * 12)["restore"]):
        rep, code = w.restore(run, confirm=bad)
        assert code == 4 and rep["error"] == "CONFIRMATION_INVALID", bad
    assert snapshot(w) == before                                                                                         # sin la frase exacta no se restaura nada
    config.SPACES_BUCKET = "otro-bucket"
    rep, code = w.restore(run, confirm=True)
    assert code == 4 and rep["error"] == "ENV_MISMATCH" and snapshot(w) == before                                         # un respaldo de otro entorno jamás se restaura aquí
    config.SPACES_BUCKET = "bkt-test"
    base = f"{remote_store.BACKUP_NAMESPACE}/{purge.MIGRATION}/{run}/"
    w.spaces.backup.pop(base + "sealed.json")
    rep, code = w.restore(run, confirm=True)
    assert code == 4 and "BACKUP_NOT_SEALED_OR_INVALID" in rep["error"] and snapshot(w) == before


# ═══════════════════ CLI y salida ═══════════════════
def test_cli_argument_rules_and_exit_codes(w, capsys):
    seed(w)
    with pytest.raises(SystemExit):
        purge.main(["--confirm", "x"], w.deps)
    with pytest.raises(SystemExit):
        purge.main(["--execute", "--verify"], w.deps)
    capsys.readouterr()
    for flag, mode in (("--backup", "backup"), ("--execute", "execute"), ("--restore", "restore")):
        assert purge.main([flag], w.deps) == 4
        rep = json.loads(capsys.readouterr().out)
        assert rep["mode"] == mode and rep["exit_code"] == 4 and rep["writes_performed"] == 0
    assert purge.main(["--dry-run"], w.deps) == 0 and json.loads(capsys.readouterr().out)["mode"] == "dry_run"


def test_no_output_ever_carries_personal_data_or_object_names_of_the_whitelist(w, capsys):
    seed(w)
    for argv in ([], ["--verify"]):
        purge.main(argv, w.deps)
    out = capsys.readouterr().out
    assert not [p for p in PII if p in out] and "p1.json" not in out and "ev1" not in out


# ═══════════════════ helpers de solo lectura agregados a `remote_store` ═══════════════════
class _Paginator:
    def __init__(self, pages, fail_on=None):
        self.pages, self.fail_on = pages, fail_on

    def paginate(self, Bucket, Prefix):
        for i, page in enumerate(self.pages):
            if self.fail_on == i:
                raise RuntimeError("boom")
            yield {"Contents": [o for o in page if o["Key"].startswith(Prefix)]}


class _Client:
    def __init__(self, pages=(), fail_on=None, head=None):
        self.pages, self.fail_on, self.head, self.calls = pages, fail_on, head or {}, []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return _Paginator(self.pages, self.fail_on)

    def head_object(self, Bucket, Key):
        self.calls.append(("HEAD", Key))
        result = self.head.get(Key, "missing")
        if result == "missing":
            err = RuntimeError("404")
            err.response = {"Error": {"Code": "404"}, "ResponseMetadata": {"HTTPStatusCode": 404}}
            raise err
        if isinstance(result, Exception):
            raise result
        return {}


@pytest.fixture()
def real_rs(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(config, "PERSIST_ENABLED", True)
    monkeypatch.setattr(config, "SPACES_PREFIX", "prod")
    monkeypatch.setattr(config, "SPACES_BUCKET", "bkt")
    monkeypatch.setattr(config, "PERSIST_PATHS", ["human-capital", "activity_log"])
    return monkeypatch


def test_list_persisted_objects_maps_the_env_prefix_skips_markers_and_never_returns_a_partial_listing(real_rs):
    lm = datetime(2026, 10, 1, 8, 30, 5, tzinfo=timezone.utc)
    pages = [[{"Key": "prod/human-capital/", "Size": 0}, {"Key": "prod/human-capital/profiles/a.json", "Size": 7, "ETag": '"abc"', "LastModified": lm}],
             [{"Key": "prod/human-capital/catalogs/c.json", "Size": 3, "ETag": '"def"', "LastModified": lm}, {"Key": "prod/activity_log/x.jsonl", "Size": 1}]]
    real_rs.setattr(remote_store, "_get_client", lambda: _Client(pages))
    out = remote_store.list_persisted_objects("human-capital")
    assert out == {"profiles/a.json": {"size": 7, "etag": "abc", "last_modified": "2026-10-01T08:30:05Z"}, "catalogs/c.json": {"size": 3, "etag": "def", "last_modified": "2026-10-01T08:30:05Z"}}
    real_rs.setattr(remote_store, "_get_client", lambda: _Client(pages, fail_on=1))
    with pytest.raises(remote_store.RemoteReadError):
        remote_store.list_persisted_objects("human-capital")                                                              # un listado a medias NO se devuelve como completo
    with pytest.raises(remote_store.RemoteReadError):
        remote_store.list_persisted_objects("no-es-un-directorio-persistido")
    real_rs.setattr(config, "PERSIST_ENABLED", False)
    real_rs.setattr(config, "PERSIST_READ_ONLY", False)
    with pytest.raises(remote_store.RemoteReadError):
        remote_store.list_persisted_objects("human-capital")                                                              # sin lectura remota autorizada no hay listado


def test_persisted_object_exists_distinguishes_present_absent_and_undeterminable(real_rs, tmp_path):
    path = tmp_path / "human-capital" / "profiles" / "a.json"
    client = _Client(head={"prod/human-capital/profiles/a.json": True, "prod/human-capital/profiles/err.json": RuntimeError("500")})
    real_rs.setattr(remote_store, "_get_client", lambda: client)
    assert remote_store.persisted_object_exists(path) is True
    assert remote_store.persisted_object_exists(tmp_path / "human-capital" / "profiles" / "ausente.json") is False        # 404 confirmado = ausente
    with pytest.raises(remote_store.RemoteReadError):
        remote_store.persisted_object_exists(tmp_path / "human-capital" / "profiles" / "err.json")                         # cualquier otro error: indeterminado, NO «ausente»
    with pytest.raises(remote_store.RemoteReadError):
        remote_store.persisted_object_exists(tmp_path / "fuera-de-lo-persistido" / "x.json")
    assert [c[0] for c in client.calls] == ["HEAD", "HEAD", "HEAD"]                                                      # solo lecturas (HEAD)
