"""OPERACIÓN EXCEPCIONAL — restauración ACOTADA del pool de Biogenesis al estado previo al incidente del 2026-11-06 (publicación de CALIDAD sobre PRODUCCION).

NO forma parte de la aplicación: no se importa desde el Hub, ni corre al arrancar, ni en tareas programadas. Solo se ejecuta A MANO desde la Console del componente web.
Por defecto es DRY-RUN: no escribe nada. Ver README.md (procedimiento, recuperación ante fallos y retiro).

ALCANCE CERRADO: exactamente los 10 objetos de ``PLAN`` (hashes fijos):
  · 7 CSN que CALIDAD sobrescribió  → se restauran con los BYTES EXACTOS de la evidencia autoritativa
  · 2 CSN exclusivos de CALIDAD + el manifiesto de linaje de CALIDAD → cuarentena reversible
EVIDENCIA: se lee de Spaces (``quarantine/biogenesis-20261106-evidence/…``, prefijo privado FUERA de SPACES_PERSIST_PATHS) y se valida su sha256 contra los hashes FIJOS de
``PLAN`` —que son los de la firma de la última regeneración exitosa— y contra esa firma leída en el momento. Nunca se usa evidencia que no coincida.

MODOS
  (por defecto)                                          DRY-RUN: preflight completo y lista de acciones; no escribe.
  --apply    --confirm RESTAURAR-BIOGENESIS-20261106     restaura (o completa un estado mixto hacia lo restaurado).
  --rollback --confirm REVERTIR-BIOGENESIS-20261106      vuelve al estado previo a la restauración desde la cuarentena.

GARANTÍAS (reutiliza la unidad transaccional de la publicación; nada nuevo de persistencia)
  · preflight por hashes, antes y otra vez BAJO el candado; aborta ante cualquier hash o estado desconocido (``OTHER``), disco ≠ Spaces, journals/intenciones pendientes,
    compuerta cerrada o restore incompleto;
  · cuarentena primero y verificada (bytes actuales de los 10 objetos, en Spaces y en disco, con sha256);
  · ``hold_publication`` + candado de escritor entre procesos + compuerta de lectores cerrada + journal con deshacer + registro de intención en Spaces antes de tocar el remoto
    + ``converge`` verificado. Una caída la resuelve la recuperación existente (``publication_txn.recover_pending`` / arranque).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:                                       # ejecución como script desde la raíz de la app (Console)
    sys.path.insert(0, str(_REPO))

CLIENT = "Biogenesis"
SIGNATURE_FLOW = "Biogenesis/General"
QID = "biogenesis-20261106-calidad"                                  # cuarentena (bytes previos a la restauración)
EVIDENCE_PREFIX = "quarantine/biogenesis-20261106-evidence/catalog_local/"   # evidencia autoritativa (la sube upload_evidence.py)
CONFIRM_APPLY = "RESTAURAR-BIOGENESIS-20261106"
CONFIRM_ROLLBACK = "REVERTIR-BIOGENESIS-20261106"
_POOL = ".csn_cache/Biogenesis/"
# Rutas relativas a catalog_local. current = estado INVENTARIADO (CALIDAD, 2026-11-06); target = estado previo (firma de la última regeneración exitosa); None = cuarentena.
PLAN: dict[str, dict] = {
    _POOL + "analytic-models/AM_ANALISIS_VENCIMIENTO.json": {"current": "d4abab3caee37a92a318ac96d5e6fa3e35aac0f88371ec9ffadc3aa78d770db1", "target": "b56a252f1dc73f96f42e7c3e230b3263751d70c8f2a4925a32a2ad9bdb0d2a25"},
    _POOL + "data-flows/DF_ANALISIS_VENCIMIENTOS.json": {"current": "881b3002f135913ab8aa52a0b3907becc52ef22ec96cf42250ea3262836fbe8f", "target": "96c03229f45914f483640696e0b99343e4277cbd24660d51c6f3e567191cd72d"},
    _POOL + "views/3VF_ANALISIS_VENCIMIENTO.json": {"current": "66c887679702bee61d3857fd4e746e38ad11e6d9ba84cb2f3934a4f0c3ac978d", "target": "af8477c1d1566bcb0fa071208dec81ee5cb5b935f4b00b3afb49d84c98fe0a93"},
    _POOL + "views/VD_T001L.json": {"current": "17292d4730a7d47bcf5673bc0dfdf67afdbc059200d52557a7470f0051253b8e", "target": "ca3ede4ceb390ffbbaba62e7ef806d9f1b41e182b10250bde14bfc1e31489882"},
    _POOL + "views/VT_0MATL_GRP_1.json": {"current": "5750692626811ac8041be4898d631eb815775c9d9016986ea0bd2ced27b1aa02", "target": "80c16785c4be4d8c328d92fcdb0a0540ed4a303f12afc4dee567036fb04109ac"},
    _POOL + "views/VT_0MATL_GRP_2.json": {"current": "b72e328c401cbc25075712a45bfe8d791164fbf506bc4d27ff6675aae17e8a0a", "target": "6641836aefd87fcc1b17b2fb069be0ea245bea9bd36e20444a8116aa2dce8089"},
    _POOL + "views/VT_T001W.json": {"current": "8434da557f63f90f4bb0aff43d57b052b4e1041d476962a74380d0c88c074e12", "target": "54b1104d777aa6870f50a848b4c81f7684e1c4935d027700472dc3931f8fb7f1"},
    _POOL + "local-tables/1LT_I_PRODUCTPLANTBASIC.json": {"current": "b1acbbba0d248577290be9ef83280c8387dd1897d4cdaeed55e201e9a53aa02b", "target": None},
    _POOL + "replication-flows/1RF_I_DATOS_MAESTROS.json": {"current": "fecfc5d6c941a72bb53a334f70cafef2e8e7d4ec8311a847a64fdc2ffbbff13b", "target": None},
    ".lineage_runs/Biogenesis/CALIDAD/analytic-model/AM_ANALISIS_VENCIMIENTO/lineage-manifest.json": {"current": "219aa80ab26d048c59b569edec5b14823a83ca1931cbbb869741d32d64f12cd2", "target": None},
}
ALLOWED_POOL_EXTRA_PREFIXES = ("Conexiones/", "Espacios/")          # conectividad: fuera de la firma y fuera de alcance (debe quedar intacta)
JOURNAL_MODE_PREFIX = "biogenesis-restore-"

OLD, NEW, OTHER, MIXED = "OLD", "NEW", "OTHER", "MIXED"


class Abort(RuntimeError):
    pass


def sha(data: bytes | None) -> str | None:
    return None if data is None else hashlib.sha256(data).hexdigest()


def _say(msg: str) -> None:
    print(msg, flush=True)


@dataclass
class Ctx:
    root: Path
    rs: object
    txn: object
    pi: object
    pr: object
    gate: object
    signature: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, bytes] = field(default_factory=dict)

    def path(self, rel: str) -> Path:
        return self.root / rel

    @property
    def quarantine_local(self) -> Path:
        return self.root.parent / ".recovery_quarantine" / QID

    def remote_key(self, logical: str) -> str:
        return self.rs._key(logical)

    def key(self, rel: str) -> str:
        return self.pi.key_of(self.pi.dirs_of(self.rs), self.path(rel))


def _fault(ctx: Ctx, point: str, **kw) -> None:
    ctx.txn.fault(point, **kw)


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


def _classify(rel: str, data: bytes | None) -> str:
    d = sha(data)
    if d == PLAN[rel]["current"]:
        return OLD
    if d == PLAN[rel]["target"]:                                      # target None ⇒ ausente
        return NEW
    return OTHER


def _signature(raw: bytes, origin: str) -> dict[str, str]:
    try:
        return {f["path"]: f["sha256"] for f in json.loads(raw)["flows"][SIGNATURE_FLOW]["signature"]["files"]}
    except (ValueError, KeyError, TypeError) as exc:
        raise Abort(f"firma autoritativa ilegible en {origin}") from exc


# ═══════════════════════ preflight ═══════════════════════
def _restore_journals(ctx: Ctx) -> list[dict]:
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


def load_evidence(ctx: Ctx) -> None:
    client, bucket = _client_bucket(ctx)
    for rel, spec in PLAN.items():
        if spec["target"] is None:
            continue
        data = _get(client, bucket, ctx.remote_key(EVIDENCE_PREFIX + rel))
        if data is None:
            raise Abort(f"falta la evidencia en Spaces: {EVIDENCE_PREFIX + rel} (subirla con upload_evidence.py)")
        if sha(data) != spec["target"]:
            raise Abort(f"evidencia {rel}: sha256 ≠ hash fijo del plan")
        if ctx.signature.get("Biogenesis/" + rel[len(_POOL):]) != spec["target"]:
            raise Abort(f"evidencia {rel}: el hash fijo no coincide con la firma autoritativa vigente")
        ctx.evidence[rel] = data


def preflight(ctx: Ctx) -> tuple[str, list[str]]:
    """(estado global OLD | NEW | MIXED, informe). ``Abort`` ante cualquier desviación. MIXED = cada objeto es OLD o NEW (sin desconocidos) y disco = Spaces."""
    rs, txn, gate = ctx.rs, ctx.txn, ctx.gate
    report: list[str] = []
    if not rs.write_enabled():
        raise Abort("la persistencia remota no está habilitada (se exige DANWOR_PERSISTENCE=remote con credenciales)")
    csn_env = os.getenv("DANWOR_CATALOG_CSN_CACHE", "").strip()
    if csn_env and Path(csn_env).resolve() != (ctx.root / ".csn_cache").resolve():
        raise Abort("DANWOR_CATALOG_CSN_CACHE no es <catalog_local>/.csn_cache: fuera del alcance de este script")
    leftovers = [j for j in _restore_journals(ctx) if j["state"] not in (txn.J_REMOTE_CONFIRMED, txn.J_ROLLED_BACK, txn.J_DISCARDED)]
    if leftovers:
        raise Abort("journal de una restauración anterior SIN resolver " + json.dumps(leftovers) + ": inconsistencia entre journal, disco y Spaces. "
                    "DETENIDO: no modificar ni mover nada, no ejecutar recover_pending; conservar toda la evidencia y exigir diagnóstico "
                    "específico (ver README, «Journal anómalo»). No se escribe nada.")
    if txn.has_pending(ctx.root):
        raise Abort("hay operaciones de publicación pendientes: resolverlas primero (recover_pending)")
    if gate.read_state(ctx.root) is not None:
        raise Abort("la compuerta de lectores está cerrada (publicación, arranque o recuperación en curso)")
    if rs.sync_blocked_reason():
        raise Abort(f"el restore de Spaces no está completo: {rs.sync_blocked_reason()}")
    if ctx.pi.open_intents(ctx.root):
        raise Abort("hay registros de intención abiertos: resolverlos primero")

    local_manifest = ctx.root / ".catalog_manifest.json"
    remote_manifest = rs.read_object_bytes(local_manifest)
    if remote_manifest is None or not local_manifest.is_file():
        raise Abort("falta .catalog_manifest.json (local o remoto)")
    sig_l, sig_r = _signature(local_manifest.read_bytes(), "disco"), _signature(remote_manifest, "Spaces")
    if sig_l != sig_r:
        raise Abort("la firma de Biogenesis difiere entre el disco y Spaces")
    ctx.signature = sig_r
    report.append(f"firma autoritativa: {len(sig_r)} archivos (idéntica en disco y en Spaces)")
    load_evidence(ctx)
    report.append("evidencia (Spaces): 7/7 = hash fijo = firma autoritativa")

    states_l = {rel: _classify(rel, ctx.path(rel).read_bytes() if ctx.path(rel).is_file() else None) for rel in PLAN}
    try:
        states_r = {rel: _classify(rel, rs.read_object_bytes(ctx.path(rel))) for rel in PLAN}
    except rs.RemoteReadError as exc:
        raise Abort(f"Spaces ilegible: {exc}") from exc
    unknown = [r for r in PLAN if states_l[r] == OTHER or states_r[r] == OTHER]
    if unknown:
        raise Abort(f"objetos con un contenido que no es ni el inventariado ni el objetivo: {unknown}")
    if states_l != states_r:
        raise Abort(f"disco y Spaces difieren en los objetos del plan: {[r for r in PLAN if states_l[r] != states_r[r]]} — resolver con la recuperación antes")
    kinds = set(states_l.values())
    whole = kinds.pop() if len(kinds) == 1 else MIXED

    pool = ctx.root / ".csn_cache" / CLIENT
    plan_pool = {"Biogenesis/" + r[len(_POOL):] for r in PLAN if r.startswith(_POOL)}
    seen: set[str] = set()
    for p in pool.rglob("*"):
        if not p.is_file():
            continue
        k = "Biogenesis/" + p.relative_to(pool).as_posix()
        seen.add(k)
        if k in plan_pool:
            continue
        if k in ctx.signature:
            if sha(p.read_bytes()) != ctx.signature[k]:
                raise Abort(f"archivo fuera del plan distinto de la firma: {k}")
        elif not k[len("Biogenesis/"):].startswith(ALLOWED_POOL_EXTRA_PREFIXES):
            raise Abort(f"archivo inesperado en el pool de Biogenesis: {k}")
    missing = sorted(set(ctx.signature) - seen - plan_pool)
    if missing:
        raise Abort(f"faltan archivos de la firma en el pool: {missing[:5]}")
    report.append(f"pool de Biogenesis: {len(seen)} archivos; fuera del plan = firma; extra solo conectividad")
    report.append(f"estado de los 10 objetos: {whole} (disco y Spaces coinciden)" + ("" if whole != MIXED else f" — {states_l}"))
    return whole, report


# ═══════════════════════ cuarentena ═══════════════════════
def ensure_quarantine(ctx: Ctx, *, write: bool) -> list[str]:
    """Bytes INVENTARIADOS (previos a la restauración) de los 10 objetos en Spaces (``quarantine/<QID>/…``) y en disco, con manifest.json. Idempotente; si ya existe debe coincidir.
    Solo se toma de objetos en estado OLD; los que ya están en NEW deben tener su copia previa ya presente (estado mixto tras una caída)."""
    client, bucket = _client_bucket(ctx)
    lines: list[str] = []
    inv = {"qid": QID, "client": CLIENT, "items": []}
    for rel, spec in PLAN.items():
        rk = ctx.remote_key(f"quarantine/{QID}/catalog_local/{rel}")
        lp = ctx.quarantine_local / "catalog_local" / rel
        inv["items"].append({"path": rel, "sha256": spec["current"], "remote_key": rk})
        lines.append(f"  cuarentena {rel} → {rk}")
        if not write:
            continue
        existing = _get(client, bucket, rk)
        here = ctx.path(rel).read_bytes() if ctx.path(rel).is_file() else None
        if existing is not None:
            if sha(existing) != spec["current"]:
                raise Abort(f"la cuarentena remota ya contiene otra versión de {rel}")
        elif sha(here) == spec["current"]:
            client.put_object(Bucket=bucket, Key=rk, Body=here)
            if sha(_get(client, bucket, rk)) != spec["current"]:
                raise Abort(f"cuarentena remota no verificada: {rel}")
            existing = here
        else:
            raise Abort(f"{rel} ya no está en el estado inventariado y su copia previa no existe en la cuarentena")
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_bytes(existing)
    if write:
        body = json.dumps(inv, indent=1).encode("utf-8")
        mk = ctx.remote_key(f"quarantine/{QID}/manifest.json")
        client.put_object(Bucket=bucket, Key=mk, Body=body)
        if sha(_get(client, bucket, mk)) != sha(body):
            raise Abort("manifest de cuarentena no verificado")
        ctx.quarantine_local.mkdir(parents=True, exist_ok=True)
        (ctx.quarantine_local / "manifest.json").write_bytes(body)
    return lines


def quarantine_bytes(ctx: Ctx, rel: str) -> bytes:
    client, bucket = _client_bucket(ctx)
    data = _get(client, bucket, ctx.remote_key(f"quarantine/{QID}/catalog_local/{rel}"))
    if data is None or sha(data) != PLAN[rel]["current"]:
        raise Abort(f"la cuarentena remota de {rel} falta o no coincide: no se puede revertir")
    return data


# ═══════════════════════ unidad transaccional ═══════════════════════
def run_unit(ctx: Ctx, targets: dict[str, bytes | None], *, label: str) -> str:
    rs, txn, pi, pr, gate = ctx.rs, ctx.txn, ctx.pi, ctx.pr, ctx.gate
    root = ctx.root
    with rs.hold_publication(("catalog_local",)) as hold:
        if not hold.exclusive:
            raise Abort("no se pudo retener el sincronizador (hold no exclusivo)")
        with txn.writer_lock(30):
            whole, _ = preflight(ctx)                                  # revalidación BAJO el candado
            done = NEW if label == "apply" else OLD
            if whole == done:
                return "NO_CHANGE"
            op = txn.new_operation_id()
            paths = {rel: ctx.path(rel) for rel in targets}
            keyed = {ctx.key(rel): p for rel, p in paths.items()}
            removed = [ctx.key(rel) for rel, data in targets.items() if data is None]
            journal = txn.Journal(root, op, {"operation_id": op, "mode": JOURNAL_MODE_PREFIX + label, "client_dir": CLIENT, "catalog_root": str(root),
                                             "host_id": txn.host_id(), "started_at": txn.now_iso(), "planned_writes": sorted(keyed), "planned_deletes": sorted(removed),
                                             "touched_dirs": sorted({str(p.parent) for p in paths.values()})})
            journal.update(txn.J_PLANNED)
            gate.begin(op, CLIENT, root)
            undo = txn.UndoLog(txn.undo_dir(root, op))
            try:
                for p in paths.values():
                    undo.capture(p)
                undo.save()
                journal.update(txn.J_APPLYING)
                _fault(ctx, "restore_after_undo")
                from core import atomic_io
                for i, (rel, data) in enumerate(sorted(targets.items())):
                    if data is None:
                        paths[rel].unlink(missing_ok=True)
                    else:
                        atomic_io.write_bytes(paths[rel], data)
                    _fault(ctx, "restore_mid_local", index=i)
                for rel, data in targets.items():
                    now = paths[rel].read_bytes() if paths[rel].is_file() else None
                    if sha(now) != sha(data):
                        raise RuntimeError(f"verificación local fallida: {rel}")
            except Exception:
                undo.restore()
                journal.update(txn.J_ROLLED_BACK, error_code="LOCAL_APPLY_FAILED")
                gate.end(op, root)
                raise
            journal.update(txn.J_COMMIT_UNCERTAIN, unit={"keyed": {k: str(p) for k, p in keyed.items()}, "removed": removed, "markers": [], "persisted": ["catalog_local"]})
            _fault(ctx, "restore_after_local_commit")
            listing = {}
            for k, p in keyed.items():
                remote = rs.read_object_bytes(p)
                if remote is not None:
                    listing[k] = {"etag": hashlib.md5(remote).hexdigest(), "size": len(remote)}  # noqa: S324 — identidad de contenido (formato del listado)
            try:
                pi.write(rs, root, CLIENT, operation_id=op, previous_operation_id=None, created_at=txn.now_iso(),
                         keyed={k: p for k, p in keyed.items() if k not in removed}, removed={k: keyed[k] for k in removed}, listing=listing, markers=())
            except pi.IntentError as exc:
                undo.restore()
                journal.update(txn.J_ROLLED_BACK, error_code="INTENT_FAILED")
                gate.end(op, root)
                raise Abort(f"no se pudo registrar la intención en Spaces ({exc}); Spaces no se tocó y el disco volvió al estado previo") from exc
            _fault(ctx, "restore_after_intent")
            outcome = pr.converge(rs, keyed)
            _fault(ctx, "restore_after_remote")
            if outcome.status == pr.CONFIRMED:
                journal.update(txn.J_REMOTE_CONFIRMED, durability=txn.DURABILITY_REMOTE)
                _fault(ctx, "restore_before_intent_close")
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


def build_ctx() -> Ctx:
    import config
    from core import publication_gate, remote_store
    from tools.catalog import publication_intent, publication_remote, publication_txn
    root = Path(os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(Path(config.BASE_DIR) / "catalog_local")).resolve()
    return Ctx(root=root, rs=remote_store, txn=publication_txn, pi=publication_intent, pr=publication_remote, gate=publication_gate)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Restauración ACOTADA de Biogenesis (operación excepcional; DRY-RUN por defecto). Ver README.md.")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help=f"ejecuta la restauración (exige --confirm {CONFIRM_APPLY})")
    mode.add_argument("--rollback", action="store_true", help=f"revierte desde la cuarentena (exige --confirm {CONFIRM_ROLLBACK})")
    ap.add_argument("--confirm", default="")
    args = ap.parse_args(argv)
    ctx = build_ctx()
    try:
        if args.apply and args.confirm != CONFIRM_APPLY:
            raise Abort(f"--apply exige --confirm {CONFIRM_APPLY}")
        if args.rollback and args.confirm != CONFIRM_ROLLBACK:
            raise Abort(f"--rollback exige --confirm {CONFIRM_ROLLBACK}")
        whole, report = preflight(ctx)
        _say("PREFLIGHT OK\n  " + "\n  ".join(report))
        if args.apply:
            if whole == NEW:
                _say("Nada que hacer: los 10 objetos ya están en el estado objetivo.")
                return 0
            for line in ensure_quarantine(ctx, write=True):
                _say(line)
            result = run_unit(ctx, {rel: (ctx.evidence[rel] if PLAN[rel]["target"] else None) for rel in PLAN}, label="apply")
        elif args.rollback:
            if whole == OLD:
                _say("Nada que revertir: los 10 objetos ya están en el estado previo a la restauración.")
                return 0
            result = run_unit(ctx, {rel: quarantine_bytes(ctx, rel) for rel in PLAN}, label="rollback")
        else:
            _say(f"\nDRY-RUN (no se escribió nada). Estado actual: {whole}. Acciones de --apply:")
            for line in ensure_quarantine(ctx, write=False):
                _say(line)
            for rel, spec in PLAN.items():
                verb = "restaurar (evidencia de Spaces)" if spec["target"] else "retirar a cuarentena (disco y Spaces)"
                _say(f"  {verb}: {rel}  {spec['current'][:12]} → {(spec['target'] or 'ausente')[:12]}")
            return 0
        _say(f"RESULTADO: {result}")
        if result in ("COMPLETED", "NO_CHANGE"):
            final, report = preflight(ctx)
            _say("VERIFICACIÓN\n  " + "\n  ".join(report))
            return 0 if final == (NEW if args.apply else OLD) else 3
        return 2
    except Abort as exc:
        _say(f"ABORT: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
