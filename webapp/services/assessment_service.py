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

import copy
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
from tools.catalog.methodological_judgment import CURRENT_RULESET_VERSION, JUDGMENT_SCHEMA_VERSION, evaluate_judgment
from tools.catalog.methodological_judgment.contract import signature as _judgment_signature
from tools.catalog import leaf_materialization_evidence as _lme

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
    transitional: bool = False                  # COMPATIBILIDAD DE TRANSICION (ver ``classify_currentness``): un perfil cubierto EXACTAMENTE por esta migracion sigue CURRENT hasta migrarse


CLOSURE_EQUAL, TARGETED_CLOSURE_EXPANSION = "CLOSURE_EQUAL", "TARGETED_CLOSURE_EXPANSION"

# Registro de migraciones de evidencia: pequeno, explicito y SIN encadenamiento automatico. Cada migracion cubre EXACTAMENTE una diferencia de versiones; un perfil
# cuya diferencia es la de OTRA migracion se reporta como tal (``applicable_migration``) y no se migra por esta.
#   * ``evidence_g1_g3_v1`` (perfil ANTERIOR a G1/G3: sin ``canonical_evidence``, signals v2, assessability v1, finding_evidence v1). Sigue registrada: un perfil historico
#     puede existir o restaurarse en ese contrato aunque produccion ya migro a otro cliente. IMPORTANTE: el motor de ESTE build solo emite el contrato VIGENTE; no puede
#     fabricar el contrato intermedio G1/G3. Por eso, con este build, la migracion lleva el perfil al contrato vigente en UN paso explicito del operador (por eso sus claves
#     objetivo incluyen ``confidence`` y ``closure``). Para dejar un perfil en el contrato intermedio G1/G3 hay que correrla desde un despliegue anterior al evidence release.
#   * ``evidence_closure_contract_v2`` (perfil en el contrato G1/G3 vigente en 11ce071 -> evidence release).
#   * ``evidence_field_lineage_v3`` (perfil en el contrato del evidence release v2 -> evidence release de linaje de campo): SOLO agrega evidencia, por lo que exige ``CLOSURE_EQUAL``.
#   * ``signals_layer_boundary_v4`` (perfil en ``signal-rules/v3`` -> ``signal-rules/v4``): SOLO cambia la revision LAYER_BOUNDARY_REVIEW (primer consumidor FUNCIONAL, capa con evidencia, fuentes de contrato gobernado).
#     Regenera con el MISMO motor sobre el MISMO pool local; ``migration_postcheck`` exige que TODO el perfil salvo esa revision (y lo derivado de ella) quede identico, y el Juicio se REVALIDA o la migracion se abstiene.
#   * ``evidence_confidence_v3`` (perfil en ``confidence-cuts/v2`` -> ``confidence-cuts/v3``): SOLO cambia la CONFIANZA (el factor ``resolved_provenance`` mide el origen de los datos). Regenera con el
#     MISMO motor sobre el MISMO pool local (sin Datasphere); ``migration_postcheck`` exige que TODO el perfil salvo ``summary.confidence`` y ``versions.confidence`` quede idéntico (evidencia, cobertura, cierre).
# Las dos primeras regeneran con el MISMO motor y aceptan el cierre solo bajo ``TARGETED_CLOSURE_EXPANSION``. Ninguna se encadena con otra: un perfil se migra por la UNICA cuyo
# conjunto de claves de version coincide EXACTAMENTE con su diferencia (``targeted_migration``); un perfil v1 NO salta a v3 por la migracion v2->v3.
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
    "evidence_field_lineage_v3": MigrationTarget(
        "evidence_field_lineage_v3", ("assessability", "canonical_evidence"),
        {"assessability": frozenset({"assessability-rules/v3"}), "canonical_evidence": frozenset({"canonical-evidence/v2"})},
        "Evidence release (linaje de campo): procedencia de cada campo publicado hasta las hojas frontera + clase explicita de contrato de fuente por hoja "
        "(canonical_evidence v3, assessability v4). Misma entrada y mismo cierre semantico: solo se agrega evidencia (CLOSURE_EQUAL); un perfil con otros cambios de version o de entrada NO es elegible",
        CLOSURE_EQUAL),
    "evidence_leaf_materialization_v4": MigrationTarget(
        "evidence_leaf_materialization_v4", ("canonical_evidence",), {"canonical_evidence": frozenset({"canonical-evidence/v3"})},
        "canonical-evidence/v4 (v4.C): perfil v3 -> v4 = v3 + seccion `leaf_materialization` (materializacion derivada de las hojas locales con referencias recuperables al pool). Misma entrada, "
        "mismo cierre y el MISMO Assessment metodologico: el perfil solo puede diferir en `canonical_evidence.version`, `versions.canonical_evidence` y la seccion nueva (CLOSURE_EQUAL + perfil identico "
        "salvo eso). Se ABSTIENE si falta el pool, difiere su firma o no se resuelven las referencias; el Juicio v2 se revalida (misma metodologia, nueva firma) o la migracion se abstiene",
        CLOSURE_EQUAL, transitional=True),
    "signals_layer_boundary_v4": MigrationTarget(
        "signals_layer_boundary_v4", ("signals",), {"signals": frozenset({"signal-rules/v3"})},
        "signal-rules/v4: LAYER_BOUNDARY_REVIEW evalua el primer consumidor FUNCIONAL (atraviesa vistas de paso), solo con capa respaldada por evidencia (rol declarado / nomenclatura de capas) y sin fuentes de "
        "contrato gobernado. Misma entrada, mismo cierre, mismo resto del perfil y MISMO Juicio: el perfil solo puede diferir en `versions.signals` y en la revision LAYER_BOUNDARY_REVIEW con lo derivado de ella "
        "(areas, superficie y resultado de regla, preguntas de contexto y la parte de «insumos completos de las reglas» de la confianza, sin cambiar su nivel). CLOSURE_EQUAL + Juicio revalidado (misma metodologia, nueva firma) o la migracion se abstiene",
        CLOSURE_EQUAL, transitional=True),
    "evidence_confidence_v3": MigrationTarget(
        "evidence_confidence_v3", ("confidence",), {"confidence": frozenset({"confidence-cuts/v2"})},
        "Confianza metodologica v3: `resolved_provenance` mide el ORIGEN DE LOS DATOS (una hoja de identidad UNKNOWN con productor y clase de contrato resuelta cuenta como resuelta). "
        "Misma entrada, mismo cierre y mismo resto del perfil: solo cambia summary.confidence (CLOSURE_EQUAL + perfil idéntico salvo la confianza)",
        CLOSURE_EQUAL),
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
    pending_migration: str | None = None     # solo CURRENT por COMPATIBILIDAD DE TRANSICION: migracion que aun debe aplicarse (None = vigente de verdad)

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
        targeted = targeted_migration(stored_versions, versions)
        if targeted is not None and EVIDENCE_MIGRATIONS[targeted].transitional:
            # COMPATIBILIDAD DE TRANSICION ESTRICTA: misma entrada (pool igual) y la UNICA diferencia es la que cubre EXACTAMENTE una migracion marcada ``transitional``
            # (hoy canonical_evidence v3->v4). Sigue CURRENT para la presentacion, pero queda identificado como pendiente de esa migracion. Cualquier otra causa
            # (pool distinto, otras claves de version, valores viejos desconocidos) NO entra aca y sigue siendo STALE. Se retira quitando ``transitional``.
            return Currentness(CURRENT, version_diff=diff, pending_migration=targeted)
        return Currentness(STALE, STALE_VERSION_ONLY, diff, True, targeted)
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
    judgment: dict | None = field(default=None, compare=False)   # Juicio metodológico DERIVADO y PERSISTIDO del perfil (nunca recalculado por la UI)
    business_classification: dict | None = field(default=None, compare=False)   # Clasificación de NEGOCIO v2 PERSISTIDA (línea de negocio + dominio; hermana del Juicio)
    domain_classification: dict | None = field(default=None, compare=False)   # Clasificación de dominio PERSISTIDA (hermana del Juicio; sugerencia, jamás insumo de calidad)
    previous_profile_retained: bool = False
    durable: str | None = field(default=None, compare=False)   # solo tras ``generate``: "remote" | "local" | "memory" (ver ``_store_envelope``)
    stale_reason: str | None = field(default=None, compare=False)       # solo STALE: VERSION_ONLY | INPUT_CHANGED | VERSION_AND_INPUT
    stale_targeted: str | None = field(default=None, compare=False)     # solo STALE VERSION_ONLY: nombre de la migracion de evidencia que lo cubre (None = no cubierto)
    stale_version_diff: tuple = field(default=(), compare=False)        # solo STALE: claves de ``versions`` que difieren
    pending_migration: str | None = field(default=None, compare=False)  # solo CURRENT por compatibilidad de transicion: migracion de evidencia pendiente (sigue siendo CURRENT)

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


