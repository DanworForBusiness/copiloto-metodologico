"""Operación excepcional ops/recovery/biogenesis_20261106 — pruebas con DATOS SINTÉTICOS (sin CSN del cliente): el plan y la firma se reemplazan por equivalentes de prueba;
el código ejercitado (preflight, cuarentena, unidad transaccional, recuperación, carga de evidencia) es el real. Bucket simulado en memoria; todo bajo ``tmp_path``.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import threading
from pathlib import Path

import pytest

import config
from core import publication_gate as gate
from core import remote_store
from tests.publication_fakes import FakeS3, SimulatedCrash, enable_remote
from tools.catalog import publication_intent as pi
from tools.catalog import publication_txn as txn

OPS = Path(__file__).resolve().parents[1] / "ops" / "recovery" / "biogenesis_20261106"
sha = lambda b: hashlib.sha256(b).hexdigest()


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, OPS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


R = _load("restore_biogenesis")
U = _load("upload_evidence")
POOL = R._POOL
RESTORE = ["analytic-models/AM_X.json", "views/VT_A.json", "views/VT_B.json"]
QUAR = ["local-tables/1LT_QA.json", "replication-flows/1RF_QA.json"]
MANIFEST_QA = ".lineage_runs/Biogenesis/CALIDAD/analytic-model/AM_X/lineage-manifest.json"
KEEP = ["views/VT_C.json", "remote-tables/RT_1.json", "Conexiones/conn_space_QA.json"]


class S3(FakeS3):
    def put_object(self, Bucket, Key, Body, **kwargs):                                     # boto3 acepta ContentType/ACL; el doble los ignora (sin ACL ⇒ privado)
        assert "ACL" not in kwargs, "la evidencia nunca se sube con ACL explícita"
        return super().put_object(Bucket=Bucket, Key=Key, Body=Body)

    def get_object_acl(self, Bucket, Key):
        return {"Grants": [{"Grantee": {"Type": "CanonicalUser", "ID": "owner"}, "Permission": "FULL_CONTROL"}]}


@pytest.fixture()
def world(tmp_path, monkeypatch):
    base = tmp_path / "app"
    cat = base / "catalog_local"
    old = {POOL + r: f'{{"definitions": {{"{r}": "CALIDAD"}}}}'.encode() for r in RESTORE + QUAR}
    old[MANIFEST_QA] = b'{"space": "CALIDAD"}'
    new = {POOL + r: f'{{"definitions": {{"{r}": "PRODUCCION"}}}}'.encode() for r in RESTORE}
    keep = {POOL + r: f'{{"keep": "{r}"}}'.encode() for r in KEEP}
    for rel, data in {**old, **keep}.items():
        (cat / rel).parent.mkdir(parents=True, exist_ok=True)
        (cat / rel).write_bytes(data)
    (cat / "Biogenesis" / "D" / "vistas").mkdir(parents=True)
    (cat / "Biogenesis" / "D" / "vistas" / "VT_A.md").write_text("ficha", encoding="utf-8")
    (cat / ".csn_cache" / "Zeta" / "views").mkdir(parents=True)
    (cat / ".csn_cache" / "Zeta" / "views" / "VT_A.json").write_text("otro cliente", encoding="utf-8")
    signature = {"Biogenesis/" + r[len(POOL):]: sha(d) for r, d in {**new, **keep}.items() if "Conexiones/" not in r}
    (cat / ".catalog_manifest.json").write_text(json.dumps({"flows": {"Biogenesis/General": {"signature": {"files": [{"path": p, "sha256": h} for p, h in signature.items()]}}}}), encoding="utf-8")
    plan = {rel: {"current": sha(d), "target": sha(new[rel]) if rel in new else None} for rel, d in old.items()}
    monkeypatch.setattr(R, "PLAN", plan)
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(cat))
    monkeypatch.delenv("DANWOR_CATALOG_CSN_CACHE", raising=False)
    monkeypatch.setenv(txn.LOCK_DIR_ENV, str(tmp_path / "locks"))
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    s3 = S3()
    enable_remote(monkeypatch, s3)
    monkeypatch.setattr(config, "BASE_DIR", base, raising=False)
    from webapp.services import datasphere_publication_service  # noqa: F401 — guarda real del sincronizador
    remote_store.mirror_up()
    ev = tmp_path / "evidence"
    for rel, data in new.items():
        p = ev / "pool" / "Biogenesis" / rel[len(POOL):]
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    gate.set_resolver(None)

    class W:
        pass
    w = W()
    w.cat, w.base, w.s3, w.ev, w.old, w.new = cat, base, s3, ev, old, new
    yield w
    gate.clear(cat)


def upload(w):
    assert U.run(w.ev, apply=True, client=w.s3, bucket="audit-bucket") == 0


def state(w):
    loc = {r: R._classify(r, (w.cat / r).read_bytes() if (w.cat / r).is_file() else None) for r in R.PLAN}
    rem = {r: R._classify(r, remote_store.read_object_bytes(w.cat / r)) for r in R.PLAN}
    return loc, rem


def whole(w, s):
    return state(w) == ({r: s for r in R.PLAN}, {r: s for r in R.PLAN})


def outside(w):
    loc = {p.relative_to(w.base).as_posix(): sha(p.read_bytes()) for p in w.base.rglob("*") if p.is_file()
           and not any(x in p.parts for x in (".publication_runtime", ".recovery_quarantine")) and p.relative_to(w.cat).as_posix() not in R.PLAN}
    rem = {k: sha(v) for k, v in w.s3.objects.items() if "/quarantine/" not in k and "/.publication_state/" not in k and k[len("audit/catalog_local/"):] not in R.PLAN}
    return loc, rem


APPLY = ["--apply", "--confirm", R.CONFIRM_APPLY]
ROLLBACK = ["--rollback", "--confirm", R.CONFIRM_ROLLBACK]


# ═══════════ carga de evidencia ═══════════
def test_evidence_upload_is_dry_run_by_default_then_private_verified_and_never_overwrites(world):
    before = dict(world.s3.objects)
    assert U.run(world.ev, apply=False, client=world.s3, bucket="audit-bucket") == 0 and world.s3.objects == before
    upload(world)
    keys = [k for k in world.s3.objects if "/quarantine/biogenesis-20261106-evidence/" in k]
    assert len(keys) == 3 and all(sha(world.s3.objects[k]) in {sha(d) for d in world.new.values()} for k in keys)
    upload(world)                                                                         # idempotente
    world.s3.objects[keys[0]] = b"otro"
    assert U.run(world.ev, apply=True, client=world.s3, bucket="audit-bucket") == 1      # nunca sobrescribe evidencia


def test_evidence_upload_refuses_local_evidence_that_does_not_match_the_fixed_hashes(world):
    f = next(world.ev.rglob("*.json"))
    f.write_bytes(f.read_bytes() + b" ")
    with pytest.raises(SystemExit):
        U.run(world.ev, apply=True, client=world.s3, bucket="audit-bucket")


# ═══════════ dry-run y preflight ═══════════
def test_dry_run_is_the_default_and_writes_nothing(world, capsys):
    upload(world)
    before_l, before_r = outside(world), dict(world.s3.objects)
    assert R.main([]) == 0
    out = capsys.readouterr().out
    assert "DRY-RUN (no se escribió nada)" in out and out.count("restaurar (evidencia de Spaces)") == 3 and out.count("retirar a cuarentena") == 3
    assert outside(world) == before_l and world.s3.objects == before_r and whole(world, R.OLD)


def test_execution_requires_the_explicit_confirmation(world):
    upload(world)
    before = dict(world.s3.objects)
    assert R.main(["--apply"]) == 1 and R.main(["--apply", "--confirm", "si"]) == 1 and R.main(["--rollback"]) == 1
    assert world.s3.objects == before and whole(world, R.OLD)


def test_without_the_evidence_in_spaces_it_aborts(world):
    assert R.main(APPLY) == 1 and whole(world, R.OLD)


def test_evidence_altered_in_spaces_is_never_used(world):
    upload(world)
    k = next(k for k in world.s3.objects if "/quarantine/biogenesis-20261106-evidence/" in k)
    world.s3.objects[k] = world.s3.objects[k] + b" "
    assert R.main(APPLY) == 1 and whole(world, R.OLD)


@pytest.mark.parametrize("where", ["local-fuera-del-plan", "remoto-del-plan", "extra-inesperado"])
def test_any_deviation_from_the_inventoried_state_aborts_without_writing(world, where):
    upload(world)
    if where == "local-fuera-del-plan":
        (world.cat / POOL / "views/VT_C.json").write_bytes(b"cambiado")
    elif where == "remoto-del-plan":
        world.s3.objects["audit/catalog_local/" + POOL + RESTORE[0]] = b"tercer contenido"
    else:
        (world.cat / POOL / "views/VT_NUEVA.json").write_bytes(b"{}")
    before = dict(world.s3.objects)
    assert R.main(APPLY) == 1 and world.s3.objects == before
    assert not (world.cat.parent / ".recovery_quarantine").exists()


# ═══════════ ejecución, idempotencia y reversión ═══════════
def test_apply_restores_exact_bytes_quarantines_and_changes_nothing_else(world):
    upload(world)
    out_before = outside(world)
    assert R.main(APPLY) == 0 and whole(world, R.NEW)
    for rel, data in world.new.items():
        assert (world.cat / rel).read_bytes() == data
    for rel, data in world.old.items():                                                  # cuarentena verificable de lo previo (incluido el manifiesto)
        assert world.s3.objects[f"audit/quarantine/{R.QID}/catalog_local/{rel}"] == data
    assert outside(world) == out_before
    assert not txn.has_pending(world.cat) and gate.read_state(world.cat) is None and not pi.open_intents(world.cat)


def test_apply_is_idempotent_and_rollback_is_exact(world):
    upload(world)
    out0 = outside(world)
    plan0 = {r: (world.cat / r).read_bytes() for r in R.PLAN}
    plan0_remote = {r: remote_store.read_object_bytes(world.cat / r) for r in R.PLAN}
    assert R.main(APPLY) == 0
    snap = dict(world.s3.objects)
    assert R.main(APPLY) == 0 and world.s3.objects == snap
    assert R.main(ROLLBACK) == 0 and whole(world, R.OLD)
    assert {r: (world.cat / r).read_bytes() for r in R.PLAN} == plan0
    assert {r: remote_store.read_object_bytes(world.cat / r) for r in R.PLAN} == plan0_remote
    assert outside(world) == out0


@pytest.mark.parametrize("point", ["restore_after_undo", "restore_after_local_commit", "restore_after_intent"])
def test_no_intermediate_state_is_published_or_readable(world, monkeypatch, point):
    upload(world)
    seen = {}

    def hook(p, **kw):
        if p == point:
            seen["uploads"] = remote_store.mirror_up()

            def reader():
                try:
                    gate.check(world.cat, wait=0)
                    seen["reader"] = "abierto"
                except (gate.PublicationInProgress, gate.PublicationBlocked):
                    seen["reader"] = "bloqueado"
            t = threading.Thread(target=reader)
            t.start()
            t.join(10)
    monkeypatch.setattr(txn, "FAULT_HOOK", hook)
    assert R.main(APPLY) == 0
    assert seen == {"uploads": 0, "reader": "bloqueado"}


# ═══════════ interrupciones, reinicio y estados ambiguos ═══════════
POINTS = ["restore_after_undo", "restore_mid_local", "restore_after_local_commit", "restore_after_intent", "restore_after_remote", "restore_before_intent_close"]


def _crash(monkeypatch, point):
    def hook(p, **kw):
        if p == point and (p != "restore_mid_local" or kw.get("index") == 2):
            raise SimulatedCrash(point)
    monkeypatch.setattr(txn, "FAULT_HOOK", hook)


def _lose_disk(w):
    for d in (w.cat, w.base / ".publication_runtime", w.base / ".recovery_quarantine"):
        shutil.rmtree(d, ignore_errors=True)
    remote_store._signatures.clear()
    remote_store.restore()


@pytest.mark.parametrize("lose_disk", [False, True], ids=["disco-conservado", "disco-perdido"])
@pytest.mark.parametrize("point", POINTS)
def test_a_crash_anywhere_converges_to_one_whole_state_and_can_complete(world, monkeypatch, point, lose_disk):
    upload(world)
    _, rem_before = outside(world)
    _crash(monkeypatch, point)
    with pytest.raises(SimulatedCrash):
        R.main(APPLY)
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    if lose_disk:
        _lose_disk(world)
    txn.recover_pending(world.cat)                                                       # arranque / lector bloqueado
    loc, rem = state(world)
    assert loc == rem and len(set(loc.values())) == 1 and R.OTHER not in loc.values()
    assert R.main(APPLY) == 0 and whole(world, R.NEW)
    assert outside(world)[1] == rem_before


def test_a_surviving_journal_over_a_rebuilt_disk_is_detected_and_nothing_is_written(world, monkeypatch):
    """Riesgo pedido: se pierde catalog_local (reconstruido desde Spaces) pero sobrevive el journal de la restauración. El script lo detecta y se detiene sin escribir."""
    upload(world)
    _crash(monkeypatch, "restore_after_local_commit")
    with pytest.raises(SimulatedCrash):
        R.main(APPLY)
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    gate.clear(world.cat)
    shutil.rmtree(world.cat)                                                             # solo el catálogo: el runtime (journal) sobrevive
    remote_store._signatures.clear()
    remote_store.restore()
    before_l, before_r = outside(world), dict(world.s3.objects)
    for argv in ([], APPLY, ROLLBACK):
        assert R.main(argv) == 1
    assert outside(world) == before_l and world.s3.objects == before_r


@pytest.mark.parametrize("direction", ["apply", "rollback"])
def test_a_mixed_state_converges_to_one_whole_state(world, direction):
    upload(world)
    ctx = R.build_ctx()
    R.preflight(ctx)
    R.ensure_quarantine(ctx, write=True)                                                 # la cuarentena ya existía (fue lo primero de la operación interrumpida)
    for rel in RESTORE[:2]:                                                              # mezcla: 2 objetos ya restaurados en disco Y en Spaces
        (world.cat / POOL / rel).write_bytes(world.new[POOL + rel])
        remote_store.push_path(world.cat / POOL / rel)
    assert R.preflight(R.build_ctx())[0] == R.MIXED
    assert R.main(APPLY if direction == "apply" else ROLLBACK) == 0
    assert whole(world, R.NEW if direction == "apply" else R.OLD)


def test_spaces_rejection_rolls_back_disk_and_spaces(world):
    upload(world)
    world.s3.fail_keys = ("views/VT_B.json",)
    assert R.main(APPLY) == 2 and whole(world, R.OLD)
    assert not txn.has_pending(world.cat) and gate.read_state(world.cat) is None


def test_after_the_restore_a_restart_from_spaces_does_not_reintroduce_the_retired_objects(world):
    upload(world)
    assert R.main(APPLY) == 0
    _lose_disk(world)
    assert whole(world, R.NEW)
    assert not any("quarantine" in str(p) for p in world.cat.rglob("*"))


# ═══════════ aislamiento del funcionamiento normal ═══════════
def test_the_operation_is_not_wired_into_the_application():
    repo = OPS.parents[2]
    for p in list((repo / "webapp").rglob("*.py")) + list((repo / "core").rglob("*.py")) + list((repo / "tools").rglob("*.py")) + [repo / "Procfile", repo / "config.py"]:
        text = p.read_text(encoding="utf-8", errors="ignore")
        assert "ops.recovery" not in text and "ops/recovery" not in text and "restore_biogenesis" not in text, p
    assert not (repo / "ops" / "__init__.py").exists()
