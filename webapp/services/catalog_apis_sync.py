"""Sincronización «solo APIs/CDS» del Catálogo (modo ``apis_only`` de ``/admin/catalog/generate``).

Trae a ``catalog_local/APIs/`` el código de las CDS views publicado en la carpeta ``01. APIs`` de SharePoint SIN regenerar nada más: no corre ningún generador (modelos, espacios,
seguridad, Data Products), no toca ``.catalog_manifest.json``, ``reports/``, Assessments ni Case Library, y no invoca ``mirror_up`` ni ``persist_changed``.

No hay importador nuevo: la lectura de SharePoint es ``generate_catalog.fetch_csn_folder`` (la del botón completo, con la autenticación Graph DELEGADA de la sesión; el token no se
guarda) acotada por parámetros al subárbol de APIs, y la copia a ``APIs/`` es ``generate_catalog._sync_apis``. Este módulo solo agrega lo que el modo necesita:

  * ``build_plan``  — PREFLIGHT de solo lectura (únicamente listados Graph y, si está autorizada, lecturas ``head_object`` de Spaces): qué hay local, qué hay en SharePoint, qué es
                       nuevo, qué cambió por cTag/eTag (y si el contenido cambia seguro o hay que verificarlo por hash), qué no cambió, qué falta en el origen (se informa, NO se borra)
                       y las claves EXACTAS de Spaces previstas.
  * ``apply``       — con la frase de confirmación y el mismo plan: importa TODO localmente antes de publicar; publica con ``push_path`` selectivo (solo las claves del plan, el
                       ``.download_manifest`` al final) bajo ``_CATALOG_REGEN_LOCK`` y ``hold_publication``; antes de pisar una clave existente la respalda (copia en el servidor +
                       hash) y deja un journal recuperable; verifica por hash después de cada publicación; ante cualquier falla hace rollback VERIFICADO y lo informa. Nunca anuncia un éxito
                       sin comprobarlo.
  * ``rollback``    — restaura desde el journal (backup) una publicación anterior: claves existentes ← backup, claves nuevas ← borradas, y el local alineado.

Protección: una API que ya existe en ``APIs/`` sin entrada en el manifest de descargas (no la administra este flujo; p. ej. ``C_SALESDOCUMENTITEMDEX_1`` e ``I_CUSTOMER``) o cuyo
contenido local difiere de su copia en caché NO se sobrescribe jamás: se informa. Un archivo que SharePoint ya no tiene se conserva.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import config
from core import remote_store


class _BusyHold:
    """Hold ficticio: otro proceso tiene el candado de escritor de ``catalog_local``; ``exclusive=False`` hace que el llamador aborte SIN modificar nada."""
    exclusive = False

    def fail(self) -> None:
        pass


@contextlib.contextmanager
def _hold_and_guard():
    """``hold_publication`` + (solo con ``DANWOR_PUBLICATION_SAFE`` activo) el candado de escritor ENTRE PROCESOS de la publicación. Sin interruptor: idéntico a antes."""
    from tools.catalog import publication_txn as txn
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(txn.external_writer_guard(0))
        except txn.CatalogBusyError:
            yield _BusyHold()
            return
        yield stack.enter_context(remote_store.hold_publication(("catalog_local",)))

log = logging.getLogger("danwor.catalog_apis_sync")

APIS_KINDS = frozenset({"api", "apis", "cds"})
PROTECTED = frozenset({"C_SALESDOCUMENTITEMDEX_1", "I_CUSTOMER"})        # nunca se modifican mientras no exista evidencia explícita (decisión del owner)
CONFIRM_PHRASE = "GO-SINCRONIZAR-APIS"
ROLLBACK_PHRASE = "GO-ROLLBACK-APIS"
WRITE_STATUSES = ("new", "changed", "maybe")                              # lo que el apply escribiría
STATUS_TEXT = {
    "new": "nueva", "changed": "cambió (tag y tamaño distintos: el contenido cambia)", "maybe": "tag distinto, mismo tamaño (metadatos o contenido de igual tamaño: se verifica por hash al aplicar)",
    "unchanged": "sin cambio detectado", "protected": "protegida: no se modifica", "diverged": "el local difiere de su copia en caché: no se pisa",
}
_JOURNAL_ID = re.compile(r"^\d{8}T\d{6}Z$")


class ApisSyncError(Exception):
    """Error esperable del modo (sin token, SharePoint ilegible, carpeta ambigua…): se muestra tal cual al operador."""


@dataclass(frozen=True)
class Item:
    name: str                   # «X.md»
    rel: str                    # ruta relativa en la caché de descargas: «01. APIs/X.md»
    status: str                 # new | changed | maybe | unchanged | protected | diverged
    size: int | None            # tamaño remoto
    local_size: int | None      # tamaño del APIs/<X>.md actual
    tag: str                    # cTag/eTag (la firma que compara el manifest de descargas)
    detail: str = ""


@dataclass
class PlannedKey:
    kind: str                   # apis | cache | manifest
    rel: str                    # «catalog_local/APIs/X.md» (relativo al bucket, sin prefijo de ambiente)
    key: str                    # clave exacta de Spaces
    local: str                  # ruta local
    remote: str = "no verificado"       # existe | no existe | no verificado


@dataclass
class Plan:
    folder: str | None = None
    items: list[Item] = field(default_factory=list)
    local_names: list[str] = field(default_factory=list)          # APIs/*.md actuales
    absent: list[str] = field(default_factory=list)               # en APIs/ y no en el origen: se conservan
    absent_cache: list[str] = field(default_factory=list)         # entradas del manifest de descargas del subárbol que el origen ya no tiene: se conservan
    keys: list[PlannedKey] = field(default_factory=list)
    remote_checked: bool = False
    scope_keys: set[str] = field(default_factory=set)             # claves que existían en Spaces dentro del alcance al armar el plan (informativo: base para detectar objetos ajenos; NO entra al plan_id)
    errors: list[str] = field(default_factory=list)
    plan_id: str = ""
    root_folders: list[str] = field(default_factory=list)

    def of(self, *statuses: str) -> list[Item]:
        return [i for i in self.items if i.status in statuses]

    @property
    def writes(self) -> list[Item]:
        return self.of(*WRITE_STATUSES)

    @property
    def skip_stems(self) -> frozenset[str]:
        return frozenset(i.name[:-3] for i in self.of("protected", "diverged"))


@dataclass
class Outcome:
    state: str                  # ok | nothing | aborted | local_only | rolled_back | incomplete
    lines: list[str] = field(default_factory=list)
    journal_id: str | None = None
    published: int = 0
    rollback_verified: bool | None = None

    @property
    def ok(self) -> bool:
        return self.state in ("ok", "nothing", "local_only")


# ── entorno ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _gen():
    """El MISMO generador del botón completo (``tools/catalog/generate_catalog``): nada se duplica."""
    tools = str(Path(__file__).resolve().parents[2] / "tools" / "catalog")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    import generate_catalog
    return generate_catalog


def _paths() -> tuple[Path, Path]:
    out = Path(os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(Path(config.BASE_DIR) / "catalog_local"))
    cache = Path(os.getenv("DANWOR_CATALOG_CSN_CACHE", "").strip() or str(out / ".csn_cache"))
    return out, cache


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str | None:
    try:
        return _sha(path.read_bytes())
    except OSError:
        return None


def _norm_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore").replace("\r\n", "\n")
    except OSError:
        return None


def _remote_key(rel: str) -> str:
    return remote_store._key(rel)                    # prefijo de ambiente (SPACES_PREFIX) + ruta relativa: el MISMO mapeo de push_path


def _client():
    return remote_store._get_client()


def _head(key: str) -> str:
    """«existe» / «no existe» / «no verificado» (solo lectura; un error que no es 404 no se interpreta como ausencia)."""
    try:
        _client().head_object(Bucket=config.SPACES_BUCKET, Key=key)
        return "existe"
    except Exception as exc:  # noqa: BLE001
        meta = getattr(exc, "response", None)
        code = str(meta.get("Error", {}).get("Code", "")) if isinstance(meta, dict) else ""
        status = str(meta.get("ResponseMetadata", {}).get("HTTPStatusCode", "")) if isinstance(meta, dict) else ""
        if code in {"404", "NoSuchKey", "NotFound"} or status == "404" or exc.__class__.__name__ in ("NoSuchKey", "NotFound"):
            return "no existe"
        return "no verificado"


# ── preflight (SOLO LECTURA) ───────────────────────────────────────────────────────────────────────────────────────────────────────
def build_plan(graph_token: str) -> Plan:
    """El plan de sincronización. No crea carpetas, no descarga contenido, no escribe archivos ni objetos: solo listados Graph (``dry_run``) y lecturas locales; si la lectura remota está
    autorizada, un ``head_object`` por clave prevista. ``ApisSyncError`` ante sin token / sin URL / Graph ilegible."""
    if not graph_token:
        raise ApisSyncError("No hay token de Microsoft Graph en la sesión. Cerrá sesión y volvé a ingresar para habilitar el acceso a SharePoint.")
    url = (config.SHAREPOINT_CATALOG_CSN_URL or "").strip()
    if not url:
        raise ApisSyncError("No está configurada la carpeta CSN de SharePoint (SHAREPOINT_CATALOG_CSN_URL).")
    gen = _gen()
    out, cache = _paths()
    report: dict = {}
    try:
        gen.fetch_csn_folder(url, graph_token, cache, only_kinds=APIS_KINDS, only_suffixes=(".md",), prune=False, dry_run=True, report=report)
    except Exception as exc:  # noqa: BLE001 — Graph / red: se informa, nunca se continúa con un plan a medias
        text = f"{type(exc).__name__}: {exc}"
        if "401" in text or "nauthorized" in text:
            raise ApisSyncError("Tu token de SharePoint expiró: cerrá sesión, volvé a ingresar y repetí la acción.") from exc
        raise ApisSyncError(f"No se pudo leer SharePoint (Graph): {text}") from exc

    plan = Plan(root_folders=list(report.get("root_folders", [])))
    matching = [f for f in plan.root_folders if gen._catalog_dir_kind(f) in APIS_KINDS]
    if len(matching) != 1:
        plan.errors.append("No se encontró una carpeta APIs/CDS en el origen." if not matching else
                           f"Hay más de una carpeta APIs/CDS en el origen ({', '.join(sorted(matching))}): no se elige una por el operador.")
        plan.plan_id = _plan_id(plan)
        return plan
    plan.folder = matching[0]
    apis_dir = out / "APIs"
    manifest = (gen._load_download_manifest(cache).get("folder")) or {}
    plan.local_names = sorted(p.name for p in apis_dir.glob("*.md")) if apis_dir.is_dir() else []
    remote_names = set()
    for f in sorted(report.get("files", []), key=lambda f: f["name"]):
        name, rel, stem = f["name"], f["rel"], f["name"][:-3]
        remote_names.add(name)
        local = apis_dir / name
        cached = cache / rel
        local_size = local.stat().st_size if local.is_file() else None
        tag = str(f.get("tag") or "")
        managed = rel in manifest
        detail = ""
        if stem in PROTECTED or (local.is_file() and not managed):
            status = "protected"
            detail = "explícitamente protegida" if stem in PROTECTED else "existe en APIs/ sin entrada en el manifest de descargas (no la administra este flujo)"
        elif not local.is_file():
            status = "new"
        elif cached.is_file() and _norm_text(cached) != _norm_text(local):
            status = "diverged"
        elif manifest.get(rel) == tag and cached.is_file():
            status = "unchanged"
        elif f.get("size") is not None and local_size is not None and f["size"] != local_size:
            status = "changed"
        else:
            status = "maybe"
        plan.items.append(Item(name, rel, status, f.get("size"), local_size, tag, detail))
    plan.absent = sorted(n for n in plan.local_names if n not in remote_names)
    plan.absent_cache = sorted(report.get("absent", []))
    plan.keys = _planned_keys(plan, gen, out, cache)
    if remote_store.read_enabled():
        for k in plan.keys:
            k.remote = _head(k.key)
        plan.remote_checked = True
        plan.scope_keys = _scope_listing(plan.folder) or set()
    plan.plan_id = _plan_id(plan)
    return plan


def _under(path: Path, root: Path) -> str | None:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _planned_keys(plan: Plan, gen, out: Path, cache: Path) -> list[PlannedKey]:
    """Las claves EXACTAS que el apply podría publicar: solo ``APIs/<X>.md`` y su crudo en la caché de las APIs a escribir, y el manifest de descargas (al final)."""
    keys: list[PlannedKey] = []

    def add(kind: str, path: Path):
        rel = _under(path, out)
        if rel is not None:                                                  # lo que cae fuera de catalog_local no se persiste
            keys.append(PlannedKey(kind, f"catalog_local/{rel}", _remote_key(f"catalog_local/{rel}"), str(path)))

    for item in plan.writes:
        add("cache", cache / item.rel)
        add("apis", out / "APIs" / item.name)
    if plan.writes:
        add("manifest", gen._download_manifest_path(cache))
    return keys


def _plan_id(plan: Plan) -> str:
    material = json.dumps({"folder": plan.folder, "errors": plan.errors, "items": sorted([i.name, i.status, i.tag, i.size, i.local_size] for i in plan.items),
                           "absent": plan.absent, "absent_cache": plan.absent_cache, "keys": [[k.key, k.remote] for k in plan.keys]}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def untouched_statement() -> str:
    return ("No se modifican: .catalog_manifest.json, reports/, flujos y vistas de modelos, espacios/conexiones, seguridad/DAC, Data Products, Assessments gobernados ni Case Library; "
            "tampoco se ejecuta mirror_up() global ni persist_changed.")


# ── snapshot local (para restaurar si la importación o la publicación fallan) ──────────────────────────────────────────────────────────
def _scope_files(out: Path, cache: Path, folder: str, gen) -> list[Path]:
    files = list((out / "APIs").glob("*.md")) if (out / "APIs").is_dir() else []
    cdir = cache / folder
    files += list(cdir.glob("*.md")) if cdir.is_dir() else []
    manifest = gen._download_manifest_path(cache)
    return files + ([manifest] if manifest.is_file() else [])


def _snapshot(out: Path, cache: Path, folder: str, gen) -> dict[str, bytes]:
    return {str(p): p.read_bytes() for p in _scope_files(out, cache, folder, gen)}


def _restore_local(snap: dict[str, bytes], out: Path, cache: Path, folder: str, gen) -> list[Path]:
    """Devuelve el local al snapshot tocando SOLO lo que cambió (un archivo intacto no recibe mtime nuevo: ninguna firma lo vería como cambio). Devuelve lo tocado."""
    touched: list[Path] = []
    for p in _scope_files(out, cache, folder, gen):
        if str(p) not in snap:
            p.unlink(missing_ok=True)                                         # creado por esta corrida
            touched.append(p)
    for path, data in snap.items():
        p = Path(path)
        if p.is_file() and p.read_bytes() == data:
            continue
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        touched.append(p)
    return touched


def _realign(touched: list[Path], out: Path, known: set[str], failed: frozenset[str] = frozenset()) -> None:
    """Tras restaurar el local, las firmas del ciclo periódico (``remote_store._signatures``) vuelven a describir lo que HAY en disco para las claves que el proceso ya tenía confirmadas:
    así ``mirror_up``/``sync_loop`` no re-suben contenido idéntico al de Spaces. Una clave cuyo rollback no se verificó pierde su firma (queda «cambiada»), y ``hold.fail()`` la retiene."""
    for path in touched:
        rel = _under(path, out)
        if rel is None:
            continue
        key = _remote_key(f"catalog_local/{rel}")
        if key in failed or not path.exists():
            remote_store._signatures.pop(key, None)
        elif key in known:
            remote_store._signatures[key] = remote_store._sig(path)


def _origin_diverged(plan: Plan, graph_token: str, gen, cache: Path, url: str) -> str:
    """¿Lo descargado sigue siendo lo que el operador confirmó? Compara (a) los tags que quedaron en el manifest de descargas con los del plan y (b) un listado FRESCO del origen
    con el plan: cualquier archivo con otro tag, nuevo o ausente significa que SharePoint cambió durante la descarga. Devuelve el motivo ('' si coincide)."""
    manifest = (gen._load_download_manifest(cache).get("folder")) or {}
    skewed = [i.name for i in plan.writes if manifest.get(i.rel) != i.tag]
    if skewed:
        return "tag distinto en lo descargado: " + ", ".join(skewed)
    report: dict = {}
    try:
        gen.fetch_csn_folder(url, graph_token, cache, only_kinds=APIS_KINDS, only_suffixes=(".md",), prune=False, dry_run=True, report=report)
    except Exception as exc:  # noqa: BLE001 — sin poder reverificar el origen no se publica
        return f"no se pudo reverificar el origen ({type(exc).__name__})"
    now = {f["name"]: f["tag"] for f in report.get("files", [])}
    planned = {i.name: i.tag for i in plan.items}
    changed = sorted(n for n in set(now) | set(planned) if now.get(n) != planned.get(n))
    return ("cambiaron en SharePoint: " + ", ".join(changed)) if changed else ""


# ── apply ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def apply(graph_token: str, plan_id: str, confirm: str) -> Outcome:
    """Aplica el plan confirmado. Serializado con la regeneración completa (``_CATALOG_REGEN_LOCK``)."""
    from webapp.services import admin_tools
    if (confirm or "").strip() != CONFIRM_PHRASE:
        return Outcome("aborted", [f"No se aplicó nada: falta la confirmación (escribí {CONFIRM_PHRASE})."])
    if not admin_tools._CATALOG_REGEN_LOCK.acquire(blocking=False):
        return Outcome("aborted", ["No se aplicó nada: ya hay una regeneración o sincronización en curso. Esperá unos segundos y volvé a intentar."])
    try:
        with _hold_and_guard() as hold:          # el ciclo periódico no publica nada de catalog_local mientras dura
            if not hold.exclusive:                                               # un ciclo de mirror_up sigue publicando catalog_local: nada se modifica hasta que termine
                return Outcome("aborted", ["No se aplicó nada: hay una publicación periódica en curso sobre catalog_local que no terminó a tiempo. No se modificó ningún archivo; reintentá en unos minutos."])
            outcome = _apply_locked(graph_token, plan_id)
            if outcome.state == "incomplete":
                hold.fail()                                                      # lo no confirmado queda retenido del ciclo periódico (misma garantía que la regeneración)
            return outcome
    finally:
        admin_tools._CATALOG_REGEN_LOCK.release()


def _apply_locked(graph_token: str, plan_id: str) -> Outcome:
    try:
        plan = build_plan(graph_token)                                           # plan FRESCO: se compara con el que el operador confirmó
    except ApisSyncError as exc:
        return Outcome("aborted", [f"No se aplicó nada: {exc}"])
    if plan.errors:
        return Outcome("aborted", ["No se aplicó nada: " + " ".join(plan.errors)])
    if plan.plan_id != plan_id:
        return Outcome("aborted", ["No se aplicó nada: el origen o el estado local cambiaron desde el preflight que confirmaste. Volvé a ejecutar el preflight y revisalo."])
    if not plan.writes:
        return Outcome("nothing", ["No hay nada para sincronizar: ninguna API nueva ni con cambios. No se escribió nada."])
    publish = remote_store.write_enabled()
    if publish:
        reason = remote_store.sync_blocked_reason()
        if reason:
            return Outcome("aborted", [f"No se aplicó nada: la persistencia remota está bloqueada ({reason})."])
        if any(k.key in remote_store._unreconciled for k in plan.keys):
            return Outcome("aborted", ["No se aplicó nada: hay claves del plan sin reconciliar con Spaces (restore sin sobrescribir)."])
        if any(k.remote == "no verificado" for k in plan.keys):
            return Outcome("aborted", ["No se aplicó nada: no se pudo verificar el estado actual de las claves en Spaces."])

    gen = _gen()
    out, cache = _paths()
    url = config.SHAREPOINT_CATALOG_CSN_URL.strip()
    snap = _snapshot(out, cache, plan.folder, gen)
    known = set(remote_store._signatures)                                         # claves que el ciclo periódico ya tiene confirmadas (para realinear tras una restauración)
    # 1) IMPORTACIÓN COMPLETA (local) antes de publicar nada
    try:
        gen.fetch_csn_folder(url, graph_token, cache, only_kinds=APIS_KINDS, only_suffixes=(".md",), prune=False,
                             skip_rels={i.rel for i in plan.of("protected", "diverged")})
        changed: list[str] = []
        gen._sync_apis(cache, out, folder=plan.folder, skip=plan.skip_stems, changed=changed)
    except Exception as exc:  # noqa: BLE001
        _realign(_restore_local(snap, out, cache, plan.folder, gen), out, known)
        return Outcome("aborted", [f"La importación falló ({type(exc).__name__}: {exc}). Se restauró el estado local previo; no se publicó nada."])
    diverged = _origin_diverged(plan, graph_token, gen, cache, url)               # lo descargado debe seguir siendo lo confirmado ANTES de publicar
    if diverged:
        _realign(_restore_local(snap, out, cache, plan.folder, gen), out, known)
        return Outcome("aborted", [f"SharePoint cambió durante la descarga ({diverged}): lo descargado ya no corresponde al plan que confirmaste. Se restauró el estado local previo; "
                                   "no se publicó nada. Volvé a ejecutar el preflight."])
    diff = [Path(p) for p in map(str, _scope_files(out, cache, plan.folder, gen)) if _file_sha(Path(p)) != (_sha(snap[p]) if p in snap else None)]
    planned = {k.local: k for k in plan.keys}
    outside = [str(p) for p in diff if str(p) not in planned]
    missing = [i.name for i in plan.of("new") if not (out / "APIs" / i.name).is_file()]
    if outside or missing:
        _realign(_restore_local(snap, out, cache, plan.folder, gen), out, known)
        return Outcome("aborted", ["La importación produjo un resultado distinto del plan (" + (f"fuera del plan: {', '.join(Path(o).name for o in outside)}" if outside else f"faltan: {', '.join(missing)}")
                                   + "). Se restauró el estado local previo; no se publicó nada."])
    order = {"cache": 0, "apis": 1, "manifest": 2}                                # el manifest de descargas va AL FINAL
    todo = sorted((planned[str(p)] for p in diff), key=lambda k: (order[k.kind], k.rel))
    lines = [f"Importación completa: {len(plan.writes)} API(s) del plan; {len(todo)} archivo(s) con contenido efectivamente distinto."]
    identical = [i.name for i in plan.writes if (out / "APIs" / i.name).is_file() and str(out / "APIs" / i.name) not in {str(p) for p in diff}]
    if identical:
        lines.append(f"Solo cambiaron metadatos (contenido idéntico, sin reescribir ni publicar): {', '.join(identical)}.")
    if not todo:
        return Outcome("nothing", lines + ["No hay contenido nuevo para publicar."])
    if not publish:
        return Outcome("local_only", lines + ["Persistencia remota NO autorizada en este entorno (DANWOR_PERSISTENCE ≠ remote): se actualizó solo el catálogo local; no se publicó nada en Spaces."])
    return _publish(todo, snap, out, cache, plan, gen, lines, known)


# ── publicación con backup, journal, hash por clave y rollback ────────────────────────────────────────────────────────────────────
def _journal_key(jid: str) -> str:
    return remote_store._key(f"backups/apis-sync-{jid}/journal.json")


def _write_journal(journal: dict) -> None:
    _client().put_object(Bucket=config.SPACES_BUCKET, Key=_journal_key(journal["id"]), Body=json.dumps(journal, ensure_ascii=False, indent=1).encode("utf-8"))


def _remote_bytes(path: Path) -> bytes | None:
    return remote_store.read_object_bytes(path)


def _scope_listing(folder: str) -> set[str] | None:
    """Claves que hoy existen en Spaces dentro del alcance de este modo (``APIs/`` y el subárbol de la carpeta en la caché). Solo lectura; None si no se pudo listar."""
    try:
        keys: set[str] = set()
        for prefix in (_remote_key("catalog_local/APIs/"), _remote_key(f"catalog_local/.csn_cache/{folder}/")):
            token = None
            while True:
                kwargs = {"Bucket": config.SPACES_BUCKET, "Prefix": prefix}
                if token:
                    kwargs["ContinuationToken"] = token
                page = _client().list_objects_v2(**kwargs)
                keys |= {o["Key"] for o in page.get("Contents", []) or []}
                if not page.get("IsTruncated"):
                    break
                token = page.get("NextContinuationToken")
        return keys
    except Exception:  # noqa: BLE001 — informativo: sin listado no se afirma nada sobre objetos ajenos
        return None


def _foreign_lines(plan: Plan, journal: dict) -> list[str]:
    """Objetos del subárbol que aparecieron en Spaces durante la operación y NO figuran en el journal (posible publicación de otro productor): se informan, nunca se tocan."""
    if not plan.scope_keys or not plan.folder:
        return []
    now = _scope_listing(plan.folder)
    if now is None:
        return ["No se pudo listar el subárbol de APIs en Spaces al terminar: no se puede informar si otro productor publicó objetos durante la operación."]
    foreign = sorted(k for k in now - plan.scope_keys if k not in {e["key"] for e in journal["keys"]})
    if not foreign:
        return []
    shown = ", ".join(k.rsplit("/", 1)[-1] for k in foreign[:10]) + (f" y {len(foreign) - 10} más" if len(foreign) > 10 else "")
    return [f"Aparecieron {len(foreign)} objeto(s) del subárbol de APIs en Spaces durante la operación y NO figuran en el journal (posible publicación de otro productor, p. ej. el ciclo periódico): {shown}. "
            "No se modificaron ni se revierten."]


def _preexisting_lines(journal: dict) -> list[str]:
    pre = [e for e in journal["keys"] if e["state"] == "preexisting"]
    return ([f"{len(pre)} clave(s) del plan ya estaban en Spaces con contenido IDÉNTICO al esperado (publicadas por otro productor, p. ej. el ciclo periódico): no se volvieron a escribir, no son "
             "creaciones de esta operación y ningún rollback las toca."] if pre else [])


def _publish(todo: list[PlannedKey], snap: dict[str, bytes], out: Path, cache: Path, plan: Plan, gen, lines: list[str], known: set[str]) -> Outcome:
    jid = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    bucket = config.SPACES_BUCKET
    journal = {"id": jid, "started": jid, "plan_id": plan.plan_id, "folder": plan.folder, "status": "backup", "keys": []}

    def recognize_new(entry: dict, path: Path) -> str:
        """Una clave que el plan daba por NUEVA: 'absent' (sigue ausente), 'identical' (apareció con EXACTAMENTE el contenido esperado: la publicó otro productor) o 'different' (apareció con otro contenido)."""
        current = _remote_bytes(path)
        if current is None:
            return "absent"
        return "identical" if _sha(current) == entry["after_sha"] else "different"

    def fail(msg: str, *, published: bool) -> Outcome:
        journal["status"] = "failed"
        verified = _rollback(journal, snap, out, cache, plan.folder, gen, known)
        journal["status"] = "rolled_back" if verified else "rollback_failed"
        try:
            _write_journal(journal)
        except Exception:  # noqa: BLE001
            log.exception("apis_only: no se pudo escribir el journal final")
        own = [e for e in journal["keys"] if e["state"] in ("rolled_back", "rollback_failed")]          # lo que ESTA operación llegó a intentar escribir
        text = [f"PUBLICACIÓN INCOMPLETA: {msg}"]
        if not own:
            text.append("Esta operación no escribió nada en Spaces: no hay escrituras propias que revertir. El catálogo local volvió al estado previo.")
        elif verified:
            text.append(f"Rollback de las {len(own)} escritura(s) propia(s) de esta operación verificado por hash (solo las claves del journal; no es una verificación global de Spaces). "
                        "El catálogo local volvió al estado previo.")
        else:
            text.append(f"El rollback de las escrituras propias NO pudo verificarse: el estado de esas claves en Spaces es incierto. Journal y backups en backups/apis-sync-{jid}/ (acción «Rollback» con ese id).")
        text += _preexisting_lines(journal) + _foreign_lines(plan, journal)
        return Outcome("rolled_back" if verified else "incomplete", lines + text, jid, sum(1 for e in journal["keys"] if e["state"] == "published"), verified)

    # backup previo de las claves EXISTENTES (copia en el servidor + verificación por hash) y journal recuperable ANTES del primer PUT
    try:
        for k in todo:
            entry = {"rel": k.rel, "key": k.key, "local": k.local, "kind": k.kind, "state": "pending", "before_sha": None, "after_sha": _file_sha(Path(k.local)),
                     "backup_key": None, "existed": k.remote == "existe"}
            journal["keys"].append(entry)
            if entry["existed"]:
                before = _remote_bytes(Path(k.local))
                if before is None:
                    return fail(f"la clave {k.key} cambió de estado desde el preflight (ya no existe).", published=False)
                entry["before_sha"] = _sha(before)
                entry["backup_key"] = remote_store._key(f"backups/apis-sync-{jid}/{k.rel}")
                _client().copy_object(Bucket=bucket, Key=entry["backup_key"], CopySource={"Bucket": bucket, "Key": k.key})
                if _sha(_client().get_object(Bucket=bucket, Key=entry["backup_key"])["Body"].read()) != entry["before_sha"]:
                    return fail(f"el backup de {k.key} no coincide por hash con el original.", published=False)
            else:
                seen = recognize_new(entry, Path(k.local))
                if seen == "identical":
                    entry["state"] = "preexisting"                                # ya publicada por otro productor con el contenido esperado: ni PUT ni rollback
                    entry["before_sha"] = entry["after_sha"]
                elif seen == "different":
                    return fail(f"la clave nueva {k.key} apareció en Spaces con un contenido DISTINTO al esperado (otro productor la escribió después del preflight); no se sobrescribe.", published=False)
        journal["status"] = "publishing"
        _write_journal(journal)
    except Exception as exc:  # noqa: BLE001
        return fail(f"no se pudo completar el backup o el journal ({type(exc).__name__}: {exc}).", published=False)

    lines.append(f"Backup verificado de {sum(1 for e in journal['keys'] if e['existed'])} clave(s) existente(s); journal en backups/apis-sync-{jid}/journal.json.")
    for entry in journal["keys"]:
        path = Path(entry["local"])
        if entry["state"] == "preexisting":
            continue
        try:
            if entry["existed"]:                                                  # nadie más debe haber cambiado la clave desde el backup
                current = _remote_bytes(path)
                if current is None or _sha(current) != entry["before_sha"]:
                    return fail(f"la clave {entry['key']} fue modificada por otro proceso durante la sincronización.", published=True)
            else:                                                                 # y una clave nueva no debe haber aparecido entre el chequeo y el PUT
                seen = recognize_new(entry, path)
                if seen == "identical":
                    entry["state"] = "preexisting"
                    entry["before_sha"] = entry["after_sha"]
                    continue
                if seen == "different":
                    return fail(f"la clave nueva {entry['key']} apareció en Spaces con un contenido DISTINTO al esperado justo antes de publicarla; no se sobrescribe.", published=True)
            entry["state"] = "attempted"
            if not remote_store.push_path(path):
                return fail(f"no se confirmó la publicación de {entry['key']}.", published=True)
            published = _remote_bytes(path)
            if published is None or _sha(published) != entry["after_sha"]:
                return fail(f"la verificación por hash de {entry['key']} no coincide tras publicarla.", published=True)
            entry["state"] = "published"
            _write_journal(journal)
        except Exception as exc:  # noqa: BLE001
            return fail(f"falló la publicación de {entry['key']} ({type(exc).__name__}: {exc}).", published=True)
    for entry in journal["keys"]:                                                 # lo reconocido como preexistente debe seguir siendo exactamente lo esperado
        if entry["state"] == "preexisting":
            current = _remote_bytes(Path(entry["local"]))
            if current is None or _sha(current) != entry["after_sha"]:
                return fail(f"la clave {entry['key']}, reconocida como ya publicada, cambió antes de cerrar la operación.", published=True)
    journal["status"] = "complete"
    try:
        _write_journal(journal)
    except Exception as exc:  # noqa: BLE001
        return fail(f"no se pudo cerrar el journal ({type(exc).__name__}: {exc}).", published=True)
    own = [e for e in journal["keys"] if e["state"] == "published"]
    lines.append(f"Publicación verificada por hash: {len(own)} clave(s) publicadas por esta operación ({sum(1 for e in own if not e['existed'])} nueva(s), "
                 f"{sum(1 for e in own if e['existed'])} existente(s) con backup); el .download_manifest se publicó al final.")
    lines += _preexisting_lines(journal) + _foreign_lines(plan, journal)
    return Outcome("ok", lines, jid, len(own))


def _rollback(journal: dict, snap: dict[str, bytes] | None, out: Path, cache: Path, folder: str, gen, known: set[str] | None = None) -> bool:
    """Deshace en ORDEN INVERSO lo intentado y verifica por hash: existentes ← backup, nuevas ← borradas; luego alinea el local (snapshot en memoria o, sin él, el backup) y las
    firmas del ciclo periódico: lo verificado queda «ya confirmado» (nada que re-subir) y lo no verificado queda «cambiado» para que ``hold.fail()`` lo retenga."""
    bucket, ok = config.SPACES_BUCKET, True
    known = set(remote_store._signatures) if known is None else known
    for entry in reversed(journal["keys"]):
        if entry["state"] in ("pending", "preexisting"):
            continue                                                              # nunca se intentó, o la publicó otro productor: no es una escritura propia, el rollback no la toca
        path = Path(entry["local"])
        try:
            if entry["existed"]:
                _client().copy_object(Bucket=bucket, Key=entry["key"], CopySource={"Bucket": bucket, "Key": entry["backup_key"]})
                restored = _remote_bytes(path)
                good = restored is not None and _sha(restored) == entry["before_sha"]
            else:
                remote_store.delete_path(path)
                good = _remote_bytes(path) is None
            entry["state"] = "rolled_back" if good else "rollback_failed"
            ok = ok and good
        except Exception:  # noqa: BLE001
            log.exception("apis_only: rollback de %s", entry.get("key"))
            entry["state"] = "rollback_failed"
            ok = False
    failed = frozenset(e["key"] for e in journal["keys"] if e["state"] == "rollback_failed")
    touched: list[Path] = []
    try:
        if snap is not None:
            touched = _restore_local(snap, out, cache, folder, gen)
        else:
            for entry in journal["keys"]:
                path = Path(entry["local"])
                if entry["existed"] and entry.get("backup_key"):
                    data = _client().get_object(Bucket=bucket, Key=entry["backup_key"])["Body"].read()
                    if not (path.is_file() and path.read_bytes() == data):
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_bytes(data)
                        touched.append(path)
                elif not entry["existed"] and path.exists():
                    path.unlink(missing_ok=True)
                    touched.append(path)
    except Exception:  # noqa: BLE001
        log.exception("apis_only: no se pudo alinear el local tras el rollback")
        ok = False
    for entry in journal["keys"]:                                                  # las claves que se tocaron en Spaces entran siempre a la realineación (están en «known» si se publicaron)
        p = Path(entry["local"])
        if p not in touched and entry["state"] in ("rolled_back", "rollback_failed"):
            touched.append(p)
    _realign(touched, out, known | {e["key"] for e in journal["keys"] if e["state"] == "rolled_back"}, failed)
    return ok


def rollback(journal_id: str, confirm: str) -> Outcome:
    """Rollback explícito de una sincronización anterior desde su journal en Spaces (el id solo admite el formato ``AAAAMMDDTHHMMSSZ``)."""
    from webapp.services import admin_tools
    if not _JOURNAL_ID.match(journal_id or ""):
        return Outcome("aborted", ["Identificador de journal inválido."])
    if (confirm or "").strip() != ROLLBACK_PHRASE:
        return Outcome("aborted", [f"No se hizo nada: falta la confirmación (escribí {ROLLBACK_PHRASE})."])
    if not remote_store.write_enabled():
        return Outcome("aborted", ["La persistencia remota no está autorizada en este entorno: no hay nada que revertir en Spaces."])
    if not admin_tools._CATALOG_REGEN_LOCK.acquire(blocking=False):
        return Outcome("aborted", ["Ya hay una regeneración o sincronización en curso."])
    try:
        with _hold_and_guard() as hold:
            if not hold.exclusive:
                return Outcome("aborted", ["No se hizo nada: hay una publicación periódica en curso sobre catalog_local que no terminó a tiempo. Reintentá en unos minutos."])
            try:
                journal = json.loads(_client().get_object(Bucket=config.SPACES_BUCKET, Key=_journal_key(journal_id))["Body"].read())
            except Exception as exc:  # noqa: BLE001
                return Outcome("aborted", [f"No se pudo leer el journal {journal_id}: {type(exc).__name__}: {exc}"])
            out, cache = _paths()
            verified = _rollback(journal, None, out, cache, journal.get("folder") or "", _gen())
            if not verified:
                hold.fail()                                                       # lo no verificado no lo puede publicar el ciclo periódico
            journal["status"] = "rolled_back" if verified else "rollback_failed"
            _write_journal(journal)
            return Outcome("ok" if verified else "incomplete", [("Rollback verificado por hash." if verified else "El rollback NO pudo verificarse por completo: revisá el journal.")], journal_id,
                           rollback_verified=verified)
    finally:
        admin_tools._CATALOG_REGEN_LOCK.release()