_UNSET: Any = object()
# Versión canónica del contrato de Juicio persistido (del propio paquete de Juicio; no se inventa un hash paralelo).
# B5.3: el contrato VIGENTE del Juicio persistido es judgment-rules/v3 (= v2 + resolucion de LVL-05 por ratificacion de gobierno INYECTADA). judgment-rules/v2 sigue reproducible en el motor
# (``evaluate_judgment`` por defecto) pero un Juicio persistido v2 es ``JDG_VERSION_STALE`` hasta una migracion explicita (``--backfill-judgment``).
JUDGMENT_CONTRACT_VERSIONS = (JUDGMENT_SCHEMA_VERSION, CURRENT_RULESET_VERSION)


def judgment_versions_of(judgment: dict | None) -> tuple[str | None, str | None]:
    j = judgment or {}
    return (j.get("judgment_schema_version"), j.get("ruleset_version"))


def judgment_is_current_version(judgment: dict | None) -> bool:
    """¿El Juicio persistido fue producido por el contrato de Juicio VIGENTE? (versión de esquema + ruleset)."""
    return bool(judgment) and judgment_versions_of(judgment) == JUDGMENT_CONTRACT_VERSIONS


def _derive_judgment(profile: dict | None, ratified: Any = None) -> dict | None:
    """Juicio metodológico DETERMINISTA del perfil, derivado con el ÚNICO motor canónico (``evaluate_judgment``, ruleset vigente v3). Es
    estado DERIVADO del perfil persistido: se calcula aquí al escribir el sobre (generate/reassess/backfill) y NUNCA se
    recalcula ni se edita en la UI. ``ratified``: ratificaciones de gobierno INYECTADAS (las enlaza el operador explícito con una instantánea del registro;
    sin ellas v3 == v2 en niveles: el registro jamás se lee de fondo). ``None`` solo si no hay perfil (estado sin assessment)."""
    return evaluate_judgment(profile, ruleset=CURRENT_RULESET_VERSION, ratified_contracts=list(ratified or ())) if profile is not None else None


