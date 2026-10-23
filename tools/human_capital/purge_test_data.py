"""purge_test_data — reinicialización CONTROLADA de los datos transaccionales de PRUEBA de Capital Humano (disco local + DigitalOcean Spaces), con respaldo sellado y verificable.

    python -m tools.human_capital.purge_test_data                       # --dry-run (por defecto): SOLO lectura; inventario, diferencias, huérfanos y las frases de confirmación
    python -m tools.human_capital.purge_test_data --backup  --confirm BACKUP-CCHH-<env8>-<digest12>
    python -m tools.human_capital.purge_test_data --execute --confirm PURGE-CCHH-<env8>-<digest12> --owner-ack NO-REAL-DATA-<digest12> --backup-run <run_id> --window-until <AAAA-MM-DDTHH:MM:SSZ>
    python -m tools.human_capital.purge_test_data --verify  --backup-run <run_id>
    python -m tools.human_capital.purge_test_data --restore --backup-run <run_id> [--confirm RESTORE-CCHH-<env8>-<digest12>]

ALCANCE (lista blanca por entidad, bajo `human-capital/`): perfiles, metadatos de documentos de perfil, búsquedas (incluye las solicitudes de Operaciones y las postulaciones, que viven DENTRO de la búsqueda),
entrevistas, onboardings, grillas, evaluaciones y publicaciones, más los temporales de escritura (`.tmp-*`, `*.tmp`) de esas carpetas. PRESERVADO siempre: `catalogs/`, `import-history/` (sus manifiestos tienen su propia política
de retención), todo archivo desconocido y todo lo que esté fuera de `human-capital/` (configuración, bitácora general, otras verticales). NO se importa ni se usa Graph: los CVs y carpetas de SharePoint no se tocan.

CONTROLES: modo remoto obligatorio y bucket/prefijo/namespace verificados; inventario local+Spaces con contenido; respaldo de TODO lo que se va a borrar (incluido contenido solo remoto y versiones remotas distintas) publicado SELLADO
en `migration-backups/cchh_reset/<run_id>/` y re-descargado verificando hashes; el MANIFIESTO SELLADO es la fuente de verdad de la ejecución y la reanudación (lo ausente cuenta como completado; lo presente debe conservar
EXACTAMENTE los hashes aprobados; cualquier objeto nuevo o modificado bloquea); confirmaciones derivadas del entorno y del inventario; ventana de mantenimiento acotada, quietud y detección de escrituras concurrentes; borrado
POR OBJETO (Spaces y luego disco), verificado, idempotente y reanudable, jamás de directorios; `--restore` protegido (solo desde un respaldo sellado, sin sobrescribir, sin rollback automático) y `--verify` posterior (disco y Spaces,
tras la ventana de sincronización). La herramienta DETECTA deriva y escrituras concurrentes pero NO puede impedir que otro proceso escriba: la ventana de mantenimiento es un acuerdo operativo, no un bloqueo técnico.

SALIDA: un JSON SIN datos personales ni secretos (conteos, hashes truncados, clases de error). Códigos: 0 ok · 2 bloqueado · 3 verificación pendiente/inconsistente · 4 confirmación faltante o inválida (cero cambios) ·
5 falla durante la ejecución/restauración (reanudable) · 6 deriva o escrituras concurrentes (no se borra nada más) · 7 verificación fallida (residuos)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

MIGRATION = "cchh_reset"
PERSIST_NAME = "human-capital"
TOOL_SCHEMA = "cchh-reset-approval/v1"
QUIET_MINUTES = 10
SNAPSHOT_GAP_S = 20
WINDOW_MAX_MINUTES = 60
VERIFY_AFTER_S = 150
RETENTION_DAYS = 14
DELETE_VERIFY_ATTEMPTS = 3
EXIT_OK, EXIT_BLOCKED, EXIT_PENDING, EXIT_NOT_CONFIRMED, EXIT_FAILED, EXIT_DRIFT, EXIT_VERIFY_FAILED = 0, 2, 3, 4, 5, 6, 7

# Orden de borrado: dependientes primero.
ENTITY_ORDER = ("scoring-evaluations", "scoring-grids", "onboarding", "entrevistas", "busquedas", "profile-documents", "profiles", "publicaciones")
_FLAT = ("scoring-grids", "onboarding", "entrevistas", "busquedas", "profile-documents", "profiles", "publicaciones")
_ID = r"[A-Za-z0-9_-]{1,64}"
_FLAT_RE = re.compile(rf"^(?P<entity>{'|'.join(_FLAT)})/(?P<name>{_ID})\.json$")
_EVAL_RE = re.compile(rf"^(?P<entity>scoring-evaluations)/(?P<bid>{_ID})/(?P<name>{_ID})\.json$")
_TEMP_RE = re.compile(rf"^(?P<entity>{'|'.join(ENTITY_ORDER)})/(?:{_ID}/)?(?:\.tmp-[^/]+|[^/]+\.tmp)$")
PRESERVED_DIRS = ("catalogs", "import-history")
ISOLATION_NOTE = ("La herramienta detecta deriva y escrituras concurrentes, pero no puede impedir que otro proceso escriba: la ventana de mantenimiento es un acuerdo operativo y este control no afirma aislamiento absoluto.")


# ═══════════════════ utilidades puras ═══════════════════
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ref(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()[:8]


def classify(rel: str) -> tuple[str, str | None]:
    """`(clase, entidad)`: `purge` (objeto transaccional de la lista blanca), `temp` (temporal de escritura de esas carpetas), `preserved` (catálogos, import-history) o `unknown` (todo lo demás)."""
    if _FLAT_RE.match(rel):
        return "purge", _FLAT_RE.match(rel).group("entity")
    if _EVAL_RE.match(rel):
        return "purge", "scoring-evaluations"
    m = _TEMP_RE.match(rel)
    if m:
        return "temp", m.group("entity")
    if rel.split("/", 1)[0] in PRESERVED_DIRS:
        return "preserved", None
    return "unknown", None


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def environment(deps: dict) -> dict:
    """Hechos NO secretos del entorno y su huella (`env8`). El bucket y el prefijo son nombres, no credenciales; el endpoint se reduce al host."""
    cfg = deps["config"]
    host = urllib.parse.urlsplit(getattr(cfg, "SPACES_ENDPOINT", "") or "").hostname or ""
    facts = {"mode": str(getattr(cfg, "PERSISTENCE_MODE", "")), "bucket": str(getattr(cfg, "SPACES_BUCKET", "") or ""), "prefix": str(getattr(cfg, "SPACES_PREFIX", "") or ""),
             "endpoint_host": host, "base_dir": Path(cfg.BASE_DIR).resolve().as_posix(), "persist_paths": sorted(str(p) for p in getattr(cfg, "PERSIST_PATHS", []))}
    facts["env8"] = _sha(json.dumps(facts, sort_keys=True).encode("utf-8"))[:8]
    return facts


def preflight(deps: dict, *, need_write: bool = True) -> tuple[dict, list[str]]:
    rs, env = deps["remote_store"], environment(deps)
    blockers: list[str] = []
    if env["mode"] != "remote":
        blockers.append("MODE_NOT_REMOTE")
    if not rs.read_enabled():
        blockers.append("REMOTE_READ_NOT_ENABLED")
    if need_write and not rs.write_enabled():
        blockers.append("REMOTE_WRITE_NOT_ENABLED")
    if not env["bucket"]:
        blockers.append("BUCKET_NOT_SET")
    if PERSIST_NAME not in env["persist_paths"]:
        blockers.append("HC_NOT_IN_PERSIST_PATHS")
    if rs.backup_namespace_collision():
        blockers.append("NAMESPACE_COLLISION")
    env["namespace_collision"] = "NAMESPACE_COLLISION" in blockers
    return env, blockers


# ═══════════════════ inventario ═══════════════════
def _root(deps: dict) -> Path:
    return Path(deps["config"].BASE_DIR) / PERSIST_NAME


def _local_files(deps: dict) -> dict[str, dict]:
    root = _root(deps)
    out: dict[str, dict] = {}
    if root.is_dir():
        for path in root.rglob("*"):
            if path.is_file() and not path.is_symlink():
                st = path.stat()
                out[path.relative_to(root).as_posix()] = {"size": st.st_size, "mtime": st.st_mtime}
    return out


def _extract_refs(entity: str, rel: str, data: bytes) -> dict:
    """Solo los identificadores necesarios para detectar huérfanos; nunca se conserva el documento."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(doc, dict):
        return {}
    stem = rel.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if entity == "profiles":
        return {"id": doc.get("profile_id") or stem, "deleted": bool(doc.get("deleted_at"))}
    if entity == "busquedas":
        return {"id": doc.get("busqueda_id") or stem, "apps": [(a.get("application_id"), a.get("profile_id")) for a in (doc.get("candidatos") or []) if isinstance(a, dict)]}
    if entity == "entrevistas":
        return {"profile": doc.get("profile_id"), "busqueda": doc.get("busqueda_id"), "app": doc.get("application_id")}
    if entity == "onboarding":
        return {"app": doc.get("application_id") or stem, "profile": doc.get("profile_id"), "busqueda": doc.get("busqueda_id")}
    if entity == "profile-documents":
        return {"profile": doc.get("profile_id")}
    if entity == "scoring-evaluations":
        return {"busqueda": rel.split("/")[1], "app": doc.get("application_id")}
    if entity == "scoring-grids":
        return {"busqueda": stem}
    if entity == "publicaciones":
        return {"busqueda": doc.get("busqueda_id")}
    return {}


