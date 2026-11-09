"""
review_evidence.py — Sidecar `review-evidence/v1`: identidad estable + HUELLA material por revisión (Iteración II).

PLANO: evidencia material autoritativa de cada revisión resoluble, calculada desde el FactIndex COMPLETO en la MISMA ejecución
de `shadow.assess` que produce el perfil (misma evidencia, mismo ciclo, misma versión de reglas). Vive en un sidecar HERMANO del
perfil dentro del envelope gobernado, FUERA de `canonical-evidence/v5` y FUERA del `profile` que firma el Juicio (no churn, D8).

Por qué un sidecar y no el perfil frío: `fact_index` del perfil publicado solo lleva NOMBRES de hechos (no valores) y
`canonical-evidence/v5` excluye `direct_source` (Iteración I). La huella material autoritativa (valores de los hechos de
evidencia de cada hallazgo) no está en el perfil frío; el sidecar la persiste una vez, ligada por `input_signature` + la firma
EXACTA del perfil. En lectura, si el sidecar falta / no liga / no tiene la entrada ⇒ la compatibilidad es UNKNOWN (nunca MATCH).

IDENTIDAD por familia (en `subject.technical_id`, JSON canónico): el artefacto entra en la identidad SOLO cuando la revisión es
sobre un artefacto único (TF, ROLE_LAYER); en FIELD_VOCABULARY_MIX el ancla es la etiqueta normalizada y el conjunto de
artefactos va a la HUELLA (crecer ⇒ STALE, no una revisión nueva). En DUPLICATED_REMOTE_IMPORT el ancla es la identidad remota y su
conjunto de representaciones locales va a la HUELLA, proyectada desde el hecho de clausura (adaptador `_FOOTPRINT_ADAPTERS`). Nunca índices de presentación (occurrence_id) ni texto del informe.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

REVIEW_EVIDENCE_VERSION = "review-evidence/v1"

KLASS_MODEL_REVIEW = "MODEL_REVIEW"
KLASS_CONTEXTUAL_OPPORTUNITY = "CONTEXTUAL_OPPORTUNITY"

# Reglas resolubles en v1 y su familia. Las demás señales no son resolubles por este contrato (se omiten del sidecar).
KLASS_BY_RULE = {
    "TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY": KLASS_CONTEXTUAL_OPPORTUNITY,
    "ROLE_LAYER_DIVERGENCE": KLASS_MODEL_REVIEW,
    "FIELD_VOCABULARY_MIX": KLASS_MODEL_REVIEW,
    "LAYER_BOUNDARY_REVIEW": KLASS_MODEL_REVIEW,
    "DUPLICATED_REMOTE_IMPORT": KLASS_MODEL_REVIEW,
    "PASS_THROUGH_REVIEW": KLASS_MODEL_REVIEW,
    "REPEATED_DERIVATION_IN_LINEAGE": KLASS_MODEL_REVIEW,
    "LABEL_NOT_ENRICHING": KLASS_MODEL_REVIEW,
    "AM_SEMANTIC_UNDERUSE_REVIEW": KLASS_MODEL_REVIEW,
    # OBSERVACIÓN (conserva SignalKind.OBSERVATION): resoluble por ocurrencia, misma familia de disposiciones que las revisiones.
    "DIRECT_RAW_TABLE_ACCESS": KLASS_MODEL_REVIEW,
}

# Reglas cuya identidad EXIGE su ancla (arista/artefacto/AM): sin ancla no hay identidad inequívoca ⇒ None (no resoluble).
_ANCHOR_REQUIRED = frozenset({"PASS_THROUGH_REVIEW", "REPEATED_DERIVATION_IN_LINEAGE", "LABEL_NOT_ENRICHING", "AM_SEMANTIC_UNDERUSE_REVIEW",
                              "DIRECT_RAW_TABLE_ACCESS"})


def canonical_json(obj: Any) -> str:
    """JSON determinista (claves ordenadas, separadores estables, UTF-8 literal). Igual forma que el contrato de gobierno/Juicio."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ─────────────────────────── identidad estable por familia ───────────────────────────

