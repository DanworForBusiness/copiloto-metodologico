"""v4_dryrun_report — DRY-RUN de ``evidence_leaf_materialization_v4`` con informe por cliente, digests y VERIFICACION de ausencia de escrituras. Solo lectura, sin opciones de escritura.

    python -m tools.catalog.v4_dryrun_report            # informe completo (JSON por stdout)
    python -m tools.catalog.v4_dryrun_report --brief    # igual, sin la lista de firmas de Juicio por objeto (salida mas corta)

No duplica el framework de migraciones: llama a ``assessment_backfill.migrate_stale_evidence(entries, MIG, apply=False)`` (la MISMA funcion que ejecuta el CLI ``--dry-run``) y reutiliza
``migration_approval.compute_plan_digest``; solo agrega el informe (el mismo del script validado ``v4c_gate_dryrun2.py``) y la evidencia de no-escrituras (la del ``v4_dryrun_prod_wrapper.py``).
NO existe ``--apply`` ni ninguna opcion que escriba: la herramienta nunca invoca la aplicacion asegurada, el commit de sobres, el respaldo ni la restauracion.
Garantias de solo lectura (con tests de entornos aislados, sin acceso real a Spaces):
  * antes y despues se compara el sha256 de cada sobre gobernado local y el listado (clave/ETag/tamaño) del prefijo gobernado y del namespace ``migration-backups`` en Spaces (solo list);
  * durante la corrida el cliente S3 se sustituye por uno de SOLO LECTURA (list/get/head): cualquier otro metodo lanza ``PermissionError`` y queda contado;
  * un audit hook de Python cuenta aperturas de archivo para escritura y mutaciones del filesystem fuera del directorio temporal;
  * el veredicto ``no_writes_verified`` exige todo lo anterior; si falla, la herramienta lo declara y sale con codigo 6.
Fail-closed (no corre el dry-run y sale con 2 si hay incertidumbre previa): lectura remota no habilitada (no se podria verificar el bucket), sin almacen gobernado / sin sobres, sin entradas de Catalogo.
El informe NO contiene secretos, endpoints ni contenido de perfiles: solo conteos, estados, causas, digests y firmas abreviadas (objetos identificados por sha8).
Salida: 0 ok · 1 hay fallas en registros · 2 bloqueado antes de correr · 3 hay abstenciones · 5 error durante la corrida · 6 FALLO el veredicto de no-escrituras."""
from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

EXIT_OK, EXIT_FAILED_RECORDS, EXIT_BLOCKED, EXIT_ABSTENTIONS, EXIT_ERROR, EXIT_WRITES_DETECTED = 0, 1, 2, 3, 5, 6
READ_ONLY_METHODS = frozenset({"list_objects_v2", "get_object", "head_object", "get_paginator", "head_bucket", "list_objects"})
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC
_FS_MUTATIONS = frozenset({"os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.replace", "os.truncate", "os.symlink", "os.link", "shutil.rmtree", "shutil.move", "shutil.copyfile"})


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ───────────────────────────── detector de escrituras ─────────────────────────────
class WriteWatch:
    """Cuenta (no impide) aperturas para escritura y mutaciones de FS fuera del temporal, mientras ``active``. El hook de auditoria de Python no se puede quitar: se instala una sola vez."""
    installed = False
    active = False
    events: collections.Counter = collections.Counter()
    paths: list[str] = []
    tmp = tempfile.gettempdir()

    @classmethod
    def install(cls) -> None:
        if not cls.installed:
            sys.addaudithook(cls._hook)
            cls.installed = True

    @classmethod
    def reset(cls) -> None:
        cls.events, cls.paths = collections.Counter(), []

    @classmethod
    def _hook(cls, event: str, args: tuple) -> None:
        if not cls.active:
            return
        if event == "open":
            path, mode, flags = args
            w = (isinstance(mode, str) and any(c in mode for c in "wax+")) or (isinstance(flags, int) and flags & _WRITE_FLAGS)
            if w and not str(path).endswith(".pyc") and not str(path).startswith(cls.tmp):
                cls.events["file_write_outside_tmp"] += 1
                cls.paths.append(str(path))
        elif event in _FS_MUTATIONS and args and not str(args[0]).startswith(cls.tmp):
            cls.events["fs_mutation_outside_tmp"] += 1
            cls.paths.append(str(args[0]))
        elif event == "socket.connect":
            cls.events["network_connect"] += 1                                                    # informativo: las LECTURAS a Spaces abren conexiones; las escrituras las bloquea el cliente de solo lectura