def _find_orphans(objs: dict[str, dict]) -> dict:
    profiles = {o["refs"].get("id"): o["refs"] for o in objs.values() if o["entity"] == "profiles" and o.get("refs")}
    live_profiles = {pid for pid, r in profiles.items() if not r.get("deleted")}
    searches = {o["refs"].get("id"): {a for a, _ in o["refs"].get("apps", [])} for o in objs.values() if o["entity"] == "busquedas" and o.get("refs")}
    dangling_apps = sum(1 for o in objs.values() if o["entity"] == "busquedas" for _a, p in (o.get("refs") or {}).get("apps", []) if p not in live_profiles)
    count = lambda entity, pred: sum(1 for o in objs.values() if o["entity"] == entity and o.get("refs") is not None and pred(o["refs"]))     # noqa: E731
    app_ok = lambda r: (r.get("busqueda") in searches) and (not r.get("app") or r["app"] in searches[r["busqueda"]])                           # noqa: E731
    return {
        "entrevistas": count("entrevistas", lambda r: r.get("profile") not in live_profiles or (r.get("busqueda") is not None and not app_ok(r))),
        "onboarding": count("onboarding", lambda r: r.get("profile") not in live_profiles or not app_ok(r)),
        "scoring_evaluations": count("scoring-evaluations", lambda r: not app_ok(r)),
        "scoring_grids": count("scoring-grids", lambda r: r.get("busqueda") not in searches),
        "profile_documents": count("profile-documents", lambda r: r.get("profile") not in live_profiles),
        "publicaciones_with_missing_busqueda": count("publicaciones", lambda r: r.get("busqueda") is not None and r["busqueda"] not in searches),
        "applications_with_missing_or_deleted_profile": dangling_apps,
        "profiles_soft_deleted": sum(1 for r in profiles.values() if r.get("deleted")),
    }


