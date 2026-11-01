"""Motor PURO `connectivity-reconciliation/v1` (F2.2a): compara dos observaciones `connectivity-observation/v1`.

Determinístico, sin I/O ni escrituras. Sirve para snapshot↔snapshot y snapshot↔Catálogo publicado: la base (`baseline`) puede ser otra extracción o
el Catálogo, y su PROCEDENCIA (fecha, cobertura, límites) viaja en el resultado — el Catálogo nunca se asume «último estado SAP».

Separación de planos (ajuste metodológico del owner):
  * OBSERVACIÓN — presencia de cada conexión en cada lado (PRESENT / ABSENT / UNKNOWN) y comparación por grupos.
  * EVIDENCIA DE AUSENCIA — con qué fuerza consta que algo NO está (cobertura + exhaustividad del lado que no la ve). Nunca es una baja.
  * DECISIÓN GOBERNADA — NO existe en este motor (retiros y publicación son F2.4). Ninguna salida de acá es una decisión.

Cambios técnicos vs. operativos: solo los grupos de arquitectura cuentan como cambio técnico; `parameters` y `descriptive` se informan aparte y
`operational` (estado/conexión del agente, replicación) se registra como nota transitoria, NUNCA como cambio.

Alcance: sin `GovernedScope` (tenant + Spaces con environment gobernado) no se concluye nada: SCOPE_UNRESOLVED. Lo que la observación actual no pidió
queda OUT_OF_SCOPE. Nada se infiere por nombre.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .observation import (
    ARCHITECTURE_GROUPS, FIELD_GROUP, SCHEMA as OBSERVATION_SCHEMA, UNKNOWN,
)

SCHEMA = "connectivity-reconciliation/v1"

# Resultados por conexión (vocabulario CERRADO).
MATCHED = "MATCHED"                    # presente en ambos lados
ONLY_IN_CURRENT = "ONLY_IN_CURRENT"    # presente solo en la observación actual (NO afirma «alta»: ver baseline_absence)
ONLY_IN_BASELINE = "ONLY_IN_BASELINE"  # presente solo en la base (NO afirma «baja»: ver current_absence)
NO_EVIDENCE = "NO_EVIDENCE"            # un lado no permite saber (Space inaccesible, ejecución fallida)
OUT_OF_SCOPE = "OUT_OF_SCOPE"          # la observación actual no pidió ese Space
SCOPE_UNRESOLVED = "SCOPE_UNRESOLVED"  # tenant/environment no gobernados para ese Space
OUTCOMES = (MATCHED, ONLY_IN_CURRENT, ONLY_IN_BASELINE, NO_EVIDENCE, OUT_OF_SCOPE, SCOPE_UNRESOLVED)

UNCHANGED, CHANGED, NOT_COMPARABLE = "UNCHANGED", "CHANGED", "NOT_COMPARABLE"
PRESENT = "PRESENT"


@dataclass(frozen=True)
class GovernedScope:
    """Alcance GOBERNADO (lo provee F2.2b; acá lo pasa el llamador): tenant confirmado y Spaces con environment asignado.
    Para una base sin tenant (Catálogo publicado), este alcance es la afirmación gobernada de que esos Spaces del cliente pertenecen al tenant."""
    tenant_id: str
    space_environments: dict = field(default_factory=dict)


class ReconciliationError(ValueError):
    """Entradas fuera del contrato."""


def _check(doc: dict, side: str) -> None:
    if not isinstance(doc, dict) or doc.get("schema") != OBSERVATION_SCHEMA:
        raise ReconciliationError(f"{side}: no es {OBSERVATION_SCHEMA}")


def _index(doc: dict) -> dict:
    return {(c["key"]["space"], c["key"]["connection_name"]): c for c in doc["connections"]}


def _summary(doc: dict) -> dict:
    p, cov = doc["provenance"], doc["coverage"]
    return {"source_kind": p["source_kind"], "snapshot_id": p.get("snapshot_id"), "captured_at": p.get("captured_at"),
            "client": p.get("client"), "tenant_id": p.get("tenant_id"), "tenant_governed": p.get("tenant_governed", False),
            "status": cov["status"], "exhaustiveness": cov["exhaustiveness"], "requested_spaces": cov["requested_spaces"],
            "inaccessible_spaces": cov["inaccessible_spaces"], "space_discovery": doc["scope"].get("space_discovery")}


def _limits(current: dict, baseline: dict) -> list[str]:
    out = []
    for side, doc in (("current", current), ("baseline", baseline)):
        p, cov = doc["provenance"], doc["coverage"]
        if p["source_kind"] == "published_catalog":
            out.append(f"{side}:PUBLISHED_CATALOG_WITHOUT_EVIDENCE_DATE_OR_COVERAGE")
        if cov["exhaustiveness"] == "UNVERIFIABLE":
            out.append(f"{side}:EXHAUSTIVENESS_UNVERIFIABLE")
        if cov["status"] in ("PARTIAL", "FAILED"):
            out.append(f"{side}:EXECUTION_{cov['status']}")
        if doc["scope"].get("space_discovery") != "PERFORMED":
            out.append(f"{side}:SPACE_DISCOVERY_NOT_PERFORMED")
        if not p.get("tenant_governed"):
            out.append(f"{side}:TENANT_NOT_GOVERNED")
    return sorted(set(out))


def _space_presence(doc: dict, space: str) -> str:
    """¿Este lado permite afirmar presencia/ausencia en el Space? UNKNOWN si no lo pidió, si fue inaccesible o si la ejecución falló."""
    cov = doc["coverage"]
    if space not in cov["requested_spaces"] or space in cov["inaccessible_spaces"] or cov["status"] == "FAILED":
        return UNKNOWN
    return PRESENT


def _absence_evidence(doc: dict, space: str) -> dict:
    """Con qué fuerza consta la AUSENCIA en ese lado. Es evidencia, no una baja (el retiro requiere decisión gobernada en F2.4)."""
    cov = doc["coverage"]
    status, exh = cov["status"], cov["exhaustiveness"]
    if doc["provenance"]["source_kind"] == "published_catalog":
        strength = "UNKNOWN"                    # el Catálogo no declara cobertura: no es evidencia de inexistencia en SAP
    elif status == "COMPLETE" and exh == "VERIFIED":
        strength = "VERIFIED"
    elif status == "COMPLETE":
        strength = "UNVERIFIABLE"               # ejecución completa, listado sin prueba de exhaustividad (CLI sin total/cursor)
    else:
        strength = "INCOMPLETE"
    return {"strength": strength, "status": status, "exhaustiveness": exh, "space_accessible": space not in cov["inaccessible_spaces"],
            "observed_at": doc["provenance"].get("captured_at")}


def _compare_groups(a: dict, b: dict) -> dict:
    """Comparación por grupo con los tres estados de campo. UNKNOWN en cualquier lado ⇒ NOT_COMPARABLE (nunca CHANGED)."""
    out = {}
    for group in sorted({g for g in FIELD_GROUP.values()} - {"operational"}):
        names = sorted(n for n, g in FIELD_GROUP.items() if g == group)
        unknown = [n for n in names if UNKNOWN in (a["fields"][n]["state"], b["fields"][n]["state"])]
        changed = [{"field": n, "baseline": b["fields"][n], "current": a["fields"][n]}
                   for n in names if n not in unknown and a["fields"][n] != b["fields"][n]]
        if changed:
            verdict = CHANGED
        elif unknown:
            verdict = NOT_COMPARABLE
        else:
            verdict = UNCHANGED
        out[group] = {"result": verdict, "changed_fields": changed, "unknown_fields": unknown}
    return out


def _rollup(groups: dict, names) -> str:
    results = [groups[g]["result"] for g in names]
    if CHANGED in results:
        return CHANGED
    return NOT_COMPARABLE if NOT_COMPARABLE in results else UNCHANGED


def _operational_notes(a: dict, b: dict) -> list:
    """Diferencias TRANSITORIAS (estado del agente, replicación): informativas, nunca un cambio de arquitectura."""
    notes = []
    for n, g in sorted(FIELD_GROUP.items()):
        if g == "operational" and UNKNOWN not in (a["fields"][n]["state"], b["fields"][n]["state"]) and a["fields"][n] != b["fields"][n]:
            notes.append({"field": n, "baseline": b["fields"][n], "current": a["fields"][n]})
    return notes


def _item(space, name, outcome, environment=None, **extra) -> dict:
    return {"key": {"space": space, "connection_name": name}, "environment": environment, "outcome": outcome, **extra}


def reconcile(current: dict, baseline: dict, scope: GovernedScope | None) -> dict:
    """Reconciliación de `current` (observación más reciente) contra `baseline` (snapshot previo o Catálogo publicado)."""
    _check(current, "current")
    _check(baseline, "baseline")
    cur, base = _index(current), _index(baseline)
    tenant_cur, tenant_base = current["provenance"].get("tenant_id"), baseline["provenance"].get("tenant_id")
    spaces = sorted({s for s, _ in cur} | {s for s, _ in base} | set(current["coverage"]["requested_spaces"]))
    items = []
    for space in spaces:
        keys = sorted({n for s, n in cur if s == space} | {n for s, n in base if s == space})
        env = (scope.space_environments.get(space) if scope else None)
        resolved = (scope is not None and env is not None and tenant_cur == scope.tenant_id
                    and tenant_base in (None, scope.tenant_id))
        if not resolved:
            items += [_item(space, n, SCOPE_UNRESOLVED) for n in keys]
            continue
        if space not in current["coverage"]["requested_spaces"]:
            items += [_item(space, n, OUT_OF_SCOPE, env) for n in keys]
            continue
        cur_known, base_known = _space_presence(current, space), _space_presence(baseline, space)
        for name in keys:
            a, b = cur.get((space, name)), base.get((space, name))
            if a and b:
                groups = _compare_groups(a, b)
                items.append(_item(space, name, MATCHED, env,
                                   technical=_rollup(groups, ARCHITECTURE_GROUPS),
                                   parameters=groups["parameters"]["result"], descriptive=groups["descriptive"]["result"],
                                   groups=groups, operational_notes=_operational_notes(a, b),
                                   detail_state={"baseline": b["detail_state"], "current": a["detail_state"]}))
            elif a:
                if base_known == UNKNOWN and baseline["provenance"]["source_kind"] != "published_catalog":
                    items.append(_item(space, name, NO_EVIDENCE, env, missing_side="baseline"))
                else:
                    items.append(_item(space, name, ONLY_IN_CURRENT, env, baseline_absence=_absence_evidence(baseline, space)))
            else:
                if cur_known == UNKNOWN:
                    items.append(_item(space, name, NO_EVIDENCE, env, missing_side="current"))
                else:
                    items.append(_item(space, name, ONLY_IN_BASELINE, env, current_absence=_absence_evidence(current, space)))
    summary = {o: sum(1 for i in items if i["outcome"] == o) for o in OUTCOMES}
    summary["technical_changes"] = sum(1 for i in items if i.get("technical") == CHANGED)
    return {"schema": SCHEMA, "current": _summary(current), "baseline": _summary(baseline),
            "scope": None if scope is None else {"tenant_id": scope.tenant_id, "governed_spaces": sorted(scope.space_environments)},
            "limits": _limits(current, baseline), "summary": summary, "items": items}


def absence_history(key: tuple, observations: list) -> dict:
    """Ausencias CONSECUTIVAS más recientes de una conexión `(space, nombre)` en observaciones de extracción COMPLETE con el Space accesible.
    Solo orienta la PRIORIDAD DE REVISIÓN: no es evidencia de eliminación y no habilita retiros (F2.4, decisión gobernada)."""
    space, name = key
    ordered = sorted((o for o in observations if o["provenance"]["source_kind"] != "published_catalog"),
                     key=lambda o: o["provenance"].get("captured_at") or "")
    streak = 0
    for obs in reversed(ordered):
        cov = obs["coverage"]
        if cov["status"] != "COMPLETE" or space not in cov["requested_spaces"] or space in cov["inaccessible_spaces"]:
            break                                       # sin evidencia comparable: la racha se corta (no se extrapola)
        if (space, name) in _index(obs):
            break
        streak += 1
    priority = "NONE" if streak == 0 else ("NORMAL" if streak == 1 else "HIGH")
    return {"key": {"space": space, "connection_name": name}, "consecutive_absences": streak, "review_priority": priority,
            "is_deletion_evidence": False}