def _derive_business_classification(entry: Any, am: str | None) -> dict | None:
    """Clasificación de NEGOCIO v2 (línea de negocio + dominio) DERIVADA del contexto local del Catálogo. Sin red, sin Datasphere, sin leer el Juicio ni el perfil."""
    from webapp.services import domain_classification_service
    return domain_classification_service.derive_business_for_entry(entry, am)


def _derive_domain_classification(entry: Any, am: str | None) -> dict | None:
    """Clasificación de dominio DERIVADA del contexto local del Catálogo (nombres/etiquetas del flujo y dominios del cliente). Sin red,
    sin Datasphere, sin leer el Juicio ni el perfil: cambiarla NO puede cambiar ningún resultado metodológico. Fail-soft (``None``)."""
    from webapp.services import domain_classification_service
    return domain_classification_service.derive_for_entry(entry, am)


def _envelope(client: str, am: str, *, status: str, profile: dict | None, pool_signature: str | None,
              versions: dict | None, error_code: str | None = None, generated_at: str | None = None,
              judgment: dict | None = _UNSET, domain_classification: dict | None = None,
              business_classification: dict | None = None) -> dict:
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
        "judgment": _derive_judgment(profile) if judgment is _UNSET else judgment,
        # Clasificación de dominio: derivado HERMANO del Juicio (contrato/versión propios). No entra en las firmas del perfil ni del artefacto.
        "domain_classification": domain_classification,
        # Clasificación de negocio v2: derivado HERMANO (contrato/versión propios); la v1 de dominio se conserva sin cambios y no se confunde con ésta.
        "business_classification": business_classification,
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


# ── backfill de Juicio: materializa el Juicio DERIVADO en sobres gobernados existentes, SIN reassess ni adquisición ──
# El Juicio se deriva del ``profile_json`` YA persistido con el motor canónico; se compara por ``output_signature`` para
# idempotencia exacta (un segundo apply no hace trabajo). No cambia identidad, perfil, firmas del artefacto ni versiones
# de Assessment. ``profile_json`` None (p. ej. FAILED sin perfil previo) no tiene Juicio y se reporta como al día.
JDG_CURRENT, JDG_MISSING, JDG_VERSION_STALE, JDG_CONTENT_STALE, JDG_NO_PROFILE = (
    "judgment_current", "judgment_missing", "judgment_version_stale", "judgment_content_stale", "no_profile")
# B5.3: el Juicio persistido consumio una decision de gobierno que YA NO es valida (revocada / evidencia STALE): queda marcado PENDIENTE DE REEVALUACION, no se reescribe en silencio.
JDG_DECISION_STALE = "judgment_decision_stale"


def iter_governed_envelopes() -> Iterable[tuple[dict, Path]]:
    """(sobre, ruta) de cada registro gobernado durable del almacén (SOLO LECTURA de disco; sin validar vigencia de pool)."""
    d = governed_dir()
    if d is None:
        return
    base = d / GOVERNED_LAYOUT_VERSION
    if not base.is_dir():
        return
    for client_dir in sorted(p for p in base.iterdir() if p.is_dir()):
        for f in sorted(client_dir.glob("*.json")):
            if f.name.startswith(".tmp"):
                continue
            try:
                env = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("assessment: registro gobernado ilegible en backfill (%s); se omite", f, exc_info=True)
                continue
            if isinstance(env, dict) and env.get("envelope_version") == ENVELOPE_VERSION:
                yield env, f


DC_CURRENT, DC_MISSING, DC_STALE, DC_NO_ROOT = "domain_classification_current", "domain_classification_missing", "domain_classification_stale", "no_root"


def classify_domain_classification_backfill(env: dict, entry: Any) -> tuple[str, dict | None]:
    """(acción, clasificación fresca) para UN sobre gobernado + SU entrada del Catálogo. Idempotente por ``input_signature``+versión."""
    fresh = _derive_domain_classification(entry, env.get("terminal_am"))
    if fresh is None:
        return DC_NO_ROOT, None
    persisted = env.get("domain_classification")
    if persisted is None:
        return DC_MISSING, fresh
    if (persisted.get("version"), persisted.get("input_signature")) == (fresh.get("version"), fresh.get("input_signature")):
        return DC_CURRENT, fresh
    return DC_STALE, fresh


BC_CURRENT, BC_MISSING, BC_STALE = "business_classification_current", "business_classification_missing", "business_classification_stale"


def classify_business_classification_backfill(env: dict, entry: Any) -> tuple[str, dict | None]:
    """(acción, clasificación v2 fresca) para UN sobre + SU entrada. Idempotente por ``contract_version`` + ``input_signature`` (una v1 nunca cuenta como v2)."""
    fresh = _derive_business_classification(entry, env.get("terminal_am"))
    if fresh is None:
        return DC_NO_ROOT, None
    persisted = env.get("business_classification")
    if persisted is None:
        return BC_MISSING, fresh
    if (persisted.get("contract_version"), persisted.get("input_signature")) == (fresh.get("contract_version"), fresh.get("input_signature")):
        return BC_CURRENT, fresh
    return BC_STALE, fresh


def apply_business_classification_backfill(env: dict, bclass: dict | None) -> str:
    """Reescribe el MISMO sobre (identidad, perfil, Juicio, firmas, versiones y la clasificación de dominio v1) solo con la clasificación de NEGOCIO v2 nueva."""
    new_env = dict(env)
    new_env["business_classification"] = bclass
    return _store_envelope(new_env)