def take_inventory(deps: dict, *, with_content: bool = True) -> dict:
    """Inventario local + Spaces de `human-capital/`. Con `with_content` lee y hashea (local y remoto) los objetos de la lista blanca. Lanza `RemoteReadError` si el listado remoto no se completa."""
    rs = deps["remote_store"]
    local = _local_files(deps)
    remote = rs.list_persisted_objects(PERSIST_NAME)
    root = _root(deps)
    objs: dict[str, dict] = {}
    preserved_local: dict[str, str] = {}
    preserved_remote = {}
    unknown = {"local": 0, "remote": 0, "top_level": {}}
    for rel in sorted(set(local) | set(remote)):
        kind, entity = classify(rel)
        if kind in ("preserved", "unknown"):
            if kind == "preserved":
                if rel in local and with_content:
                    preserved_local[rel] = _sha((root / rel).read_bytes())
                if rel in remote:
                    preserved_remote[rel] = remote[rel].get("etag")
            else:
                for side, present in (("local", rel in local), ("remote", rel in remote)):
                    unknown[side] += int(present)
                top = rel.split("/", 1)[0] if "/" in rel else "<raíz>"
                top = top if re.fullmatch(r"[a-z0-9_-]{1,40}", top) else "<otro>"
                unknown["top_level"][top] = unknown["top_level"].get(top, 0) + 1
            continue
        o = {"rel": rel, "kind": kind, "entity": entity, "local": None, "remote": None, "refs": None}
        if rel in local:
            o["local"] = {**local[rel], "sha": None}
        if rel in remote:
            o["remote"] = {**remote[rel], "sha": None}
        if with_content:
            data_for_refs = None
            if o["local"]:
                data = (root / rel).read_bytes()
                o["local"]["sha"], data_for_refs = _sha(data), data
            if o["remote"]:
                data = rs.read_object_bytes(root / rel)
                if data is None:
                    raise deps["remote_error"]("objeto remoto desapareció durante el inventario")
                o["remote"]["sha"] = _sha(data)
                data_for_refs = data_for_refs if data_for_refs is not None else data
            if data_for_refs is not None and kind == "purge":
                o["refs"] = _extract_refs(entity, rel, data_for_refs)
        objs[rel] = o
    return {"objects": objs, "preserved_local": preserved_local, "preserved_remote": preserved_remote, "unknown": unknown}


def plan_digest(env8: str, objs: dict) -> str:
    lines = sorted(f"{o['entity']}|{rel}|{(o['local'] or {}).get('sha')}|{(o['remote'] or {}).get('sha')}" for rel, o in objs.items())
    return _sha((env8 + "\n" + "\n".join(lines)).encode("utf-8"))


def approved_map(objs: dict) -> dict:
    return {rel: {"entity": o["entity"], "local": (o["local"] or {}).get("sha"), "remote": (o["remote"] or {}).get("sha")} for rel, o in objs.items()}


def counts(objs: dict) -> dict:
    out: dict = {}
    for entity in ENTITY_ORDER:
        mine = [o for o in objs.values() if o["entity"] == entity and o["kind"] == "purge"]
        out[entity] = {"total": len(mine), "both": sum(1 for o in mine if o["local"] and o["remote"]), "local_only": sum(1 for o in mine if o["local"] and not o["remote"]),
                       "remote_only": sum(1 for o in mine if o["remote"] and not o["local"]),
                       "differ": sum(1 for o in mine if o["local"] and o["remote"] and o["local"]["sha"] != o["remote"]["sha"]),
                       "bytes": sum(((o["local"] or o["remote"] or {}).get("size") or 0) for o in mine)}
    temps = [o for o in objs.values() if o["kind"] == "temp"]
    out["temporales"] = {"total": len(temps), "local": sum(1 for o in temps if o["local"]), "remote": sum(1 for o in temps if o["remote"])}
    return out


def _times(o: dict) -> list[datetime]:
    out = []
    if o["local"] and o["local"].get("mtime") is not None:
        out.append(datetime.fromtimestamp(o["local"]["mtime"], tz=timezone.utc))
    if o["remote"]:
        t = _parse_iso(o["remote"].get("last_modified"))
        if t:
            out.append(t)
    return out


def recent_writes(objs: dict, now: datetime) -> int:
    limit = timedelta(minutes=QUIET_MINUTES)
    return sum(1 for o in objs.values() if any(now - t < limit for t in _times(o)))


def metadata_snapshot(objs: dict) -> list:
    return sorted((rel, (o["local"] or {}).get("size"), (o["local"] or {}).get("mtime"), (o["remote"] or {}).get("size"), (o["remote"] or {}).get("etag"), (o["remote"] or {}).get("last_modified")) for rel, o in objs.items())


def phrases(env8: str, digest12: str) -> dict:
    return {"backup": f"BACKUP-CCHH-{env8}-{digest12}", "purge": f"PURGE-CCHH-{env8}-{digest12}", "owner_ack": f"NO-REAL-DATA-{digest12}", "restore": f"RESTORE-CCHH-{env8}-{digest12}"}


def _base_report(mode: str, env: dict, blockers: list[str]) -> dict:
    return {"tool": "purge_test_data", "mode": mode, "environment": {k: env[k] for k in ("mode", "bucket", "prefix", "endpoint_host", "base_dir", "env8")}, "blockers": blockers, "isolation_note": ISOLATION_NOTE,
            "scope": {"purged": list(ENTITY_ORDER), "preserved": list(PRESERVED_DIRS) + ["archivos desconocidos", "todo fuera de human-capital/ (configuración, bitácora, otras verticales)"], "graph_operations": 0, "sharepoint_deletes": 0}}