class ReadOnlyClient:
    """Proxy del cliente S3: solo list/get/head. Cualquier otro metodo (put/upload/delete/copy...) lanza ``PermissionError`` y queda contado."""

    def __init__(self, inner: Any):
        self._inner, self.allowed, self.blocked = inner, collections.Counter(), collections.Counter()

    def __getattr__(self, name: str):
        if name in READ_ONLY_METHODS:
            self.allowed[name] += 1
            return getattr(self._inner, name)

        def refuse(*a: Any, **k: Any):
            self.blocked[name] += 1
            raise PermissionError(f"{name}: BLOQUEADO (dry-run de solo lectura)")
        return refuse


def _local_tree(governed: Path | None) -> dict:
    return {p.relative_to(governed).as_posix(): _sha(p.read_bytes()) for p in sorted(governed.rglob("*")) if p.is_file()} if governed else {}


def _remote_listing(deps: dict) -> dict:
    rs, config = deps["remote_store"], deps["config"]
    out: dict = {}
    client = rs._get_client()
    for root in (rs._key(config.GOVERNED_ASSESSMENTS_NAME) + "/", rs._key(rs.BACKUP_NAMESPACE) + "/"):
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=config.SPACES_BUCKET, Prefix=root):
            for o in page.get("Contents", []) or []:
                out[o["Key"]] = [o.get("ETag"), o.get("Size")]
    return out


# ───────────────────────────── informe (mismo contenido que v4c_gate_dryrun2.py) ─────────────────────────────
def _client_report(records: list[dict], envs: dict, resolutions: dict, captured: dict, svc: Any, bf: Any, sha8: Any, brief: bool) -> dict:
    per: dict[str, dict] = {}
    for r in records:
        client, am = str(r["client"]), str(r["root"])
        c = per.setdefault(client, {"records": 0, "eligible": 0, "pending_migration_compat": 0, "would_regenerate": 0, "states": collections.Counter(), "abstained": collections.Counter(),
                                    "failed": collections.Counter(), "other_reasons": collections.Counter(), "pool_signature": collections.Counter(), "judgment_actions": collections.Counter(),
                                    "judgment_levels_old_new": collections.Counter(), "judgment_signatures": []})
        c["records"] += 1
        c["states"][f"{r['state']}|{r.get('stale_reason')}"] += 1
        if r.get("eligible"):
            c["eligible"] += 1
            c["pending_migration_compat"] += int(r["state"] == svc.CURRENT)
        c["would_regenerate"] += int(r["action"] == bf.WOULD_REGENERATE)
        if r["action"] == bf.ABSTAINED:
            c["abstained"][str(r["reason"])] += 1
        elif r["action"] == bf.FAILED_ACTION:
            c["failed"][str(r["reason"])] += 1
        elif not r.get("eligible"):
            c["other_reasons"][str(r["reason"])] += 1
        env = envs.get((client, am))
        res = next((resolutions[e] for e in r["entry_ids"] if e in resolutions), None)
        c["pool_signature"]["sin_sobre" if env is None else ("no_resuelto" if res is None or res.kind != "OK" else ("coincide" if res.pool_signature == env.get("input_pool_signature") else "DIFIERE"))] += 1
        jd = (r.get("delta") or {}).get("judgment") or {}
        c["judgment_actions"][str(jd.get("judgment_action"))] += 1
        c["judgment_levels_old_new"][f"{jd.get('previous_judgment_level')}->{jd.get('new_judgment_level')}"] += 1
        cand = captured.get((client, am))
        if cand is not None and env is not None and not brief:
            old_j, new_j = env.get("judgment") or {}, cand.envelope.get("judgment") or {}
            c["judgment_signatures"].append({"object": sha8(am), "action": jd.get("judgment_action"), "ruleset_old": old_j.get("ruleset_version"), "ruleset_new": new_j.get("ruleset_version"),
                                             "output_signature_old": str(old_j.get("output_signature"))[:12], "output_signature_new": str(new_j.get("output_signature"))[:12],
                                             "source_profile_signature_old": str((old_j.get("source_assessment") or {}).get("profile_signature"))[:12],
                                             "source_profile_signature_new": str((new_j.get("source_assessment") or {}).get("profile_signature"))[:12]})
    ser = lambda v: dict(v) if isinstance(v, collections.Counter) else v                          # noqa: E731
    return {cl: {k: ser(v) for k, v in c.items() if not (k == "judgment_signatures" and brief)} for cl, c in sorted(per.items())}


