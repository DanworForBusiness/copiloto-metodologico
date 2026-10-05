"""
assessment_service.py — integración DELGADA Catálogo ↔ Assessment de modelado.

Responsabilidades (y solo esas):

  * resolver una ``CatalogEntry`` → su Modelo Analítico (AM) terminal, con el
    grafo de artefactos del pool CSN (NO por nombre de directorio, ni por
    similitud/patrón de nombres: ver ``resolve_entry``);
  * calcular/leer la llave de vigencia y exponer el ESTADO;
  * invocar la API Python EXISTENTE del paquete ``modeling_assessment``
    (``shadow.assess``) SOLO en la generación explícita (``generate``, POST);
  * validar el perfil canónico y cachearlo (memoria + disco operativo);
  * exportar JSON canónico y Markdown (``render_markdown``) del MISMO perfil;
  * single-flight por (cliente, AM) y sobre de falla estable.

NO razona sobre el modelo, NO reinterpreta hechos, NO formatea el informe (eso es
``assessment_presenter`` + la plantilla Jinja), NO hace shell-out.

CONTRATO DE LECTURA: ``status_for_entry`` / ``statuses_for_entries`` JAMÁS
ejecutan el motor ni escriben la caché. Solo leen el pool (huella de tamaños y
mtimes, más un hash de contenido memoizado por huella) y la caché. Un miss de
caché es ``NOT_YET_ASSESSED``.

ALMACÉN DURABLE — sobre separado del esquema canónico (``assessment-cache/v1``): el documento del assessment NO se toca para el UI.
El assessment GOBERNADO del Catálogo (lo que produce ``generate``: Publicar → Assessment y "Generar informe", MISMA función) se
persiste bajo ``config.governed_assessments_dir()`` (``governed-assessments/v1/<cliente>/<AM>__<tag>.json``), directorio registrado en
``PERSIST_PATHS``: ``core.remote_store`` lo restaura al arrancar y lo sube (write-through ``push_path`` + ``mirror_up``), sin cliente
Spaces propio. Sobrevive a reinicio y deploy; la vigencia se sigue evaluando contra la firma del pool y las versiones (jamás se asume
CURRENT). El Assessment AD-HOC NO pasa por acá: sigue efímero con TTL en ``ASSESSMENT_CACHE_DIR/adhoc`` (nunca espejado a Spaces).

VIGENCIA (V1): ``input_pool_signature`` + bloque COMPLETO de ``versions``. La
firma del pool es de TODO el cliente: si cambia cualquier modelo del cliente, todos
sus AM pasan a STALE (falso STALE posible, JAMÁS falso CURRENT). No hay huella por
AM todavía — limitación conocida de V1.

SINGLE-FLIGHT: ``threading`` en proceso (el Procfile corre UN proceso uvicorn).
Con varios procesos/instancias cada uno tendría su propia caché y su propio lock.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import config
from core import remote_store
from tools.catalog.modeling_assessment import paths as ma_paths
from tools.catalog.modeling_assessment import profile as ma_profile
from tools.catalog.modeling_assessment import shadow as ma_shadow
from tools.catalog.modeling_assessment.render_md import render_markdown
from tools.catalog.modeling_assessment.scope import AssessmentUniverse, UnknownTerminalAmError

log = logging.getLogger("danwor.assessment")

ENVELOPE_VERSION = "assessment-cache/v1"

# ── estados (contrato de producto) ──────────────────────────────────────────
CURRENT = "CURRENT"
STALE = "STALE"
NOT_YET_ASSESSED = "NOT_YET_ASSESSED"
NOT_APPLICABLE = "NOT_APPLICABLE"
FAILED = "FAILED"
INCOMPATIBLE_SCHEMA = "INCOMPATIBLE_SCHEMA"
STATES = (CURRENT, STALE, NOT_YET_ASSESSED, NOT_APPLICABLE, FAILED, INCOMPATIBLE_SCHEMA)
VIEWABLE_STATES = (CURRENT, STALE)

# Causa de un STALE (NO es un estado publico nuevo: los seis estados no cambian). Sirve para el mensaje al usuario y para decidir que migracion aplica.
STALE_VERSION_ONLY = "VERSION_ONLY"                    # misma entrada (firma del pool igual); cambio el contrato de evidencia/metodologia (versiones)
STALE_INPUT_CHANGED = "INPUT_CHANGED"                  # cambio la entrada tecnica (firma del pool); mismas versiones
STALE_VERSION_AND_INPUT = "VERSION_AND_INPUT"          # ambas
STALE_REASONS = (STALE_VERSION_ONLY, STALE_INPUT_CHANGED, STALE_VERSION_AND_INPUT)

# ── códigos de falla estables → mensaje de cara al usuario (español, sin traza) ──
ERROR_MESSAGES = {
    "POOL_UNAVAILABLE": ("No hay datos de entrada disponibles para evaluar este modelo. "
                         "Regenerá el catálogo (o esperá a que termine la restauración) y reintentá."),
    "POOL_AMBIGUOUS_DOMAIN": "El cliente tiene más de un conjunto de artefactos y no se pudo elegir uno.",
    "POOL_LOAD_ERROR": "No se pudo cargar el conjunto de artefactos del cliente.",
    "AM_NOT_TERMINAL": "El modelo ya no figura como modelo analítico terminal del cliente.",
    "GENERATION_ERROR": "No se pudo generar el assessment. Reintentá; si persiste, avisá al equipo.",
    "INVALID_PROFILE": "El assessment generado no superó la validación del esquema.",
}
NA_REASONS = {
    "NO_TERMINAL_AM": "Este flujo no contiene un modelo analítico terminal que evaluar.",
    "MULTIPLE_TERMINAL_AMS": ("Este flujo contiene más de un modelo analítico terminal; "
                              "el assessment se emite por modelo y no se elige uno por inferencia."),
}
INCOMPATIBLE_MESSAGE = "El informe guardado tiene una versión de esquema que esta aplicación no reconoce."


# ═══════════════════════════ vigencia: clasificador PURO y registro de migraciones de evidencia ═══════════════════════════
@dataclass(frozen=True)
class MigrationTarget:
    """Migracion de contrato de evidencia (registro chico y explicito; NO es un framework). ``target_keys``: claves del bloque ``versions`` que cambian;
    ``eligible_old``: valores VIEJOS elegibles por clave (``None`` = la clave no existia en el perfil viejo). El valor OBJETIVO sale siempre de
    ``current_assessment_versions()`` (constantes reales del paquete). Toda clave NO objetivo debe coincidir ya con el valor vigente."""
    name: str
    target_keys: tuple[str, ...]
    eligible_old: dict
    description: str
    closure_policy: str = "CLOSURE_EQUAL"       # CLOSURE_EQUAL | TARGETED_CLOSURE_EXPANSION (ver ``migration_postcheck``)


CLOSURE_EQUAL, TARGETED_CLOSURE_EXPANSION = "CLOSURE_EQUAL", "TARGETED_CLOSURE_EXPANSION"

# Registro de migraciones de evidencia: pequeno, explicito y SIN encadenamiento automatico. Cada migracion cubre EXACTAMENTE una diferencia de versiones; un perfil
# cuya diferencia es la de OTRA migracion se reporta como tal (``applicable_migration``) y no se migra por esta.
#   * ``evidence_g1_g3_v1`` (perfil ANTERIOR a G1/G3: sin ``canonical_evidence``, signals v2, assessability v1, finding_evidence v1). Sigue registrada: un perfil historico
#     puede existir o restaurarse en ese contrato aunque produccion ya migro a otro cliente. IMPORTANTE: el motor de ESTE build solo emite el contrato VIGENTE; no puede
#     fabricar el contrato intermedio G1/G3. Por eso, con este build, la migracion lleva el perfil al contrato vigente en UN paso explicito del operador (por eso sus claves
#     objetivo incluyen ``confidence`` y ``closure``). Para dejar un perfil en el contrato intermedio G1/G3 hay que correrla desde un despliegue anterior al evidence release.
#   * ``evidence_closure_contract_v2`` (perfil en el contrato G1/G3 vigente en 11ce071 -> evidence release).
# Ambas regeneran con el MISMO motor, por lo que ambas aceptan el cierre solo bajo ``TARGETED_CLOSURE_EXPANSION``.
EVIDENCE_MIGRATIONS: dict[str, MigrationTarget] = {
    "evidence_g1_g3_v1": MigrationTarget(
        "evidence_g1_g3_v1", ("signals", "assessability", "finding_evidence", "canonical_evidence", "confidence", "closure"),
        {"signals": frozenset({"signal-rules/v2"}), "assessability": frozenset({"assessability-rules/v1"}),
         "finding_evidence": frozenset({"finding-evidence/v1"}), "canonical_evidence": frozenset({None}),
         "confidence": frozenset({"confidence-cuts/v1"}), "closure": frozenset({None})},
        "Gate 4 (perfil anterior a G1/G3): procedencia canonica de hojas (G1) + semantica estructural de derivaciones (G3) + canonical_evidence; con este build aterriza en el "
        "contrato vigente en un solo paso explicito (no encadena otra migracion)",
        TARGETED_CLOSURE_EXPANSION),
    "evidence_closure_contract_v2": MigrationTarget(
        "evidence_closure_contract_v2", ("assessability", "confidence", "canonical_evidence", "closure"),
        {"assessability": frozenset({"assessability-rules/v2"}), "confidence": frozenset({"confidence-cuts/v1"}),
         "canonical_evidence": frozenset({"canonical-evidence/v1"}), "closure": frozenset({None})},
        "Evidence release: cierre semantico corregido (los targets de flujo que Modeling lee son miembros) + completitud del cierre + contrato publicado + armonizacion + "
        "expresiones por clausula + reconvergencia + complejidad de vistas + propiedades de carga (canonical_evidence v2, assessability v3, confidence v2, closure v1)",
        TARGETED_CLOSURE_EXPANSION),
}


@dataclass(frozen=True)
class Currentness:
    """Resultado del clasificador de vigencia (puro). ``state`` es CURRENT o STALE (los demas estados salen de otras condiciones). Para STALE:
    ``reason`` (VERSION_ONLY | INPUT_CHANGED | VERSION_AND_INPUT), ``version_diff`` (claves del bloque ``versions`` que difieren) y, para VERSION_ONLY,
    ``targeted`` = nombre de la migracion de evidencia que cubre EXACTAMENTE esa diferencia (o None = VERSION_ONLY_UNTARGETED)."""
    state: str
    reason: str | None = None
    version_diff: tuple[str, ...] = ()
    pool_same: bool = True
    targeted: str | None = None

    @property
    def version_only_untargeted(self) -> bool:
        return self.state == STALE and self.reason == STALE_VERSION_ONLY and self.targeted is None


def version_diff(old: dict | None, new: dict | None) -> tuple[str, ...]:
    old, new = old or {}, new or {}
    return tuple(sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k)))


def targeted_migration(old_versions: dict | None, versions: dict) -> str | None:
    """Migracion del registro que cubre EXACTAMENTE la diferencia entre las versiones guardadas y las vigentes (mismas claves, valores viejos conocidos,
    toda otra clave ya igual al vigente); None si ninguna."""
    diff = set(version_diff(old_versions, versions))
    old = old_versions or {}
    for m in EVIDENCE_MIGRATIONS.values():
        if diff == set(m.target_keys) and all(old.get(k) in m.eligible_old[k] for k in m.target_keys):
            return m.name
    return None


def classify_currentness(stored_signature: str | None, stored_versions: dict | None, pool_signature: str | None, versions: dict) -> Currentness:
    """Pura: CURRENT si la firma del pool y el bloque COMPLETO de versiones coinciden; si no, STALE con su causa. Mismo criterio que siempre."""
    pool_same = stored_signature == pool_signature
    diff = version_diff(stored_versions, versions)
    if pool_same and not diff:
        return Currentness(CURRENT)
    if pool_same:
        return Currentness(STALE, STALE_VERSION_ONLY, diff, True, targeted_migration(stored_versions, versions))
    return Currentness(STALE, STALE_INPUT_CHANGED if not diff else STALE_VERSION_AND_INPUT, diff, False, None)


# ═══════════════════════════ modelo de datos ═══════════════════════════
@dataclass(frozen=True)
class AssessmentStatus:
    state: str
    entry_id: str
    client: str
    terminal_am: str | None
    reason: str | None = None            # español; NOT_APPLICABLE / FAILED / INCOMPATIBLE_SCHEMA
    error_code: str | None = None
    generated_at: str | None = None
    profile: dict | None = None          # perfil canónico validado (CURRENT / STALE / FAILED con último perfil previo)
    previous_profile_retained: bool = False
    durable: str | None = field(default=None, compare=False)   # solo tras ``generate``: "remote" | "local" | "memory" (ver ``_store_envelope``)
    stale_reason: str | None = field(default=None, compare=False)       # solo STALE: VERSION_ONLY | INPUT_CHANGED | VERSION_AND_INPUT
    stale_targeted: str | None = field(default=None, compare=False)     # solo STALE VERSION_ONLY: nombre de la migracion de evidencia que lo cubre (None = no cubierto)
    stale_version_diff: tuple = field(default=(), compare=False)        # solo STALE: claves de ``versions`` que difieren

    @property
    def viewable(self) -> bool:
        return self.profile is not None and self.state in (CURRENT, STALE, FAILED)

    @property
    def can_generate(self) -> bool:
        return self.state in (NOT_YET_ASSESSED, STALE, FAILED, INCOMPATIBLE_SCHEMA)

    @property
    def summary(self) -> dict | None:
        return headline_summary(self.profile) if self.profile is not None and self.state in (CURRENT, STALE) else None


def headline_summary(profile: dict | None) -> dict | None:
    """Datos MÍNIMOS de la cabecera del flujo, leídos del JSON canónico (sin reinterpretar nada)."""
    if not profile or profile.get("assessment_state") != "ASSESSED":
        return None
    s = profile["summary"]
    review = profile["review_required"]
    obs = profile["observations"]
    return {
        "strict": s["coverage"]["strict"],
        "confidence": s["confidence"]["level"],
        "review_occurrences": sum(g["occurrence_count"] for g in review),
        "review_groups": len(review),
        "observation_occurrences": sum(g["occurrence_count"] for g in obs),
        "observation_groups": len(obs),
    }


@dataclass(frozen=True)
class Resolution:
    """Entrada de Catálogo → AM terminal. ``kind``: OK | NO_TERMINAL_AM | MULTIPLE_TERMINAL_AMS |
    POOL_UNAVAILABLE | POOL_AMBIGUOUS_DOMAIN | POOL_LOAD_ERROR."""
    kind: str
    client: str
    terminal_am: str | None = None
    client_dir: str | None = None
    domain_dir: str | None = None
    pool_signature: str | None = None
    pool_root: Path | None = field(default=None, compare=False)


# ═══════════════════════════ pool CSN ═══════════════════════════
def catalog_dir() -> Path:
    return Path(os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(config.BASE_DIR / "catalog_local"))


def pool_root() -> Path | None:
    """Raíz del pool de crudos que LEYÓ el generador del catálogo (``admin_tools.run_catalog_generation``):
    ``DANWOR_CATALOG_CSN_CACHE`` o ``<catalog_local>/.csn_cache`` (deploy con SharePoint); si no existe o está
    vacío, el stand-in local ``DANWOR_CATALOG_INBOUND_DIR`` / ``catalog_inbound`` (desarrollo). None si no hay ninguno.
    Solo se LEE; jamás se copia ni se escribe nada ahí."""
    cache = Path(os.getenv("DANWOR_CATALOG_CSN_CACHE", "").strip() or str(catalog_dir() / ".csn_cache"))
    inbound = Path(os.getenv("DANWOR_CATALOG_INBOUND_DIR", "").strip() or str(config.BASE_DIR / "catalog_inbound"))
    for cand in (cache, inbound):
        try:
            if cand.is_dir() and any(cand.iterdir()):
                return cand
        except OSError:
            continue
    return None


@dataclass
class _PoolIndex:
    fingerprint: tuple
    signature: str | None
    terminal_ams: tuple[str, ...]
    error: str | None


_index_lock = threading.RLock()
_index_memo: dict[tuple, _PoolIndex] = {}


def invalidate_pool_index() -> None:
    """Olvida las huellas del pool (se llama junto con ``_invalidate_catalog_cache``). La vigencia se
    recalcula en el próximo acceso; la huella de tamaños/mtimes ya detecta cambios sin esta llamada."""
    with _index_lock:
        _index_memo.clear()


def _stat_fingerprint(files: Iterable[Path]) -> tuple:
    out = []
    for p in files:
        st = p.stat()
        out.append((str(p), st.st_size, st.st_mtime_ns))
    return tuple(sorted(out))


def _index_for(root: Path, key: tuple[str, str], group: dict) -> _PoolIndex:
    files = [*group["jsons"], *group["txts"]]
    fp = _stat_fingerprint(files)
    memo_key = (str(root), key)
    with _index_lock:
        cached = _index_memo.get(memo_key)
        if cached is not None and cached.fingerprint == fp:
            return cached
        signature = ma_shadow.pool_signature(root, files)
        try:
            terminals = AssessmentUniverse.from_entities(ma_shadow.load_pool(group)).terminal_ams
            idx = _PoolIndex(fp, signature, tuple(terminals), None)
        except ma_shadow.AssessmentPoolLoadError as exc:
            log.warning("assessment: pool no cargable para %s: %s", key, exc)
            idx = _PoolIndex(fp, signature, (), "POOL_LOAD_ERROR")
        _index_memo[memo_key] = idx
        return idx


def resolve_entries(entries: Iterable[Any]) -> dict[str, Resolution]:
    """``entry_id`` → ``Resolution`` para un lote de entradas (una sola pasada de pool por cliente)."""
    entries = list(entries)
    root = pool_root()
    out: dict[str, Resolution] = {}
    per_client: dict[str, tuple[str, Any, _PoolIndex | None]] = {}
    for e in entries:
        client = str(e.cliente)
        if root is None:
            out[e.entry_id] = Resolution("POOL_UNAVAILABLE", client)
            continue
        if client.lower() not in per_client:
            tag, payload = _select(root, client, str(e.dominio))
            idx = _index_for(root, payload[0], payload[1]) if tag == "OK" else None
            per_client[client.lower()] = (tag, payload, idx)
        tag, payload, idx = per_client[client.lower()]
        if tag != "OK":
            out[e.entry_id] = Resolution(tag, client, pool_root=root)
            continue
        key = payload[0]
        if idx.error:
            out[e.entry_id] = Resolution(idx.error, client, key[0], key[1], idx.signature, root)
            continue
        # AM terminal = intersección de los artefactos del flujo con los AM terminales del GRAFO del pool.
        names = {str(a) for a in (e.artefactos or [])}
        hits = sorted(names & set(idx.terminal_ams))
        if len(hits) == 1:
            out[e.entry_id] = Resolution("OK", client, hits[0], key[0], key[1], idx.signature, root)
        else:
            kind = "NO_TERMINAL_AM" if not hits else "MULTIPLE_TERMINAL_AMS"
            out[e.entry_id] = Resolution(kind, client, None, key[0], key[1], idx.signature, root)
    return out


def pool_json_files(client: str, domain_label: str = "") -> tuple[Path, list[Path]] | None:
    """(raíz del pool, archivos .json elegibles del cliente) con la MISMA resolución cliente/dominio que el Assessment; None si no hay pool o el
    cliente no figura / es ambiguo. Solo LECTURA."""
    root = pool_root()
    if root is None:
        return None
    tag, payload = _select(root, client, domain_label)
    return (root, list(payload[1]["jsons"])) if tag == "OK" else None


def pool_client_files(client: str) -> tuple[Path, list[Path]] | None:
    """(raíz del pool, TODOS los .json elegibles del cliente — de todos sus dominios) o None si no hay pool o el cliente no figura. Solo LECTURA.
    Es el inventario técnico disponible del cliente (no la resolución de un Assessment concreto)."""
    root = pool_root()
    if root is None:
        return None
    wanted = (client or "").strip().lower()
    files: list[Path] = []
    for (cli, _dom), group in sorted(ma_shadow.bridge.legacy_pool_groups(root).items()):
        if wanted and cli.lower() == wanted:
            files.extend(group["jsons"])
    return (root, sorted(set(files))) if files else None


def _select(root: Path, client: str, domain_label: str):
    try:
        return "OK", ma_shadow.select_pool(root, client)
    except ma_shadow.ClientNotFoundError:
        return "POOL_UNAVAILABLE", None
    except ma_shadow.AmbiguousClientDomainError:
        try:
            return "OK", ma_shadow.select_pool(root, client, domain_label)
        except (ma_shadow.ClientNotFoundError, ma_shadow.AmbiguousClientDomainError):
            return "POOL_AMBIGUOUS_DOMAIN", None


def resolve_entry(entry: Any) -> Resolution:
    return resolve_entries([entry])[entry.entry_id]


# ═══════════════════════════ versiones y sobre de caché ═══════════════════════════
def current_assessment_versions() -> dict:
    """Bloque ``versions`` del código ACTUAL, tomado del propio paquete (sin duplicar las constantes ni correr
    el motor): el constructor INDETERMINATE emite exactamente el mismo bloque que un assessment completo."""
    doc = ma_profile.build_indeterminate_assessment(client="", requested_model=None, message="")
    return dict(doc["versions"])


def _artifact_signature(profile: dict) -> str:
    return hashlib.sha256(ma_profile.canonical_assessment_json(profile).encode("utf-8")).hexdigest()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _envelope(client: str, am: str, *, status: str, profile: dict | None, pool_signature: str | None,
              versions: dict | None, error_code: str | None = None, generated_at: str | None = None) -> dict:
    return {
        "envelope_version": ENVELOPE_VERSION,
        "client": client,
        "terminal_am": am,
        "generated_at": generated_at or _now(),
        "assessment_schema_version": (profile or {}).get("schema_version", ma_profile.ASSESSMENT_SCHEMA_VERSION),
        "assessment_versions": versions,
        "input_pool_signature": pool_signature,
        "assessment_artifact_signature": _artifact_signature(profile) if profile is not None else None,
        "status": status,
        "profile_json": profile,
        "error_code": error_code,
    }


# ═══════════════════════════ almacén: memoria + disco DURABLE (gobernado) ═══════════════════════════
_cache_lock = threading.RLock()
_memory: dict[tuple[str, str], dict] = {}
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")
GOVERNED_LAYOUT_VERSION = "v1"
DURABLE_REMOTE, DURABLE_LOCAL, DURABLE_MEMORY = "remote", "local", "memory"
_RESTORE_WAIT_S = 5.0                   # tope de espera, UNA vez por lote de lectura, a que `remote_store.restore()` termine de bajar el estado


def reset_memory_cache() -> None:
    with _cache_lock:
        _memory.clear()


def _key(client: str, am: str) -> tuple[str, str]:
    return (client.lower(), am)


def _is_within(child: Path, parent: Path) -> bool:
    """Contención de rutas (resuelve symlinks/relativas). Utilidad local: no es lógica del assessment."""
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def cache_dir() -> Path | None:
    """Directorio de la caché EFÍMERA (la usa el assessment AD-HOC, subdirectorio ``adhoc``), o None (solo memoria) si cae bajo un
    directorio persistido/espejado, bajo ``catalog_local`` o bajo el pool. FAIL-CLOSED: no se escribe jamás donde ``mirror_up()`` pueda subirlo."""
    d = Path(config.ASSESSMENT_CACHE_DIR)
    forbidden = [catalog_dir(), *( [pool_root()] if pool_root() else [] )]
    try:
        hit = ma_paths.persisted_dir_containing(d)
    except Exception:                                   # noqa: BLE001 — si no puede verificarse, no se escribe
        log.exception("assessment: no se pudo verificar la ruta de caché; se usa solo memoria")
        return None
    if hit is None:
        for f in forbidden:
            if _is_within(d, f):
                hit = f
                break
    if hit is not None:
        log.error("assessment: ASSESSMENT_CACHE_DIR %s cae bajo %s (persistido/gobernado): caché de disco DESACTIVADA", d, hit)
        return None
    return d


def governed_dir() -> Path | None:
    """Raíz del almacén durable del assessment gobernado, o None (solo memoria) si cayera bajo el catálogo o el pool: ese directorio es
    otra fuente gobernada y no debe mezclarse ni espejarse dos veces."""
    d = config.governed_assessments_dir()
    for f in [catalog_dir(), *([pool_root()] if pool_root() else [])]:
        if _is_within(d, f):
            log.error("assessment: el almacén gobernado %s cae bajo %s: persistencia durable DESACTIVADA", d, f)
            return None
    return d


def governed_path(client: str, am: str) -> Path | None:
    """``<raíz>/v1/<cliente>/<AM>__<tag>.json``. Clave determinística del Catálogo (cliente en minúsculas + AM); el nombre se sanea
    (sin etiquetas de display ni segmentos de ruta) y el tag evita colisiones entre nombres que saneados coinciden."""
    d = governed_dir()
    if d is None:
        return None
    tag = hashlib.sha1(f"{client.lower()}\0{am}".encode("utf-8")).hexdigest()[:10]
    client_dir = _SAFE.sub("_", client).strip(".") or "_"
    return d / GOVERNED_LAYOUT_VERSION / client_dir / f"{_SAFE.sub('_', am).strip('.') or '_'}__{tag}.json"


def _read_governed(path: Path, client: str, am: str) -> dict | None:
    """Sobre durable validado, o None. FAIL-CLOSED: archivo ilegible/ajeno/alterado o de otra identidad = no evaluado (nunca CURRENT a ciegas)."""
    try:
        env = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(env, dict) or env.get("envelope_version") != ENVELOPE_VERSION:
            raise ValueError("sobre desconocido")
        if str(env.get("client", "")).lower() != client.lower() or env.get("terminal_am") != am:
            raise ValueError("identidad del sobre distinta de la llave")
        prof = env.get("profile_json")
        if prof is not None and env.get("assessment_artifact_signature") != _artifact_signature(prof):
            raise ValueError("firma del artefacto no coincide")
    except Exception:                                   # noqa: BLE001 — archivo corrupto/ajeno/alterado = no evaluado
        log.warning("assessment: sobre durable ilegible (%s); se trata como no evaluado", path, exc_info=True)
        return None
    return env


def _load_envelope(client: str, am: str) -> dict | None:
    with _cache_lock:
        mem = _memory.get(_key(client, am))
    if mem is not None:
        return mem
    path = governed_path(client, am)
    if path is None or not path.is_file():
        return None
    env = _read_governed(path, client, am)
    if env is None:
        return None
    with _cache_lock:
        _memory[_key(client, am)] = env
    return env


def _store_envelope(env: dict) -> str:
    """Persiste el sobre canónico: memoria + archivo atómico local + write-through a Spaces (``remote_store.push_path``; el ciclo
    ``mirror_up`` reintenta si falla). Devuelve el nivel de durabilidad REALMENTE logrado: ``remote`` (PUT remoto confirmado),
    ``local`` (escrito en disco; sin Spaces configurado o con el PUT pendiente) o ``memory`` (no se pudo escribir). Un fallo nunca
    corrompe un sobre bueno previo (tmp + os.replace) ni se informa como éxito."""
    with _cache_lock:
        _memory[_key(env["client"], env["terminal_am"])] = env
    path = governed_path(env["client"], env["terminal_am"])
    if path is None:
        return DURABLE_MEMORY
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(env, fh, ensure_ascii=False)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    except OSError:
        log.error("assessment: no se pudo escribir el almacén durable (%s); queda SOLO en memoria", path, exc_info=True)
        return DURABLE_MEMORY
    if not remote_store.enabled():
        return DURABLE_LOCAL
    if remote_store.push_path(path):
        return DURABLE_REMOTE
    log.warning("assessment: escrito en disco pero NO confirmado en Spaces (%s): queda local; mirror_up reintenta "
                "(si el directorio no figura en SPACES_PERSIST_PATHS, sumá 'governed-assessments')", path)
    return DURABLE_LOCAL


# ═══════════════════════════ estado (solo lectura) ═══════════════════════════
def _failed_message(code: str | None) -> str:
    return ERROR_MESSAGES.get(code or "", ERROR_MESSAGES["GENERATION_ERROR"])


def _status_from(entry: Any, res: Resolution, versions: dict) -> AssessmentStatus:
    eid, client = entry.entry_id, res.client
    if res.kind in ("NO_TERMINAL_AM", "MULTIPLE_TERMINAL_AMS"):
        return AssessmentStatus(NOT_APPLICABLE, eid, client, None, reason=NA_REASONS[res.kind])
    if res.kind != "OK":
        # Sin pool o pool ilegible: no hay AM resoluble ni nada que mostrar. Se ofrece reintentar (POST) solo si
        # el pool está presente; sin pool el POST devolverá la falla estable POOL_UNAVAILABLE.
        if res.kind == "POOL_UNAVAILABLE":
            return AssessmentStatus(NOT_YET_ASSESSED, eid, client, None)
        return AssessmentStatus(FAILED, eid, client, None, reason=_failed_message(res.kind), error_code=res.kind)
    am = res.terminal_am
    env = _load_envelope(client, am)
    if env is None:
        return AssessmentStatus(NOT_YET_ASSESSED, eid, client, am)
    prof = env.get("profile_json")
    if env.get("status") == "FAILED":
        code = env.get("error_code")
        return AssessmentStatus(FAILED, eid, client, am, reason=_failed_message(code), error_code=code,
                                generated_at=env.get("generated_at"), profile=prof, previous_profile_retained=prof is not None)
    if env.get("assessment_schema_version") != ma_profile.ASSESSMENT_SCHEMA_VERSION or not _valid_profile(env):
        return AssessmentStatus(INCOMPATIBLE_SCHEMA, eid, client, am, reason=INCOMPATIBLE_MESSAGE, error_code="INCOMPATIBLE_SCHEMA",
                                generated_at=env.get("generated_at"))
    cur = classify_currentness(env.get("input_pool_signature"), env.get("assessment_versions"), res.pool_signature, versions)
    if cur.state == CURRENT:
        return AssessmentStatus(CURRENT, eid, client, am, generated_at=env.get("generated_at"), profile=prof)
    return AssessmentStatus(STALE, eid, client, am, generated_at=env.get("generated_at"), profile=prof, stale_reason=cur.reason, stale_targeted=cur.targeted,
                            stale_version_diff=cur.version_diff)


_validated: dict[str, bool] = {}


def _valid_profile(env: dict) -> bool:
    """El perfil del sobre es un assessment ASSESSED que supera ``validate_assessment``. Se memoiza por la firma
    del artefacto (derivada del contenido), no por identidad de objeto."""
    prof = env.get("profile_json")
    if not isinstance(prof, dict) or prof.get("assessment_state") != "ASSESSED":
        return False
    key = env.get("assessment_artifact_signature") or ""
    if key in _validated:
        return _validated[key]
    try:
        ma_profile.validate_assessment(prof)
        ok = True
    except Exception:                                   # noqa: BLE001 — cualquier incompatibilidad estructural
        ok = False
    if len(_validated) > 256:
        _validated.clear()
    _validated[key] = ok
    return ok


def statuses_for_entries(entries: Iterable[Any]) -> dict[str, AssessmentStatus]:
    """Estados para un lote de entradas. NUNCA ejecuta el motor ni escribe el almacén."""
    entries = list(entries)
    if remote_store.restore_status() == "in_progress":  # tras un deploy: no concluir "Sin assessment" mientras Spaces aún baja el estado
        remote_store.await_restore(_RESTORE_WAIT_S)
    versions = current_assessment_versions()
    resolutions = resolve_entries(entries)
    return {e.entry_id: _status_from(e, resolutions[e.entry_id], versions) for e in entries}


def status_for_entry(entry: Any) -> AssessmentStatus:
    return statuses_for_entries([entry])[entry.entry_id]


# ═══════════════════════════ generación (POST) ═══════════════════════════
def _engine_assess(root: Path, client_dir: str, am: str, domain_dir: str, evidence_scope: dict | None = None,
                   generic_root: bool = False, root_type: str | None = None) -> tuple[dict, dict]:
    """Único punto de contacto con el motor (API Python existente; sin shell-out). Seam de tests. Sin argumentos opcionales es la llamada de
    siempre (Catálogo: solo un AM terminal es unidad válida)."""
    kw: dict = {}
    if evidence_scope:
        kw["evidence_scope"] = evidence_scope
    if generic_root:
        kw["generic_root"] = True
        if root_type:
            kw["root_type"] = root_type
    return ma_shadow.assess(root, client_dir, am, domain_dir, **kw)


def assess_evidence_root(root: Path, client_dir: str, am: str, domain: str = "General", *,
                        evidence_scope: dict | None = None, root_type: str | None = None) -> tuple[dict | None, str | None]:
    """Evalúa EXACTAMENTE el objeto raíz ``am`` (modelo analítico, vista o tabla; ``root_type`` es el que informó el extractor) sobre una raíz
    de evidencia ARBITRARIA (p. ej. el workspace de un assessment ad-hoc) con el MISMO motor. Devuelve (perfil canónico validado, None) o (None, código de falla estable). No lee ni escribe la caché del Catálogo."""
    try:
        doc, meta = _engine_assess(root, client_dir, am, domain, evidence_scope=evidence_scope, generic_root=True, root_type=root_type)
        state = doc.get("assessment_state")
        if state == "ASSESSED":
            ma_profile.validate_assessment(doc)
            return doc, None
        if state == "NOT_APPLICABLE":
            return None, "AM_NOT_TERMINAL"
        log.warning("assessment: INDETERMINATE (ad-hoc) para %s/%s: %s", client_dir, am, (meta or {}).get("errors"))
        return None, "POOL_LOAD_ERROR"
    except ma_shadow.UnknownRootObjectError:            # el objeto no figura en la evidencia (NO es un fallo de "AM terminal")
        return None, "ROOT_NOT_IN_EVIDENCE"
    except UnknownTerminalAmError:
        return None, "AM_NOT_TERMINAL"
    except ma_shadow.ClientNotFoundError:
        return None, "POOL_UNAVAILABLE"
    except ValueError:
        log.exception("assessment: perfil inválido (ad-hoc) para %s/%s", client_dir, am)
        return None, "INVALID_PROFILE"
    except Exception:                                   # noqa: BLE001 — traza SOLO al log del servidor
        log.exception("assessment: fallo de generación (ad-hoc) para %s/%s", client_dir, am)
        return None, "GENERATION_ERROR"


class _Flight:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: AssessmentStatus | None = None


_flights_lock = threading.Lock()
_flights: dict[tuple[str, str], _Flight] = {}


def generate(entry: Any) -> AssessmentStatus:
    """Genera/regenera/reintenta el assessment del AM de ``entry`` y devuelve el estado resultante.

    Idempotente sobre la vigencia: si ya está CURRENT no recalcula. Single-flight por (cliente, AM): dos POST
    simultáneos ejecutan el motor UNA vez y ambos reciben el mismo resultado."""
    res = resolve_entry(entry)
    versions = current_assessment_versions()
    if res.kind == "POOL_UNAVAILABLE":                  # sin pool no hay nada que generar: falla estable (no se cachea)
        return AssessmentStatus(FAILED, entry.entry_id, res.client, None, reason=_failed_message("POOL_UNAVAILABLE"),
                                error_code="POOL_UNAVAILABLE")
    if res.kind != "OK":
        return _status_from(entry, res, versions)
    key = _key(res.client, res.terminal_am)
    with _flights_lock:
        flight = _flights.get(key)
        leader = flight is None
        if leader:
            flight = _flights[key] = _Flight()
    if not leader:
        flight.event.wait()
        return flight.result if flight.result is not None else AssessmentStatus(
            FAILED, entry.entry_id, res.client, res.terminal_am, reason=_failed_message("GENERATION_ERROR"), error_code="GENERATION_ERROR")
    try:
        flight.result = _generate_locked(entry, res, versions)
        return flight.result
    finally:
        with _flights_lock:
            _flights.pop(key, None)
        flight.event.set()


def _generate_locked(entry: Any, res: Resolution, versions: dict) -> AssessmentStatus:
    current = _status_from(entry, res, versions)
    if current.state == CURRENT:
        return current
    client, am = res.client, res.terminal_am
    previous = _load_envelope(client, am)
    # Si la regeneración falla, el último perfil VÁLIDO y compatible sigue siendo consultable (con aviso de falla).
    schema_ok = bool(previous) and previous.get("assessment_schema_version") == ma_profile.ASSESSMENT_SCHEMA_VERSION
    retained = previous if schema_ok and _valid_profile(previous) else None
    code: str | None = None
    try:
        doc, meta = _engine_assess(res.pool_root, res.client_dir, am, res.domain_dir)
        state = doc.get("assessment_state")
        if state == "ASSESSED":
            ma_profile.validate_assessment(doc)
            sig = (doc.get("scope") or {}).get("pool_signature") or meta.get("pool_signature")
            durable = _store_envelope(_envelope(client, am, status="OK", profile=doc, pool_signature=sig, versions=dict(doc["versions"])))
            return replace(_status_from(entry, res, versions), durable=durable)
        if state == "NOT_APPLICABLE":
            return AssessmentStatus(NOT_APPLICABLE, entry.entry_id, client, None, reason=NA_REASONS["NO_TERMINAL_AM"])
        code = "POOL_LOAD_ERROR"                        # INDETERMINATE: el pool no pudo cargarse
        log.warning("assessment: INDETERMINATE para %s/%s: %s", client, am, (meta or {}).get("errors"))
    except UnknownTerminalAmError:
        code = "AM_NOT_TERMINAL"
        log.warning("assessment: %s/%s ya no es terminal", client, am)
    except ma_shadow.ClientNotFoundError:
        code = "POOL_UNAVAILABLE"
        log.warning("assessment: pool ausente para %s", client)
    except ValueError:
        code = "INVALID_PROFILE"
        log.exception("assessment: perfil inválido para %s/%s", client, am)
    except Exception:                                   # noqa: BLE001 — traza SOLO al log del servidor
        code = "GENERATION_ERROR"
        log.exception("assessment: fallo de generación para %s/%s", client, am)
    durable = _store_envelope(_envelope(
        client, am, status="FAILED", profile=retained["profile_json"] if retained else None,
        pool_signature=retained.get("input_pool_signature") if retained else None,
        versions=retained.get("assessment_versions") if retained else None,
        error_code=code, generated_at=retained.get("generated_at") if retained else None))
    return replace(_status_from(entry, res, versions), durable=durable)


# ═══════════════════════════ enriquecimiento controlado de assessments gobernados CURRENT ═══════════════════════════
# OPERACIÓN EXPLÍCITA del dueño (``assessment_backfill --enrich-current-missing``): NO es comportamiento de request ni de arranque. Regenera el
# assessment COMPLETO con el motor vigente sobre la evidencia local ya presente (sin Datasphere, sin credenciales) y, solo si TODO valida,
# reemplaza el registro gobernado de la MISMA llave. Distinto de ``generate``: nunca escribe un sobre FAILED sobre un registro bueno.
class EnrichmentError(Exception):
    """Fallo ANTES de escribir: el registro previo (memoria, disco y remoto) queda intacto. ``code`` es estable; sin trazas ni rutas."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RegeneratedAssessment:
    """Resultado de regenerar en MEMORIA (nada escrito todavía): el perfil nuevo, su sobre candidato y la llave del registro que reemplazaría.
    ``previous_signature``: firma del artefacto del registro que se reemplazaría (la migracion la reverifica justo antes de escribir)."""
    client: str
    terminal_am: str
    profile: dict
    envelope: dict
    previous_signature: str | None = None