# ═══════════════════ dry-run ═══════════════════
def dry_run(deps: dict) -> tuple[dict, int]:
    env, blockers = preflight(deps, need_write=False)
    rep = _base_report("dry_run", env, blockers)
    rep["writes_performed"] = 0
    if blockers:
        return rep, EXIT_BLOCKED
    try:
        inv = take_inventory(deps)
    except Exception as exc:                                                                     # noqa: BLE001 — sin detalle: puede traer rutas
        rep["blockers"] = blockers + ["LISTING_FAILED:" + type(exc).__name__]
        return rep, EXIT_BLOCKED
    objs = inv["objects"]
    digest = plan_digest(env["env8"], objs)
    now = deps["now"]()
    rep.update({"counts": counts(objs), "totals": {"objects": len(objs), "local": sum(1 for o in objs.values() if o["local"]), "remote": sum(1 for o in objs.values() if o["remote"])},
                "inconsistencies": {"local_only": sum(1 for o in objs.values() if o["local"] and not o["remote"]), "remote_only": sum(1 for o in objs.values() if o["remote"] and not o["local"]),
                                    "differ": sum(1 for o in objs.values() if o["local"] and o["remote"] and o["local"]["sha"] != o["remote"]["sha"])},
                "orphans": _find_orphans(objs),
                "preserved": {"catalogs_local": sum(1 for r in inv["preserved_local"] if r.startswith("catalogs/")), "import_history_local": sum(1 for r in inv["preserved_local"] if r.startswith("import-history/")),
                              "preserved_remote_objects": len(inv["preserved_remote"])},
                "unknown_files": inv["unknown"], "recent_writes_last_%d_min" % QUIET_MINUTES: recent_writes(objs, now),
                "plan_digest": digest, "digest12": digest[:12], "confirmations": phrases(env["env8"], digest[:12]),
                "retention": {"backup_recommended_days": RETENTION_DAYS, "automatic_backup_deletion": False}})
    if not objs:
        rep["note"] = "No hay objetos transaccionales para purgar."
    return rep, EXIT_OK


# ═══════════════════ backup ═══════════════════
def _quiet_and_stable(deps: dict, objs: dict, rep: dict) -> int | None:
    """Quietud + dos inventarios de metadatos separados 20 s idénticos. Devuelve un código de salida si falla."""
    now = deps["now"]()
    recent = recent_writes(objs, now)
    rep["recent_writes_last_%d_min" % QUIET_MINUTES] = recent
    if recent:
        rep["error"] = "RECENT_WRITES"
        return EXIT_DRIFT
    first = metadata_snapshot(objs)
    deps["sleep"](SNAPSHOT_GAP_S)
    second = metadata_snapshot(take_inventory(deps, with_content=False)["objects"])
    rep["snapshots_equal"] = first == second
    if first != second:
        rep["error"] = "CONCURRENT_WRITES_DETECTED"
        return EXIT_DRIFT
    return None


def do_backup(deps: dict, confirm: str | None) -> tuple[dict, int]:
    env, blockers = preflight(deps)
    rep = _base_report("backup", env, blockers)
    rep["writes_performed"] = 0
    if blockers:
        return rep, EXIT_BLOCKED
    if not confirm:
        rep["error"] = "CONFIRMATION_REQUIRED"
        return rep, EXIT_NOT_CONFIRMED
    rs, dbk, gb = deps["remote_store"], deps["dbk"], deps["gb"]
    try:
        inv = take_inventory(deps)
    except Exception as exc:                                                                     # noqa: BLE001
        rep["error"] = "LISTING_FAILED:" + type(exc).__name__
        return rep, EXIT_BLOCKED
    objs = inv["objects"]
    digest = plan_digest(env["env8"], objs)
    expected = phrases(env["env8"], digest[:12])["backup"]
    if confirm != expected:
        shape = re.fullmatch(rf"BACKUP-CCHH-{env['env8']}-[0-9a-f]{{12}}", confirm or "")
        rep["error"] = "INVENTORY_CHANGED_SINCE_DRY_RUN" if shape else "CONFIRMATION_INVALID"
        rep["required"] = "--confirm <frase del dry-run actual>"
        return rep, (EXIT_DRIFT if shape else EXIT_NOT_CONFIRMED)
    if not objs:
        rep["error"] = "NOTHING_TO_BACK_UP"
        return rep, EXIT_BLOCKED
    code = _quiet_and_stable(deps, objs, rep)
    if code:
        return rep, code
    run_id = dbk.new_run_id(digest)
    base = dbk.prefix(MIGRATION, run_id)
    rep["run_id"] = run_id
    root = _root(deps)
    work = Path(tempfile.mkdtemp(prefix="cchh-reset-"))
    try:
        stage, dest, fetched = work / "stage", work / "backup", work / "fetched"
        stage.mkdir()
        targets = []
        for rel in sorted(objs):                                                                 # contenido a respaldar: el local si existe; si no, el remoto (objetos SOLO remotos)
            o = objs[rel]
            if o["local"]:
                data = (root / rel).read_bytes()
                expected_sha = o["local"]["sha"]
            else:
                data = rs.read_object_bytes(root / rel)
                expected_sha = o["remote"]["sha"]
            if data is None or _sha(data) != expected_sha:
                rep["error"] = "OBJECT_CHANGED_DURING_BACKUP"
                return rep, EXIT_DRIFT
            target = stage / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            targets.append({"client": o["entity"], "root": rel, "path": target})

        def remote_read(path: Path):
            return rs.read_object_bytes(root / Path(path).resolve().relative_to(stage.resolve()).as_posix())

        manifest = gb.create_backup(dest, targets, governed_dir=stage, migration=MIGRATION, plan_digest=digest, environment=env["env8"], remote_read=remote_read, require_remote=True)
        until = deps["now"]() + timedelta(days=RETENTION_DAYS)
        approval = {"schema": TOOL_SCHEMA, "env8": env["env8"], "digest12": digest[:12], "plan_digest": digest, "created_at": _iso(deps["now"]()), "retention_days": RETENTION_DAYS,
                    "retention_recommended_until": _iso(until), "automatic_backup_deletion": False, "counts": counts(objs), "objects": approved_map(objs),
                    "preserved": inv["preserved_local"], "unknown": {"local": inv["unknown"]["local"], "remote": inv["unknown"]["remote"]}, "owner_must_confirm_no_real_data": True}
        published = dbk.publish(dest, MIGRATION, run_id, approval=approval)
        verified = dbk.fetch(MIGRATION, run_id, fetched)                                          # re-descarga verificando sello, manifiesto y cada hash
        fetched_approval = json.loads((fetched / "approval.json").read_text(encoding="utf-8"))
        keys_ok = {o["key"] for o in verified["objects"]} == set(objs) and fetched_approval["objects"] == approved_map(objs) and fetched_approval["plan_digest"] == digest
        problems = gb.verify_backup(fetched)
        rep.update({"writes_performed": published["files"], "published": {k: published[k] for k in ("sealed", "files", "objects", "manifest_sha256")}, "prefix": base,
                    "verification": {"fetched_objects": len(verified["objects"]), "manifest_matches_inventory": keys_ok, "verify_problems": len(problems),
                                     "remote_only_objects_included": sum(1 for o in objs.values() if o["remote"] and not o["local"]),
                                     "differing_remote_versions_included": sum(1 for o in verified["objects"] if o.get("remote_state") == "DIFFERS")},
                    "retention": {"backup_recommended_days": RETENTION_DAYS, "recommended_until": _iso(until), "automatic_backup_deletion": False},
                    "next": {"execute": f"--execute --confirm {phrases(env['env8'], digest[:12])['purge']} --owner-ack {phrases(env['env8'], digest[:12])['owner_ack']} --backup-run {run_id} --window-until <UTC>"}})
        if not keys_ok or problems:
            rep["error"] = "BACKUP_VERIFICATION_FAILED"
            return rep, EXIT_FAILED
        return rep, EXIT_OK
    except Exception as exc:                                                                     # noqa: BLE001 — fail-closed; un run sin sello es inutilizable e inocuo
        rep["error"] = getattr(exc, "code", None) or type(exc).__name__
        return rep, EXIT_FAILED
    finally:
        shutil.rmtree(work, ignore_errors=True)                                                  # solo el directorio temporal LOCAL (contiene el staging con los datos); jamás nada remoto


