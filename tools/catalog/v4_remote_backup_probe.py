"""v4_remote_backup_probe — prueba INOCUA y acotada del respaldo durable en Spaces de ``evidence_leaf_materialization_v4`` (ver ``_handoffs/V4_REMOTE_PROBE_PLAN.md``).

    python -m tools.catalog.v4_remote_backup_probe --dry-run                              # por defecto: SOLO lectura, cero escrituras (informa que haria)
    python -m tools.catalog.v4_remote_backup_probe --execute --confirm WRITE-4-PROBE-OBJECTS

Reutiliza EXACTAMENTE la logica ya validada (``governed_backup.create_backup`` + ``durable_backup.publish/fetch``). Limites duros (todos con tests con Spaces falso):
  * solo crea UN run nuevo ``probe-<UTC>`` bajo ``migration-backups/evidence_leaf_materialization_v4/`` (``run_id`` generado aqui; no se acepta otro);
  * como maximo CUATRO objetos (``objects/<1 sobre>``, ``manifest.json``, ``manifest.sha256``, ``sealed.json``); si por cualquier motivo el respaldo tendria otro conjunto de archivos
    (p. ej. el remoto difiere del local => ``objects_remote/``) aborta ANTES de escribir nada remoto;
  * cada objeto se confirma por bytes, el sello lleva el hash de cada archivo y el run se vuelve a bajar verificando sello, manifiesto y hashes;
  * NO modifica Assessments, NO migra, NO borra respaldos (ni usa ``--apply``); verifica que el sobre original (local y remoto) y todo el almacen gobernado quedaron identicos;
  * fail-closed: ante CUALQUIER incertidumbre (modo remoto no habilitado, colision de namespace, plan vacio, run ya existente, listado/lectura irresoluble) no escribe y sale con codigo != 0;
  * el informe JSON NO contiene secretos, endpoints ni contenido de perfiles: solo claves del respaldo, tamanos, hashes y banderas.
Salida: 0 ok (dry-run sin bloqueos, o ejecucion verificada) · 2 dry-run con bloqueos (no se ejecutaria) · 4 ``--execute`` sin la confirmacion exacta (cero escrituras) · 5 falla durante la ejecucion
(se informan las claves parciales escritas; un run sin sello es inutilizable e inocuo; nada se borra)."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

CONFIRM_PHRASE = "WRITE-4-PROBE-OBJECTS"
MAX_OBJECTS = 4
EXIT_OK, EXIT_BLOCKED, EXIT_NOT_CONFIRMED, EXIT_FAILED = 0, 2, 4, 5


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _tree(governed: Path | None) -> dict:
    return {p.relative_to(governed).as_posix(): _sha(p.read_bytes()) for p in sorted(governed.rglob("*")) if p.is_file()} if governed else {}


def new_probe_run_id(now: datetime.datetime | None = None) -> str:
    return "probe-" + (now or datetime.datetime.now(datetime.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")


def _preflight(deps: dict) -> dict:
    """Comprobaciones de SOLO lectura comunes al dry-run y a la ejecucion. Devuelve el contexto (o ``blockers``)."""
    rs, svc, bf, catalog_reads, dbk, mig = deps["remote_store"], deps["svc"], deps["bf"], deps["catalog_reads"], deps["dbk"], deps["MIG"]
    ctx: dict[str, Any] = {"blockers": [], "migration": mig}
    ctx["remote_read_enabled"], ctx["remote_write_enabled"] = bool(rs.read_enabled()), bool(rs.write_enabled())
    if not ctx["remote_read_enabled"]:
        ctx["blockers"].append("REMOTE_READ_NOT_ENABLED")
    if not ctx["remote_write_enabled"]:
        ctx["blockers"].append("REMOTE_WRITE_NOT_ENABLED")
    collision = rs.backup_namespace_collision()
    ctx["namespace_collision"] = collision is not None
    if collision:
        ctx["blockers"].append("NAMESPACE_COLLISION")
    governed = svc.governed_dir()
    if governed is None or not Path(governed).is_dir():
        ctx["blockers"].append("NO_GOVERNED_STORE")
        return ctx
    ctx["governed"] = Path(governed)
    try:
        _m, entries = catalog_reads._catalog_entries()
        plan = bf.migrate_stale_evidence(list(entries or []), mig, apply=False)["plan"]            # SOLO lectura (dry-run del plan)
    except Exception as exc:                                                                      # noqa: BLE001 — fail-closed, sin detalle
        ctx["blockers"].append(f"PLAN_UNAVAILABLE:{type(exc).__name__}")
        return ctx
    ctx["plan"] = plan
    if not plan["items"]:
        ctx["blockers"].append("EMPTY_PLAN")
        return ctx
    item = plan["items"][0]                                                                       # determinista: primer item del plan ordenado
    src = svc.governed_path(item["client"], item["root"])
    if src is None or not Path(src).is_file():
        ctx["blockers"].append("SOURCE_ENVELOPE_MISSING")
        return ctx
    ctx["item"], ctx["src"] = item, Path(src)
    try:
        ctx["prior_runs"] = [r["run_id"] for r in dbk.list_runs(mig)]
        local = Path(src).read_bytes()
        remote = rs.read_object_bytes(src)
    except Exception as exc:                                                                      # noqa: BLE001
        ctx["blockers"].append(f"REMOTE_STATE_UNRESOLVABLE:{type(exc).__name__}")
        return ctx
    ctx["original_local_sha256"] = _sha(local)
    ctx["original_remote_sha256"] = _sha(remote) if remote is not None else None
    if remote is None:
        ctx["blockers"].append("REMOTE_ORIGINAL_ABSENT")
    elif remote != local:
        ctx["blockers"].append("REMOTE_DIFFERS_FROM_LOCAL")                                       # el respaldo tendria un 5.o objeto (objects_remote/): no se prueba con incertidumbre
    key = Path(src).resolve().relative_to(Path(governed).resolve()).as_posix()
    ctx["object_key"] = key
    ctx["expected_files"] = [f"objects/{key}", "manifest.json", "manifest.sha256", "sealed.json"]
    return ctx


def _base_report(ctx: dict, deps: dict, mode: str) -> dict:
    mig = deps["MIG"]
    return {
        "tool": "v4_remote_backup_probe", "mode": mode, "migration": mig, "namespace": f"{deps['remote_store'].BACKUP_NAMESPACE}/{mig}/probe-*",
        "remote_read_enabled": ctx.get("remote_read_enabled"), "remote_write_enabled": ctx.get("remote_write_enabled"), "namespace_collision": ctx.get("namespace_collision"),
        "prior_runs_in_namespace": ctx.get("prior_runs"), "plan_items": len((ctx.get("plan") or {}).get("items", [])), "blockers": list(ctx["blockers"]),
        "max_objects": MAX_OBJECTS, "expected_object_count": len(ctx.get("expected_files", [])), "assessments_modified": False, "migration_executed": False, "deletes_performed": 0,
    }


def dry_run(deps: dict) -> tuple[dict, int]:
    """Cero escrituras (ni remotas ni locales): informa que haria y si algo lo bloquearia."""
    ctx = _preflight(deps)
    rep = _base_report(ctx, deps, "dry_run")
    rep.update(writes_performed=0, would_write_run_prefix=f"{rep['namespace'][:-1]}<UTC>", would_write_files=ctx.get("expected_files"), object_key=ctx.get("object_key"),
               original_local_sha256=ctx.get("original_local_sha256"), original_remote_sha256=ctx.get("original_remote_sha256"),
               would_execute=not ctx["blockers"], confirm_phrase_required_for_execute=CONFIRM_PHRASE)
    return rep, (EXIT_BLOCKED if ctx["blockers"] else EXIT_OK)


def execute(deps: dict, confirm: str | None, *, now: datetime.datetime | None = None) -> tuple[dict, int]:
    rs, svc, dbk, gb, mig = deps["remote_store"], deps["svc"], deps["dbk"], deps["gb"], deps["MIG"]
    if confirm != CONFIRM_PHRASE:
        rep = {"tool": "v4_remote_backup_probe", "mode": "execute_refused", "error": "CONFIRMATION_REQUIRED", "required": f"--confirm {CONFIRM_PHRASE}", "writes_performed": 0,
               "assessments_modified": False, "migration_executed": False, "deletes_performed": 0}
        return rep, EXIT_NOT_CONFIRMED
    ctx = _preflight(deps)
    rep = _base_report(ctx, deps, "execute")
    rep.update(writes_performed=0, run_id=None, exact_keys_written=[], object_key=ctx.get("object_key"))
    if ctx["blockers"]:
        rep["error"] = "BLOCKED_BEFORE_ANY_WRITE"
        return rep, EXIT_FAILED
    run_id = new_probe_run_id(now)
    if not run_id.startswith("probe-"):                                                           # defensa: jamas un run que no sea de prueba
        rep["error"] = "BAD_RUN_ID"
        return rep, EXIT_FAILED
    rep["run_id"], prefix = run_id, dbk.prefix(mig, run_id)
    governed, src, item = ctx["governed"], ctx["src"], ctx["item"]
    tree_before = _tree(governed)
    stage_root = Path(tempfile.mkdtemp(prefix="probe-backup-"))
    try:
        if rs.list_backup_keys(prefix):                                                           # inexistencia previa del run
            rep["error"] = "RUN_ALREADY_EXISTS"
            return rep, EXIT_FAILED
        stage = stage_root / "bk"
        gb.create_backup(stage, [{"client": item["client"], "root": item["root"], "path": src}], governed_dir=governed, migration=mig, plan_digest=ctx["plan"]["digest"],
                         environment=ctx["plan"]["environment"], remote_read=rs.read_object_bytes, require_remote=True)
        staged = sorted(p.relative_to(stage).as_posix() for p in stage.rglob("*") if p.is_file() and p.name != "journal.jsonl")
        if len(staged) + 1 > MAX_OBJECTS or any(not (f.startswith("objects/") or f in ("manifest.json", "manifest.sha256")) for f in staged):
            rep["error"] = "UNEXPECTED_BACKUP_FILE_SET"                                           # >4 objetos o un archivo inesperado (p. ej. objects_remote/): aborta SIN escribir en remoto
            rep["staged_files"] = staged
            return rep, EXIT_FAILED
        published = dbk.publish(stage, mig, run_id)                                               # PUT sin sobrescribir + GET por bytes por objeto; sello al final
        keys = rs.list_backup_keys(prefix)
        rep.update(exact_keys_written=keys, writes_performed=len(keys), published={k: published[k] for k in ("sealed", "files", "objects", "manifest_sha256")})
        seal = json.loads(rs.get_backup_object(f"{prefix}/{dbk.SEAL}").decode("utf-8"))
        hash_checks = {rel: _sha(rs.get_backup_object(f"{prefix}/{rel}")) == h for rel, h in seal["files"].items()}
        fetched = Path(tempfile.mkdtemp(prefix="probe-fetch-", dir=stage_root)) / "f"
        manifest = dbk.fetch(mig, run_id, fetched)                                                # re-descarga verificando sello, manifiesto y cada hash
        verify = gb.verify_backup(fetched)
        local_after, remote_after = src.read_bytes(), rs.read_object_bytes(src)
        rep["verifications"] = {
            "key_count_is_4": len(keys) == MAX_OBJECTS, "keys_all_under_probe_run": all(k.startswith(prefix + "/") for k in keys), "sealed": bool(published["sealed"]),
            "seal_manifest_sha256_matches_local": seal["manifest_sha256"] == gb.load_manifest(stage)["manifest_sha256"], "hash_checks_all_true": all(hash_checks.values()),
            "hash_checks": hash_checks, "fetched_objects": len(manifest["objects"]), "fetched_verify_problems": verify,
            "original_envelope_sha256_local_before": ctx["original_local_sha256"], "original_envelope_unchanged_local": _sha(local_after) == ctx["original_local_sha256"],
            "original_envelope_unchanged_remote": remote_after is not None and _sha(remote_after) == ctx["original_remote_sha256"],
            "governed_store_files": len(tree_before), "governed_store_tree_unchanged": _tree(governed) == tree_before,
        }
        v = rep["verifications"]
        ok = (v["key_count_is_4"] and v["keys_all_under_probe_run"] and v["sealed"] and v["seal_manifest_sha256_matches_local"] and v["hash_checks_all_true"] and v["fetched_objects"] == 1
              and not v["fetched_verify_problems"] and v["original_envelope_unchanged_local"] and v["original_envelope_unchanged_remote"] and v["governed_store_tree_unchanged"])
        rep["verdict_ok"] = bool(ok)
        if not ok:
            rep["error"] = "VERIFICATION_FAILED"
        return rep, (EXIT_OK if ok else EXIT_FAILED)
    except Exception as exc:                                                                      # noqa: BLE001 — fail-closed; sin detalle (podria contener rutas/credenciales)
        rep["error"] = getattr(exc, "code", None) or type(exc).__name__
        try:
            rep["exact_keys_written"] = rs.list_backup_keys(prefix)                                # lo parcial queda registrado; NO se borra (un run sin sello es inutilizable)
            rep["writes_performed"] = len(rep["exact_keys_written"])
        except Exception:                                                                         # noqa: BLE001
            rep["exact_keys_written"] = "UNKNOWN"
        return rep, EXIT_FAILED
    finally:
        import shutil
        shutil.rmtree(stage_root, ignore_errors=True)                                             # solo el directorio temporal LOCAL de staging; jamas nada remoto


def _load_deps() -> dict:
    from core import remote_store
    from webapp.services import assessment_backfill as bf, assessment_service as svc, catalog_reads, durable_backup as dbk, governed_backup as gb
    return {"remote_store": remote_store, "svc": svc, "bf": bf, "catalog_reads": catalog_reads, "dbk": dbk, "gb": gb, "MIG": svc.MATERIALIZATION_MIGRATION}


def main(argv: list[str] | None = None, deps: dict | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true", help="(por defecto) solo lectura: informa que haria y si algo lo bloquea")
    g.add_argument("--execute", action="store_true", help=f"escribe UN run probe-* de 4 objetos; exige --confirm {CONFIRM_PHRASE}")
    ap.add_argument("--confirm", default=None, metavar="FRASE", help=f"confirmacion explicita de --execute: debe ser exactamente {CONFIRM_PHRASE}")
    args = ap.parse_args(argv)
    if args.confirm is not None and not args.execute:
        ap.error("--confirm solo tiene sentido con --execute")
    deps = deps or _load_deps()
    rep, code = execute(deps, args.confirm) if args.execute else dry_run(deps)
    rep["exit_code"] = code
    sys.stdout.buffer.write((json.dumps(rep, indent=1, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