def apply_domain_classification_backfill(env: dict, dclass: dict | None) -> str:
    """Reescribe el MISMO sobre (identidad, perfil, Juicio, firmas y versiones intactos) solo con la clasificación nueva."""
    new_env = dict(env)
    new_env["domain_classification"] = dclass
    return _store_envelope(new_env)


def classify_judgment_decisions(env: dict, snapshot: Any = None) -> dict:
    """Clasificacion completa de UN sobre para el operador de Juicio: ``{"action", "fresh", "lost", "link_report"}``. ``snapshot`` = instantanea de decisiones (``judgment_decisions.Snapshot``);
    ``None`` = SIN decisiones (v3 == v2 en niveles). Acciones: ``no_profile``, ``judgment_missing``, ``judgment_current``, ``judgment_version_stale`` (contrato anterior, p. ej. v2),
    ``judgment_decision_stale`` (v3 vigente que consumio una decision ya revocada/obsoleta: PENDIENTE DE REEVALUACION) y ``judgment_content_stale`` (cualquier otra diferencia)."""
    from webapp.services import judgment_decisions as jd
    prof = env.get("profile_json")
    if prof is None:
        return {"action": JDG_NO_PROFILE, "fresh": None, "lost": [], "link_report": None}
    snap = snapshot if snapshot is not None else jd.empty_snapshot()
    fresh, report = jd.derive(prof, str(env.get("client") or ""), snap)
    persisted = env.get("judgment")
    out = {"fresh": fresh, "lost": [], "link_report": report}
    if persisted is None:
        return {**out, "action": JDG_MISSING}
    if (persisted.get("output_signature"), persisted.get("judgment_schema_version"), persisted.get("ruleset_version")) ==        (fresh.get("output_signature"), fresh.get("judgment_schema_version"), fresh.get("ruleset_version")):
        return {**out, "action": JDG_CURRENT}
    if not judgment_is_current_version(persisted):
        return {**out, "action": JDG_VERSION_STALE}
    lost = jd.lost_decisions(persisted, fresh, report)
    return {**out, "action": JDG_DECISION_STALE if lost else JDG_CONTENT_STALE, "lost": lost}


def classify_judgment_backfill(env: dict, snapshot: Any = None) -> tuple[str, dict | None]:
    """(acción, Juicio canónico fresco). Deriva el Juicio del perfil persistido (con la instantánea de decisiones inyectada, si la hay) y lo compara con el persistido por
    ``output_signature``. Acciones: ``no_profile``, ``judgment_current``, ``judgment_missing`` / ``judgment_version_stale`` / ``judgment_content_stale`` (debe actualizarse) y
    ``judgment_decision_stale`` (pendiente de reevaluación: NO se reescribe en silencio)."""
    r = classify_judgment_decisions(env, snapshot)
    return r["action"], r["fresh"]


def apply_judgment_backfill(env: dict, judgment: dict | None) -> str:
    """Reescribe el MISMO sobre gobernado (misma identidad, mismo ``profile_json`` y firmas) solo con el Juicio nuevo (y retira la marca de reevaluación pendiente, que ya no aplica).
    Devuelve la durabilidad lograda (``remote``/``local``/``memory``)."""
    new_env = dict(env)
    new_env["judgment"] = judgment
    new_env.pop("judgment_review", None)
    return _store_envelope(new_env)


def apply_judgment_review_marker(env: dict, marker: dict) -> str:
    """Marca el Juicio PERSISTIDO como pendiente de reevaluacion (clave hermana ``judgment_review``) SIN tocar el Juicio, el perfil, las firmas ni las versiones."""
    new_env = dict(env)
    new_env["judgment_review"] = marker
    return _store_envelope(new_env)


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
    jdg = _persisted_judgment(env)
    dcl = env.get("domain_classification")
    bcl = env.get("business_classification")
    if env.get("status") == "FAILED":
        code = env.get("error_code")
        return AssessmentStatus(FAILED, eid, client, am, reason=_failed_message(code), error_code=code,
                                generated_at=env.get("generated_at"), profile=prof, judgment=jdg, domain_classification=dcl, business_classification=bcl, previous_profile_retained=prof is not None)
    if env.get("assessment_schema_version") != ma_profile.ASSESSMENT_SCHEMA_VERSION or not _valid_profile(env):
        # La clasificación de negocio v2 es un eje INDEPENDIENTE del esquema del perfil/Juicio: una incompatibilidad del perfil no la invalida
        # (el consumidor igual valida su propio contrato). El perfil y el Juicio SÍ siguen sin exponerse.
        return AssessmentStatus(INCOMPATIBLE_SCHEMA, eid, client, am, reason=INCOMPATIBLE_MESSAGE, error_code="INCOMPATIBLE_SCHEMA",
                                generated_at=env.get("generated_at"), business_classification=bcl)
    cur = classify_currentness(env.get("input_pool_signature"), env.get("assessment_versions"), res.pool_signature, versions)
    if cur.state == CURRENT:
        return AssessmentStatus(CURRENT, eid, client, am, generated_at=env.get("generated_at"), profile=prof, judgment=jdg, domain_classification=dcl, business_classification=bcl,
                                pending_migration=cur.pending_migration)
    return AssessmentStatus(STALE, eid, client, am, generated_at=env.get("generated_at"), profile=prof, judgment=jdg, domain_classification=dcl, business_classification=bcl, stale_reason=cur.reason, stale_targeted=cur.targeted,
                            stale_version_diff=cur.version_diff)


