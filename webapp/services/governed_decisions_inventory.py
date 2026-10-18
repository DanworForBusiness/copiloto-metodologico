"""
governed_decisions_inventory.py — Inventario READ-ONLY de contratos gobernados POR CLIENTE (v4.B / B4, soporte del listado).

SINGLE-PASS (lo priorizó el owner; ver `B4_PERFORMANCE_ASSESSMENT.md`): UNA pasada de evidencia (cross-scan estricto, reusando
`governed_decisions_ops._iter_client_envelopes_strict` de B3) + UN listado de eventos del cliente
(`governed_decisions_store.list_client_keys`) + agrupación por `technical_id` en memoria. NO repite el cross-scan por contrato
ni lista por subject. SOLO LECTURA: no escribe eventos, no toca Assessments / Judgment / canonical-evidence/v4 (D8). Fail-closed:
un Assessment ilegible/alterado propaga `ContractEvidenceUnverifiable` (igual que el resolve puntual); una evidencia contradictoria
entre consumidores NO rompe el listado (se marca `evidence_ambiguous` y el estado de evidencia cae a UNKNOWN para esa fila).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from webapp.services import assessment_service as _assess
from webapp.services import governed_decisions as gd
from webapp.services import governed_decisions_ops as ops
from webapp.services import governed_decisions_store as store


def list_clients() -> list[str]:
    """Clientes con Assessments gobernados (universo de contratos posibles), ordenados. SOLO LECTURA — poblar el selector del
    listado. Usa el recorrido estándar `iter_governed_envelopes` (el detalle/listado por cliente sí usa el lector ESTRICTO)."""
    seen: dict[str, str] = {}
    for env, _path in _assess.iter_governed_envelopes():
        c = str(env.get("client") or "").strip()
        if c:
            seen.setdefault(c.lower(), c)                          # preserva el primer casing visto; dedupe case-insensible
    return [seen[k] for k in sorted(seen)]


def _collect_evidence(client: str) -> dict:
    """UNA pasada del cross-scan estricto → {technical_id: {identity, slices:[...], consumers:set}}. Omite hojas no ratificables
    (no REMOTE_IMPORTED, identidad ambigua, contract_class ausente) con el MISMO criterio que `gd.cds_identity`/`extract_*`."""
    acc: dict[str, dict] = {}
    for env in ops._iter_client_envelopes_strict(client):          # propaga ContractEvidenceUnverifiable (fail-closed)
        ce = (env.get("profile_json") or {}).get("canonical_evidence") or {}
        leaves = ((ce.get("source_contract_classes") or {}).get("leaves") or [])
        am = env.get("terminal_am")
        for lf in leaves:
            if not isinstance(lf, dict) or lf.get("leaf_kind") != gd.LEAF_KIND_REMOTE_IMPORTED:
                continue
            try:
                slice_ = gd.extract_cds_contract_evidence(lf)      # valida identidad única + contract_class; excluye contexto
            except gd.ValidationError:
                continue                                           # hoja no ratificable → no es un contrato (se omite, no rompe)
            tid = gd.cds_technical_id(slice_["identity"])
            rec = acc.setdefault(tid, {"identity": slice_["identity"], "slices": [], "consumers": set()})
            rec["slices"].append(slice_)
            if am:
                rec["consumers"].add(am)
    return acc


def _load_all_client_events(client: str) -> dict:
    """SINGLE-PASS: baja TODOS los eventos del cliente (`list_client_keys`) y los agrupa por `technical_id`. Fail-closed por
    evento (ausente/no-JSON/esquema inválido). Un evento cuyo `subject.client` no coincide (colisión de slug = otro cliente) se
    OMITE (mismo criterio que el lector estricto de Assessments)."""
    groups: dict[str, list[dict]] = {}
    if not store.remote_store.read_enabled():
        return groups                                              # sin registro durable de decisiones (p. ej. dev local): sin eventos (seguro)
    for key in store.list_client_keys(client):
        raw = store.remote_store.get_governed_decision_object(key)
        if raw is None:
            raise store.HistoryIntegrityError(f"clave listada sin objeto: {key}")
        try:
            event = json.loads(raw.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise store.HistoryIntegrityError(f"objeto no-JSON: {key}") from exc
        gd.validate_event(event)
        subj = event.get("subject") or {}
        if subj.get("client") != client:
            continue                                               # colisión de slug: evento de otro cliente, no de éste
        groups.setdefault(subj.get("technical_id"), []).append(event)
    return groups


def list_client_contracts(client: str, now: datetime) -> list[dict]:
    """Resumen por contrato de un cliente, en una sola pasada. Incluye contratos CON evidencia (hojas REMOTE_IMPORTED) y/o CON
    decisiones registradas (eventos). Cada fila: identidad, estado en 3 dimensiones (decisión/evidencia/revisión), digest de
    evidencia, consumidores (trazabilidad) y conteo de eventos. SOLO LECTURA."""
    evidence = _collect_evidence(client)                           # {tid: {identity, slices, consumers}}
    digests: dict[str, str | None] = {}
    ambiguous: set[str] = set()
    for tid, rec in evidence.items():
        canon = {gd.canonical_json(s) for s in rec["slices"]}
        if len(canon) == 1:
            digests[tid] = hashlib.sha256(gd.canonical_json(rec["slices"][0]).encode("utf-8")).hexdigest()
        else:
            digests[tid] = None                                    # evidencia contradictoria → no verificable (UNKNOWN), no rompe
            ambiguous.add(tid)
    events = _load_all_client_events(client)                       # {tid: [events]}
    rows: list[dict] = []
    for tid in (set(evidence) | set(events)):
        evs = store._dedupe_and_check(events.get(tid, []))         # fail-closed ante contradicción del historial
        st = gd.derive_status(evs, digests.get(tid), now)
        identity = evidence[tid]["identity"] if tid in evidence else json.loads(tid)
        rows.append({
            "technical_id": tid,
            "identity": identity,
            "decision": st["decision"], "evidence": st["evidence"], "review": st["review"], "principal": st["principal"],
            "effective_review_by": st.get("effective_review_by"),
            "evidence_digest": digests.get(tid),
            "evidence_ambiguous": tid in ambiguous,
            "consumers": sorted(evidence[tid]["consumers"]) if tid in evidence else [],
            "events_count": len(evs),
        })
    rows.sort(key=lambda r: ((r["identity"].get("remote_connection") or ""), r["identity"].get("remote_entity") or ""))
    return rows
