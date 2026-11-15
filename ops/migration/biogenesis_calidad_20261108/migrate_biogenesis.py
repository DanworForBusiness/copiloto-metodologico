"""OPERACIÓN EXCEPCIONAL — migración del Catálogo de Biogenesis a la evidencia del Space CALIDAD (25 modelos), como UNA unidad de cliente.

NO forma parte de la aplicación: no se importa desde el Hub, ni corre al arrancar, ni en tareas programadas. Solo se ejecuta A MANO desde la Console del componente web.
Por defecto es DRY-RUN: no escribe nada. Ver README.md (hitos, procedimiento, recuperación ante fallos).

QUÉ HACE (solo sobre la unidad de publicación del cliente: ``publication_ledger.unit_files`` + el ledger)
  · pool ``.csn_cache/Biogenesis``: las familias de MODELO se reemplazan por la evidencia de CALIDAD (Fase 1) y se retiran los paquetes legacy de la raíz;
    las familias que la extracción no cubre (Conexiones, Espacios, data-access-controls) quedan intactas;
  · ``.lineage_runs/Biogenesis``: un manifiesto v3 por modelo con ``space: CALIDAD`` y su tenant (se retira el de PRODUCCION);
  · Catálogo materializado del cliente y ``.catalog_manifest.json`` regenerados con el generador real (las entradas de OTROS clientes deben quedar idénticas);
  · Assessments gobernados regenerados con el motor real, REPRODUCIENDO las ratificaciones que consumía cada Juicio previo; si alguna deja de aplicar, ABORTA
    (mismo criterio que la migración v5 de Armonización);
  · ledger: una activación por modelo con ``space: CALIDAD`` (``published_space`` la lee).

CÓMO (sin persistencia nueva; reutiliza la unidad transaccional de la publicación)
  1. Evidencia: la de la Fase 1, en el prefijo PRIVADO ``quarantine/biogenesis-20261108-calidad-evidence/`` (lo sube ``upload_evidence.py``); se valida contra el sha256 FIJO
     ``EVIDENCE_SHA256`` de su manifiesto y archivo por archivo.
  2. Estado objetivo: se calcula en un directorio de TRABAJO aislado (subproceso con ``DANWOR_PERSISTENCE=local``) con el código real (generador, motor, ledger).
  3. Aplicación: diferencia byte a byte entre el estado actual y el objetivo, aplicada como unidad: cuarentena verificada PRIMERO (bytes actuales, Spaces + disco),
     ``hold_publication`` + candado de escritor + compuerta de lectores (que además detiene el ``mirror_up`` del proceso web) + journal con deshacer + intención en Spaces + ``converge``.
     El estado actual se revalida BAJO el candado contra la huella que se revisó en el dry-run (``--expect-baseline``).

MODOS
  (por defecto)                                                         DRY-RUN: preflight + estado objetivo + diferencia; no escribe.
  --apply --confirm MIGRAR-BIOGENESIS-CALIDAD-20261108 --expect-baseline <huella del dry-run>
  --rollback --confirm REVERTIR-BIOGENESIS-CALIDAD-20261108            vuelve EXACTAMENTE al estado previo desde la cuarentena.
  --evidence-dir <carpeta>                                             (ensayo) lee la evidencia de una carpeta local en vez de Spaces.
Después de un COMPLETED: reiniciar el componente web (sus cachés en memoria son de otro proceso; al arrancar restaura desde Spaces, que ya tiene la versión nueva).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

CLIENT, SPACE = "Biogenesis", "CALIDAD"
QID = "biogenesis-20261108-calidad-migration"
EVIDENCE_PREFIX = "quarantine/biogenesis-20261108-calidad-evidence/"
EVIDENCE_MANIFEST = "evidence_manifest.json"
EVIDENCE_SHA256 = "076dbd55c1f33316e56c4466c8aac92304080a2d302d1ef3610f6602757093a7"   # sha256 canónico del manifiesto de la evidencia de la Fase 1 (se fija antes del PR)
CONFIRM_APPLY = "MIGRAR-BIOGENESIS-CALIDAD-20261108"
CONFIRM_ROLLBACK = "REVERTIR-BIOGENESIS-CALIDAD-20261108"
JOURNAL_MODE_PREFIX = "biogenesis-migration-"
MODEL_FAMILIES = ("analytic-models", "views", "local-tables", "remote-tables", "data-flows", "replication-flows", "transformation-flows", "task-chains")
FEATURES = ["analytic-models", "data-flows", "replication-flows", "tables", "task-chains", "transformation-flows", "views"]
PERSISTED = ("catalog_local", "governed-assessments")
LEDGER_KEY = f"catalog_local/.publication_state/{CLIENT}.publication.json"
OLD, NEW, MIXED = "OLD", "NEW", "MIXED"


class Abort(RuntimeError):
    pass


def sha(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def canon(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _say(msg: str) -> None:
    print(msg, flush=True)


# ═══════════════════════ evidencia ═══════════════════════
def evidence_files(folder: Path) -> dict[str, bytes]:
    """Archivos de la evidencia de la Fase 1 dentro de ``folder`` (ruta relativa → bytes): pool unificado, cierres por modelo y resumen."""
    out: dict[str, bytes] = {}
    for p in sorted((folder / "pool" / CLIENT).rglob("*.json")):
        out[p.relative_to(folder).as_posix()] = p.read_bytes()
    for p in sorted((folder / "roots").glob("*/result.json")):
        out[p.relative_to(folder).as_posix()] = p.read_bytes()
    out["extraction_summary.json"] = (folder / "extraction_summary.json").read_bytes()
    return out


def evidence_manifest(files: dict[str, bytes]) -> dict:
    return {"client": CLIENT, "space": SPACE, "files": {rel: sha(data) for rel, data in sorted(files.items())}}


def evidence_digest(manifest: dict) -> str:
    return hashlib.sha256(canon(manifest)).hexdigest()


def validate_evidence(files: dict[str, bytes], expected: str) -> dict:
    """Coherencia interna + huella fija. Devuelve ``{"summary", "closures": {root: {"root_type", "closure": [{path, sha256}]}}, "tenant"}``."""
    man = evidence_manifest(files)
    if evidence_digest(man) != expected:
        raise Abort(f"la evidencia no coincide con la huella fija ({evidence_digest(man)[:16]} ≠ {expected[:16]})")
    summary = json.loads(files["extraction_summary.json"])
    if summary.get("space") != SPACE or summary.get("client") != CLIENT or summary.get("failed_roots") or summary.get("merged_pool", {}).get("conflicts"):
        raise Abort("el resumen de la extracción no es una extracción COMPLETA de Biogenesis desde CALIDAD")
    closures = {}
    for rel, data in files.items():
        if rel.startswith("roots/"):
            r = json.loads(data)
            if not r.get("ok") or r.get("space") != SPACE:
                raise Abort(f"cierre no válido: {rel}")
            for item in r["closure"]:
                pool_rel = f"pool/{CLIENT}/{item['path']}"
                if sha(files.get(pool_rel)) != item["sha256"]:
                    raise Abort(f"el cierre de {r['root']} no coincide con el pool unificado en {item['path']}")
            closures[r["root"]] = {"root_type": r.get("root_type") or "analytic-model", "closure": r["closure"]}
    if sorted(closures) != sorted(summary.get("ok_roots") or []):
        raise Abort("los cierres no coinciden con los modelos OK del resumen")
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    return {"summary": summary, "closures": closures, "tenant": dsp._tenant_id(summary.get("host") or "")}


# ═══════════════════════ contexto real ═══════════════════════
@dataclass
class Ctx:
    root: Path                      # catalog_local real
    governed_root: Path | None      # raíz del almacén gobernado (… /governed-assessments)
    rs: object
    txn: object
    pi: object
    pr: object
    gate: object
    evidence_dir: Path | None = None
    expected_evidence: str = EVIDENCE_SHA256
    evidence: dict[str, bytes] = field(default_factory=dict)

    @property
    def governed_client(self) -> Path | None:
        if self.governed_root is None:
            return None
        from webapp.services import assessment_service as asvc
        return asvc.governed_client_dir(CLIENT)

    @property
    def quarantine_local(self) -> Path:
        return self.root.parent / ".recovery_quarantine" / QID

    def remote_key(self, logical: str) -> str:
        return self.rs._key(logical)


def unit(ctx_root: Path, governed_client: Path | None) -> dict[str, Path]:
    """Unidad del cliente (clave → ruta) + el ledger. MISMA definición que la publicación transaccional."""
    from tools.catalog import publication_ledger as ledger_mod
    files = ledger_mod.unit_files(ctx_root, ctx_root / ".csn_cache", CLIENT, governed_client)
    lp = ledger_mod.ledger_path(ctx_root, CLIENT)
    if lp.is_file():
        files[LEDGER_KEY] = lp
    return files


def path_of(ctx: Ctx, key: str) -> Path:
    name, _, rel = key.partition("/")
    if name == "catalog_local":
        return ctx.root / rel
    if name == "governed-assessments" and ctx.governed_root is not None:
        return ctx.governed_root / rel
    raise Abort(f"clave fuera de la unidad: {key}")


def fingerprint(files: dict[str, Path]) -> str:
    return hashlib.sha256(canon({k: sha(p.read_bytes()) for k, p in sorted(files.items())})).hexdigest()


def _client_bucket(ctx: Ctx):
    import config
    return ctx.rs._get_client(), config.SPACES_BUCKET


def _get(client, bucket: str, key: str) -> bytes | None:
    try:
        return client.get_object(Bucket=bucket, Key=key)["Body"].read()
    except Exception as exc:                                         # noqa: BLE001 — ausente ⇒ None; cualquier otra falla se propaga
        code = str(((getattr(exc, "response", None) or {}).get("Error") or {}).get("Code", ""))
        if code in ("NoSuchKey", "404"):
            return None
        raise


def _put_verified(ctx: Ctx, key: str, data: bytes) -> None:
    client, bucket = _client_bucket(ctx)
    existing = _get(client, bucket, key)
    if existing is not None:
        if sha(existing) != sha(data):
            raise Abort(f"{key} ya existe con OTRO contenido (nunca se sobrescribe evidencia ni cuarentena)")
        return
    client.put_object(Bucket=bucket, Key=key, Body=data)
    if sha(_get(client, bucket, key)) != sha(data):
        raise Abort(f"escritura no verificada: {key}")


def load_evidence(ctx: Ctx) -> dict:
    if ctx.evidence_dir is not None:
        files = evidence_files(ctx.evidence_dir)
    else:
        client, bucket = _client_bucket(ctx)
        raw = _get(client, bucket, ctx.remote_key(EVIDENCE_PREFIX + EVIDENCE_MANIFEST))
        if raw is None:
            raise Abort(f"falta la evidencia en Spaces ({EVIDENCE_PREFIX}): subirla con upload_evidence.py (hito H2)")
        man = json.loads(raw)
        if evidence_digest(man) != ctx.expected_evidence:
            raise Abort("el manifiesto de evidencia en Spaces no coincide con la huella fija")
        files = {}
        for rel, digest in man["files"].items():
            data = _get(client, bucket, ctx.remote_key(EVIDENCE_PREFIX + rel))
            if sha(data) != digest:
                raise Abort(f"evidencia {rel}: falta o sha256 ≠ manifiesto")
            files[rel] = data
    ctx.evidence = files
    return validate_evidence(files, ctx.expected_evidence)


# ═══════════════════════ preflight ═══════════════════════
def _migration_journals(ctx: Ctx) -> list[dict]:
    out = []
    jd = ctx.txn.journal_dir(ctx.root)
    for jf in sorted(jd.glob("*.json")) if jd.is_dir() else []:
        try:
            data = json.loads(jf.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out.append({"file": jf.name, "state": "ILEGIBLE"})
            continue
        if str(data.get("mode") or "").startswith(JOURNAL_MODE_PREFIX):
            out.append({"file": jf.name, "state": data.get("state"), "mode": data.get("mode")})
    return out


def preflight(ctx: Ctx) -> tuple[dict[str, Path], list[str]]:
    """Entorno sano y disco = Spaces en toda la unidad del cliente. Devuelve (unidad actual, informe). ``Abort`` ante cualquier desviación."""
    rs, txn, gate = ctx.rs, ctx.txn, ctx.gate
    if not rs.write_enabled():
        raise Abort("la persistencia remota no está habilitada (se exige DANWOR_PERSISTENCE=remote con credenciales)")
    csn_env = os.getenv("DANWOR_CATALOG_CSN_CACHE", "").strip()
    if csn_env and Path(csn_env).resolve() != (ctx.root / ".csn_cache").resolve():
        raise Abort("DANWOR_CATALOG_CSN_CACHE no es <catalog_local>/.csn_cache: fuera del alcance de este script")
    if ctx.governed_root is None:
        raise Abort("no hay almacén gobernado de Assessments: fuera del alcance de este script")
    leftovers = [j for j in _migration_journals(ctx) if j["state"] not in (txn.J_REMOTE_CONFIRMED, txn.J_ROLLED_BACK, txn.J_DISCARDED)]
    if leftovers:
        raise Abort("journal de una migración anterior SIN resolver " + json.dumps(leftovers) + ". DETENIDO: no modificar ni mover nada; diagnóstico específico (README).")
    if txn.has_pending(ctx.root):
        raise Abort("hay operaciones de publicación pendientes: resolverlas primero (recover_pending)")
    if gate.read_state(ctx.root) is not None:
        raise Abort("la compuerta de lectores está cerrada (publicación, arranque o recuperación en curso)")
    if rs.sync_blocked_reason():
        raise Abort(f"el restore de Spaces no está completo: {rs.sync_blocked_reason()}")
    if ctx.pi.open_intents(ctx.root):
        raise Abort("hay registros de intención abiertos: resolverlos primero")
    files = unit(ctx.root, ctx.governed_client)
    prefixes = sorted({k.rsplit("/", 1)[0] + "/" for k in files} | {f"catalog_local/.csn_cache/{CLIENT}/", f"catalog_local/{CLIENT}/", f"catalog_local/.lineage_runs/{CLIENT}/"})
    pre = ctx.pr.preflight(rs, PERSISTED, prefixes, probe=path_of(ctx, LEDGER_KEY))
    if not pre.ok:
        raise Abort(f"Spaces no verificable ({pre.code}): {pre.detail}")
    remote_keys = {k for k in pre.listing if any(k.startswith(p) for p in _unit_prefixes())}
    local_md5 = {k: hashlib.md5(p.read_bytes()).hexdigest() for k, p in files.items()}   # noqa: S324 — identidad de contenido (formato del listado)
    differ = sorted(k for k in set(local_md5) | remote_keys if local_md5.get(k) != str((pre.listing.get(k) or {}).get("etag", "")).strip('"'))
    if differ:
        raise Abort(f"disco y Spaces difieren en {len(differ)} archivo(s) de la unidad (p. ej. {differ[:3]}): resolver con la recuperación antes")
    return files, [f"unidad de {CLIENT}: {len(files)} archivos (disco = Spaces)"]


def _unit_prefixes() -> tuple[str, ...]:
    return (f"catalog_local/.csn_cache/{CLIENT}/", f"catalog_local/.lineage_runs/{CLIENT}/", f"catalog_local/{CLIENT}/", "catalog_local/.catalog_manifest.json",
            LEDGER_KEY, f"governed-assessments/v1/{CLIENT}/")


# ═══════════════════════ estado objetivo (subproceso aislado) ═══════════════════════
def build_target(ctx: Ctx, info: dict) -> tuple[Path, dict]:
    """Copia la unidad actual a un directorio de trabajo y calcula ahí el estado objetivo con el código real, en un SUBPROCESO con persistencia local (nunca toca el
    disco real ni Spaces). Devuelve (directorio, informe del estado objetivo)."""
    work = Path(tempfile.mkdtemp(prefix="biog-migration-target-"))
    shutil.copytree(ctx.root, work / "catalog_local", ignore=shutil.ignore_patterns(".tmp-atomic-*"))
    if ctx.governed_client is not None and ctx.governed_client.is_dir():
        shutil.copytree(ctx.governed_client, work / "governed" / ctx.governed_client.relative_to(ctx.governed_root))
    ev = work / "evidence"
    for rel, data in ctx.evidence.items():
        (ev / rel).parent.mkdir(parents=True, exist_ok=True)
        (ev / rel).write_bytes(data)
    env = {k: v for k, v in os.environ.items() if not k.startswith("SPACES_")}
    env.update(DANWOR_CATALOG_DIR=str(work / "catalog_local"), DANWOR_CATALOG_CSN_CACHE=str(work / "catalog_local" / ".csn_cache"),
               DANWOR_GOVERNED_ASSESSMENTS_DIR=str(work / "governed"), DANWOR_PERSISTENCE="local", PYTHONIOENCODING="utf-8")
    proc = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--_stage", str(work), "--_evidence-sha", ctx.expected_evidence],
                          env=env, cwd=str(_REPO), capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        raise Abort("no se pudo calcular el estado objetivo: " + (proc.stdout + proc.stderr).strip()[-1500:])
    return work, json.loads((work / "target_report.json").read_text(encoding="utf-8"))


def _stage(work: Path, expected: str) -> int:
    """SUBPROCESO: calcula el estado objetivo dentro de ``work`` (variables de entorno ya apuntando ahí, persistencia local)."""
    import config
    assert config.PERSISTENCE_MODE == "local" and not config.PERSIST_ENABLED
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    from tools.catalog import catalog_service as cs
    from tools.catalog import generate_catalog as G
    from tools.catalog import publication_ledger as ledger_mod
    from tools.catalog import publication_txn as txn
    from webapp.services import assessment_service as asvc
    from webapp.services import judgment_decisions as jd
    root = work / "catalog_local"
    info = validate_evidence(evidence_files(work / "evidence"), expected)
    before_manifest = json.loads((root / ".catalog_manifest.json").read_text(encoding="utf-8")) if (root / ".catalog_manifest.json").is_file() else {}
    previous = {am: env for am, env in asvc.governed_envelopes_of(CLIENT).items()}
    # pool
    pool = root / ".csn_cache" / CLIENT
    legacy = sorted(p.name for p in pool.glob("*.json"))
    for name in legacy:
        (pool / name).unlink()
    for fam in MODEL_FAMILIES:
        shutil.rmtree(pool / fam, ignore_errors=True)
    for p in sorted((work / "evidence" / "pool" / CLIENT).rglob("*.json")):
        dest = pool / p.relative_to(work / "evidence" / "pool" / CLIENT)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(p, dest)
    # linaje: un manifiesto v3 por modelo, desde CALIDAD
    shutil.rmtree(root / ".lineage_runs" / CLIENT, ignore_errors=True)
    op = txn.new_operation_id()
    for am, c in sorted(info["closures"].items()):
        objects = []
        for item in c["closure"]:
            otype, _, fname = item["path"].partition("/")
            text = (pool / item["path"]).read_text(encoding="utf-8")
            objects.append({"object_type": otype, "technical_name": fname[:-5], "sha256": txn.sha256_text(text)})
        manifest = txn.seal_manifest({
            "schemaVersion": 3, "extractorVersion": dsp.EXTRACTOR_VERSION, "createdAt": txn.now_iso(), "operationId": op, "hostId": txn.host_id(),
            "client": CLIENT, "space": SPACE, "tenantId": info["tenant"], "rootObject": am, "rootType": c["root_type"], "mode": "PUBLISH",
            "requestedFeatures": FEATURES, "effectiveFeatures": FEATURES, "objects": sorted(objects, key=lambda o: (o["object_type"], o["technical_name"])),
            "edges": [], "externalSources": [], "migration": {"qid": QID, "evidence_sha256": expected},
            **({"analyticModel": am} if c["root_type"] == "analytic-model" else {})})
        path = dsp._canonical_run_dir(root, CLIENT, SPACE, c["root_type"], am) / "lineage-manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    # Catálogo (generador real, acotado al cliente)
    G.generate_all(root / ".csn_cache", root, only_clients={CLIENT})
    after_manifest = json.loads((root / ".catalog_manifest.json").read_text(encoding="utf-8"))
    # Entradas de OTROS clientes: el generador las conserva tal cual; si cambió su propia versión (generator_sha256) las DESCARTA a propósito para que esos clientes
    # se regeneren con el código nuevo (sus fichas no se tocan). Nunca pueden cambiar de contenido; el resto del manifiesto debe quedar idéntico.
    others_b = {k: v for k, v in (before_manifest.get("flows") or {}).items() if not k.startswith(CLIENT + "/")}
    others_a = {k: v for k, v in (after_manifest.get("flows") or {}).items() if not k.startswith(CLIENT + "/")}
    generator_changed = before_manifest.get("generator_sha256") != after_manifest.get("generator_sha256")
    if any(canon(others_b.get(k)) != canon(v) for k, v in others_a.items()) or (not generator_changed and set(others_a) != set(others_b)):
        raise SystemExit("ABORT: la regeneración cambió entradas de OTROS clientes en .catalog_manifest.json")
    rest_b = {k: v for k, v in before_manifest.items() if k not in ("flows", "generator_sha256")}
    rest_a = {k: v for k, v in after_manifest.items() if k not in ("flows", "generator_sha256")}
    if canon(rest_b) != canon(rest_a):
        raise SystemExit("ABORT: la regeneración cambió secciones de .catalog_manifest.json ajenas a los flujos")
    # Assessments: motor real + REPLAY de las ratificaciones que consumía cada Juicio previo
    asvc.reset_memory_cache()
    asvc.invalidate_pool_index()
    entries = cs.load_catalog(root / CLIENT)
    resolutions = asvc.resolve_entries(entries)
    levels, regenerated = {}, set()
    for e in entries:
        res = resolutions[e.entry_id]
        if res.kind != "OK":
            raise SystemExit(f"ABORT: la ficha {e.entry_id} no resuelve un modelo terminal ({res.kind})")
        am = res.terminal_am
        if am in regenerated:
            continue
        prep = asvc.prepare_regeneration(e, res)
        if prep is None:
            raise SystemExit(f"ABORT: {am} no produjo un Assessment aplicable")
        prev_j = (previous.get(am) or {}).get("judgment")
        fresh = asvc._derive_judgment(prep.envelope["profile_json"], jd.consumed(prev_j))
        lost = jd.lost_decisions(prev_j, fresh)
        if lost:
            raise SystemExit(f"ABORT: {am}: ratificaciones que dejarían de aplicar {lost} (RATIFICATION_NOT_REAPPLIED)")
        env = {**prep.envelope, "judgment": fresh}
        if asvc.commit_prepared_local(asvc.PreparedAssessment(prep.client, am, "REGENERATE", env, e.entry_id)) == asvc.DURABLE_MEMORY:
            raise SystemExit(f"ABORT: no se pudo escribir el Assessment de {am}")
        regenerated.add(am)
        levels[am] = {"before": (prev_j or {}).get("level") or (prev_j or {}).get("status"), "before_rule": (prev_j or {}).get("level_rule"),
                      "after": fresh.get("level") or fresh.get("status"), "after_rule": fresh.get("level_rule"),
                      "ratifications": len(jd.consumed(fresh))}
    removed = sorted(set(previous) - regenerated)
    for am in removed:
        asvc.remove_governed_local(CLIENT, am)
    # ledger: una activación por modelo (CALIDAD), con los digests de la unidad final
    state, ledger = ledger_mod.load(root, CLIENT)
    if state not in (ledger_mod.L_OK, ledger_mod.L_ABSENT):
        raise SystemExit(f"ABORT: ledger de {CLIENT} en estado {state}")
    gdir = asvc.governed_client_dir(CLIENT)
    digests = ledger_mod.digests(ledger_mod.unit_files(root, root / ".csn_cache", CLIENT, gdir))
    for am, c in sorted(info["closures"].items()):
        ledger = ledger_mod.build(ledger, operation_id=op, activated_at=txn.now_iso(), client_dir=CLIENT, space=SPACE, root_object=am, root_type=c["root_type"],
                                  extra={"migration": {"qid": QID, "evidence_sha256": expected, "tenant": info["tenant"]}}, file_digests=digests,
                                  counts={"migration": QID})
    ledger_mod.write(root, CLIENT, ledger)
    report = {"legacy_retired": legacy, "levels": levels, "assessments_removed": removed, "models": sorted(info["closures"]), "tenant": info["tenant"]}
    (work / "target_report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    return 0


def plan_targets(ctx: Ctx, work: Path, current: dict[str, Path]) -> dict[str, bytes | None]:
    """Clave → bytes objetivo (None = retirar), SOLO donde difiere del estado actual. Verifica que fuera de la unidad nada cambió en el directorio de trabajo."""
    from webapp.services import assessment_service as asvc  # noqa: F401
    wroot = work / "catalog_local"
    wgov_client = work / "governed" / ctx.governed_client.relative_to(ctx.governed_root) if ctx.governed_client is not None else None
    from tools.catalog import publication_ledger as ledger_mod
    target_files = ledger_mod.unit_files(wroot, wroot / ".csn_cache", CLIENT, wgov_client)
    target_files = {k.replace("governed-assessments/", "governed-assessments/", 1): p for k, p in target_files.items()}
    lp = ledger_mod.ledger_path(wroot, CLIENT)
    if lp.is_file():
        target_files[LEDGER_KEY] = lp
    targets: dict[str, bytes | None] = {}
    for k in sorted(set(current) | set(target_files)):
        new = target_files[k].read_bytes() if k in target_files else None
        old = current[k].read_bytes() if k in current else None
        if sha(new) != sha(old):
            targets[k] = new
    unit_rel = {k.split("/", 1)[1] for k in target_files if k.startswith("catalog_local/")} | {k.split("/", 1)[1] for k in current if k.startswith("catalog_local/")}
    for p in wroot.rglob("*"):
        if not p.is_file() or p.name.startswith(".tmp-atomic-"):
            continue
        rel = p.relative_to(wroot).as_posix()
        if rel in unit_rel:
            continue
        real = ctx.root / rel
        if not real.is_file() or sha(real.read_bytes()) != sha(p.read_bytes()):
            raise Abort(f"el estado objetivo cambiaría un archivo FUERA de la unidad de {CLIENT}: {rel}")
    return targets


# ═══════════════════════ cuarentena ═══════════════════════
def ensure_quarantine(ctx: Ctx, current: dict[str, Path], baseline_fp: str, *, write: bool) -> dict:
    """Bytes ACTUALES de toda la unidad en Spaces (``quarantine/<QID>/…``) y en disco, con manifest.json (clave → sha256). Idempotente: si ya existe, debe ser de la MISMA huella."""
    inv = {"qid": QID, "client": CLIENT, "baseline": baseline_fp, "files": {k: sha(p.read_bytes()) for k, p in sorted(current.items())}}
    if not write:
        return inv
    mk = ctx.remote_key(f"quarantine/{QID}/manifest.json")
    client, bucket = _client_bucket(ctx)
    existing = _get(client, bucket, mk)
    if existing is not None and json.loads(existing).get("baseline") != baseline_fp:
        raise Abort("la cuarentena remota ya existe con OTRA huella de línea base")
    for k, p in sorted(current.items()):
        data = p.read_bytes()
        _put_verified(ctx, ctx.remote_key(f"quarantine/{QID}/{k}"), data)
        lp = ctx.quarantine_local / k
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_bytes(data)
    body = json.dumps(inv, indent=1, sort_keys=True).encode("utf-8")
    _put_verified(ctx, mk, body)
    ctx.quarantine_local.mkdir(parents=True, exist_ok=True)
    (ctx.quarantine_local / "manifest.json").write_bytes(body)
    return inv


def load_quarantine(ctx: Ctx) -> tuple[dict, dict[str, bytes]]:
    client, bucket = _client_bucket(ctx)
    raw = _get(client, bucket, ctx.remote_key(f"quarantine/{QID}/manifest.json"))
    if raw is None:
        raise Abort("no hay cuarentena de esta migración en Spaces: no hay nada que revertir")
    inv = json.loads(raw)
    files = {}
    for k, digest in inv["files"].items():
        data = _get(client, bucket, ctx.remote_key(f"quarantine/{QID}/{k}"))
        if sha(data) != digest:
            raise Abort(f"la cuarentena de {k} falta o no coincide: no se puede revertir")
        files[k] = data
    return inv, files


def record_applied(ctx: Ctx, targets: dict[str, bytes | None], current: dict[str, Path]) -> dict:
    """Huella del estado MIGRADO (clave → sha256 o None) para reconocer un estado mixto tras una caída."""
    applied = {k: sha(p.read_bytes()) for k, p in current.items()}
    applied.update({k: sha(v) for k, v in targets.items()})
    body = json.dumps({"qid": QID, "files": dict(sorted(applied.items()))}, indent=1, sort_keys=True).encode("utf-8")
    key = ctx.remote_key(f"quarantine/{QID}/applied.json")
    client, bucket = _client_bucket(ctx)
    existing = _get(client, bucket, key)
    if existing is None or sha(existing) != sha(body):
        client.put_object(Bucket=bucket, Key=key, Body=body)
        if sha(_get(client, bucket, key)) != sha(body):
            raise Abort("registro del estado migrado no verificado")
    return json.loads(body)


def classify(ctx: Ctx, current: dict[str, Path]) -> str:
    """OLD (= cuarentena), NEW (= aplicado) o MIXED (cada archivo es uno de los dos). Cualquier otro contenido ⇒ ``Abort``. Sin cuarentena ⇒ OLD."""
    client, bucket = _client_bucket(ctx)
    raw_q = _get(client, bucket, ctx.remote_key(f"quarantine/{QID}/manifest.json"))
    if raw_q is None:
        return OLD
    q = json.loads(raw_q)["files"]
    raw_a = _get(client, bucket, ctx.remote_key(f"quarantine/{QID}/applied.json"))
    a = json.loads(raw_a)["files"] if raw_a is not None else {}
    now = {k: sha(p.read_bytes()) for k, p in current.items()}
    keys = set(q) | set(a) | set(now)
    is_old = all(now.get(k) == q.get(k) for k in keys)
    is_new = bool(a) and all(now.get(k) == a.get(k) for k in keys)
    if is_old:
        return OLD
    if is_new:
        return NEW
    unknown = [k for k in keys if now.get(k) not in (q.get(k), a.get(k))]
    if unknown:
        raise Abort(f"archivos con un contenido que no es ni el previo ni el migrado: {sorted(unknown)[:5]}")
    return MIXED


# ═══════════════════════ unidad transaccional ═══════════════════════
def run_unit(ctx: Ctx, targets: dict[str, bytes | None], *, label: str, expect_fp: str | None) -> str:
    rs, txn, pi, pr, gate = ctx.rs, ctx.txn, ctx.pi, ctx.pr, ctx.gate
    root = ctx.root
    with rs.hold_publication(PERSISTED) as hold:
        if not hold.exclusive:
            raise Abort("no se pudo retener el sincronizador (hold no exclusivo)")
        with txn.writer_lock(30):
            current, _ = preflight(ctx)                                 # revalidación BAJO el candado
            if expect_fp is not None and fingerprint(current) != expect_fp:
                raise Abort("el estado de la unidad cambió desde el dry-run revisado (huella distinta): repetir el dry-run")
            if not targets:
                return "NO_CHANGE"
            op = txn.new_operation_id()
            paths = {k: path_of(ctx, k) for k in targets}
            removed = [k for k, d in targets.items() if d is None]
            journal = txn.Journal(root, op, {"operation_id": op, "mode": JOURNAL_MODE_PREFIX + label, "client_dir": CLIENT, "catalog_root": str(root),
                                             "host_id": txn.host_id(), "started_at": txn.now_iso(), "planned_writes": sorted(k for k in targets if k not in removed),
                                             "planned_deletes": sorted(removed), "touched_dirs": sorted({str(p.parent) for p in paths.values()})})
            journal.update(txn.J_PLANNED)
            gate.begin(op, CLIENT, root)
            undo = txn.UndoLog(txn.undo_dir(root, op))
            try:
                for p in paths.values():
                    undo.capture(p)
                undo.save()
                journal.update(txn.J_APPLYING)
                txn.fault("migration_after_undo")
                from core import atomic_io
                for i, (k, data) in enumerate(sorted(targets.items())):
                    if data is None:
                        paths[k].unlink(missing_ok=True)
                    else:
                        atomic_io.write_bytes(paths[k], data)
                    txn.fault("migration_mid_local", index=i)
                for k, data in targets.items():
                    now = paths[k].read_bytes() if paths[k].is_file() else None
                    if sha(now) != sha(data):
                        raise RuntimeError(f"verificación local fallida: {k}")
            except Exception:
                undo.restore()
                journal.update(txn.J_ROLLED_BACK, error_code="LOCAL_APPLY_FAILED")
                gate.end(op, root)
                raise
            keyed = dict(paths)
            journal.update(txn.J_COMMIT_UNCERTAIN, unit={"keyed": {k: str(p) for k, p in keyed.items()}, "removed": removed, "markers": [LEDGER_KEY], "persisted": list(PERSISTED)})
            txn.fault("migration_after_local_commit")
            listing = {}
            for k, p in keyed.items():
                remote = rs.read_object_bytes(p)
                if remote is not None:
                    listing[k] = {"etag": hashlib.md5(remote).hexdigest(), "size": len(remote)}  # noqa: S324
            try:
                pi.write(rs, root, CLIENT, operation_id=op, previous_operation_id=None, created_at=txn.now_iso(),
                         keyed={k: p for k, p in keyed.items() if k not in removed}, removed={k: keyed[k] for k in removed}, listing=listing,
                         markers=(LEDGER_KEY.rsplit("/", 1)[1],))
            except pi.IntentError as exc:
                undo.restore()
                journal.update(txn.J_ROLLED_BACK, error_code="INTENT_FAILED")
                gate.end(op, root)
                raise Abort(f"no se pudo registrar la intención en Spaces ({exc}); Spaces no se tocó y el disco volvió al estado previo") from exc
            txn.fault("migration_after_intent")
            outcome = pr.converge(rs, keyed, last=[LEDGER_KEY] if LEDGER_KEY in keyed else [])
            txn.fault("migration_after_remote")
            if outcome.status == pr.CONFIRMED:
                journal.update(txn.J_REMOTE_CONFIRMED, durability=txn.DURABILITY_REMOTE)
                pi.close_if(rs, root, CLIENT, op)
                shutil.rmtree(txn.undo_dir(root, op), ignore_errors=True)
                gate.end(op, root)
                return "COMPLETED"
            if outcome.status == pr.FAILED:
                hold.fail()
                undo.restore()
                journal.update(txn.J_REMOTE_DIVERGED, local_restored=True)
                back = pr.converge(rs, keyed)
                if back.status == pr.CONFIRMED:
                    journal.update(txn.J_ROLLED_BACK, error_code="PERSISTENCE_FAILED")
                    pi.close_if(rs, root, CLIENT, op)
                    gate.end(op, root)
                    return "ROLLED_BACK"
            gate.mark_uncertain(op, CLIENT, root, "PERSISTENCE_UNCERTAIN")
            return "RECOVERY_REQUIRED"


def build_ctx(evidence_dir: Path | None = None) -> Ctx:
    import config
    from core import publication_gate, remote_store
    from tools.catalog import publication_intent, publication_remote, publication_txn
    from webapp.services import assessment_service as asvc
    root = Path(os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(Path(config.BASE_DIR) / "catalog_local")).resolve()
    return Ctx(root=root, governed_root=asvc.governed_dir(), rs=remote_store, txn=publication_txn, pi=publication_intent, pr=publication_remote,
               gate=publication_gate, evidence_dir=evidence_dir)


def main(argv: list[str] | None = None, ctx: Ctx | None = None) -> int:
    ap = argparse.ArgumentParser(description="Migración de Biogenesis a CALIDAD (operación excepcional; DRY-RUN por defecto). Ver README.md.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help=f"ejecuta la migración (exige --confirm {CONFIRM_APPLY} y --expect-baseline)")
    mode.add_argument("--rollback", action="store_true", help=f"revierte desde la cuarentena (exige --confirm {CONFIRM_ROLLBACK})")
    mode.add_argument("--_stage", default="", help=argparse.SUPPRESS)
    ap.add_argument("--_evidence-sha", default="", help=argparse.SUPPRESS)
    ap.add_argument("--confirm", default="")
    ap.add_argument("--expect-baseline", default="")
    ap.add_argument("--evidence-dir", default="")
    args = ap.parse_args(argv)
    if args._stage:
        return _stage(Path(args._stage), args._evidence_sha)
    ctx = ctx or build_ctx(Path(args.evidence_dir) if args.evidence_dir else None)
    try:
        if args.apply and args.confirm != CONFIRM_APPLY:
            raise Abort(f"--apply exige --confirm {CONFIRM_APPLY}")
        if args.apply and not args.expect_baseline:
            raise Abort("--apply exige --expect-baseline <huella> (la imprime el dry-run revisado)")
        if args.rollback and args.confirm != CONFIRM_ROLLBACK:
            raise Abort(f"--rollback exige --confirm {CONFIRM_ROLLBACK}")
        if not ctx.expected_evidence or ctx.expected_evidence.startswith("__"):
            raise Abort("EVIDENCE_SHA256 no está fijado: el script no se puede usar todavía")
        current, report = preflight(ctx)
        state = classify(ctx, current)
        _say("PREFLIGHT OK\n  " + "\n  ".join(report) + f"\n  estado: {state}")
        if args.rollback:
            if state == OLD:
                _say("Nada que revertir: la unidad ya está en el estado previo a la migración.")
                return 0
            inv, qfiles = load_quarantine(ctx)
            targets = {k: qfiles.get(k) for k in sorted(set(current) | set(qfiles)) if sha(current[k].read_bytes() if k in current else None) != sha(qfiles.get(k))}
            result = run_unit(ctx, targets, label="rollback", expect_fp=None)
        else:
            if state == NEW:
                _say("Nada que hacer: la unidad ya está migrada.")
                return 0
            if state == MIXED:
                raise Abort("estado MIXTO (tras una caída): revertir primero con --rollback y repetir el dry-run")
            info = load_evidence(ctx)
            baseline_fp = fingerprint(current)
            work, target_report = build_target(ctx, info)
            try:
                targets = plan_targets(ctx, work, current)
            except Abort:
                shutil.rmtree(work, ignore_errors=True)
                raise
            writes = sum(1 for v in targets.values() if v is not None)
            _say(f"\nESTADO OBJETIVO: {len(targets)} cambio(s) en la unidad · {writes} escrituras · {len(targets) - writes} retiros · paquetes legacy retirados: "
                 f"{len(target_report['legacy_retired'])} · tenant: {target_report['tenant']}")
            for am, lv in sorted(target_report["levels"].items()):
                _say(f"  {am:<34} {lv['before']} ({lv['before_rule']}) → {lv['after']} ({lv['after_rule']}) · ratificaciones {lv['ratifications']}")
            if target_report["assessments_removed"]:
                _say(f"  Assessments que se retiran: {target_report['assessments_removed']}")
            if not args.apply:
                shutil.rmtree(work, ignore_errors=True)
                _say(f"\nDRY-RUN (no se escribió nada). Huella de la línea base revisada: --expect-baseline {baseline_fp}")
                return 0
            if baseline_fp != args.expect_baseline:
                shutil.rmtree(work, ignore_errors=True)
                raise Abort("la línea base actual no es la revisada en el dry-run (huella distinta): repetir el dry-run")
            ensure_quarantine(ctx, current, baseline_fp, write=True)
            record_applied(ctx, targets, current)
            try:
                result = run_unit(ctx, targets, label="apply", expect_fp=baseline_fp)
            finally:
                shutil.rmtree(work, ignore_errors=True)
        _say(f"RESULTADO: {result}")
        if result in ("COMPLETED", "NO_CHANGE"):
            final, _ = preflight(ctx)
            got = classify(ctx, final)
            _say(f"VERIFICACIÓN: estado {got}" + ("\nReiniciar el componente web para que sirva la versión nueva (cachés en memoria)." if result == "COMPLETED" else ""))
            return 0 if got == (OLD if args.rollback else NEW) else 3
        return 2
    except Abort as exc:
        _say(f"ABORT: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