def _persisted_judgment(env: dict) -> dict | None:
    """Juicio PERSISTIDO en el sobre. Compatibilidad TEMPORAL de rollout (sobres históricos previos al backfill de
    Juicio): si falta, se deriva en SOLO LECTURA del ``profile_json`` ya persistido (misma función canónica); JAMÁS se
    persiste desde una lectura. El backfill lo materializa; esta rama desaparece cuando el corpus está migrado."""
    jdg = env.get("judgment")
    if jdg is not None:
        return jdg
    prof = env.get("profile_json")
    return _derive_judgment(prof) if prof is not None else None


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


def generate(entry: Any, *, force: bool = False) -> AssessmentStatus:
    """Genera/regenera/reintenta el assessment del AM de ``entry`` y devuelve el estado resultante.

    Idempotente sobre la vigencia: si ya está CURRENT no recalcula. ``force=True`` reevalúa EXPLÍCITAMENTE aunque ya esté
    CURRENT (misma generación canónica: re-assess sobre el pool + reemplazo de la MISMA identidad gobernada); lo usa el modo
    ``--reassess-current`` del backfill para recuperar evidencia cuando el contexto técnico del pool se refrescó. Single-flight
    por (cliente, AM): dos POST simultáneos ejecutan el motor UNA vez y ambos reciben el mismo resultado."""
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
        flight.result = _generate_locked(entry, res, versions, force=force)
        return flight.result
    finally:
        with _flights_lock:
            _flights.pop(key, None)
        flight.event.set()


def _generate_locked(entry: Any, res: Resolution, versions: dict, *, force: bool = False) -> AssessmentStatus:
    current = _status_from(entry, res, versions)
    if current.state == CURRENT and not force:
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
            durable = _store_envelope(_envelope(client, am, status="OK", profile=doc, pool_signature=sig, versions=dict(doc["versions"]),
                                                domain_classification=_derive_domain_classification(entry, am),
                                                business_classification=_derive_business_classification(entry, am)))
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
        error_code=code, generated_at=retained.get("generated_at") if retained else None,
        domain_classification=(retained or {}).get("domain_classification"), business_classification=(retained or {}).get("business_classification")))
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
    notes: dict = field(default_factory=dict, compare=False)       # decisiones del gancho de sobre (p. ej. accion de revalidacion del Juicio), solo para el informe


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


def _regenerate_core(entry: Any, *, precondition: Any, postcheck: Any, log_label: str, envelope_hook: Any = None) -> RegeneratedAssessment:
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
    notes: dict = {}
    if envelope_hook is None:
        env = _envelope(client, am, status="OK", profile=doc, pool_signature=sig, versions=dict(doc["versions"]),
                        domain_classification=_derive_domain_classification(entry, am), business_classification=_derive_business_classification(entry, am))
    else:                                                 # migracion estricta: el gancho decide el Juicio y los derivados hermanos (sin recalificar ni re-derivar a escondidas)
        overrides, notes = envelope_hook(doc, previous)
        env = _envelope(client, am, status="OK", profile=doc, pool_signature=sig, versions=dict(doc["versions"]), **overrides)
    if not _envelope_resolves_current(env, res, versions):
        raise EnrichmentError("WOULD_NOT_BE_CURRENT")
    return RegeneratedAssessment(client, am, doc, env, (previous or {}).get("assessment_artifact_signature"), notes)


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


def _without_confidence(profile: dict | None) -> dict | None:
    """Perfil sin la confianza (``summary.confidence`` y ``versions.confidence``): lo ÚNICO que ``evidence_confidence_v3`` puede cambiar."""
    if not isinstance(profile, dict):
        return None
    out = copy.deepcopy(profile)
    (out.get("summary") or {}).pop("confidence", None)
    (out.get("versions") or {}).pop("confidence", None)
    return out


MATERIALIZATION_MIGRATION = "evidence_leaf_materialization_v4"


def _without_materialization(profile: dict | None) -> dict | None:
    """Perfil sin lo UNICO que ``evidence_leaf_materialization_v4`` puede cambiar: ``canonical_evidence.version``, la seccion ``leaf_materialization`` y ``versions.canonical_evidence``."""
    if not isinstance(profile, dict):
        return None
    out = copy.deepcopy(profile)
    ce = out.get("canonical_evidence")
    if isinstance(ce, dict):
        ce.pop(_lme.SECTION_KEY, None)
        ce.pop("version", None)
    (out.get("versions") or {}).pop("canonical_evidence", None)
    return out


LAYER_BOUNDARY_MIGRATION = "signals_layer_boundary_v4"
_LAYER_BOUNDARY_ID = "LAYER_BOUNDARY_REVIEW"
_LAYER_BOUNDARY_SURFACE = "rule.layer_boundary_review"
_RULE_INPUTS_COMPONENT = "complete_rule_inputs"
_CONTEXT_COUNTERS = ("open_questions", "questions_backed_by_findings", "questions_without_findings", "occurrences")