def facts_present(profile: dict | None) -> set[str]:
    """Ids de hechos (sin el sufijo ``@sujeto``) que figuran en el ``fact_index`` del perfil canónico."""
    return {str(x.get("fact", "")).split("@", 1)[0] for x in (profile or {}).get("fact_index") or [] if isinstance(x, dict)}


def missing_facts(profile: dict | None, required: Iterable[str]) -> list[str]:
    """Hechos requeridos que el perfil NO trae (la elegibilidad del enriquecimiento sale del CONTENIDO canónico, no de fechas ni versiones)."""
    present = facts_present(profile)
    return [f for f in required if f not in present]


def _envelope_resolves_current(env: dict, res: Resolution, versions: dict) -> bool:
    """Espejo de la regla de ``_status_from`` (misma firma del pool + mismo bloque de versiones + esquema válido): ¿el sobre candidato leería CURRENT?"""
    return (env.get("status") == "OK" and env.get("assessment_schema_version") == ma_profile.ASSESSMENT_SCHEMA_VERSION and _valid_profile(env)
            and env.get("input_pool_signature") == res.pool_signature and env.get("assessment_versions") == versions)


def _regenerate_core(entry: Any, *, precondition: Any, postcheck: Any, log_label: str) -> RegeneratedAssessment:
    """NUCLEO SEGURO COMPARTIDO por todo flujo de regeneracion controlada (enriquecimiento de CURRENT, migracion de evidencia de STALE version-only):
    resuelve el pool local, exige ``precondition(previous, res, versions)`` sobre el registro gobernado actual, corre el motor canonico en MEMORIA
    (sin Datasphere, OAuth ni ``restore()``), VALIDA, exige que la firma del pool regenerada sea la del pool local, aplica ``postcheck(doc, previous, res,
    versions)`` y que el sobre candidato lea CURRENT. No escribe nada. Cualquier fallo lanza ``EnrichmentError`` (codigo estable) ANTES de tocar el registro."""
    res = resolve_entry(entry)
    versions = current_assessment_versions()
    if res.kind != "OK":
        raise EnrichmentError(res.kind if res.kind in ERROR_MESSAGES else "POOL_UNAVAILABLE")
    client, am = res.client, res.terminal_am
    previous = _load_envelope(client, am)
    precondition(previous, res, versions)
    try:
        doc, meta = _engine_assess(res.pool_root, res.client_dir, am, res.domain_dir)
        if doc.get("assessment_state") != "ASSESSED":
            log.warning("assessment: %s %s/%s: el motor no produjo un assessment (%s)", log_label, client, am, (meta or {}).get("errors"))
            raise EnrichmentError("ENGINE_NOT_ASSESSED")
        ma_profile.validate_assessment(doc)
    except EnrichmentError:
        raise
    except ValueError:
        log.exception("assessment: %s %s/%s: perfil inválido", log_label, client, am)
        raise EnrichmentError("INVALID_PROFILE") from None
    except Exception:                                   # noqa: BLE001 — traza SOLO al log del servidor
        log.exception("assessment: %s %s/%s: fallo del motor", log_label, client, am)
        raise EnrichmentError("GENERATION_ERROR") from None
    sig = (doc.get("scope") or {}).get("pool_signature") or (meta or {}).get("pool_signature")
    if sig != res.pool_signature:
        raise EnrichmentError("POOL_SIGNATURE_MISMATCH")
    postcheck(doc, previous, res, versions)
    env = _envelope(client, am, status="OK", profile=doc, pool_signature=sig, versions=dict(doc["versions"]))
    if not _envelope_resolves_current(env, res, versions):
        raise EnrichmentError("WOULD_NOT_BE_CURRENT")
    return RegeneratedAssessment(client, am, doc, env, (previous or {}).get("assessment_artifact_signature"))