# ═══════════════════ ejecución ═══════════════════
def _load_run(deps: dict, run_id: str, work: Path) -> tuple[dict, dict]:
    """Baja el run SELLADO y verificado; devuelve `(manifiesto, aprobación)`. Lanza `DurableBackupError` si no está sellado o no es íntegro."""
    dbk = deps["dbk"]
    fetched = work / "fetched"
    manifest = dbk.fetch(MIGRATION, run_id, fetched)
    approval = json.loads((fetched / "approval.json").read_text(encoding="utf-8"))
    if approval.get("schema") != TOOL_SCHEMA or approval.get("plan_digest") != manifest.get("plan_digest"):
        raise deps["backup_error"]("APPROVAL_MISMATCH")
    return manifest, approval


def _journal(deps: dict, run_id: str, event: dict) -> None:
    dbk, rs = deps["dbk"], deps["remote_store"]
    existing = rs.list_backup_keys(f"{dbk.prefix(MIGRATION, run_id)}/journal")
    dbk.publish_event(MIGRATION, run_id, len(existing) + 1, event)


def classify_state(current: dict, approved: dict) -> dict:
    """Estado de la ejecución según el MANIFIESTO SELLADO (fuente de verdad): ausente = completado; presente = debe conservar exactamente el hash aprobado de ese lado; objeto nuevo, lado nuevo o
    contenido distinto = deriva (bloquea)."""
    done, pending, drift, new = [], [], [], []
    for rel in sorted(set(approved) | set(current)):
        if rel not in approved:
            new.append(rel)
            continue
        o, want = current.get(rel), approved[rel]
        if o is None or (not o["local"] and not o["remote"]):
            done.append(rel)
            continue
        ok = True
        for side in ("local", "remote"):
            have = (o[side] or {}).get("sha") if o[side] else None
            if have is not None and have != want[side]:
                ok = False                                                                       # modificado, o apareció de nuevo un lado que no estaba aprobado (want None)
        (pending if ok else drift).append(rel)
    return {"done": done, "pending": pending, "drift": drift, "new": new}


def _delete_verified(deps: dict, path: Path, rel: str) -> None:
    rs, sleep = deps["remote_store"], deps["sleep"]
    if not rs.delete_path(path):
        raise deps["purge_error"]("REMOTE_DELETE_FAILED")
    for attempt in range(DELETE_VERIFY_ATTEMPTS):
        if not rs.persisted_object_exists(path):
            return
        sleep(1.0 * (attempt + 1))
    raise deps["purge_error"]("REMOTE_DELETE_NOT_CONFIRMED")