def _without_layer_boundary(profile: dict | None) -> dict | None:
    """Perfil sin lo UNICO que ``signals_layer_boundary_v4`` puede cambiar: ``versions.signals``, la revision LAYER_BOUNDARY_REVIEW y lo derivado de ella (su resultado de regla y de superficie, su grupo en el area y su pregunta de
    contexto con los contadores de preguntas). Todo lo demas (evidencia, cobertura, cierre, confianza, las OTRAS revisiones/observaciones) debe quedar IDENTICO."""
    if not isinstance(profile, dict):
        return None
    out = copy.deepcopy(profile)
    (out.get("versions") or {}).pop("signals", None)
    out["review_required"] = [r for r in out.get("review_required") or [] if r.get("id") != _LAYER_BOUNDARY_ID]
    out["rule_results"] = [r for r in out.get("rule_results") or [] if r.get("rule") != _LAYER_BOUNDARY_ID]
    for area in out.get("areas") or []:
        area["finding_groups"] = [g for g in area.get("finding_groups") or [] if g != _LAYER_BOUNDARY_ID]
    for surface in out.get("surfaces") or []:                       # la superficie de la regla conserva su evaluabilidad; solo su RESULTADO (hallazgos) deriva de la revision
        if surface.get("surface_id") == _LAYER_BOUNDARY_SURFACE:
            surface.pop("finding_count", None)
            surface.pop("rule_result", None)
    ctx = (out.get("summary") or {}).get("context_required")
    if isinstance(ctx, dict):
        ctx["questions"] = [q for q in ctx.get("questions") or [] if q.get("id") != _LAYER_BOUNDARY_ID]
        for k in _CONTEXT_COUNTERS:
            ctx.pop(k, None)
    for lim in out.get("evidence_limitations") or []:               # el titulo de la limitacion de la regla copia el de la revision (texto actualizado)
        if lim.get("surface_id") == _LAYER_BOUNDARY_SURFACE:
            lim.pop("title", None)
    conf = (out.get("summary") or {}).get("confidence")             # la parte de «insumos completos de las reglas» de la confianza se deriva del RESULTADO de cada regla (ver ``_check_layer_boundary_delta``)
    if isinstance(conf, dict):
        for c in conf.get("components") or []:
            if c.get("id") == _RULE_INPUTS_COMPONENT:
                for k in ("value", "summary", "level"):
                    c.pop(k, None)
    return out


def _rule_conclusion_share(profile: dict | None) -> float | None:
    """Parte de las conclusiones de reglas (PATTERN_FOUND / ASSESSED_ABSENCE) alcanzadas con insumos completamente evaluados: MISMA definicion que el factor ``complete_rule_inputs`` de la confianza."""
    surfaces = [s for s in (profile or {}).get("surfaces") or [] if s.get("kind") == "RULE_EVALUATION" and s.get("rule_result") in ("PATTERN_FOUND", "ASSESSED_ABSENCE")]
    return round(sum(1 for s in surfaces if s.get("assessability") == "ASSESSED") / len(surfaces), 4) if surfaces else None


def _rule_inputs_component_value(profile: dict | None) -> Any:
    comp = next((c for c in ((profile or {}).get("summary") or {}).get("confidence", {}).get("components") or [] if c.get("id") == _RULE_INPUTS_COMPONENT), None)
    return None if comp is None else comp.get("value")


def _check_layer_boundary_delta(new: dict, old: dict | None) -> None:
    """Postcondicion de ``signals_layer_boundary_v4`` sobre el perfil candidato: (1) la confianza (nivel, eje, limitacion principal) NO cambia (un cambio de nivel es una decision humana: ``CONFIDENCE_LEVEL_CHANGED``);
    (2) todo el perfil salvo la revision y lo derivado de ella queda identico; (3) el unico movimiento permitido en la confianza (la parte de «insumos completos de las reglas») debe estar EXPLICADO por los
    resultados de regla del PROPIO perfil (viejo y nuevo): se recalcula desde sus superficies y debe coincidir. Cualquier otra cosa => ``NON_LAYER_BOUNDARY_CONTENT_CHANGED``."""
    head = lambda p: {k: ((p.get("summary") or {}).get("confidence") or {}).get(k) for k in ("level", "driver", "main_limitation")}      # noqa: E731
    if old is None or head(new) != head(old):
        raise EnrichmentError("CONFIDENCE_LEVEL_CHANGED")
    if _without_layer_boundary(new) != _without_layer_boundary(old):
        raise EnrichmentError("NON_LAYER_BOUNDARY_CONTENT_CHANGED")
    for prof in (new, old):
        share = _rule_conclusion_share(prof)
        if share is not None and round(float(_rule_inputs_component_value(prof) or 0), 4) != share:
            raise EnrichmentError("NON_LAYER_BOUNDARY_CONTENT_CHANGED")

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
        if name == "evidence_confidence_v3" and _without_confidence(doc) != _without_confidence((previous or {}).get("profile_json")):
            raise EnrichmentError("NON_CONFIDENCE_CONTENT_CHANGED")      # solo la confianza puede cambiar: evidencia, cobertura, areas y hallazgos quedan IDENTICOS
        if name == MATERIALIZATION_MIGRATION and _without_materialization(doc) != _without_materialization((previous or {}).get("profile_json")):
            raise EnrichmentError("NON_MATERIALIZATION_CONTENT_CHANGED")  # mismo Assessment metodologico: solo version + seccion nueva pueden diferir
        if name == LAYER_BOUNDARY_MIGRATION:
            _check_layer_boundary_delta(doc, (previous or {}).get("profile_json"))      # solo la revision LAYER_BOUNDARY_REVIEW (y lo derivado de ella) puede diferir; la confianza no cambia de nivel
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


# ═══════════════════════════ v4.C: migracion de la seccion `leaf_materialization` — pool, referencias y revalidacion del Juicio ═══════════════════════════
def _pool_resolver(pool_root: Path | None):
    """Resuelve una referencia (ruta RELATIVA al pool) a su JSON; ``None`` si el archivo ya no existe o no se puede leer. Solo LECTURA; nunca sale de la raiz del pool."""
    def resolve(rel: str) -> Any:
        if pool_root is None:
            return None
        try:
            p = (pool_root / rel).resolve()
            if not _is_within(p, pool_root.resolve()) or not p.is_file():
                return None
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
    return resolve