def _divergences(per_client: dict, captured_summary: dict) -> list[dict]:
    out = []
    for cl, c in per_client.items():
        if c["pool_signature"].get("DIFIERE"):
            out.append({"client": cl, "kind": "POOL_SIGNATURE_DIFFERS", "count": c["pool_signature"]["DIFIERE"]})
        if c["abstained"]:
            out.append({"client": cl, "kind": "ABSTENTIONS", "causes": c["abstained"]})
        if c["failed"]:
            out.append({"client": cl, "kind": "FAILURES", "causes": c["failed"]})
        odd = {a: n for a, n in c["judgment_actions"].items() if a != "judgment_signature_rebound"}
        if odd:
            out.append({"client": cl, "kind": "JUDGMENT_ACTION_NOT_REBOUND", "actions": odd})
    if captured_summary.get("ruleset_changed"):
        out.append({"client": "*", "kind": "JUDGMENT_RULESET_CHANGED", "count": captured_summary["ruleset_changed"]})
    return out


def _preflight_blockers(deps: dict) -> tuple[list[str], dict]:
    rs, svc, catalog_reads = deps["remote_store"], deps["svc"], deps["catalog_reads"]
    blockers: list[str] = []
    ctx: dict = {}
    if not rs.read_enabled():
        blockers.append("REMOTE_READ_NOT_ENABLED")                                                # sin lectura remota no se puede verificar que el bucket no cambio
    governed = svc.governed_dir()
    if governed is None or not Path(governed).is_dir() or not any(Path(governed).rglob("*.json")):
        blockers.append("NO_GOVERNED_STATE")
    else:
        ctx["governed"] = Path(governed)
    try:
        _m, entries = catalog_reads._catalog_entries()
        ctx["entries"] = list(entries or [])
        if not ctx["entries"]:
            blockers.append("NO_CATALOG_ENTRIES")
    except Exception as exc:                                                                      # noqa: BLE001 — sin detalle
        blockers.append(f"CATALOG_UNAVAILABLE:{type(exc).__name__}")
    return blockers, ctx