def _readable_previous(previous: dict | None) -> bool:
    return bool(previous is not None and previous.get("status") == "OK" and _valid_profile(previous)
                and previous.get("assessment_schema_version") == ma_profile.ASSESSMENT_SCHEMA_VERSION)


def regenerate_in_memory(entry: Any, required_facts: Iterable[str]) -> RegeneratedAssessment:
    """ENRIQUECIMIENTO: corre el motor canónico sobre la evidencia local actual y VALIDA el resultado, sin escribir nada. Lanza ``EnrichmentError`` (código
    estable) si el registro gobernado actual no es un CURRENT legible, si el motor o la validación fallan, si la firma del pool regenerada difiere de la del
    pool local, si el hecho requerido no aparece o si el registro nuevo no leería CURRENT."""
    required = tuple(required_facts)

    def precondition(previous: dict | None, res: Resolution, versions: dict) -> None:
        if (not _readable_previous(previous) or previous.get("input_pool_signature") != res.pool_signature or previous.get("assessment_versions") != versions):
            raise EnrichmentError("PREVIOUS_NOT_CURRENT")

    def postcheck(doc: dict, previous: dict | None, res: Resolution, versions: dict) -> None:
        if missing_facts(doc, required):
            raise EnrichmentError("ENRICHMENT_MISSING")

    return _regenerate_core(entry, precondition=precondition, postcheck=postcheck, log_label="enriquecimiento")