def review_identity(rule: str, root_am: str, values: Mapping[str, Any], affected_artifacts: list) -> dict | None:
    """Identidad ESTABLE de una revisión (dict canónico con `klass`). None si la regla no es resoluble en v1.
    Deriva de campos ESTRUCTURADOS de la ocurrencia (no del occurrence_id ni de texto). El artefacto entra en la identidad solo
    cuando la revisión es sobre un artefacto único."""
    klass = KLASS_BY_RULE.get(rule)
    if klass is None:
        return None
    first = affected_artifacts[0] if affected_artifacts else None
    ident: dict[str, Any] = {"klass": klass, "root_am": root_am, "rule": rule}
    if rule == "TRANSFORMATION_FLOW_CONSOLIDATION_OPPORTUNITY":
        ident["artifact"] = values.get("materialized_table") or first
    elif rule == "ROLE_LAYER_DIVERGENCE":
        ident["artifact"] = values.get("artifact") or first
        ident["motive"] = sorted(values.get("divergence_kinds") or [])          # motivos residuales (no los resueltos por evidencia)
    elif rule == "FIELD_VOCABULARY_MIX":
        ident["motive"] = values.get("label")                                    # etiqueta normalizada; artefactos → HUELLA, no identidad
    elif rule == "LAYER_BOUNDARY_REVIEW":
        ident["motive"] = values.get("source")                                   # fuente remota
    elif rule == "DUPLICATED_REMOTE_IMPORT":
        ident["motive"] = values.get("remote_entity")                            # identidad remota; representaciones locales → HUELLA
    elif rule == "PASS_THROUGH_REVIEW":
        ident["motive"] = values.get("edge")                                     # arista `proveedor->consumidor`
    elif rule == "REPEATED_DERIVATION_IN_LINEAGE":
        p, c = values.get("provider"), values.get("consumer")
        ident["motive"] = f"{p}->{c}" if p and c else None                       # arista `proveedor->consumidor`
    elif rule == "LABEL_NOT_ENRICHING":
        ident["artifact"] = values.get("artifact") or first
    elif rule == "AM_SEMANTIC_UNDERUSE_REVIEW":
        ident["artifact"] = first                                                # el modelo analítico evaluado (sujeto del hecho)
    elif rule == "DIRECT_RAW_TABLE_ACCESS":
        ident["artifact"] = first                                                # artefacto local que realiza el acceso
        ident["motive"] = values.get("remote_entity")                            # tabla física remota referenciada
        if not (ident["artifact"] and ident["motive"]):
            return None
    if rule in _ANCHOR_REQUIRED and not (ident.get("motive") or ident.get("artifact")):
        return None
    return ident


def review_technical_id(identity: Mapping[str, Any]) -> str:
    """`technical_id` exacto y estable = JSON canónico de la identidad (claves ordenadas). Declara `klass` (lo cruza el contrato)."""
    return canonical_json(dict(identity))


# ─────────────────────────── huella material (autoritativa) ───────────────────────────

def footprint_material(evidence_facts: list, fact_by_key: Mapping[str, Any]) -> dict | None:
    """Huella MATERIAL autoritativa = valores de los hechos de evidencia de la ocurrencia (del FactIndex). Determinista.
    None si falta algún hecho referido (no se puede acreditar la huella ⇒ la lectura dará UNKNOWN)."""
    material: dict[str, Any] = {}
    for key in sorted(evidence_facts or []):
        if key not in fact_by_key:
            return None
        material[key] = fact_by_key[key]
    return material or None


# Hecho de CLAUSURA que comparten todas las ocurrencias de DUPLICATED_REMOTE_IMPORT: la huella se PROYECTA a la entrada de la
# identidad remota de la revisión (cambiar otra entidad no invalida esta; cambiar su propio conjunto de representaciones sí ⇒ STALE).
DUPLICATE_REMOTE_IMPORTS_FACT = "ingestion.duplicate_remote_imports@closure"


def _duplicated_remote_import_footprint(occ: Mapping[str, Any], fact_by_key: Mapping[str, Any]) -> dict | None:
    """Huella proyectada: {hecho: {remote_entity, local_artifacts (todas, ordenadas), in_closure (ordenadas)}} tomada del HECHO
    (no de los valores del informe). None si el hecho falta o no contiene la entidad (⇒ no se publica ⇒ lectura UNKNOWN)."""
    remote = (occ.get("values") or {}).get("remote_entity")
    if not remote or DUPLICATE_REMOTE_IMPORTS_FACT not in (occ.get("evidence_facts") or []):
        return None
    value = fact_by_key.get(DUPLICATE_REMOTE_IMPORTS_FACT)
    if not isinstance(value, Mapping):
        return None
    entries = [g for g in (value.get("in_universe_touching_closure") or []) if isinstance(g, Mapping) and g.get("remote_entity") == remote]
    if len(entries) != 1:
        return None
    g = entries[0]
    return {DUPLICATE_REMOTE_IMPORTS_FACT: {"remote_entity": remote, "local_artifacts": sorted(g.get("local_artifacts") or []),
                                            "in_closure": sorted(g.get("in_closure") or [])}}


