"""
commercial_service.py — Semántica Comercial sobre Odoo (Commercial Hub —
Batch 1, read-only). Clasificación: SERVICE/DOMAIN. Traduce los datos
crudos de `crm.stage`/`crm.tag`/`crm.lead` (vía `webapp.services.
odoo_client.OdooClient`, INTEGRATION — este módulo no conoce JSON-RPC) a
los contratos Hub de Summary/Pipeline. NO conoce sesión/HTTP/templates —
eso vive en `webapp.routers.commercial`.

MIGRAR COMPORTAMIENTO, NO ESTRUCTURA (ver HANDOFF — Commercial Hub Odoo
Batch 1): la semántica de abajo es **legacy-compatible semantic
resolution**, portada 1:1 desde `crm-comercial`/`backend/routers/crm.py`
(`/summary`, `/pipeline`) — NO es la convención ideal definitiva. Primero
paridad, después mejora (fuera de este batch).

Reglas portadas verbatim:
- Etapas: "Prospectos" = stage cuyo nombre empieza con "0"; "Activas" =
  stage 1..8 (primer carácter del nombre); "Suspendidas" = nombre contiene
  "suspendido" (case-insensitive, sin relación con el prefijo numérico).
- NN = primer `crm.tag` cuyo nombre contiene "new" (case-insensitive); BI =
  primer tag cuyo nombre contiene TANTO "base" COMO "instal". IDs resueltos
  en cada corrida — NUNCA hardcodeados.
- Horas = entero de `lead_properties` cuyo `string` (label) es "Horas".
- Ganadas: `stage_id.is_won=True` + `date_closed` dentro del año calendario
  actual (límites SOLO fecha, sin hora — asimetría real del legacy con
  Perdidas, preservada a propósito).
- Perdidas: `active=False` + `probability=0` + `date_closed` dentro del año
  actual (límites CON hora, "00:00:00"/"23:59:59" — ver arriba) + exclusión
  de motivos administrativos (`_EXCLUDED_LOST_MOTIVOS`).
- Conversión = ganadas / (ganadas + perdidas); con suspendidas en el
  denominador = ganadas / (ganadas + perdidas + suspendidas).

DESVÍO DELIBERADO respecto de la estructura legacy (NUNCA de los NÚMEROS):
el legacy repite la consulta Odoo completa por cada `tag_id` (Todos/NN/BI
son 3 requests separadas). Acá se hace UNA sola consulta sin filtro de tag
por fuente (Prospectos/Activas/Ganadas/Perdidas/Suspendidas) y las tres
vistas (Todos/NN/BI) se calculan en memoria sobre ESE mismo resultado —
matemáticamente equivalente (mismo universo, mismo predicado NN/BI aplicado
después en vez de antes) y NUNCA cambia un número, solo evita 3x el
tráfico Odoo. El cache guarda las tres vistas juntas.

Cache: TTL ~300s (`CACHE_TTL_SECONDS`), in-memory, aislado a este módulo —
mismo patrón que `core.sharepoint_cases._ttl_cache` (thread-safe, key→
(timestamp, valor), `.clear()` para tests) pero sin importar ese símbolo
privado de otro dominio: cada repositorio del Hub es dueño de su propio
cache (ver ese módulo, comentario de `core.microsoft365`). `clear_cache()`
es la única superficie de invalidación — sin endpoint Refresh todavía
(ver HANDOFF §9, sin necesidad real demostrada).
"""
from __future__ import annotations

import threading
import time
from datetime import date
from typing import Callable

from webapp.services.odoo_client import OdooClient, OdooTransportError

CACHE_TTL_SECONDS = 300

_EXCLUDED_LOST_MOTIVOS = {"cierre administrativo"}
_TIPOS = ("todos", "nn", "bi")


class CommercialConfigurationError(Exception):
    """ODOO_URL/DB/USERNAME/API_KEY incompleto — Comercial no puede cargar
    datos. Distinto de `CommercialDataError`: esto es falta de config, no
    una falla de Odoo ya alcanzable."""