def closure_identity(profile: dict | None) -> tuple | None:
    """Identidad CANONICA del cierre semantico de un perfil: AM terminal + raiz (objeto y tipo) + CONJUNTO de nombres tecnicos de artefactos del cierre
    (ordenado: el orden incidental de la lista no cuenta). None si el perfil no trae la lista de artefactos (no verificable)."""
    sc = (profile or {}).get("scope") or {}
    arts = sc.get("closure_artifacts")
    if not isinstance(arts, list):
        return None
    return (sc.get("terminal_analytic_model"), sc.get("root_object"), sc.get("root_type"), tuple(sorted({str(a) for a in arts})))


def migration_postcheck(name: str, doc: dict, previous: dict | None, versions: dict) -> None:
    """Postcondiciones de la migracion de evidencia ``name`` sobre el candidato regenerado (todas ANTES de escribir)."""
    m = EVIDENCE_MIGRATIONS[name]
    if doc.get("versions") != versions:
        raise EnrichmentError("CANDIDATE_VERSIONS_NOT_CURRENT")
    if any(doc["versions"].get(k) != versions.get(k) or versions.get(k) is None for k in m.target_keys):
        raise EnrichmentError("CANDIDATE_TARGET_VERSIONS_MISMATCH")
    ce = doc.get("canonical_evidence")                   # por CONTENIDO (no por nombres de hecho en fact_index)
    if not isinstance(ce, dict):
        raise EnrichmentError("CANONICAL_EVIDENCE_MISSING")
    try:
        ma_profile.validate_canonical_evidence(ce)
    except ValueError:
        raise EnrichmentError("CANONICAL_EVIDENCE_INVALID") from None
    old_scope = (previous or {}).get("profile_json", {}).get("scope") or {}
    new_scope = doc.get("scope") or {}
    if old_scope.get("terminal_analytic_model") != new_scope.get("terminal_analytic_model") or old_scope.get("root_object") != new_scope.get("root_object"):
        raise EnrichmentError("TERMINAL_AM_CHANGED")
    old_closure, new_closure = closure_identity((previous or {}).get("profile_json")), closure_identity(doc)
    if old_closure is None or new_closure is None:
        raise EnrichmentError("CLOSURE_NOT_VERIFIABLE")
    if m.closure_policy == CLOSURE_EQUAL:
        if old_closure != new_closure:                   # misma entrada + migracion de solo evidencia NO puede cambiar el cierre semantico
            raise EnrichmentError("CLOSURE_DRIFT")
        return
    _check_targeted_closure_expansion(old_closure, new_closure, doc)