# Hecho de CLAUSURA de REPEATED_DERIVATION_IN_LINEAGE: lista TODAS las rederivaciones del linaje. La huella se PROYECTA a las
# rederivaciones de la arista de la revisión, conservando su detalle técnico completo (campo, relación, motivo, distancia...).
REDERIVATION_FACT = "propagation.rederivation@closure"


def _repeated_derivation_footprint(occ: Mapping[str, Any], fact_by_key: Mapping[str, Any]) -> dict | None:
    """Huella proyectada: {hecho: {provider, consumer, occurrences (de ESA arista, ordenadas por campo)}} tomada del HECHO. None si
    el hecho falta o no tiene rederivaciones para la arista (⇒ no se publica ⇒ lectura UNKNOWN)."""
    values = occ.get("values") or {}
    provider, consumer = values.get("provider"), values.get("consumer")
    if not provider or not consumer or REDERIVATION_FACT not in (occ.get("evidence_facts") or []):
        return None
    value = fact_by_key.get(REDERIVATION_FACT)
    if not isinstance(value, Mapping):
        return None
    own = [o for o in (value.get("occurrences") or []) if isinstance(o, Mapping) and o.get("provider") == provider and o.get("consumer") == consumer]
    if not own:
        return None
    own = sorted(own, key=lambda o: canonical_json(o))
    return {REDERIVATION_FACT: {"provider": provider, "consumer": consumer, "occurrences": own}}


# DIRECT_RAW_TABLE_ACCESS: huella = SOLO la relación de acceso del artefacto a la tabla física (hechos por artefacto ⇒ independiente
# de otras observaciones). Criterio de inclusión:
#   · source.contract     conexión, entidad remota, namespace, estado de la fuente y stem (SIN `artifact_label`: cosmético).
#   · source.query_filter condiciones del acceso (consulta remota, WHERE, columnas consultadas); None si no se pudo evaluar.
#   · source.keys         SOLO `key_fields`: granularidad/integridad de lo que se lee (los conteos son derivados).
# Excluidos: source.downstream_reach (consumidores aguas abajo: un consumidor nuevo no invalida la justificación del acceso) y
# source.identity_continuity (clasificación heurística del NOMBRE local frente al stem remoto: contexto derivado, no la relación).
_RAW_CONTRACT_FIELDS = ("remote_connection", "remote_entity", "namespace", "source_state", "remote_stem")


def _direct_raw_table_access_footprint(occ: Mapping[str, Any], fact_by_key: Mapping[str, Any]) -> dict | None:
    artifacts = occ.get("affected_artifacts") or []
    if not artifacts:
        return None
    art = artifacts[0]
    refs = set(occ.get("evidence_facts") or [])
    ck = f"source.contract@{art}"
    contract = fact_by_key.get(ck) if ck in refs else None
    if not isinstance(contract, Mapping) or contract.get("remote_entity") != (occ.get("values") or {}).get("remote_entity"):
        return None                                                              # sin contrato verificable de ESTE acceso ⇒ UNKNOWN
    material: dict[str, Any] = {ck: {k: contract.get(k) for k in _RAW_CONTRACT_FIELDS}}
    qk = f"source.query_filter@{art}"
    if qk in refs:
        qf = fact_by_key.get(qk)
        material[qk] = ({k: qf.get(k) for k in ("has_remote_query", "where", "query_column_count")} if isinstance(qf, Mapping) else None)
    kk = f"source.keys@{art}"
    if kk in refs:
        keys = fact_by_key.get(kk)
        material[kk] = {"key_fields": list(keys.get("key_fields") or [])} if isinstance(keys, Mapping) else None
    return material


# Adaptadores de huella por regla. Las reglas sin adaptador usan `footprint_material` (valores completos de sus hechos): sus
# digests NO cambian al agregar adaptadores.
_FOOTPRINT_ADAPTERS = {"DUPLICATED_REMOTE_IMPORT": _duplicated_remote_import_footprint,
                       "REPEATED_DERIVATION_IN_LINEAGE": _repeated_derivation_footprint,
                       "DIRECT_RAW_TABLE_ACCESS": _direct_raw_table_access_footprint}