def do_execute(deps: dict, args) -> tuple[dict, int]:
    env, blockers = preflight(deps)
    rep = _base_report("execute", env, blockers)
    rep.update(deleted_now=0, writes_performed=0)
    if blockers:
        return rep, EXIT_BLOCKED
    now = deps["now"]()
    window = _parse_iso(args.window_until)
    if not (args.confirm and args.owner_ack and args.backup_run and window):
        rep["error"] = "CONFIRMATION_REQUIRED"
        rep["required"] = "--confirm, --owner-ack, --backup-run y --window-until (AAAA-MM-DDTHH:MM:SSZ)"
        return rep, EXIT_NOT_CONFIRMED
    if not now < window <= now + timedelta(minutes=WINDOW_MAX_MINUTES):
        rep["error"] = "WINDOW_INVALID"
        rep["window"] = {"max_minutes": WINDOW_MAX_MINUTES, "must_be_future": True}
        return rep, EXIT_NOT_CONFIRMED
    rs = deps["remote_store"]
    work = Path(tempfile.mkdtemp(prefix="cchh-reset-"))
    try:
        try:
            manifest, approval = _load_run(deps, args.backup_run, work)
        except Exception as exc:                                                                 # noqa: BLE001
            rep["error"] = "BACKUP_NOT_SEALED_OR_INVALID:" + (getattr(exc, "code", None) or type(exc).__name__)
            return rep, EXIT_NOT_CONFIRMED
        if approval["env8"] != env["env8"]:
            rep["error"] = "ENV_MISMATCH"
            return rep, EXIT_NOT_CONFIRMED
        want = phrases(env["env8"], approval["digest12"])
        if args.confirm != want["purge"] or args.owner_ack != want["owner_ack"]:
            rep["error"] = "CONFIRMATION_INVALID"
            return rep, EXIT_NOT_CONFIRMED
        approved = approval["objects"]
        rep["backup_run"] = {"run_id": args.backup_run, "sealed": True, "objects": len(approved), "digest12": approval["digest12"]}
        try:
            inv = take_inventory(deps)
        except Exception as exc:                                                                 # noqa: BLE001
            rep["error"] = "LISTING_FAILED:" + type(exc).__name__
            return rep, EXIT_BLOCKED
        state = classify_state(inv["objects"], approved)
        rep["state_before"] = {k: len(v) for k, v in state.items()}
        if state["drift"] or state["new"]:
            rep["error"] = "DRIFT_FROM_APPROVED_MANIFEST"                                        # nuevo o modificado: se bloquea la continuación; no se borra nada
            return rep, EXIT_DRIFT
        current = {rel: inv["objects"][rel] for rel in state["pending"]}
        code = _quiet_and_stable(deps, current, rep) if current else None
        if code:
            return rep, code
        started_at = deps["now"]()
        _journal(deps, args.backup_run, {"event": "execute_started", "at": _iso(started_at), "window_until": args.window_until, "pending": len(state["pending"]), "already_absent": len(state["done"])})
        root = _root(deps)
        order = {e: i for i, e in enumerate(ENTITY_ORDER)}
        deleted = 0
        try:
            for rel in sorted(state["pending"], key=lambda r: (order[approved[r]["entity"]], r)):
                if deps["now"]() > window:
                    rep["error"] = "WINDOW_EXPIRED"
                    rep["deleted_now"] = deleted
                    return rep, EXIT_FAILED
                path, o, want_o = root / rel, current[rel], approved[rel]
                if o["local"]:                                                                   # verificación por objeto contra el manifiesto, justo antes de borrar
                    if not path.is_file() or _sha(path.read_bytes()) != want_o["local"]:
                        raise deps["drift_error"]("LOCAL_CHANGED")
                if o["remote"]:
                    data = rs.read_object_bytes(path)
                    if data is None or _sha(data) != want_o["remote"]:
                        raise deps["drift_error"]("REMOTE_CHANGED")
                    _delete_verified(deps, path, rel)                                            # Spaces primero (evita que `restore()` lo reponga) y luego el disco
                if o["local"]:
                    path.unlink()
                    if path.exists():
                        raise deps["purge_error"]("LOCAL_DELETE_NOT_CONFIRMED")
                deleted += 1
        except deps["drift_error"] as exc:
            rep.update(error=str(exc) or "DRIFT", deleted_now=deleted)
            return rep, EXIT_DRIFT
        except Exception as exc:                                                                 # noqa: BLE001
            rep.update(error=getattr(exc, "code", None) or str(exc) or type(exc).__name__, deleted_now=deleted, resumable=True)
            return rep, EXIT_FAILED
        eval_root = root / "scoring-evaluations"                                                  # solo carpetas VACÍAS de evaluaciones (os.rmdir); jamás un borrado de directorio
        if eval_root.is_dir():
            for d in sorted(eval_root.iterdir()):
                if d.is_dir():
                    try:
                        os.rmdir(d)
                    except OSError:
                        pass
        final = take_inventory(deps)
        after = classify_state(final["objects"], approved)
        rep["state_after"] = {k: len(v) for k, v in after.items()}
        rep["deleted_now"] = deleted
        if after["new"] or after["drift"]:
            rep["error"] = "OBJECTS_APPEARED_OR_CHANGED_DURING_PURGE"
            return rep, EXIT_DRIFT
        if after["pending"]:
            rep.update(error="OBJECTS_REMAIN", resumable=True)
            return rep, EXIT_FAILED
        finished_at = deps["now"]()
        _journal(deps, args.backup_run, {"event": "execute_finished", "finished_at": _iso(finished_at), "deleted_now": deleted, "already_absent": len(state["done"])})
        rep["finished_at"] = _iso(finished_at)
        rep["verify_not_before"] = _iso(finished_at + timedelta(seconds=VERIFY_AFTER_S))
        rep["next"] = {"verify": f"--verify --backup-run {args.backup_run}", "wait_seconds_before_final_verify": VERIFY_AFTER_S}
        return rep, EXIT_OK
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ═══════════════════ verificación ═══════════════════
def _finished_at(deps: dict, run_id: str) -> datetime | None:
    dbk, rs = deps["dbk"], deps["remote_store"]
    latest = None
    for key in rs.list_backup_keys(f"{dbk.prefix(MIGRATION, run_id)}/journal"):
        raw = rs.get_backup_object(key)
        ev = json.loads(raw.decode("utf-8")) if raw else {}
        if ev.get("event") == "execute_finished":
            moment = _parse_iso(ev.get("finished_at"))
            if moment and (latest is None or moment > latest):
                latest = moment
    return latest