def closure_delta(old: tuple | None, new: tuple | None) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(agregados, quitados) entre dos identidades de cierre (conjuntos de artefactos)."""
    o, n = set(old[3]) if old else set(), set(new[3]) if new else set()
    return tuple(sorted(n - o)), tuple(sorted(o - n))


def _check_targeted_closure_expansion(old: tuple, new: tuple, doc: dict) -> None:
    """TARGETED_CLOSURE_EXPANSION: el cierre puede CRECER solo si TODA la diferencia se atribuye CAUSALMENTE a la correccion aprobada (un target de flujo que Modeling
    ya leia en el mismo pool y que la clasificacion previa excluia). Cualquier otra diferencia es ``CLOSURE_DRIFT`` (cero escrituras). No debilita la regla global."""
    if old[:3] != new[:3]:
        raise EnrichmentError("CLOSURE_DRIFT")                                  # identidad de la raiz estable (AM terminal, objeto raiz, tipo)
    added, removed = closure_delta(old, new)
    if removed:
        raise EnrichmentError("CLOSURE_DRIFT")                                  # el cierre viejo debe ser un subconjunto del nuevo: nada desaparece
    if not added:
        return
    pc = ((doc.get("canonical_evidence") or {}).get("closure") or {}).get("plane_context") or {}
    members = {m["artifact"]: m for m in pc.get("flow_produced_members") or []}
    flows = {f["flow_name"] for f in ((doc.get("canonical_evidence") or {}).get("processing_flow_properties") or {}).get("flows") or []}
    old_set = set(old[3])
    if pc.get("loader_file_flag_closure_size") != len(old_set) or set(pc.get("added_vs_loader_file_flag") or []) != set(added) or pc.get("removed_vs_loader_file_flag"):
        raise EnrichmentError("CLOSURE_DRIFT")                                  # la politica previa reproducida debe dar EXACTAMENTE el cierre guardado
    for a in added:
        m = members.get(a)
        if (m is None or a in flows or m.get("excluded_before") != "LOADER_FILE_FLAG" or m.get("loader_file_flag") is not True or m.get("semantic_providers")
                or not (set(m.get("semantic_consumers") or []) & old_set)):
            raise EnrichmentError("CLOSURE_DRIFT")                              # no es un target de flujo leido por Modeling / es un flujo / no es hoja / no estaba referenciado


def regenerate_stale_evidence(entry: Any, migration: str) -> RegeneratedAssessment:
    """MIGRACION DE EVIDENCIA: regenera en MEMORIA un assessment gobernado STALE VERSION_ONLY cubierto por ``migration`` (registro ``EVIDENCE_MIGRATIONS``).
    Misma disciplina que el enriquecimiento (mismo nucleo): sin escrituras, sin Datasphere, sin OAuth, sin ``restore()``; cualquier fallo conserva el registro
    previo. Lanza ``EnrichmentError`` con codigo estable."""
    def precondition(previous: dict | None, res: Resolution, versions: dict) -> None:
        if not _readable_previous(previous):
            raise EnrichmentError("PREVIOUS_NOT_READABLE")
        cur = classify_currentness(previous.get("input_pool_signature"), previous.get("assessment_versions"), res.pool_signature, versions)
        if cur.state != STALE or cur.reason != STALE_VERSION_ONLY or cur.targeted != migration:
            raise EnrichmentError("PREVIOUS_NOT_TARGETED_VERSION_ONLY_STALE")

    return _regenerate_core(entry, precondition=precondition, log_label=f"migración {migration}",
                            postcheck=lambda doc, previous, res, versions: migration_postcheck(migration, doc, previous, versions))


def commit_stale_migration(candidate: RegeneratedAssessment) -> str:
    """Escribe el candidato de una migracion por el MISMO camino seguro (``commit_regenerated``), pero reverifica antes que el registro gobernado siga siendo el
    que se regenero (p. ej. alguien lo regenero por la UI durante la ventana STALE): si cambio, NO se pisa (``PREVIOUS_CHANGED_DURING_RUN``)."""
    current = _load_envelope(candidate.client, candidate.terminal_am)
    if current is None or current.get("assessment_artifact_signature") != candidate.previous_signature:
        raise EnrichmentError("PREVIOUS_CHANGED_DURING_RUN")
    return commit_regenerated(candidate)


def commit_regenerated(candidate: RegeneratedAssessment) -> str:
    """Reemplaza el registro gobernado de la MISMA llave por el candidato ya validado, por el camino canónico ``_store_envelope`` (archivo atómico +
    write-through a Spaces). Devuelve ``remote`` | ``local`` | ``memory``. Si queda solo en memoria (disco no escribible) el registro previo sigue siendo
    el vigente y la copia en memoria se RESTAURA: no queda un perfil nuevo que nadie podría persistir."""
    key = _key(candidate.client, candidate.terminal_am)
    with _cache_lock:
        previous = _memory.get(key)
    durable = _store_envelope(candidate.envelope)
    if durable == DURABLE_MEMORY:
        with _cache_lock:
            if previous is not None:
                _memory[key] = previous
            else:
                _memory.pop(key, None)
    return durable


def retry_remote_persistence(client: str, am: str) -> bool:
    """Reintento EXPLÍCITO del PUT remoto del registro gobernado (``remote_store.push_path``, la misma API del write-through). True solo si el PUT
    quedó confirmado. No borra ni toca nada remoto si falla."""
    path = governed_path(client, am)
    return bool(path is not None and path.is_file() and remote_store.enabled() and remote_store.push_path(path))


REMOTE_CONFIRMED, REMOTE_PENDING, REMOTE_UNVERIFIABLE = "confirmed", "pending", "unverifiable"


def verify_remote_durability(client: str, am: str) -> str:
    """¿El registro gobernado local YA es el objeto remoto? Verificación por BYTES EXACTOS (``remote_store.read_object_bytes``, lectura pura: no escribe
    ni restaura nada). ``confirmed`` = el objeto remoto es idéntico al archivo local; ``pending`` = ausente o distinto (el local aún no se subió);
    ``unverifiable`` = no se pudo determinar (Spaces deshabilitado, GET irresoluble o archivo local ausente): se trata como NO confirmado.
    Es lo que permite que la seguridad del reinicio sobreviva entre invocaciones del CLI: un perfil local-pero-no-remoto sigue bloqueando."""
    path = governed_path(client, am)
    if path is None or not path.is_file():
        return REMOTE_UNVERIFIABLE
    try:
        remote = remote_store.read_object_bytes(path)
        local = path.read_bytes()
    except (remote_store.RemoteReadError, OSError):
        return REMOTE_UNVERIFIABLE
    if remote is None:
        return REMOTE_PENDING
    return REMOTE_CONFIRMED if remote == local else REMOTE_PENDING


# ═══════════════════════════ exportaciones ═══════════════════════════
def export_json(status: AssessmentStatus) -> str:
    """El perfil canónico validado exacto que usa el UI."""
    return ma_profile.canonical_assessment_json(status.profile)


def export_markdown(status: AssessmentStatus) -> str:
    """Resultado exacto de ``render_markdown(perfil)``; la webapp no mantiene otra plantilla de Markdown."""
    return render_markdown(status.profile)


def export_filename(status: AssessmentStatus, ext: str) -> str:
    return ma_paths.profile_filename(status.terminal_am or "assessment", ext)