def occurrence_footprint(rule: str, occ: Mapping[str, Any], fact_by_key: Mapping[str, Any]) -> dict | None:
    """Huella material de UNA ocurrencia según su regla (adaptador específico o genérico)."""
    adapter = _FOOTPRINT_ADAPTERS.get(rule)
    if adapter is not None:
        return adapter(occ, fact_by_key)
    return footprint_material(occ.get("evidence_facts") or [], fact_by_key)


def footprint_digest(material: Mapping[str, Any]) -> str:
    """Digest sha256 hex de la huella material canónica."""
    return hashlib.sha256(canonical_json(material).encode("utf-8")).hexdigest()


# ─────────────────────────── firma del perfil (binding) ───────────────────────────

def profile_signature(profile: Mapping[str, Any]) -> str:
    """Firma EXACTA del perfil = sha256 de su JSON canónico, prefijado `sha256:` (misma forma de firma que usa el Juicio sobre
    el perfil). El perfil publicado es JSON-safe (sin sets/tuplas) ⇒ coincide bit a bit. Liga el sidecar a SU perfil."""
    return "sha256:" + hashlib.sha256(canonical_json(profile).encode("utf-8")).hexdigest()


def input_signature(profile: Mapping[str, Any]) -> str:
    """Firma de INSUMOS del Assessment (procedencia): `pool_signature` de fuentes + bloque `versions`. Distinta de la firma
    exacta del perfil; acota el mismo ciclo/versión de reglas."""
    scope = profile.get("scope") or {}
    return canonical_json({"pool_signature": scope.get("pool_signature"), "versions": profile.get("versions") or {}})


# ─────────────────────────── construcción del sidecar (en shadow.assess) ───────────────────────────

def _review_groups(doc: Mapping[str, Any]):
    for key in ("review_required", "observations"):
        for g in (doc.get(key) or ()):
            if isinstance(g, dict):
                yield g


def build_sidecar(doc: Mapping[str, Any], facts, root_am: str) -> dict:
    """Arma `review-evidence/v1` desde el perfil (`doc`) y los HECHOS COMPLETOS de la MISMA ejecución (con `direct_source`).
    `facts` = lista de Fact (tiene `.key` y `.value`). Liga por `input_signature` + firma EXACTA del perfil. Puro, sin I/O."""
    fact_by_key = {getattr(f, "key", None): getattr(f, "value", None) for f in (facts or ())}
    footprints: dict[str, dict] = {}
    for group in _review_groups(doc):
        rule = group.get("id")
        if rule not in KLASS_BY_RULE:
            continue
        for occ in (group.get("occurrences") or ()):
            ident = review_identity(rule, root_am, occ.get("values") or {}, occ.get("affected_artifacts") or [])
            if ident is None:
                continue
            material = occurrence_footprint(rule, occ, fact_by_key)
            if material is None:
                continue                                                         # no acreditable ⇒ no se publica (lectura → UNKNOWN)
            tid = review_technical_id(ident)
            footprints[tid] = {"rule": rule, "klass": ident["klass"], "digest": footprint_digest(material),
                               "affected_artifacts": sorted(occ.get("affected_artifacts") or [])}
    return {"version": REVIEW_EVIDENCE_VERSION, "root_am": root_am, "input_signature": input_signature(doc),
            "profile_signature": profile_signature(doc), "footprints": footprints}


# ─────────────────────────── verificación de ligadura + huella vigente (en lectura) ───────────────────────────

def sidecar_binds_profile(sidecar: Any, profile: Mapping[str, Any]) -> bool:
    """¿El sidecar corresponde EXACTAMENTE a este perfil? Verifica versión + `input_signature` + firma exacta del perfil.
    Cualquier desajuste ⇒ False (la lectura degradará a UNKNOWN; nunca MATCH por defecto)."""
    if not isinstance(sidecar, dict):
        return False
    if sidecar.get("version") != REVIEW_EVIDENCE_VERSION:
        return False
    if sidecar.get("profile_signature") != profile_signature(profile):
        return False
    if sidecar.get("input_signature") != input_signature(profile):
        return False
    return True


def current_footprint_digest(technical_id: str, sidecar: Any, profile: Mapping[str, Any]) -> str | None:
    """Digest de la huella VIGENTE de una revisión, desde el sidecar ligado a ESTE perfil. None (⇒ UNKNOWN) si el sidecar no
    liga, no existe o no contiene la entrada de esa identidad."""
    if not sidecar_binds_profile(sidecar, profile):
        return None
    fp = (sidecar.get("footprints") or {}).get(technical_id)
    if not isinstance(fp, dict):
        return None
    return fp.get("digest")