def _materialization_postcheck(doc: dict, res: Resolution) -> None:
    """Antes de escribir NADA: la seccion nueva debe estar EVALUADA sobre el pool LOCAL (misma firma), sin limitaciones de referencia, y TODA referencia debe resolverse contra
    la fuente (recuperable y con el mismo hash). Cualquier falla => abstencion (``EnrichmentError``): el perfil anterior queda intacto; no se reconstruye evidencia desde otras fuentes."""
    sec = ((doc.get("canonical_evidence") or {}).get(_lme.SECTION_KEY))
    if not isinstance(sec, dict):
        raise EnrichmentError("MATERIALIZATION_SECTION_MISSING")
    if sec.get("evaluation_state") != _lme.EVALUATED:
        raise EnrichmentError("MATERIALIZATION_NOT_EVALUATED")
    csn = (sec.get("derivation_inputs") or {}).get("csn") or {}
    if not csn.get("provided"):
        raise EnrichmentError("MATERIALIZATION_CSN_NOT_PROVIDED")
    if csn.get("pool_signature") != res.pool_signature:
        raise EnrichmentError("MATERIALIZATION_POOL_SIGNATURE_MISMATCH")
    if _lme.LIM_REFERENCE_UNAVAILABLE in (csn.get("limitations") or ()):
        raise EnrichmentError("MATERIALIZATION_REFERENCES_INCOMPLETE")
    audit = _lme.audit_section(sec, _pool_resolver(res.pool_root))
    if audit["has_unrecoverable"]:
        raise EnrichmentError("MATERIALIZATION_SOURCE_NOT_RECOVERABLE")
    if audit["references"] and not audit["all_verified"]:
        raise EnrichmentError("MATERIALIZATION_REFERENCE_UNRESOLVED")


# Juicio persistido y cambio de ``profile_signature``: el Juicio de un sobre lleva la firma del perfil que evaluo (``source_assessment``) y su propia ``output_signature``; al migrar el perfil
# (v3 -> v4) esas dos claves cambian aunque la metodologia NO. Estrategia: REVALIDAR, no recalificar. La parte metodologica (todo salvo las claves de enlace al perfil) del Juicio nuevo, derivado
# por el MISMO motor v2, debe ser IDENTICA a la del Juicio persistido; solo entonces se reconoce la nueva firma. Si difiere en algo, NO se reescribe (divergencia: decision humana).
JUDGMENT_PROFILE_BINDING_KEYS = ("source_assessment", "output_signature")
JDG_SIGNATURE_REBOUND, JDG_METHODOLOGY_DIVERGED = "judgment_signature_rebound", "judgment_methodology_diverged"


def judgment_methodology(judgment: dict | None) -> dict | None:
    """La parte METODOLOGICA del Juicio (nivel, estado, cierre, fortalezas, razones, revisiones, limitaciones, calificadores, CTA, evidencia de apoyo...): todo salvo el enlace al perfil evaluado."""
    if not isinstance(judgment, dict):
        return None
    return {k: v for k, v in judgment.items() if k not in JUDGMENT_PROFILE_BINDING_KEYS}


def judgment_methodology_diff(previous: dict | None, fresh: dict | None) -> list[str]:
    """Claves de la parte metodologica que difieren entre dos Juicios (vacio = misma metodologia)."""
    a, b = judgment_methodology(previous) or {}, judgment_methodology(fresh) or {}
    return sorted(k for k in set(a) | set(b) if _judgment_signature(a.get(k)) != _judgment_signature(b.get(k)))


def revalidate_judgment(previous: dict | None, profile: dict | None) -> tuple[str, dict | None]:
    """(accion, Juicio a persistir). NO recalifica: ``judgment_current`` (identico), ``judgment_signature_rebound`` (misma metodologia, nueva firma de perfil: se persiste el Juicio fresco),
    ``judgment_methodology_diverged`` (la metodologia difiere: NO se reescribe), ``judgment_missing`` (no hay Juicio: no se crea aqui; es ``--backfill-judgment``),
    ``judgment_version_stale`` (contrato de Juicio anterior: se conserva tal cual; es ``--backfill-judgment``) y ``no_profile``."""
    if profile is None:
        return JDG_NO_PROFILE, previous
    if previous is None:
        return JDG_MISSING, None
    if not judgment_is_current_version(previous):
        return JDG_VERSION_STALE, previous
    from webapp.services import judgment_decisions as jd
    fresh = _derive_judgment(profile, jd.consumed(previous))                   # REPLAY de las MISMAS decisiones que consumio el Juicio previo (revalidar no recalifica ni relee el registro)
    if judgment_methodology_diff(previous, fresh):
        return JDG_METHODOLOGY_DIVERGED, None
    return (JDG_CURRENT if _judgment_signature(previous) == _judgment_signature(fresh) else JDG_SIGNATURE_REBOUND), fresh


def classify_judgment_revalidation(env: dict) -> tuple[str, dict | None]:
    """Clasificacion de un sobre gobernado para el operador ``--revalidate-judgment`` (ver ``revalidate_judgment``)."""
    return revalidate_judgment(env.get("judgment"), env.get("profile_json"))


JUDGMENT_REVALIDATING_MIGRATIONS = frozenset({MATERIALIZATION_MIGRATION, LAYER_BOUNDARY_MIGRATION})      # migraciones que NO recalifican: revalidan el Juicio (misma metodologia, nueva firma de perfil)


