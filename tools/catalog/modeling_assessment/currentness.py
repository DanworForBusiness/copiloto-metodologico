"""currentness.py — regla de VIGENCIA de un assessment persistido (PURA: sin E/S, sin persistencia, sin servicios).

Fuente ÚNICA de la regla que usan el servicio del Hub (``webapp.services.assessment_service``, que reexporta estos nombres) y la simulación de
reassessment (``tools.catalog.reassessment``). Se extrajo SIN CAMBIOS de comportamiento: el registro de migraciones de evidencia, el clasificador
``classify_currentness`` y la combinación con la firma de dependencias (``resolve_currentness``) son los mismos de antes.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

CURRENT = "CURRENT"
STALE = "STALE"

# Causa de un STALE (NO es un estado publico nuevo: los seis estados no cambian). Sirve para el mensaje al usuario y para decidir que migracion aplica.
STALE_VERSION_ONLY = "VERSION_ONLY"                    # misma entrada (firma del pool igual); cambio el contrato de evidencia/metodologia (versiones)
STALE_INPUT_CHANGED = "INPUT_CHANGED"                  # cambio la entrada tecnica (firma del pool); mismas versiones
STALE_VERSION_AND_INPUT = "VERSION_AND_INPUT"          # ambas
STALE_REASONS = (STALE_VERSION_ONLY, STALE_INPUT_CHANGED, STALE_VERSION_AND_INPUT)

DEP_INDEPENDENT, DEP_AFFECTED, DEP_UNCERTAIN = "INDEPENDENT", "AFFECTED", "UNCERTAIN"


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


def dependency_verdict_from(stored_signature: str | None, stored_rule: str | None, current_rule: str, current_signature: str | None) -> str:
    """Comparación PURA de firmas de dependencias: ``INDEPENDENT`` = existen, son de la MISMA regla y coinciden; ``AFFECTED`` = existen y difieren;
    ``UNCERTAIN`` = falta alguna o la regla es otra (no se afirma independencia)."""
    if not stored_signature or stored_rule != current_rule or current_signature is None:
        return DEP_UNCERTAIN
    return DEP_INDEPENDENT if current_signature == stored_signature else DEP_AFFECTED


def resolve_currentness(stored_signature: str | None, stored_versions: dict | None, pool_signature: str | None, versions: dict,
                        dependency: Callable[[], str]) -> tuple[Currentness, bool, str | None]:
    """(vigencia, ``pool_drift``, veredicto de dependencias o None). Si el pool completo cambió, eso solo invalida el assessment cuando cambió algo
    que ÉL puede leer: ``dependency()`` (perezoso: solo se calcula en ese caso) decide; ``INDEPENDENT`` = su entrada no cambió a efectos de vigencia."""
    cur = classify_currentness(stored_signature, stored_versions, pool_signature, versions)
    drift, verdict = False, None
    if cur.state == STALE and cur.reason in (STALE_INPUT_CHANGED, STALE_VERSION_AND_INPUT):
        verdict = dependency()
        if verdict == DEP_INDEPENDENT:
            drift = True
            cur = classify_currentness(stored_signature, stored_versions, stored_signature, versions)
    return cur, drift, verdict
