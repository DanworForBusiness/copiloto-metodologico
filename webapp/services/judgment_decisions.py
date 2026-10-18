"""
judgment_decisions.py — instantánea VERIFICABLE de decisiones de gobierno + derivación del Juicio `judgment-rules/v3` + invalidación (v4.B / B5.3).

El motor de Juicio es PURO y jamás lee el registro (D8): este módulo vive FUERA del motor. Un OPERADOR EXPLÍCITO (`--backfill-judgment`) toma UNA instantánea
del registro `governed-decisions/` (solo `list`/`get`, fail-closed), la INYECTA a `evaluate_judgment(ruleset=v3, ratified_contracts=...)` mediante el enlace
puro (`governed_ratification_link`) y persiste en el Juicio la ENTRADA consumida (`decision_inputs`: hoja, identidad, technical_id, event_id, evidence_ref).

Nunca se recalcula al visualizar un Assessment ni se lee el registro "de fondo": sin instantánea (`EMPTY`), v3 produce los mismos niveles que v2.
Invalidación: si una decisión consumida por un Juicio persistido ya no se inyecta (revocada / evidencia STALE / inexistente), el Juicio queda MARCADO
`PENDING_REEVALUATION` (clave hermana `judgment_review`) SIN reescribir el Juicio histórico; solo una reevaluación explícita lo reemplaza.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from tools.catalog.methodological_judgment import CURRENT_RULESET_VERSION, evaluate_judgment
from webapp.services import governed_decisions as gd
from webapp.services import governed_ratification_link as link

SNAPSHOT_VERSION = "governed-decision-snapshot/v1"
MODE_REGISTRY, MODE_NONE = "registry", "none"
REVIEW_PENDING = "PENDING_REEVALUATION"
REVIEW_CAUSE = "GOVERNED_DECISION_NO_LONGER_VALID"


class DecisionSnapshotError(RuntimeError):
    """El registro de decisiones no se pudo leer/validar: el operador NO continúa (un registro ilegible NUNCA equivale a «sin decisiones»)."""


@dataclass(frozen=True)
class Snapshot:
    """Instantánea inmutable: eventos YA validados, por cliente. `digest` = sha256 del contenido canónico completo (verificable y reproducible)."""
    mode: str
    taken_at: datetime
    events: Mapping[str, tuple] = field(default_factory=dict)           # cliente -> eventos (cualquier contrato del cliente)

    @property
    def digest(self) -> str:
        flat = sorted((c, e["event_id"], gd.canonical_json(e)) for c, evs in self.events.items() for e in evs)
        return "sha256:" + hashlib.sha256(gd.canonical_json({"version": SNAPSHOT_VERSION, "events": flat}).encode("utf-8")).hexdigest()

    def load_events(self, client: str, technical_id: str) -> list[dict]:
        return [e for e in self.events.get(client, ()) if (e.get("subject") or {}).get("technical_id") == technical_id]

    def summary(self) -> dict:
        return {"version": SNAPSHOT_VERSION, "mode": self.mode, "taken_at": self.taken_at.isoformat(), "digest": self.digest,
                "event_count": sum(len(v) for v in self.events.values()), "clients": {c: len(v) for c, v in sorted(self.events.items())}}


def empty_snapshot(now: datetime | None = None) -> Snapshot:
    """«Sin decisiones» EXPLÍCITO (`--decisions none`): v3 sin ratificaciones inyectadas."""
    return Snapshot(MODE_NONE, now or datetime.now(timezone.utc), {})


def take_snapshot(clients: list[str], *, now: datetime | None = None) -> Snapshot:
    """Lee el registro (SOLO list/get; nunca escribe) de cada cliente y valida cada evento + historial por contrato (fail-closed). Exige lectura remota autorizada."""
    from webapp.services import governed_decisions_inventory as inv          # import perezoso: el inventario depende del servicio de Assessment
    from webapp.services import governed_decisions_store as store
    if not store.remote_store.read_enabled():
        raise DecisionSnapshotError("lectura remota del registro no autorizada (DANWOR_PERSISTENCE=read-only|remote requerido); use --decisions none para un Juicio v3 sin decisiones")
    out: dict[str, tuple] = {}
    try:
        for client in sorted({c for c in clients if c}):
            groups = inv._load_all_client_events(client)                      # valida cada evento; omite eventos de otro cliente por colisión de slug
            events: list[dict] = []
            for _tid, evs in sorted(groups.items()):
                events.extend(store._dedupe_and_check(evs))
            out[client] = tuple(sorted(events, key=lambda e: str(e["event_id"])))
    except DecisionSnapshotError:
        raise
    except Exception as exc:  # noqa: BLE001 — cualquier fallo de lectura/validación detiene el operador
        raise DecisionSnapshotError(f"{type(exc).__name__}: {exc}") from exc
    return Snapshot(MODE_REGISTRY, now or datetime.now(timezone.utc), out)


def derive(profile: Mapping[str, Any], client: str, snapshot: Snapshot) -> tuple[dict, dict]:
    """(Juicio del ruleset VIGENTE —v3/v4 incluyen la politica de ratificacion—, reporte del enlace). Determinista dado (perfil, instantánea): el motor solo recibe las ratificaciones ENLAZADAS (identidad + vigencia + digest MATCH)."""
    rep = link.link_ratifications(profile, client, now=snapshot.taken_at, load_events=snapshot.load_events)
    linked = rep["ratified_contracts"]
    j = evaluate_judgment(profile, ruleset=CURRENT_RULESET_VERSION, ratified_contracts=linked)
    # La ENTRADA persistida = solo las ratificaciones que RESOLVIERON algo (hoja decision-critica). Una ratificacion valida de un contrato que este modelo usa sin que sea decision-critica (NO_EFFECT)
    # no cambia el Juicio: no entra en ``decision_inputs`` (evita churn de firmas y falsos «pendiente de reevaluacion» si luego se revoca). Se re-evalua una sola vez sin ellas.
    applied_ids = {d.get("event_id") for d in j.get("dispositions_applied", ()) if d.get("decision") == "GOVERNED_CONTRACT_RATIFIED" and d.get("status") == "APPLIED"}
    effective = [i for i in linked if i["event_id"] in applied_ids]
    if len(effective) != len(linked):
        j = evaluate_judgment(profile, ruleset=CURRENT_RULESET_VERSION, ratified_contracts=effective)
    return j, rep


def consumed(judgment: Mapping[str, Any] | None) -> list[dict]:
    """Decisiones INYECTADAS que declara un Juicio v3 persistido (vacío para v2 o sin decisiones)."""
    return [dict(i) for i in (((judgment or {}).get("decision_inputs") or {}).get("ratified_contracts") or ())]


def lost_decisions(persisted: Mapping[str, Any] | None, fresh: Mapping[str, Any] | None, report: Mapping[str, Any] | None = None) -> list[dict]:
    """Decisiones que el Juicio persistido consumió y que la reevaluación con la instantánea ACTUAL ya no inyecta (revocadas, evidencia STALE, inexistentes). Vacío = sigue vigente."""
    still = {i["event_id"] for i in consumed(fresh)}
    why = {r["leaf"]: r.get("reason") for r in (report or {}).get("report", ())}
    return [{"event_id": i["event_id"], "leaf": i["leaf"], "technical_id": i.get("technical_id"), "reason": why.get(i["leaf"]) or "NOT_REINJECTED"}
            for i in consumed(persisted) if i["event_id"] not in still]


def review_marker(persisted: Mapping[str, Any], lost: list[dict], snapshot: Snapshot, *, now: datetime | None = None) -> dict:
    """Marca de invalidación (clave hermana `judgment_review`). NO modifica el Juicio: lo declara pendiente de reevaluación y deja la causa auditable."""
    return {"state": REVIEW_PENDING, "cause": REVIEW_CAUSE, "judgment_output_signature": persisted.get("output_signature"), "judgment_ruleset_version": persisted.get("ruleset_version"),
            "reasons": sorted(lost, key=lambda r: (str(r["leaf"]), str(r["event_id"]))), "snapshot": {"mode": snapshot.mode, "digest": snapshot.digest},
            "flagged_at": (now or datetime.now(timezone.utc)).isoformat()}