class CommercialDataError(Exception):
    """Falla real de Odoo (transporte/auth/RPC) — nunca de configuración.
    `stage` es `"authenticate"` o `"execute"` (ver `OdooTransportError`)."""

    def __init__(self, message: str, *, stage: str):
        super().__init__(message)
        self.stage = stage


# ── cache in-memory, TTL ~300s ──────────────────────────────────────────────
_cache: dict[str, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def _cached(key: str, fetch_fn: Callable[[], object]):
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit is not None and (now - hit[0]) < CACHE_TTL_SECONDS:
            return hit[1]
    value = fetch_fn()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def clear_cache() -> None:
    """Invalida el cache — usado por tests (evita fugas entre casos)."""
    with _cache_lock:
        _cache.clear()


# ── helpers puros (legacy-compatible semantic resolution) ──────────────────

def _stage_first_char(stage: dict) -> str:
    return (stage.get("name") or "").strip()[:1]


def _prop_match(prop, label: str) -> bool:
    return isinstance(prop, dict) and (prop.get("string") or "").strip() == label


def get_property_num(properties, label: str) -> int:
    """Entero de `crm.lead.lead_properties` por su label visible (p. ej.
    "Horas"). `properties` puede ser `False`/`None` (Odoo) o una lista de
    dicts — legacy-compatible semantic resolution."""
    if not properties:
        return 0
    for prop in properties:
        if _prop_match(prop, label) and prop.get("value") not in (None, False, ""):
            try:
                return int(prop["value"])
            except (ValueError, TypeError):
                return 0
    return 0


def _get_prop_label(properties, label: str) -> str:
    prop = next((p for p in (properties or []) if _prop_match(p, label)), None)
    if not prop:
        return "Sin definir"
    val = prop.get("value")
    if not val:
        return "Sin definir"
    sel_label = next((opt[1] for opt in (prop.get("selection") or []) if opt[0] == val), None)
    return sel_label or "Sin definir"


def _is_excluded_motivo(motivo: str) -> bool:
    return (motivo or "").lower().strip() in _EXCLUDED_LOST_MOTIVOS


class CommercialService:
    def __init__(self, client: OdooClient | None = None):
        self._client = client or OdooClient()

    # ── stage/tag resolution ────────────────────────────────────────────
    def _stage_partition(self) -> tuple[list[int], list[int], list[int]]:
        stages = self._client.search_read("crm.stage", [], fields=["id", "name"])
        valid_ids = [s["id"] for s in stages if _stage_first_char(s) in "12345678"]
        prospect_ids = [s["id"] for s in stages if _stage_first_char(s) == "0"]
        suspended_ids = [s["id"] for s in stages if "suspendido" in (s.get("name") or "").lower()]
        return valid_ids, prospect_ids, suspended_ids

    def _nn_bi_tags(self):
        tags = self._client.search_read("crm.tag", [], fields=["id", "name"])
        nn_tag = next((t for t in tags if "new" in (t.get("name") or "").lower()), None)
        bi_tag = next(
            (t for t in tags if "base" in (t.get("name") or "").lower() and "instal" in (t.get("name") or "").lower()),
            None,
        )
        return nn_tag, bi_tag

    @staticmethod
    def _in_tag(opp: dict, tag_id) -> bool:
        return bool(tag_id) and tag_id in (opp.get("tag_ids") or [])

    # ── agregación ───────────────────────────────────────────────────────
    def _aggregate_full(self, opps: list) -> dict:
        hours = sum(get_property_num(o.get("lead_properties"), "Horas") for o in opps)
        revenue = round(sum(o.get("expected_revenue", 0) or 0 for o in opps), 2)
        w_revenue = round(
            sum((o.get("probability", 0) or 0) / 100 * (o.get("expected_revenue", 0) or 0) for o in opps), 2
        )
        w_hours = round(
            sum((o.get("probability", 0) or 0) / 100 * get_property_num(o.get("lead_properties"), "Horas") for o in opps), 1
        )
        return {"count": len(opps), "hours": hours, "revenue": revenue, "weighted_revenue": w_revenue, "weighted_hours": w_hours}

    def _aggregate_count_hours(self, opps: list) -> dict:
        return {"count": len(opps), "hours": sum(get_property_num(o.get("lead_properties"), "Horas") for o in opps)}

    # ── perdidas: resolución de motivo + exclusión administrativa ───────
    def _resolve_lost_motivos(self, lost_opps: list) -> dict:
        """{opp_id: motivo_label} — intenta `lost_reason_id` (Odoo ≤16),
        luego `lost_reason_ids` (Odoo 17+), cae a `lead_properties` como
        fallback. Cualquier falla de transporte en un intento se ignora
        (best-effort — la ausencia de un campo en una versión de Odoo no
        debe romper Perdidas)."""
        if not lost_opps:
            return {}
        opp_ids = [o["id"] for o in lost_opps]
        lr_domain = [["id", "in", opp_ids], ["active", "=", False]]
        result: dict = {}

        try:
            recs = self._client.search_read("crm.lead", domain=lr_domain, fields=["id", "lost_reason_id"])
            for r in recs:
                val = r.get("lost_reason_id")
                if val and isinstance(val, (list, tuple)) and len(val) > 1 and val[1]:
                    result[r["id"]] = val[1]
        except OdooTransportError:
            pass

        if not any(result.values()):
            try:
                recs2 = self._client.search_read("crm.lead", domain=lr_domain, fields=["id", "lost_reason_ids"])
                id_to_rids = {r["id"]: (r.get("lost_reason_ids") or []) for r in recs2}
                all_rids = list({rid for rids in id_to_rids.values() for rid in rids})
                if all_rids:
                    rname_recs = self._client.search_read(
                        "crm.lost.reason", domain=[["id", "in", all_rids]], fields=["id", "name"]
                    )
                    rid_to_name = {r["id"]: r["name"] for r in rname_recs}
                    for oid, rids in id_to_rids.items():
                        if rids:
                            label = " / ".join(rid_to_name[rid] for rid in rids if rid in rid_to_name)
                            if label:
                                result[oid] = label
            except OdooTransportError:
                pass

        for o in lost_opps:
            if o["id"] not in result:
                props = o.get("lead_properties") or []
                if props:
                    result[o["id"]] = _get_prop_label(props, "Motivo de Perdida Danwor")
        return result

    def _filter_excluded_lost(self, opps: list, motivo_map: dict) -> list:
        return [o for o in opps if not _is_excluded_motivo(motivo_map.get(o["id"], ""))]

    # ── Summary ──────────────────────────────────────────────────────────
    def _build_summary(self) -> dict:
        today = date.today()
        year_start, year_end = f"{today.year}-01-01", f"{today.year}-12-31"
        valid_ids, prospect_ids, suspended_ids = self._stage_partition()
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        nn_label = nn_tag["name"] if nn_tag else "New Names"
        bi_label = bi_tag["name"] if bi_tag else "Base Inst."

        prospects = (
            self._client.search_read(
                "crm.lead",
                domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", prospect_ids]],
                fields=["tag_ids", "lead_properties", "probability", "expected_revenue"],
            )
            if prospect_ids else []
        )
        active = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", valid_ids]],
            fields=["expected_revenue", "probability", "lead_properties", "tag_ids"],
        )
        pipeline_opps = active + prospects

        won_raw = self._client.search_read(
            "crm.lead",
            domain=[
                ["type", "=", "opportunity"], ["stage_id.is_won", "=", True],
                ["date_closed", ">=", year_start], ["date_closed", "<=", year_end],
            ],
            fields=["lead_properties", "tag_ids"],
        )
        lost_raw = self._client.search_read(
            "crm.lead",
            domain=[
                ["type", "=", "opportunity"], ["active", "=", False], ["probability", "=", 0],
                ["date_closed", ">=", f"{year_start} 00:00:00"], ["date_closed", "<=", f"{year_end} 23:59:59"],
            ],
            fields=["lead_properties", "tag_ids"],
        )
        lost_motivos = self._resolve_lost_motivos(lost_raw)
        lost = self._filter_excluded_lost(lost_raw, lost_motivos)
        suspended = (
            self._client.search_read(
                "crm.lead",
                domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", suspended_ids]],
                fields=["lead_properties", "tag_ids"],
            )
            if suspended_ids else []
        )

        def _split(opps: list) -> dict:
            return {
                "todos": opps,
                "nn": [o for o in opps if self._in_tag(o, nn_id)],
                "bi": [o for o in opps if self._in_tag(o, bi_id)],
            }

        prospects_by, active_by, pipeline_by = _split(prospects), _split(active), _split(pipeline_opps)
        won_by, lost_by, suspended_by = _split(won_raw), _split(lost), _split(suspended)

        views = {}
        for tipo in _TIPOS:
            won_count, lost_count, susp_count = len(won_by[tipo]), len(lost_by[tipo]), len(suspended_by[tipo])
            denom, denom_susp = won_count + lost_count, won_count + lost_count + susp_count
            pipe_agg = self._aggregate_full(pipeline_by[tipo])
            views[tipo] = {
                "labels": {"nn": nn_label, "bi": bi_label},
                "prospectos": self._aggregate_count_hours(prospects_by[tipo]),
                "pipeline": {"hours": pipe_agg["hours"], "weighted_hours": pipe_agg["weighted_hours"]},
                "activas": self._aggregate_full(active_by[tipo]),
                "ganadas": self._aggregate_count_hours(won_by[tipo]),
                "perdidas": self._aggregate_count_hours(lost_by[tipo]),
                "suspendidas": self._aggregate_count_hours(suspended_by[tipo]),
                "conversion": round(won_count / denom * 100, 1) if denom > 0 else 0,
                "conversion_with_suspended": round(won_count / denom_susp * 100, 1) if denom_susp > 0 else 0,
            }
        return views

    def get_summary(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:summary", self._build_summary)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Pipeline por etapa ───────────────────────────────────────────────
    def _build_pipeline(self) -> dict:
        valid_ids, _prospect_ids, _suspended_ids = self._stage_partition()
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        opps = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", valid_ids]],
            fields=["stage_id", "expected_revenue", "lead_properties", "probability", "tag_ids"],
        )

        def _stage_sort_key(name: str) -> int:
            try:
                return int(name.strip()[0])
            except (ValueError, IndexError):
                return 99

        def _group(subset: list) -> dict:
            stages: dict = {}
            for opp in subset:
                name = opp["stage_id"][1] if opp.get("stage_id") else "Sin etapa"
                bucket = stages.setdefault(
                    name, {"count": 0, "revenue": 0.0, "hours": 0, "weighted_count": 0.0, "weighted_hours": 0.0}
                )
                prob = (opp.get("probability", 0) or 0) / 100
                hrs = get_property_num(opp.get("lead_properties"), "Horas")
                bucket["count"] += 1
                bucket["revenue"] += opp.get("expected_revenue", 0) or 0
                bucket["hours"] += hrs
                bucket["weighted_count"] += prob
                bucket["weighted_hours"] += prob * hrs

            stages_list = sorted(
                [
                    {
                        "stage": name, "count": v["count"], "revenue": round(v["revenue"], 2),
                        "hours": v["hours"], "weighted_count": round(v["weighted_count"], 1),
                        "weighted_hours": round(v["weighted_hours"], 1),
                    }
                    for name, v in stages.items()
                ],
                key=lambda x: _stage_sort_key(x["stage"]),
            )
            return {
                "stages": stages_list,
                "opportunities": len(subset),
                "revenue": round(sum(o.get("expected_revenue", 0) or 0 for o in subset), 2),
            }

        return {
            "todos": _group(opps),
            "nn": _group([o for o in opps if self._in_tag(o, nn_id)]),
            "bi": _group([o for o in opps if self._in_tag(o, bi_id)]),
        }

    def get_pipeline(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:pipeline", self._build_pipeline)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]