def _materialization_envelope_hook(doc: dict, previous: dict | None) -> tuple[dict, dict]:
    """Sobre candidato de una migracion que REVALIDA el Juicio (``evidence_leaf_materialization_v4``, ``signals_layer_boundary_v4``): el Juicio se REVALIDA (misma metodologia, nueva firma) o la migracion se abstiene; los derivados HERMANOS (clasificacion de
    dominio y de negocio) se conservan tal cual (no son parte de esta migracion y no dependen del perfil)."""
    prev = previous or {}
    action, judgment = revalidate_judgment(prev.get("judgment"), doc)
    if action == JDG_METHODOLOGY_DIVERGED:
        raise EnrichmentError("JUDGMENT_METHODOLOGY_DIVERGED")
    return ({"judgment": judgment, "domain_classification": prev.get("domain_classification"), "business_classification": prev.get("business_classification")},
            {"judgment_action": action, "previous_judgment_level": (prev.get("judgment") or {}).get("level"), "new_judgment_level": (judgment or {}).get("level")})


def regenerate_stale_evidence(entry: Any, migration: str) -> RegeneratedAssessment:
    """MIGRACION DE EVIDENCIA: regenera en MEMORIA un assessment gobernado STALE VERSION_ONLY cubierto por ``migration`` (registro ``EVIDENCE_MIGRATIONS``).
    Misma disciplina que el enriquecimiento (mismo nucleo): sin escrituras, sin Datasphere, sin OAuth, sin ``restore()``; cualquier fallo conserva el registro
    previo. Lanza ``EnrichmentError`` con codigo estable."""
    def precondition(previous: dict | None, res: Resolution, versions: dict) -> None:
        if not _readable_previous(previous):
            raise EnrichmentError("PREVIOUS_NOT_READABLE")
        cur = classify_currentness(previous.get("input_pool_signature"), previous.get("assessment_versions"), res.pool_signature, versions)
        stale_targeted = cur.state == STALE and cur.reason == STALE_VERSION_ONLY and cur.targeted == migration
        if not stale_targeted and not (cur.state == CURRENT and cur.pending_migration == migration):
            raise EnrichmentError("PREVIOUS_NOT_TARGETED_VERSION_ONLY_STALE")

    def postcheck(doc: dict, previous: dict | None, res: Resolution, versions: dict) -> None:
        migration_postcheck(migration, doc, previous, versions)
        if migration == MATERIALIZATION_MIGRATION:
            _materialization_postcheck(doc, res)

    return _regenerate_core(entry, precondition=precondition, log_label=f"migración {migration}", postcheck=postcheck,
                            envelope_hook=_materialization_envelope_hook if migration in JUDGMENT_REVALIDATING_MIGRATIONS else None)


def regenerate_current_evidence(entry: Any, snapshot: Any = None) -> RegeneratedAssessment:
    """REGENERACION SELECTIVA de un assessment gobernado CURRENT cuya EVIDENCIA quedo atras de una correccion del motor que NO cambia ``assessment_versions`` (p. ej. el parser de B5.6).
    Mismo nucleo seguro que el resto de las regeneraciones controladas (en MEMORIA, sin Datasphere/OAuth/``restore()``, sin escribir). Exige: registro previo legible y CURRENT (misma firma de pool y
    mismo bloque de versiones), Juicio previo en el contrato vigente y MISMO cierre semantico (AM terminal, raiz y conjunto de artefactos; si cambia => ``CLOSURE_DRIFT``). El Juicio se RE-DERIVA con
    el motor vigente y la instantanea de decisiones ``snapshot`` (``None`` = sin decisiones); la clasificacion de dominio y de negocio (derivados hermanos) se conservan tal cual."""
    from webapp.services import judgment_decisions as jd
    snap = snapshot if snapshot is not None else jd.empty_snapshot()

    def precondition(previous: dict | None, res: Resolution, versions: dict) -> None:
        if (not _readable_previous(previous) or previous.get("input_pool_signature") != res.pool_signature or previous.get("assessment_versions") != versions):
            raise EnrichmentError("PREVIOUS_NOT_CURRENT")
        if not judgment_is_current_version(previous.get("judgment")):
            raise EnrichmentError("PREVIOUS_JUDGMENT_NOT_CURRENT")

    def postcheck(doc: dict, previous: dict | None, res: Resolution, versions: dict) -> None:
        old_id, new_id = closure_identity((previous or {}).get("profile_json")), closure_identity(doc)
        if old_id is None or new_id != old_id:
            raise EnrichmentError("CLOSURE_DRIFT")

    def hook(doc: dict, previous: dict | None) -> tuple[dict, dict]:
        prev = previous or {}
        fresh, _report = jd.derive(doc, str(prev.get("client") or ""), snap)
        return ({"judgment": fresh, "domain_classification": prev.get("domain_classification"), "business_classification": prev.get("business_classification")},
                {"previous_judgment_level": (prev.get("judgment") or {}).get("level"), "previous_judgment_rule": (prev.get("judgment") or {}).get("level_rule"),
                 "new_judgment_level": (fresh or {}).get("level"), "new_judgment_rule": (fresh or {}).get("level_rule"),
                 "judgment_action": "judgment_current" if (prev.get("judgment") or {}).get("output_signature") == (fresh or {}).get("output_signature") else "judgment_rederived"})

    return _regenerate_core(entry, precondition=precondition, log_label="regeneración selectiva", postcheck=postcheck, envelope_hook=hook)


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
