"""Contrato ESTRUCTURAL de un artefacto y delta entre artefactos encadenados por linaje (M3). Solo hechos.

Disciplina de Fase 1: METADATA -> HECHO NORMALIZADO. Este modulo no emite `Fact`: arma los registros
normalizados que usan los extractores de ANALYTIC_MODEL, PROPAGATION y HARMONIZATION, de modo que los tres
lean el MISMO contrato con la MISMA semantica (una sola fuente de verdad para "que declara un artefacto").

Lo que NO hace (a proposito):
  * no interpreta conteos como calidad: `derived`, asociaciones, medidas y campos son contexto, no proxies;
  * no infiere ROL FUNCIONAL: `declared_role` copia lo que el export DECLARA (`modelingPattern`, `dataCategory`);
    la capa INFERIDA (criterio compuesto) se expone aparte y nunca se usa para clasificar responsabilidad;
  * no fabrica evidencia: el flag `derived` solo existe para columnas con expresion visible en el query
    (xpr/func/val/cast); la semantica de la formula NO es observable en el export estandar (C2).

Los registros devuelven listas de dicts (nunca dicts cuyas claves sean nombres de artefacto/campo): un nombre de
cliente podria chocar con el vocabulario prohibido del perfil.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Iterable

from . import legacy_bridge as bridge

# Tipos CDS numericos declarados (se compara contra `tipo`, el tipo declarado sin el prefijo `cds.`).
NUMERIC_DECLARED_TYPES = frozenset({
    "decimal", "decimalfloat", "double", "integer", "integer64", "int16", "int32", "int64", "uint8",
    "smalldecimal", "smallint", "tinyint", "bigint", "float",
})


class InferredLayerBasis(str, Enum):
    """Regla del criterio compuesto que produjo la capa inferida (misma precedencia que `_capa_of`)."""
    REMOTE_ENTITY = "REMOTE_ENTITY"        # el artefacto tiene `remote_entity`
    SECTION_28_NAME = "SECTION_28_NAME"    # el nombre codifica la capa por el prefijo §28 (p. ej. `2VR_`)
    DECLARED_ROLE = "DECLARED_ROLE"        # el export declara rol (cubo -> 4, hecho -> 3, dimension -> 2)
    NAME_PREFIX = "NAME_PREFIX"            # ultimo recurso: prefijo del nombre (AM_/EM_/V*)
    UNKNOWN = "UNKNOWN"                    # no clasificable ("9")


def inferred_layer(entity: Any) -> str:
    """CAPA INFERIDA por el criterio compuesto del legacy (`_capa_of`). NO es rol funcional ni responsabilidad
    metodologica probada. "9" = no clasificable. Se copia solo como CONTEXTO; ningun hecho de M3 selecciona poblacion
    con ella (evita clasificacion circular: el criterio mismo usa el rol declarado)."""
    return str(bridge.capa_of(entity))


def inferred_layer_basis(entity: Any) -> str:
    """Que regla del criterio compuesto produjo la capa inferida. Misma precedencia que `_capa_of`:
    (1) `remote_entity`; (2) prefijo de capa §28 en el nombre; (3) rol DECLARADO; (4) prefijo de nombre; UNKNOWN.
    La paridad con `inferred_layer` esta verificada por tests."""
    if getattr(entity, "remote_entity", ""):
        return InferredLayerBasis.REMOTE_ENTITY.value
    if bridge.parse_name(getattr(entity, "name", "")).get("capa"):
        return InferredLayerBasis.SECTION_28_NAME.value
    sem = f"{getattr(entity, 'data_category', '') or ''} {getattr(entity, 'modeling_pattern', '') or ''}".upper()
    if any(k in sem for k in ("CUBE", "FACT", "DIMENSION")):
        return InferredLayerBasis.DECLARED_ROLE.value
    return InferredLayerBasis.NAME_PREFIX.value if inferred_layer(entity) != "9" else InferredLayerBasis.UNKNOWN.value


def layer_inference(entity: Any) -> dict:
    """{capa inferida, base de la inferencia}: SIEMPRE juntas, para que se distingan."""
    return {"inferred_layer": inferred_layer(entity), "inferred_layer_basis": inferred_layer_basis(entity)}


def _fields(entity: Any) -> list[dict]:
    return list(getattr(entity, "fields", None) or [])


def field_names(entity: Any) -> list[str]:
    return [f["campo"] for f in _fields(entity)]


def declared_role(entity: Any) -> dict:
    """Rol DECLARADO por el export (copiado tal cual). `declares_*` solo informan si el texto declarado lo contiene."""
    pattern = str(getattr(entity, "modeling_pattern", "") or "")
    category = str(getattr(entity, "data_category", "") or "")
    sem = f"{category} {pattern}".upper()
    return {"modeling_pattern": pattern or None, "data_category": category or None,
            "declares_dimension": "DIMENSION" in sem, "declares_fact": "FACT" in sem, "declares_cube": "CUBE" in sem}


def field_records(entity: Any) -> list[dict]:
    out = []
    for f in _fields(entity):
        out.append({"field": f["campo"], "label_present": bool(str(f.get("label") or "").strip()),
                    "type": f.get("tipo") or None, "length": f.get("longitud") or None,
                    "key": bool(f.get("key")), "measure": bool(f.get("medida")),
                    "association_target": f.get("assoc") or None, "derived": bool(f.get("derived"))})
    return out


def measure_records(entity: Any) -> list[dict]:
    return [{"field": m["campo"], "aggregation": m.get("agg") or None} for m in (getattr(entity, "measures", None) or [])]


def association_records(entity: Any) -> list[dict]:
    out = []
    for a in (getattr(entity, "associations", None) or []):
        on = a.get("on") or {}
        out.append({"field": a.get("campo"), "target": a.get("target"),
                    "on_columns": [{"source_column": k, "target_column": v} for k, v in sorted(on.items())]})
    return out


def derived_records(entity: Any) -> list[dict]:
    """Campos con expresion visible en el query. `derived=True` NO implica medida (puede ser una FK/atributo)."""
    out = []
    for f in _fields(entity):
        if f.get("derived"):
            out.append({"field": f["campo"], "is_measure": bool(f.get("medida")),
                        "is_association_foreign_key": bool(f.get("assoc")), "cast": f.get("cast") or None})
    return out


def derived_names(entity: Any) -> list[str]:
    return [r["field"] for r in derived_records(entity)]


def keys_of(entity: Any) -> list[str]:
    return list(getattr(entity, "keys", None) or [])


def label_coverage(entity: Any) -> dict:
    fs = _fields(entity)
    labelled = sum(1 for f in fs if str(f.get("label") or "").strip())
    return {"fields": len(fs), "with_label": labelled, "without_label": len(fs) - labelled}


def derivation_visible(entity: Any) -> bool:
    """El flag `derived` solo se puede poblar cuando el export trae un query parseable."""
    return bool(getattr(entity, "has_query", False))


def numeric_non_measure_fields(entity: Any) -> list[dict]:
    """Campos de tipo numerico DECLARADO que no son medida, clave ni FK de asociacion (hecho; no se interpreta)."""
    out = []
    for f in _fields(entity):
        if f.get("medida") or f.get("key") or f.get("assoc"):
            continue
        tipo = str(f.get("tipo") or "").lower()
        if tipo in NUMERIC_DECLARED_TYPES:
            out.append({"field": f["campo"], "type": f.get("tipo"), "length": f.get("longitud") or None})
    return out


def contract_summary(entity: Any) -> dict:
    """Resumen compacto de lo que el artefacto DECLARA (contexto de rol incluido)."""
    fs = _fields(entity)
    return {"n_elements": int(getattr(entity, "n_elements", 0) or 0), "n_fields": len(fs),
            "n_associations": len(getattr(entity, "associations", None) or []),
            "n_measures": len(getattr(entity, "measures", None) or []),
            "n_declared_dimensions": len(getattr(entity, "dimensions", None) or []),
            "n_keys": len(keys_of(entity)), "n_derived": len(derived_names(entity)),
            "has_query": bool(getattr(entity, "has_query", False)),
            "repository_type": getattr(entity, "repository_type", "") or None}


def associations_by_target(entity: Any) -> dict[str, list[str]]:
    """Asociaciones agrupadas por DESTINO (identidad = destino; los nombres de campo de asociacion varian entre
    capas aunque apunten al mismo destino, y un artefacto puede tener varias asociaciones al mismo destino)."""
    out: dict[str, list[str]] = {}
    for a in association_records(entity):
        out.setdefault(a["target"], []).append(a["field"])
    return out


def edge_delta(provider: Any, consumer: Any, *, consumer_lineage_provider_count: int) -> dict:
    """Delta de contrato consumer vs UN provider de linaje (listas completas, no muestras). Solo hechos."""
    pf, cf = field_records(provider), field_records(consumer)
    p_names, c_names = [r["field"] for r in pf], [r["field"] for r in cf]
    p_set, c_set = set(p_names), set(c_names)
    p_by, c_by = {r["field"]: r for r in pf}, {r["field"]: r for r in cf}
    added = [n for n in c_names if n not in p_set]
    removed = [n for n in p_names if n not in c_set]
    retained = [n for n in c_names if n in p_set]

    type_changes = [{"field": n, "provider_type": p_by[n]["type"], "provider_length": p_by[n]["length"],
                     "consumer_type": c_by[n]["type"], "consumer_length": c_by[n]["length"]}
                    for n in retained if (p_by[n]["type"], p_by[n]["length"]) != (c_by[n]["type"], c_by[n]["length"])]
    measure_role_changes = [{"field": n, "provider_is_measure": p_by[n]["measure"], "consumer_is_measure": c_by[n]["measure"]}
                            for n in retained if p_by[n]["measure"] != c_by[n]["measure"]]

    pm = {m["field"]: m["aggregation"] for m in measure_records(provider)}
    cm = {m["field"]: m["aggregation"] for m in measure_records(consumer)}
    measures_added = [n for n in cm if n not in pm]
    measures_removed = [n for n in pm if n not in cm]
    aggregation_changes = [{"field": n, "provider_aggregation": pm[n], "consumer_aggregation": cm[n]}
                           for n in cm if n in pm and pm[n] != cm[n]]

    pa, ca = associations_by_target(provider), associations_by_target(consumer)
    assoc_introduced = [{"target": t, "consumer_fields": f} for t, f in ca.items() if t not in pa]
    assoc_inherited = [{"target": t, "provider_fields": pa[t], "consumer_fields": f} for t, f in ca.items() if t in pa]
    assoc_removed = [{"target": t, "provider_fields": f} for t, f in pa.items() if t not in ca]

    pk, ck = keys_of(provider), keys_of(consumer)
    derived_p, derived_c = derived_names(provider), derived_names(consumer)
    derived_both = [n for n in derived_c if n in set(derived_p)]
    derived_only_consumer = [n for n in derived_c if n not in set(derived_p)]

    return {
        "provider_role": declared_role(provider), "consumer_role": declared_role(consumer),
        "provider_layer_inference": layer_inference(provider),
        "consumer_layer_inference": layer_inference(consumer),
        "provider_summary": contract_summary(provider), "consumer_summary": contract_summary(consumer),
        "consumer_lineage_provider_count": consumer_lineage_provider_count,
        "fields": {"added": added, "removed": removed, "retained_count": len(retained),
                   "type_changes": type_changes, "measure_role_changes": measure_role_changes},
        "measures": {"added": measures_added, "removed": measures_removed, "aggregation_changes": aggregation_changes},
        "associations": {"introduced": assoc_introduced, "inherited": assoc_inherited, "removed": assoc_removed},
        "keys": {"provider": pk, "consumer": ck, "both_declare_keys": bool(pk) and bool(ck),
                 "equal": set(pk) == set(ck), "lost": [k for k in pk if k not in set(ck)],
                 "gained": [k for k in ck if k not in set(pk)]},
        "derived": {"in_provider": derived_p, "in_consumer": derived_c, "in_both": derived_both,
                    "only_in_consumer": derived_only_consumer,
                    "provider_derivation_visible": derivation_visible(provider),
                    "consumer_derivation_visible": derivation_visible(consumer)},
        "descriptors": {"schema_identical": not added and not removed,
                        "introduces_no_derived_field": not derived_only_consumer,
                        "introduces_no_association": not assoc_introduced,
                        "introduces_no_measure": not measures_added},
    }


def ordered_unique(values: Iterable[str]) -> list[str]:
    seen, out = set(), []
    for v in values:
        if v not in seen:
            seen.add(v)
            out.append(v)
    return out