def do_verify(deps: dict, run_id: str | None) -> tuple[dict, int]:
    env, blockers = preflight(deps, need_write=False)
    rep = _base_report("verify", env, blockers)
    rep["writes_performed"] = 0
    if blockers:
        return rep, EXIT_BLOCKED
    work = Path(tempfile.mkdtemp(prefix="cchh-reset-"))
    try:
        approval = None
        finished = None
        if run_id:
            try:
                _manifest, approval = _load_run(deps, run_id, work)
            except Exception as exc:                                                             # noqa: BLE001
                rep["error"] = "BACKUP_NOT_SEALED_OR_INVALID:" + (getattr(exc, "code", None) or type(exc).__name__)
                return rep, EXIT_BLOCKED
            if approval["env8"] != env["env8"]:
                rep["error"] = "ENV_MISMATCH"
                return rep, EXIT_BLOCKED
            finished = _finished_at(deps, run_id)
        try:
            inv = take_inventory(deps)
        except Exception as exc:                                                                 # noqa: BLE001
            rep["error"] = "LISTING_FAILED:" + type(exc).__name__
            return rep, EXIT_BLOCKED
        objs = inv["objects"]
        residual = {"local": sum(1 for o in objs.values() if o["local"]), "remote": sum(1 for o in objs.values() if o["remote"]), "by_entity": {e: sum(1 for o in objs.values() if o["entity"] == e) for e in ENTITY_ORDER if any(o["entity"] == e for o in objs.values())}}
        rep["residual_objects"] = residual
        rep["filesystem_empty"] = residual["local"] == 0
        rep["spaces_empty"] = residual["remote"] == 0
        preserved = {"missing": 0, "changed": 0, "checked": 0}
        if approval:
            for rel, sha in (approval.get("preserved") or {}).items():
                preserved["checked"] += 1
                if rel not in inv["preserved_local"]:
                    preserved["missing"] += 1
                elif inv["preserved_local"][rel] != sha:
                    preserved["changed"] += 1                                                    # advertencia: pudo editarse legítimamente después de la purga
        rep["preserved"] = preserved
        rep["unknown_files"] = inv["unknown"]
        was = (approval or {}).get("unknown") or {}
        unknown_missing = max(0, was.get("local", 0) - inv["unknown"]["local"]) + max(0, was.get("remote", 0) - inv["unknown"]["remote"])
        rep["unknown_files"]["missing_since_backup"] = unknown_missing
        if residual["local"] or residual["remote"] or preserved["missing"] or unknown_missing:
            rep["error"] = "RESIDUE_OR_PRESERVED_MISSING"
            return rep, EXIT_VERIFY_FAILED
        smoke = _smoke_reads()
        rep["repository_reads"] = smoke
        if not all(v == "ok" for v in smoke.values()):
            rep["error"] = "REPOSITORY_READ_FAILED"
            return rep, EXIT_VERIFY_FAILED
        if run_id:
            if finished is None:
                rep["error"] = "EXECUTE_NOT_RECORDED"
                return rep, EXIT_PENDING
            ready = finished + timedelta(seconds=VERIFY_AFTER_S)
            remaining = (ready - deps["now"]()).total_seconds()
            rep["sync_window"] = {"finished_at": _iso(finished), "not_before": _iso(ready), "seconds_remaining": max(0, int(remaining))}
            if remaining > 0:
                rep["error"] = "PENDING_SYNC_WINDOW"
                return rep, EXIT_PENDING                                                          # todavía no pasó el ciclo de sincronización de la app: no se puede afirmar que no reaparece
        rep["verdict"] = "OK"
        return rep, EXIT_OK
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _smoke_reads() -> dict:
    """La vertical queda OPERATIVA: cada repositorio lista sin error y sin registros (lecturas; no crea nada)."""
    out = {}
    try:
        from webapp.services import (busqueda_repository, entrevista_repository, onboarding_repository, profile_repository, publicacion_repository)
    except Exception as exc:                                                                     # noqa: BLE001
        return {"import": type(exc).__name__}
    for name, mod in (("profiles", profile_repository), ("busquedas", busqueda_repository), ("entrevistas", entrevista_repository), ("onboarding", onboarding_repository), ("publicaciones", publicacion_repository)):
        try:
            out[name] = "ok" if mod.list() == [] else "not_empty"
        except Exception as exc:                                                                 # noqa: BLE001
            out[name] = type(exc).__name__
    return out