def build_report(deps: dict, brief: bool = False) -> tuple[dict, int]:
    rs, svc, bf, ma, MIG = deps["remote_store"], deps["svc"], deps["bf"], deps["ma"], deps["MIG"]
    sha8 = lambda s: _sha(str(s).encode("utf-8"))[:8]                                             # noqa: E731
    env_info = {"governed_dir": str(svc.governed_dir()), "pool_root": str(svc.pool_root()), "catalog_dir": str(svc.catalog_dir()), "mode": rs.describe_mode()}
    blockers, ctx = _preflight_blockers(deps)
    base = {"tool": "v4_dryrun_report", "migration": MIG, "environment": env_info, "apply_flag_used": False, "writes_options_available": False}
    if blockers:
        return {**base, "blocked": True, "blockers": blockers, "dry_run_executed": False}, EXIT_BLOCKED
    governed, entries = ctx["governed"], ctx["entries"]
    WriteWatch.install()
    WriteWatch.reset()
    real_get_client, guard = rs._get_client, None
    try:
        before_local, before_remote = _local_tree(governed), _remote_listing(deps)
        guard = ReadOnlyClient(real_get_client())
        rs._get_client = lambda: guard                                                            # cliente de SOLO LECTURA durante todo el dry-run
        captured: dict = {}

        def capturing_regenerate(entry: Any, migration: str):
            cand = svc.regenerate_stale_evidence(entry, migration)
            captured[(str(cand.client), str(cand.terminal_am))] = cand
            return cand
        WriteWatch.active = True
        try:
            out = bf.migrate_stale_evidence(entries, MIG, apply=False, regenerate=capturing_regenerate)    # LA misma funcion que ejecuta el CLI --dry-run
        finally:
            WriteWatch.active = False
        rs._get_client = real_get_client
        after_local, after_remote = _local_tree(governed), _remote_listing(deps)
        envs = {(str(e.get("client")), str(e.get("terminal_am"))): e for e, _ in svc.iter_governed_envelopes()}
        resolutions = svc.resolve_entries(entries)
        records = [r for r in out["records"] if (str(r["client"]), str(r["root"])) in envs]
        per_client = _client_report(records, envs, resolutions, captured, svc, bf, sha8, brief)
        ruleset_changed = sum(1 for k, cand in captured.items() if ((envs.get(k) or {}).get("judgment") or {}).get("ruleset_version") != (cand.envelope.get("judgment") or {}).get("ruleset_version"))
        plan = out["plan"]
        counts = {k: v for k, v in out["counts"].items() if v}
        zero = {"governed_local_files": len(before_local), "governed_local_tree_identical_before_after": before_local == after_local,
                "remote_listing_entries": len(before_remote), "remote_listing_identical_before_after": before_remote == after_remote,
                "read_only_s3_calls": dict(guard.allowed), "blocked_s3_write_attempts": dict(guard.blocked), "audit_events_during_dry_run": dict(WriteWatch.events),
                "write_paths_outside_tmp": WriteWatch.paths[:10], "apply_flag_used": False}
        zero["no_writes_verified"] = bool(zero["governed_local_tree_identical_before_after"] and zero["remote_listing_identical_before_after"] and not zero["blocked_s3_write_attempts"]
                                         and not WriteWatch.events.get("file_write_outside_tmp") and not WriteWatch.events.get("fs_mutation_outside_tmp") and out["apply"] is False and out["dry_run"] is True)
        report = {**base, "blocked": False, "dry_run_executed": True, "cli_equivalent": f"python -m webapp.services.assessment_backfill --regenerate-stale-evidence {MIG} --dry-run",
                  "counts": counts, "restart": out.get("restart"), "environment_warnings": out["environment_warnings"], "envelopes_total": len(envs), "catalog_entries": len(entries),
                  "plan": {"plan_digest": plan["digest"], "items_digest_env_independent": ma.compute_plan_digest(MIG, "", plan["items"]), "items": len(plan["items"]), "clients": plan["clients"],
                           "by_client_items_digest": {c: ma.compute_plan_digest(MIG, "", [i for i in plan["items"] if i["client"] == c]) for c in plan["clients"]}},
                  "per_client": per_client, "divergences": _divergences(per_client, {"ruleset_changed": ruleset_changed}), "zero_writes_evidence": zero}
        if not zero["no_writes_verified"]:
            return report, EXIT_WRITES_DETECTED
        if counts.get("failed"):
            return report, EXIT_FAILED_RECORDS
        if counts.get("abstained"):
            return report, EXIT_ABSTENTIONS
        return report, EXIT_OK
    except Exception as exc:                                                                      # noqa: BLE001 — fail-closed, sin detalle (podria contener rutas/credenciales)
        rs._get_client = real_get_client
        return {**base, "blocked": False, "dry_run_executed": False, "error": getattr(exc, "code", None) or type(exc).__name__,
                "blocked_s3_write_attempts": dict(guard.blocked) if guard else {}}, EXIT_ERROR
    finally:
        WriteWatch.active = False
        rs._get_client = real_get_client


def _load_deps() -> dict:
    import config
    from core import remote_store
    from webapp.services import assessment_backfill as bf, assessment_service as svc, catalog_reads, migration_approval as ma
    return {"config": config, "remote_store": remote_store, "svc": svc, "bf": bf, "catalog_reads": catalog_reads, "ma": ma, "MIG": svc.MATERIALIZATION_MIGRATION}


def main(argv: list[str] | None = None, deps: dict | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--brief", action="store_true", help="omite la lista de firmas de Juicio por objeto (salida mas corta); no cambia nada mas")
    args = ap.parse_args(argv)
    rep, code = build_report(deps or _load_deps(), brief=args.brief)
    rep["exit_code"] = code
    sys.stdout.buffer.write((json.dumps(rep, indent=1, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