# ═══════════════════ restauración ═══════════════════
def do_restore(deps: dict, args) -> tuple[dict, int]:
    env, blockers = preflight(deps)
    rep = _base_report("restore", env, blockers)
    rep["writes_performed"] = 0
    if blockers:
        return rep, EXIT_BLOCKED
    if not args.backup_run:
        rep["error"] = "BACKUP_RUN_REQUIRED"
        return rep, EXIT_NOT_CONFIRMED
    rs = deps["remote_store"]
    work = Path(tempfile.mkdtemp(prefix="cchh-reset-"))
    try:
        try:
            manifest, approval = _load_run(deps, args.backup_run, work)
        except Exception as exc:                                                                 # noqa: BLE001
            rep["error"] = "BACKUP_NOT_SEALED_OR_INVALID:" + (getattr(exc, "code", None) or type(exc).__name__)
            return rep, EXIT_NOT_CONFIRMED
        if approval["env8"] != env["env8"]:
            rep["error"] = "ENV_MISMATCH"                                                        # un respaldo de otro entorno jamás se restaura aquí
            return rep, EXIT_NOT_CONFIRMED
        confirm_phrase = phrases(env["env8"], approval["digest12"])["restore"]
        execute = bool(args.confirm)
        if execute and args.confirm != confirm_phrase:
            rep["error"] = "CONFIRMATION_INVALID"
            return rep, EXIT_NOT_CONFIRMED
        rep["backup_run"] = {"run_id": args.backup_run, "sealed": True, "objects": len(approval["objects"])}
        rep["required_confirmation"] = None if execute else confirm_phrase
        root = _root(deps)
        fetched = work / "fetched"
        tally = {"restore_local": 0, "restore_remote": 0, "already_present": 0, "conflicts": 0, "remote_version_not_restored": 0, "failed": 0}
        for rel, want in sorted(approval["objects"].items()):
            data = (fetched / "objects" / rel).read_bytes()
            sha = _sha(data)
            path = root / rel
            local_sha = want["local"]
            remote_sha = want["remote"]
            # lado local: si el original lo tenía, o si era SOLO remoto (se repone el local para poder empujar el remoto)
            if path.is_file():
                if local_sha is not None and _sha(path.read_bytes()) == local_sha or (local_sha is None and _sha(path.read_bytes()) == sha):
                    tally["already_present"] += 1
                else:
                    tally["conflicts"] += 1                                                      # existe con otro contenido: NO se sobrescribe
                    continue
            else:
                tally["restore_local"] += 1
                if execute:
                    try:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        tmp = path.with_name(path.name + ".restore.tmp")
                        tmp.write_bytes(data)
                        os.replace(tmp, path)
                    except OSError:
                        tally["failed"] += 1
                        continue
            if remote_sha is None:
                continue
            if remote_sha != sha:
                tally["remote_version_not_restored"] += 1                                         # la versión remota distinta queda en el respaldo (`objects_remote/`); este modo no la sobrescribe
                continue
            try:
                existing = rs.read_object_bytes(path)
            except Exception:                                                                    # noqa: BLE001
                tally["failed"] += 1
                continue
            if existing is not None:
                tally["already_present" if _sha(existing) == remote_sha else "conflicts"] += 1
                continue
            tally["restore_remote"] += 1
            if execute:
                try:
                    if not rs.push_path(path) or rs.read_object_bytes(path) is None or _sha(rs.read_object_bytes(path)) != remote_sha:
                        tally["failed"] += 1
                except Exception:                                                                # noqa: BLE001
                    tally["failed"] += 1
        rep["restore" if execute else "would_restore"] = tally
        rep["rollback_performed"] = False
        if execute:
            try:
                _journal(deps, args.backup_run, {"event": "restore_executed", "at": _iso(deps["now"]()), **tally})
            except Exception:                                                                    # noqa: BLE001 — el diario no revierte lo restaurado
                rep["journal_failed"] = True
        if execute and (tally["failed"] or tally["conflicts"]):
            rep["error"] = "RESTORE_INCOMPLETE"
            return rep, EXIT_FAILED
        return rep, EXIT_OK
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ═══════════════════ CLI ═══════════════════
class _DriftError(Exception):
    pass


class _PurgeError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _load_deps() -> dict:
    import config
    from core import remote_store
    from webapp.services import durable_backup, governed_backup
    return {"config": config, "remote_store": remote_store, "dbk": durable_backup, "gb": governed_backup, "now": lambda: datetime.now(timezone.utc), "sleep": __import__("time").sleep,
            "remote_error": remote_store.RemoteReadError, "backup_error": durable_backup.DurableBackupError, "purge_error": _PurgeError, "drift_error": _DriftError}


def main(argv: list[str] | None = None, deps: dict | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="(por defecto) solo lectura")
    mode.add_argument("--backup", action="store_true", help="respaldo sellado y verificado de TODO lo que se va a borrar (exige --confirm)")
    mode.add_argument("--execute", action="store_true", help="borrado por objeto desde el manifiesto sellado (exige --confirm, --owner-ack, --backup-run y --window-until)")
    mode.add_argument("--verify", action="store_true", help="verificación posterior de disco y Spaces (solo lectura)")
    mode.add_argument("--restore", action="store_true", help="restaura desde un respaldo sellado sin sobrescribir; sin --confirm solo informa")
    ap.add_argument("--confirm", default=None, metavar="FRASE")
    ap.add_argument("--owner-ack", default=None, metavar="FRASE", help="confirmación explícita del owner de que no hay datos ni solicitudes reales que conservar")
    ap.add_argument("--backup-run", default=None, metavar="RUN_ID")
    ap.add_argument("--window-until", default=None, metavar="AAAA-MM-DDTHH:MM:SSZ", help=f"fin de la ventana de mantenimiento (UTC; futuro y a lo sumo {WINDOW_MAX_MINUTES} min)")
    args = ap.parse_args(argv)
    if args.confirm and not (args.backup or args.execute or args.restore):
        ap.error("--confirm solo tiene sentido con --backup, --execute o --restore")
    deps = deps or _load_deps()
    if args.backup:
        rep, code = do_backup(deps, args.confirm)
    elif args.execute:
        rep, code = do_execute(deps, args)
    elif args.verify:
        rep, code = do_verify(deps, args.backup_run)
    elif args.restore:
        rep, code = do_restore(deps, args)
    else:
        rep, code = dry_run(deps)
    rep["exit_code"] = code
    sys.stdout.buffer.write((json.dumps(rep, indent=1, ensure_ascii=False, sort_keys=True, default=str) + "\n").encode("utf-8"))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
