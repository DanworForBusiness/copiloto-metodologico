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

import re
import threading
import time
from datetime import date, timedelta
from typing import Callable

from webapp.services.odoo_client import OdooClient, OdooTransportError

CACHE_TTL_SECONDS = 300
# Umbral de "estancada" del CRM legacy: más de 30 días sin cambiar de etapa.
STAGNANT_DAYS = 30

def _EMPTY_ANALYTICS() -> dict:
    """Sin etapas 1–8 configuradas no hay embudo del que hablar."""
    return {
        "pipeline_age": {"avg_days": 0, "count": 0},
        "stage_time": {"avg_days": None, "stages_with_data": 0},
        "stage_conversion": {"min_rate": None, "steps": 0},
        "close_time": {"avg_days": 0, "count": 0},
        "stagnant": {"count": 0, "threshold_days": STAGNANT_DAYS},
        "creadas": {"this_week": 0, "last_week": 0},
        "lead_conversion": {"companies": 0, "any_conv": 0, "rate_any_conv": 0,
                            "adv_opp": 0, "rate_adv_opp": 0},
    }


_EXCLUDED_LOST_MOTIVOS = {"cierre administrativo"}
_TIPOS = ("todos", "nn", "bi")

# Labels EXACTOS de las `lead_properties` que usa el legacy para estos dos
# datos (ver Active Opportunities Phase 2, HANDOFF) — portados 1:1 desde
# `crm-comercial`/`backend/routers/crm.py` (`PROP_ULTIMA_RESPUESTA`/
# `PROP_CIERRE_AJUSTADO`). Resueltos por STRING, nunca por posición: son
# properties configurables en Odoo, no campos del modelo.
_PROP_ULTIMA_RESPUESTA = "Fecha de Ultima Respuesta"
_PROP_CIERRE_AJUSTADO = "Cierre Ajustado"


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


def _get_property_date(properties, label: str) -> str | None:
    """Valor de una `lead_property` de TIPO fecha (p. ej. "Fecha de Ultima
    Respuesta"/"Cierre Ajustado") — legacy-compatible semantic resolution,
    portado 1:1 de `crm-comercial`. A diferencia de `get_property_num`/
    `_get_prop_label`, exige `prop.get("type") == "date"`: son properties
    DISTINTAS de "Horas"/"Licencias" y comparten label con propiedades de
    otro tipo sería una coincidencia de nombre, no de dato."""
    if not properties:
        return None
    for prop in properties:
        if _prop_match(prop, label) and prop.get("type") == "date":
            val = prop.get("value")
            return val if val else None
    return None


_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html_note(raw) -> str:
    """`mail.activity.note` llega como HTML de Odoo. Se normaliza a texto
    plano (tags -> espacio, whitespace colapsado) para payload y para no
    mover HTML de la fuente hacia el browser. Esto es NORMALIZACION, NO
    sustituye el escaping de salida: el cliente escapa TODO dato de origen
    Odoo igual (ver commercial.js)."""
    if not raw or not isinstance(raw, str):
        return ""
    return _WS_RE.sub(" ", _HTML_TAG_RE.sub(" ", raw)).strip()


def iso_deadline(raw) -> str | None:
    """`date_deadline` de Odoo -> "YYYY-MM-DD" validada, o None.

    Se devuelve la FECHA CALENDARIO cruda a proposito: el `days_until` lo
    calcula el cliente (ver commercial.js). El legacy lo computaba en el
    server y lo cacheaba, con lo cual al cruzar la medianoche servia un
    valor corrido y clasificaba mal el bucket. Fecha malformada/ausente ->
    None (fail-soft, MISMO criterio que el legacy: sin fecha, la actividad
    sigue siendo pendiente)."""
    if not raw or raw is True:
        return None
    text = str(raw)[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except (ValueError, TypeError):
        return None


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

    # SEGMENTACIÓN NN / BI (ver HANDOFF — Landing Homologation): `tag_ids` es
    # many2many y `_in_tag` es una prueba de PERTENENCIA, así que New Name y
    # Base Instalada NO son mutuamente excluyentes NI exhaustivos: una
    # oportunidad puede tener los dos tags, o ninguno. Por eso se cuentan los
    # CUATRO conjuntos de forma explícita en vez de derivar un resto
    # ("otros = total - nn - bi"), que sólo sería correcto si el solapamiento
    # fuera cero — cosa que el modelo de datos no garantiza. Con esto el
    # front puede mostrar la lectura conocida (New Name / Base Instalada) y
    # explicar honestamente por qué no reconcilian con el total.
    # Cero consultas nuevas: se calcula sobre las MISMAS oportunidades que ya
    # se trajeron para el resto del summary.
    def _segments(self, opps: list, nn_id, bi_id) -> dict:
        nn, bi, both, neither = [], [], [], []
        for opp in opps:
            in_nn, in_bi = self._in_tag(opp, nn_id), self._in_tag(opp, bi_id)
            if in_nn:
                nn.append(opp)
            if in_bi:
                bi.append(opp)
            if in_nn and in_bi:
                both.append(opp)
            if not in_nn and not in_bi:
                neither.append(opp)
        return {
            "nn": self._aggregate_count_hours(nn),
            "bi": self._aggregate_count_hours(bi),
            "both": self._aggregate_count_hours(both),
            "neither": self._aggregate_count_hours(neither),
        }

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
            # `segments` SÓLO en la vista sin filtrar: dentro de la vista "New
            # Name" un desglose New Name/Base Instalada sería redundante (o
            # peor, confuso: "bi" ahí serían las que tienen AMBOS tags).
            if tipo == "todos":
                views[tipo]["segments"] = {
                    "prospectos": self._segments(prospects_by[tipo], nn_id, bi_id),
                    "pipeline": self._segments(pipeline_by[tipo], nn_id, bi_id),
                    "activas": self._segments(active_by[tipo], nn_id, bi_id),
                    "ganadas": self._segments(won_by[tipo], nn_id, bi_id),
                    "perdidas": self._segments(lost_by[tipo], nn_id, bi_id),
                    "suspendidas": self._segments(suspended_by[tipo], nn_id, bi_id),
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

    # ── Detalle de Oportunidades Activas (Active Opportunities Phase 2) ──
    # Universo: MISMO que "Oportunidades activas" (stages 1–8, `valid_ids`)
    # — NO Pipeline Total (0–8): ese es el universo del Forecast, ver
    # `_build_forecast` más abajo. `weighted_hours` se centraliza ACÁ
    # (probability/100 * horas) — el legacy no lo traía en `/relevamiento`,
    # pero la columna "Hs Pond." del detalle Hub lo necesita y no tiene
    # sentido calcularlo en el cliente cuando ya se calcula igual en
    # `_build_pipeline`/`_aggregate_full` para el mismo campo. `tag_ids`
    # SOLO se usa para particionar todos/nn/bi en memoria — nunca viaja en
    # la fila (ver HANDOFF: "No Etiquetas in output UI contract"). `revenue`
    # (C5e, ver HANDOFF - filtro Etapa/Vendedor del detalle): campo ADITIVO
    # sobre el MISMO `search_read` de `crm.lead` - `expected_revenue` ya es
    # el campo estandar que el resto de este servicio usa para Revenue
    # (`_build_pipeline`/`_aggregate_full`/`_build_forecast`), asi que
    # agregarlo aca es solo sumarlo a `fields`, sin una segunda llamada a
    # Odoo. Necesario para que el Funnel derivado LOCALMENTE en el frontend
    # (cuando el owner filtra por Etapa/Vendedor) pueda seguir mostrando
    # Revenue en su tooltip con la MISMA semantica que el Funnel sin filtrar.
    def _build_opportunities(self) -> dict:
        valid_ids, _prospect_ids, _suspended_ids = self._stage_partition()
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        opps = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", valid_ids]],
            fields=["name", "partner_id", "user_id", "stage_id", "tag_ids", "source_id",
                     "probability", "priority", "lead_properties", "expected_revenue"],
        )
        opp_ids = [o["id"] for o in opps]
        # TODAS las actividades pendientes de estas oportunidades — MISMO
        # criterio que el legacy (nunca solo "la próxima"): el cliente
        # decide cuál mostrar/ordenar (ver HANDOFF — "sorting uses
        # min(days_until)"), acá solo se agrupan por oportunidad.
        acts_by_opp: dict[int, list] = {}
        if opp_ids:
            activities = self._client.search_read(
                "mail.activity",
                domain=[["res_model", "=", "crm.lead"], ["res_id", "in", opp_ids]],
                fields=["res_id", "date_deadline", "activity_type_id", "summary"],
                order="date_deadline asc",
            )
            for act in activities:
                acts_by_opp.setdefault(act["res_id"], []).append({
                    "type": act["activity_type_id"][1] if act.get("activity_type_id") else "",
                    "summary": act.get("summary") or "",
                    "deadline": iso_deadline(act.get("date_deadline")),
                })

        today = date.today()

        def _row(opp: dict) -> dict:
            props = opp.get("lead_properties") or []
            hours_raw = get_property_num(props, "Horas")
            # `get_property_num` devuelve 0 por ausencia — acá se distingue
            # "sin horas cargadas" (None, tabla muestra "—") de "cargadas en
            # cero" NO es posible con ese helper, así que se preserva el
            # mismo comportamiento que ya usa el resto del servicio (0 real)
            # en vez de inventar una distinción que Odoo no expone.
            weighted_hours = round((opp.get("probability", 0) or 0) / 100 * hours_raw, 1)
            last_response = _get_property_date(props, _PROP_ULTIMA_RESPUESTA)
            days_elapsed = (today - date.fromisoformat(last_response)).days if last_response else None
            return {
                "opportunity": opp.get("name") or "",
                "client": opp["partner_id"][1] if opp.get("partner_id") else "",
                "seller": opp["user_id"][1] if opp.get("user_id") else "",
                "stage": opp["stage_id"][1] if opp.get("stage_id") else "",
                "source": opp["source_id"][1] if opp.get("source_id") else "Sin definir",
                "licencias": _get_prop_label(props, "Licencias"),
                "hours": hours_raw,
                "weighted_hours": weighted_hours,
                "revenue": round(opp.get("expected_revenue", 0) or 0, 2),
                "probability": opp.get("probability"),
                "priority": opp.get("priority") or "0",
                "activities": acts_by_opp.get(opp["id"], []),
                "last_response": last_response,
                "days_elapsed": days_elapsed,
            }

        def _partition(subset: list) -> dict:
            return {"opportunities": [_row(o) for o in subset], "total": len(subset)}

        return {
            "todos": _partition(opps),
            "nn": _partition([o for o in opps if self._in_tag(o, nn_id)]),
            "bi": _partition([o for o in opps if self._in_tag(o, bi_id)]),
        }

    def get_opportunities(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:opportunities", self._build_opportunities)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Prospectos — detalle (Commercial Tile 5, ver HANDOFF) ────────────────
    # Universo: stage_id IN prospect_ids (`_stage_partition()[1]`, nombre de
    # etapa que empieza con "0") — EL MISMO universo que el tile "Prospectos"
    # del summary, nunca el de Activas (1-8). Legacy-compatible: recuperado de
    # `crm-comercial`/`backend/routers/crm.py::get_relevamiento` (llamado por
    # el legacy con `stage_id=<etapa 0>`, la MISMA función que también sirve
    # el detalle de Activas con `stage_id=0` → etapas 1-8 — acá se porta un
    # solo lado de ese endpoint compartido, con el universo de Prospectos).
    #
    # Fila CASI idéntica a `_build_opportunities`/`_row` (MISMAS fórmulas de
    # Hs Pond./Últ. Resp./Días resp./actividades — ya auditadas y en
    # producción para Activas) — la única fila nueva es "tags" (Etiquetas):
    # el legacy la incluye en el detalle de Prospectos y Activas por igual,
    # pero Activas la descartó a propósito en su contrato de salida (ver
    # `_build_opportunities` — "No Etiquetas in output UI contract"); acá SÍ
    # viaja porque el owner la pidió explícitamente para este tile. Nunca
    # "stage"/"source": universo de una sola etapa (no hace falta columna
    # Etapa) y Prospectos no tiene vista de Origen (esa es exclusiva de
    # Activas, C8).
    def _build_prospects(self) -> dict:
        _valid_ids, prospect_ids, _suspended_ids = self._stage_partition()
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        opps = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", prospect_ids]],
            fields=["name", "partner_id", "user_id", "tag_ids",
                     "probability", "priority", "lead_properties", "expected_revenue"],
        )
        opp_ids = [o["id"] for o in opps]
        # TODAS las actividades pendientes, no sólo la próxima — MISMO
        # criterio que `_build_opportunities` (portado 1:1 del legacy).
        acts_by_opp: dict[int, list] = {}
        if opp_ids:
            activities = self._client.search_read(
                "mail.activity",
                domain=[["res_model", "=", "crm.lead"], ["res_id", "in", opp_ids]],
                fields=["res_id", "date_deadline", "activity_type_id", "summary"],
                order="date_deadline asc",
            )
            for act in activities:
                acts_by_opp.setdefault(act["res_id"], []).append({
                    "type": act["activity_type_id"][1] if act.get("activity_type_id") else "",
                    "summary": act.get("summary") or "",
                    "deadline": iso_deadline(act.get("date_deadline")),
                })

        # Nombres de tag — UNA consulta batch sobre los ids realmente
        # presentes en este subset (MISMO criterio que el legacy
        # `get_relevamiento`), nunca una consulta por oportunidad.
        all_tag_ids = {tid for o in opps for tid in (o.get("tag_ids") or [])}
        tag_names: dict[int, str] = {}
        if all_tag_ids:
            tags = self._client.search_read("crm.tag", [["id", "in", list(all_tag_ids)]], fields=["id", "name"])
            tag_names = {t["id"]: t["name"] for t in tags}

        today = date.today()

        def _row(opp: dict) -> dict:
            props = opp.get("lead_properties") or []
            hours_raw = get_property_num(props, "Horas")
            weighted_hours = round((opp.get("probability", 0) or 0) / 100 * hours_raw, 1)
            last_response = _get_property_date(props, _PROP_ULTIMA_RESPUESTA)
            days_elapsed = (today - date.fromisoformat(last_response)).days if last_response else None
            return {
                "opportunity": opp.get("name") or "",
                "client": opp["partner_id"][1] if opp.get("partner_id") else "",
                "seller": opp["user_id"][1] if opp.get("user_id") else "",
                "tags": [tag_names[tid] for tid in (opp.get("tag_ids") or []) if tid in tag_names],
                "licencias": _get_prop_label(props, "Licencias"),
                "hours": hours_raw,
                "weighted_hours": weighted_hours,
                "revenue": round(opp.get("expected_revenue", 0) or 0, 2),
                "probability": opp.get("probability"),
                "priority": opp.get("priority") or "0",
                "activities": acts_by_opp.get(opp["id"], []),
                "last_response": last_response,
                "days_elapsed": days_elapsed,
            }

        def _partition(subset: list) -> dict:
            return {"opportunities": [_row(o) for o in subset], "total": len(subset)}

        return {
            "todos": _partition(opps),
            "nn": _partition([o for o in opps if self._in_tag(o, nn_id)]),
            "bi": _partition([o for o in opps if self._in_tag(o, bi_id)]),
        }

    def get_prospects(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:prospects", self._build_prospects)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Ganadas — detalle por año (Commercial Tile 6, ver HANDOFF) ───────────
    # Dominio CANÓNICO — el MISMO que el KPI "Ganadas" del summary
    # (`_build_summary`, ver arriba) y el legacy `/crm/kpi-detail`'s bucket
    # "won": `type=opportunity, stage_id.is_won=True, date_closed` dentro del
    # año pedido. Portado LITERAL, no re-derivado — cualquier divergencia acá
    # rompería la conservación KPI↔drill que el owner exige.
    #
    # SIN cláusula `active` EXPLÍCITA — a propósito, corrección post-audit:
    # el dominio legacy tampoco la tenía, pero eso NO significa "incluye
    # archivadas". Odoo excluye `active=False` POR DEFECTO quando el
    # dominio no menciona `active` (hay que pedir explícitamente `["active",
    # "in", [True, False]]` para verlas — MISMO criterio que
    # `_build_creadas_detail`/`_build_activities`, que sí lo hacen cuando
    # genuinamente necesitan archivadas). El KPI "Ganadas" del summary
    # (`_build_summary`, ver arriba) tiene EL MISMO dominio sin esa
    # cláusula, así que en Odoo real YA excluye archivadas hoy — agregar acá
    # `active in [True, False]` haría que el drill viera MÁS filas que el
    # KPI y rompería la conservación que el owner exige (§3). Se preserva
    # EXACTAMENTE el mismo comportamiento del KPI existente — cualquier
    # decisión de incluir archivadas es un cambio de ALCANCE MAYOR (tocaría
    # `_build_summary`, que es producción ya publicada) y requiere una
    # decisión de owner aparte, no algo que este incremento decida solo.
    #
    # Límites de fecha SOLO fecha (sin hora) — MISMA asimetría ya documentada
    # arriba (ver el docstring del módulo) respecto de Perdidas, que sí usa
    # límites con hora. Preservada tal cual, no es un bug de este incremento.
    #
    # Fila DELIBERADAMENTE mínima (Oportunidad/Cliente/Vendedor/Horas): es
    # EXACTAMENTE lo que el legacy `_renderKpiDrillBody`/`fmt()` expone para
    # el bucket "won" — revenue/priority se piden en el legacy pero NUNCA se
    # muestran, así que acá ni se piden (owner: "Do NOT fetch/display dead
    # legacy fields merely for symmetry"). Sin Etapa/Motivo/Origen/Tags/
    # Licencias/Probabilidad: ninguno existe en el contrato real de Ganadas.
    def _build_ganadas_detail(self, year: int) -> dict:
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        year_start, year_end = f"{year}-01-01", f"{year}-12-31"
        opps = self._client.search_read(
            "crm.lead",
            domain=[
                ["type", "=", "opportunity"], ["stage_id.is_won", "=", True],
                ["date_closed", ">=", year_start], ["date_closed", "<=", year_end],
            ],
            fields=["name", "partner_id", "user_id", "lead_properties", "tag_ids"],
        )

        def _row(opp: dict) -> dict:
            return {
                "opportunity": opp.get("name") or "",
                "client": opp["partner_id"][1] if opp.get("partner_id") else "",
                "seller": opp["user_id"][1] if opp.get("user_id") else "",
                "hours": get_property_num(opp.get("lead_properties"), "Horas"),
            }

        def _partition(subset: list) -> dict:
            return {"opportunities": [_row(o) for o in subset], "total": len(subset)}

        return {
            "todos": _partition(opps),
            "nn": _partition([o for o in opps if self._in_tag(o, nn_id)]),
            "bi": _partition([o for o in opps if self._in_tag(o, bi_id)]),
        }

    def get_ganadas_detail(self, tipo: str = "todos", year: int | None = None) -> dict:
        """Detalle canónico de Ganadas de UN año. `year=None` resuelve al
        actual — MISMO contrato que `get_creadas_detail`. Cache POR AÑO
        (`commercial:ganadas:<year>`): a diferencia de Creadas, acá NO hay
        ventana caliente — el frontend nunca pide el año anterior en
        background (owner: "Ganadas does NOT use Creadas' hot-window
        policy"), así que esta cache sólo evita repetir la MISMA consulta
        dentro del TTL, nunca precalienta nada."""
        if year is None:
            year = date.today().year
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached(f"commercial:ganadas:{year}", lambda: self._build_ganadas_detail(year))
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Perdidas — detalle por año (Commercial Tile 7, ver HANDOFF) ──────────
    # Dominio CANÓNICO — el MISMO que el KPI "Perdidas" del summary
    # (`_build_summary`, ver arriba): `type=opportunity, active=False,
    # probability=0, date_closed` dentro del año pedido, límites CON hora
    # ("00:00:00"/"23:59:59") — asimetría real vs Ganadas (fecha sola),
    # preservada tal cual, NUNCA normalizada por simetría.
    #
    # `active=False` va EXPLÍCITO en el dominio — a diferencia del landmine
    # de Ganadas (ausencia de cláusula `active` = `active_test=True`
    # implícito de Odoo, que excluye archivadas por defecto), acá el campo
    # SÍ está nombrado, así que Odoo respeta el valor pedido sin ambigüedad.
    #
    # Perdida NO es sólo "inactiva + probability 0": una oportunidad puede
    # matchear el dominio crudo y NO ser una Perdida de negocio si su motivo
    # es administrativo. Por eso el pipeline tiene DOS pasos más, reusando
    # LITERAL las mismas instance methods que ya usa el KPI — nunca una
    # segunda implementación del cascade de motivo ni de la exclusión (eso
    # rompería la conservación KPI↔drill en silencio):
    #   1. `lost_raw`        = candidatos crudos del dominio;
    #   2. `_resolve_lost_motivos(lost_raw)` = motivo resuelto por oportunidad
    #      (cascade de 3 niveles: lost_reason_id → lost_reason_ids → lead_
    #      properties "Motivo de Perdida Danwor");
    #   3. `_filter_excluded_lost(...)` = saca las de motivo administrativo
    #      ("cierre administrativo") — el universo que queda es la Perdida
    #      de negocio real, exactamente lo que ya cuenta el KPI.
    #
    # Fila con Motivo (a diferencia de Ganadas): "Sin definir" para
    # oportunidades sin motivo resoluble por ningún nivel del cascade — el
    # mismo fallback que ya usaba el legacy `/lost-reasons`/`/lost-history`
    # (nunca el `""` crudo de `/kpi-detail`'s `fmt()`, que acá rompería el
    # agrupamiento del desglose de Motivo). Sin Etapa/Origen/Tags/Licencias/
    # Probabilidad/Prioridad/Revenue: ninguno existe en el contrato real de
    # Perdidas (`revenue` se pedía y ordenaba en el legacy pero NUNCA se
    # mostraba — dead field, no se porta).
    def _build_perdidas_detail(self, year: int) -> dict:
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        year_start, year_end = f"{year}-01-01", f"{year}-12-31"
        lost_raw = self._client.search_read(
            "crm.lead",
            domain=[
                ["type", "=", "opportunity"], ["active", "=", False], ["probability", "=", 0],
                ["date_closed", ">=", f"{year_start} 00:00:00"], ["date_closed", "<=", f"{year_end} 23:59:59"],
            ],
            fields=["name", "partner_id", "user_id", "lead_properties", "tag_ids"],
        )
        lost_motivos = self._resolve_lost_motivos(lost_raw)
        opps = self._filter_excluded_lost(lost_raw, lost_motivos)

        def _row(opp: dict) -> dict:
            return {
                "opportunity": opp.get("name") or "",
                "client": opp["partner_id"][1] if opp.get("partner_id") else "",
                "seller": opp["user_id"][1] if opp.get("user_id") else "",
                "motivo": lost_motivos.get(opp["id"]) or "Sin definir",
                "hours": get_property_num(opp.get("lead_properties"), "Horas"),
            }

        def _partition(subset: list) -> dict:
            return {"opportunities": [_row(o) for o in subset], "total": len(subset)}

        return {
            "todos": _partition(opps),
            "nn": _partition([o for o in opps if self._in_tag(o, nn_id)]),
            "bi": _partition([o for o in opps if self._in_tag(o, bi_id)]),
        }

    def get_perdidas_detail(self, tipo: str = "todos", year: int | None = None) -> dict:
        """Detalle canónico de Perdidas de UN año. `year=None` resuelve al
        actual — MISMO contrato que `get_ganadas_detail`. Cache POR AÑO
        (`commercial:perdidas:<year>`), SIN ventana caliente (MISMA política
        que Ganadas — nunca precalienta el año anterior)."""
        if year is None:
            year = date.today().year
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached(f"commercial:perdidas:{year}", lambda: self._build_perdidas_detail(year))
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Suspendidas — detalle (Commercial Tile 8, ver HANDOFF) ───────────────
    # Universo: stage_id IN suspended_ids (`_stage_partition()[2]`, nombre de
    # etapa que CONTIENE "suspendido") — EL MISMO universo que el KPI
    # "Suspendidas" del summary. STOCK actual, SIN año — `active=True`
    # explícito (ver Ganadas: la ausencia de esa cláusula NO habría sido
    # "incluye archivadas", así que acá se mantiene el mismo patrón ya
    # correcto que Activas/Prospectos).
    #
    # Fila CASI idéntica a `_build_opportunities`/`_build_prospects` (MISMAS
    # fórmulas de Hs Pond./Últ. Resp./Días resp./actividades — ya auditadas
    # y en producción) — con DOS diferencias sobre `_build_opportunities`,
    # ambas owner decision explícita (ver HANDOFF, Tile 8 §7):
    #   - "stage" SÍ viaja (a diferencia de Activas, que la descartó, y de
    #     Prospectos, que nunca la necesitó por ser una sola etapa):
    #     Suspendidas es genuinamente multi-etapa — pueden coexistir varias
    #     etapas cuyo nombre contiene "suspendido" — así que la Etapa es
    #     información real, no ruido redundante.
    #   - "tags" (Etiquetas) SÍ viaja — MISMO criterio que Prospectos.
    # Sin "source": Origen es una vista exclusiva de Activas (C8), no parte
    # del contrato de Suspendidas.
    def _build_suspendidas_detail(self) -> dict:
        _valid_ids, _prospect_ids, suspended_ids = self._stage_partition()
        if not suspended_ids:
            # Owner §2 — "return a valid zero result... do NOT issue a
            # pointless crm.lead query with an empty/invalid stage domain."
            return {tipo: {"opportunities": [], "total": 0} for tipo in _TIPOS}
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        opps = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", suspended_ids]],
            fields=["name", "partner_id", "user_id", "stage_id", "tag_ids",
                     "probability", "priority", "lead_properties", "expected_revenue"],
        )
        opp_ids = [o["id"] for o in opps]
        acts_by_opp: dict[int, list] = {}
        if opp_ids:
            activities = self._client.search_read(
                "mail.activity",
                domain=[["res_model", "=", "crm.lead"], ["res_id", "in", opp_ids]],
                fields=["res_id", "date_deadline", "activity_type_id", "summary"],
                order="date_deadline asc",
            )
            for act in activities:
                acts_by_opp.setdefault(act["res_id"], []).append({
                    "type": act["activity_type_id"][1] if act.get("activity_type_id") else "",
                    "summary": act.get("summary") or "",
                    "deadline": iso_deadline(act.get("date_deadline")),
                })

        all_tag_ids = {tid for o in opps for tid in (o.get("tag_ids") or [])}
        tag_names: dict[int, str] = {}
        if all_tag_ids:
            tags = self._client.search_read("crm.tag", [["id", "in", list(all_tag_ids)]], fields=["id", "name"])
            tag_names = {t["id"]: t["name"] for t in tags}

        today = date.today()

        def _row(opp: dict) -> dict:
            props = opp.get("lead_properties") or []
            hours_raw = get_property_num(props, "Horas")
            weighted_hours = round((opp.get("probability", 0) or 0) / 100 * hours_raw, 1)
            last_response = _get_property_date(props, _PROP_ULTIMA_RESPUESTA)
            days_elapsed = (today - date.fromisoformat(last_response)).days if last_response else None
            return {
                "opportunity": opp.get("name") or "",
                "client": opp["partner_id"][1] if opp.get("partner_id") else "",
                "seller": opp["user_id"][1] if opp.get("user_id") else "",
                "stage": opp["stage_id"][1] if opp.get("stage_id") else "",
                "tags": [tag_names[tid] for tid in (opp.get("tag_ids") or []) if tid in tag_names],
                "licencias": _get_prop_label(props, "Licencias"),
                "hours": hours_raw,
                "weighted_hours": weighted_hours,
                "revenue": round(opp.get("expected_revenue", 0) or 0, 2),
                "probability": opp.get("probability"),
                "priority": opp.get("priority") or "0",
                "activities": acts_by_opp.get(opp["id"], []),
                "last_response": last_response,
                "days_elapsed": days_elapsed,
            }

        def _partition(subset: list) -> dict:
            return {"opportunities": [_row(o) for o in subset], "total": len(subset)}

        return {
            "todos": _partition(opps),
            "nn": _partition([o for o in opps if self._in_tag(o, nn_id)]),
            "bi": _partition([o for o in opps if self._in_tag(o, bi_id)]),
        }

    def get_suspendidas_detail(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:suspendidas", self._build_suspendidas_detail)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Forecast (Pipeline Total/Ponderado por mes) — Active Opportunities
    # Phase 2, C2 ──────────────────────────────────────────────────────────
    # Universo: stages 0-8 (`_stage_first_char in "012345678"`) — el MISMO
    # universo que "Pipeline Total"/"Pipeline Ponderado", NUNCA el de
    # Oportunidades Activas (1-8, ver `_build_opportunities`). Es semántica
    # legacy INTENCIONAL, no un bug (ver HANDOFF): Forecast agrupa por mes de
    # cierre sobre TODO el pipeline, prospectos incluidos.
    #
    # Agrupación por mes: prioriza la lead_property "Cierre Ajustado"
    # (fecha que el vendedor corrigió a mano) y cae a `date_deadline`
    # (fecha "de sistema") cuando no existe — MISMO criterio que
    # `crm-comercial`/`backend/routers/crm.py::get_pipeline_by_month`,
    # portado 1:1. Sin ninguna de las dos fechas, la oportunidad cae en el
    # bucket "Sin fecha", que se ordena SIEMPRE al final (nunca por orden
    # alfabético, que la pondría entre otros meses).
    def _build_forecast(self) -> dict:
        all_stages = self._client.search_read("crm.stage", [], fields=["id", "name"])
        pipeline_ids = [s["id"] for s in all_stages if _stage_first_char(s) in "012345678"]
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        opps = (
            self._client.search_read(
                "crm.lead",
                domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", pipeline_ids]],
                fields=["name", "partner_id", "user_id", "stage_id", "tag_ids",
                         "expected_revenue", "date_deadline", "probability", "lead_properties"],
            )
            if pipeline_ids else []
        )

        def _month_bucket_key(opp: dict) -> tuple[str, str | None]:
            props = opp.get("lead_properties") or []
            adjusted = _get_property_date(props, _PROP_CIERRE_AJUSTADO)
            deadline = iso_deadline(opp.get("date_deadline"))
            dd = adjusted or deadline
            return (dd[:7] if dd else "Sin fecha"), dd

        def _group(subset: list) -> dict:
            buckets: dict = {}
            for opp in subset:
                key, dd = _month_bucket_key(opp)
                props = opp.get("lead_properties") or []
                hours = get_property_num(props, "Horas")
                prob = (opp.get("probability", 0) or 0) / 100
                revenue = round(opp.get("expected_revenue", 0) or 0, 2)
                bucket = buckets.setdefault(
                    key, {"count": 0, "hours": 0, "weighted_hours": 0.0, "revenue": 0.0, "opportunities": []}
                )
                bucket["count"] += 1
                bucket["hours"] += hours
                bucket["weighted_hours"] += prob * hours
                bucket["revenue"] += revenue
                bucket["opportunities"].append({
                    "opportunity": opp.get("name") or "",
                    "client": opp["partner_id"][1] if opp.get("partner_id") else "",
                    "seller": opp["user_id"][1] if opp.get("user_id") else "",
                    "stage": opp["stage_id"][1] if opp.get("stage_id") else "",
                    "deadline": dd,
                    "revenue": revenue,
                    "hours": hours,
                    "probability": opp.get("probability"),
                })

            def _fmt(key: str, bucket: dict) -> dict:
                return {
                    "month": key,
                    "count": bucket["count"],
                    "hours": round(bucket["hours"], 1),
                    "weighted_hours": round(bucket["weighted_hours"], 1),
                    "revenue": round(bucket["revenue"], 2),
                    "opportunities": sorted(bucket["opportunities"], key=lambda o: o["deadline"] or ""),
                }

            months = sorted(
                (_fmt(k, v) for k, v in buckets.items() if k != "Sin fecha"),
                key=lambda m: m["month"],
            )
            if "Sin fecha" in buckets:
                months.append(_fmt("Sin fecha", buckets["Sin fecha"]))
            return {"months": months}

        return {
            "todos": _group(opps),
            "nn": _group([o for o in opps if self._in_tag(o, nn_id)]),
            "bi": _group([o for o in opps if self._in_tag(o, bi_id)]),
        }

    def get_forecast(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:forecast", self._build_forecast)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Analíticas del Pipeline ──────────────────────────────────────────
    # Homologación del bloque "Analíticas del Pipeline" del CRM legacy (ver
    # HANDOFF — Landing Homologation). Se portan los SEIS titulares que el
    # landing muestra, no los paneles de detalle: esos son drilldowns propios,
    # todavía no homologados, y fabricar su payload acá sería cargar tráfico
    # para algo que nadie consume.
    #
    # Tres de las seis (tiempo por etapa, conversión etapa a etapa,
    # estancadas) y la fecha de entrada al pipeline que necesita "Creadas" NO
    # se pueden derivar del estado actual de la oportunidad: dependen de su
    # HISTORIAL de cambios de etapa, que en Odoo vive en
    # `mail.message`/`mail.tracking.value`. Por eso ese historial se resuelve
    # UNA sola vez (`_stage_transitions`) y lo comparten las cuatro.
    def _stage_transitions(self, opp_ids: list) -> dict:
        """`opp_id -> [(YYYY-MM-DD, old_stage_id, new_stage_id)]`, cronológico.

        Dos consultas, no una por oportunidad: primero los mensajes que
        tocaron `stage_id`, después sus valores de tracking."""
        if not opp_ids:
            return {}
        messages = self._client.search_read(
            "mail.message",
            domain=[
                ["model", "=", "crm.lead"],
                ["res_id", "in", opp_ids],
                ["tracking_value_ids.field_id.name", "=", "stage_id"],
            ],
            fields=["id", "res_id", "date"],
            order="date asc",
        )
        if not messages:
            return {}
        by_id = {m["id"]: m for m in messages}
        tracking = self._client.search_read(
            "mail.tracking.value",
            domain=[
                ["mail_message_id", "in", list(by_id)],
                ["field_id.name", "=", "stage_id"],
            ],
            fields=["mail_message_id", "old_value_integer", "new_value_integer"],
        )
        out: dict = {}
        for tv in tracking:
            raw = tv.get("mail_message_id")
            msg = by_id.get(raw[0] if isinstance(raw, (list, tuple)) else raw)
            if not msg:
                continue
            old_s, new_s = tv.get("old_value_integer") or None, tv.get("new_value_integer") or None
            if old_s or new_s:
                out.setdefault(msg["res_id"], []).append((str(msg["date"])[:10], old_s, new_s))
        for opp_id in out:
            out[opp_id].sort(key=lambda t: t[0])
        return out

    @staticmethod
    def _iso_day(value):
        try:
            return date.fromisoformat(str(value)[:10])
        except (ValueError, TypeError):
            return None

    # CANONICAL "Creadas" event set (Creadas Legacy Parity — ver HANDOFF):
    # entrada a la primera etapa válida del pipeline, MISMA regla en TODOS
    # los consumidores. Portado 1:1 desde `crm-comercial`/`backend/routers/
    # crm.py::get_opp_by_month` (identidad de negocio ya verificada contra
    # ese código real, no inferida). `opps` es CUALQUIER lista de crm.lead
    # con `id`/`stage_id`/`create_date` — el KPI semanal (`_view`, ventana
    # de 2 semanas) y el drill mensual/vendedor (`_build_creadas_detail`,
    # historial completo) le pasan universos DISTINTOS al MISMO algoritmo,
    # nunca reimplementan la regla por separado (trust invariant, ver
    # ticket owner — "the drill must explain the tile").
    def _created_entry_events(self, opps: list, transitions: dict, stage_one_id, valid_ids: list) -> dict:
        """`{opp_id: date}` — sólo las oportunidades con fecha de entrada
        determinable (transición histórica a `stage_one_id`, o `create_date`
        si no hay historial Y la etapa ACTUAL ya es 1-8). Sin ninguna de las
        dos, la oportunidad no aporta un evento (mismo criterio legacy)."""
        events: dict = {}
        for o in opps:
            entry = None
            for day_str, _old, new_s in transitions.get(o["id"], []):
                if new_s == stage_one_id:
                    entry = self._iso_day(day_str)
                    break
            if entry is None:
                cur = o["stage_id"][0] if isinstance(o.get("stage_id"), (list, tuple)) else o.get("stage_id")
                if cur in valid_ids:
                    entry = self._iso_day(o.get("create_date"))
            if entry is not None:
                events[o["id"]] = entry
        return events

    # ── HOT/COLD POR AÑO ────────────────────────────────────────────────
    # `_stage_transitions` acotado: MISMAS dos consultas y MISMA forma de
    # salida, pero el universo se limita por fecha del mensaje y/o por ids.
    # Sin cota, `mail.message` con el join a
    # `tracking_value_ids.field_id.name` recorre el historial de mensajes de
    # TODOS los leads de la base — el costo que este corrective elimina.
    def _stage_transitions_scoped(self, opp_ids=None, desde=None, hasta=None) -> dict:
        domain = [
            ["model", "=", "crm.lead"],
            ["tracking_value_ids.field_id.name", "=", "stage_id"],
        ]
        if opp_ids is not None:
            if not opp_ids:
                return {}
            domain.append(["res_id", "in", list(opp_ids)])
        if desde:
            domain.append(["date", ">=", desde])
        if hasta:
            domain.append(["date", "<=", hasta])
        messages = self._client.search_read(
            "mail.message", domain=domain, fields=["id", "res_id", "date"], order="date asc")
        if not messages:
            return {}
        by_id = {m["id"]: m for m in messages}
        tracking = self._client.search_read(
            "mail.tracking.value",
            domain=[["mail_message_id", "in", list(by_id)], ["field_id.name", "=", "stage_id"]],
            fields=["mail_message_id", "old_value_integer", "new_value_integer"],
        )
        out: dict = {}
        for tv in tracking:
            raw = tv.get("mail_message_id")
            msg = by_id.get(raw[0] if isinstance(raw, (list, tuple)) else raw)
            if not msg:
                continue
            old_s, new_s = tv.get("old_value_integer") or None, tv.get("new_value_integer") or None
            if old_s or new_s:
                out.setdefault(msg["res_id"], []).append((str(msg["date"])[:10], old_s, new_s))
        for opp_id in out:
            out[opp_id].sort(key=lambda t: t[0])
        return out

    def creadas_selectable_years(self) -> list[str]:
        """Rango CONTIGUO seleccionable: del año de la oportunidad más antigua
        al actual. UNA consulta con `limit=1`.

        Es el límite inferior VERDADERO más barato, y a propósito NO afirma que
        cada año contenga eventos canónicos (owner decision — "YEAR OPTION !=
        YEAR GUARANTEED TO CONTAIN DATA"): un año seleccionado puede devolver
        "Sin resultados" legítimamente. Reconstruir el historial de etapas para
        poblar un `<select>` es exactamente el costo que este corrective saca.
        """
        actual = date.today().year
        oldest = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "in", [True, False]]],
            fields=["create_date"], limit=1, order="create_date asc",
        )
        primero = actual
        if oldest:
            d = self._iso_day(oldest[0].get("create_date"))
            if d:
                primero = min(d.year, actual)
        return [str(y) for y in range(actual, primero - 1, -1)]

    def _creadas_year_universe(self, year: int, valid_ids: list, stage_one_id):
        """Universo ACOTADO cuyo historial de transiciones alcanza para decidir
        el evento canónico de `year` — y nada más.

        La clave del diseño: en vez de reimplementar la regla canónica acotada
        (que es donde se cuelan las divergencias), se acota el UNIVERSO y
        despues se corre `_created_entry_events` TAL CUAL sobre el historial
        COMPLETO de ese universo. La equivalencia con la version full-history
        queda garantizada por construccion, no por replicar la logica.

        Dos fuentes de candidatos:
        * quien tuvo una transicion a etapa 1 con fecha DENTRO del año;
        * quien fue creado DENTRO del año y hoy esta en una etapa valida
          (los candidatos del fallback por `create_date`).

        Para esos ids se pide su historial de transiciones SIN cota de fecha:
        es lo que permite descartar una RE-ENTRADA (una oportunidad que ya
        entro en un año anterior tiene su evento canonico alla, no acá) y lo
        que mantiene intacta la semantica del fallback.
        """
        desde, hasta = f"{year}-01-01 00:00:00", f"{year}-12-31 23:59:59"
        del_anio = self._stage_transitions_scoped(desde=desde, hasta=hasta)
        candidatos = {
            opp_id for opp_id, tramos in del_anio.items()
            if any(new_s == stage_one_id for _d, _o, new_s in tramos)
        }
        fallback = self._client.search_read(
            "crm.lead",
            domain=[
                ["type", "=", "opportunity"], ["active", "in", [True, False]],
                ["create_date", ">=", desde], ["create_date", "<=", hasta],
                ["stage_id", "in", valid_ids],
            ],
            fields=["id", "stage_id", "create_date", "tag_ids"],
        )
        ids = candidatos | {o["id"] for o in fallback}
        if not ids:
            return [], {}
        # Historial COMPLETO de esos ids (sin cota de fecha, acotado por id):
        # es lo que hace que la regla canonica se pueda aplicar sin cambios.
        historial = self._stage_transitions_scoped(opp_ids=sorted(ids))
        conocidos = {o["id"]: o for o in fallback}
        faltantes = sorted(ids - set(conocidos))
        if faltantes:
            for o in self._client.search_read(
                "crm.lead",
                domain=[["id", "in", faltantes], ["active", "in", [True, False]]],
                fields=["id", "stage_id", "create_date", "tag_ids"],
            ):
                conocidos[o["id"]] = o
        return list(conocidos.values()), historial

    # ── Helpers puros del embudo (Analítica del Pipeline, ver HANDOFF) ───────
    # Extraídos de lo que antes vivía INLINE dentro de `_build_analytics`
    # (agregados-only) para que el drill de detalle (más abajo,
    # `_build_pipeline_analytics_detail`) reuse LITERAL la misma fórmula en
    # vez de reimplementarla — MISMA razón que `_stage_transitions`/
    # `_created_entry_events` ya son helpers compartidos. Puros: ninguno hace
    # I/O, sólo procesan listas/dicts ya consultados. `_build_analytics`
    # sigue devolviendo EXACTAMENTE los mismos agregados que antes — este
    # refactor no cambia un solo número (ver
    # `test_analytics_*`/`test_creadas_*`, que no se tocaron).
    @staticmethod
    def _pipeline_age_rows(opps: list, today) -> list[dict]:
        rows = []
        for o in opps:
            d = CommercialService._iso_day(o.get("create_date"))
            if d:
                rows.append({"id": o["id"], "age_days": (today - d).days})
        return rows

    @staticmethod
    def _close_time_rows(won_opps: list, today) -> list[dict]:
        rows = []
        for o in won_opps:
            cd, dc = CommercialService._iso_day(o.get("create_date")), CommercialService._iso_day(o.get("date_closed"))
            if cd and dc and (dc - cd).days >= 0:
                rows.append({"id": o["id"], "days": (dc - cd).days})
        return rows

    @staticmethod
    def _stagnant_rows(active_opps: list, transitions: dict, today, threshold_days: int) -> list[dict]:
        rows = []
        for o in active_opps:
            trans = transitions.get(o["id"])
            last = CommercialService._iso_day(trans[-1][0]) if trans else CommercialService._iso_day(o.get("create_date"))
            if last and (today - last).days > threshold_days:
                rows.append({"id": o["id"], "days_stagnant": (today - last).days, "last_move_date": last})
        return rows

    @staticmethod
    def _per_stage_elapsed(subset_active: list, subset_won: list, transitions: dict, valid_ids: list) -> dict:
        """`{stage_id: [días_transcurridos, ...]}` — cuánto tiempo pasó cada
        oportunidad EN cada etapa antes de salir de ella."""
        subset_ids = {o["id"] for o in subset_active} | {o["id"] for o in subset_won}
        created_by_id = {o["id"]: CommercialService._iso_day(o.get("create_date"))
                         for o in list(subset_active) + list(subset_won)}
        per_stage: dict = {sid: [] for sid in valid_ids}
        for opp_id, trans in transitions.items():
            if opp_id not in subset_ids:
                continue
            created = created_by_id.get(opp_id)
            if not created:
                continue
            entered = {}
            if trans[0][1]:
                entered[trans[0][1]] = created
            for day_str, old_s, new_s in trans:
                day = CommercialService._iso_day(day_str)
                if day and old_s in entered:
                    elapsed = (day - entered[old_s]).days
                    if elapsed >= 0 and old_s in per_stage:
                        per_stage[old_s].append(elapsed)
                if new_s and day:
                    entered[new_s] = day
        return per_stage

    @staticmethod
    def _stage_reached_sets(subset_active: list, subset_won: list, transitions: dict, valid_ids: list) -> dict:
        """`{stage_id: {opp_id, ...}}` — qué oportunidades ALCANZARON cada
        etapa (por historial o por estar activa ahí ahora)."""
        subset_ids = {o["id"] for o in subset_active} | {o["id"] for o in subset_won}
        reached: dict = {sid: set() for sid in valid_ids}
        for opp_id, trans in transitions.items():
            if opp_id not in subset_ids:
                continue
            for _day, _old, new_s in trans:
                if new_s in reached:
                    reached[new_s].add(opp_id)
        for o in subset_active:
            cur = o["stage_id"][0] if isinstance(o.get("stage_id"), (list, tuple)) else o.get("stage_id")
            if cur in reached:
                reached[cur].add(o["id"])
        return reached

    def _build_analytics(self) -> dict:
        today = date.today()
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None

        # Etapas 1–8 EN ORDEN (por prefijo numérico y, a igualdad, por
        # `sequence`) — el orden importa: la conversión etapa a etapa compara
        # cada etapa con la SIGUIENTE.
        all_stages = self._client.search_read(
            "crm.stage", [], fields=["id", "name", "sequence"], order="sequence asc"
        )
        valid_stages = sorted(
            [st for st in all_stages if _stage_first_char(st) in "12345678"],
            key=lambda st: (int(st["name"].strip()[0]), st.get("sequence", 99)),
        )
        valid_ids = [st["id"] for st in valid_stages]
        if not valid_ids:
            return {tipo: _EMPTY_ANALYTICS() for tipo in _TIPOS}
        stage_one_id = valid_stages[0]["id"]

        active = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", valid_ids]],
            fields=["id", "stage_id", "create_date", "tag_ids"],
        )
        year = today.year
        won = self._client.search_read(
            "crm.lead",
            domain=[
                ["type", "=", "opportunity"], ["stage_id.is_won", "=", True],
                ["date_closed", ">=", f"{year}-01-01"], ["date_closed", "<=", f"{year}-12-31"],
            ],
            fields=["id", "create_date", "date_closed", "tag_ids"],
        )
        # Ventana de "Creadas": desde el lunes de la semana pasada. Se traen
        # también archivadas — una oportunidad creada esta semana y ya perdida
        # se creó igual.
        this_monday = today - timedelta(days=today.weekday())
        last_monday = this_monday - timedelta(days=7)
        recent = self._client.search_read(
            "crm.lead",
            domain=[
                ["type", "=", "opportunity"], ["active", "in", [True, False]],
                # Fecha sin hora a propósito: Odoo compara datetime contra
                # una fecha sin ambigüedad, y evita depender del formato de
                # hora que devuelva la instancia.
                ["create_date", ">=", last_monday.isoformat()],
            ],
            fields=["id", "stage_id", "create_date", "tag_ids"],
        )

        transitions = self._stage_transitions(
            sorted({o["id"] for o in active} | {o["id"] for o in won} | {o["id"] for o in recent})
        )

        def _view(subset_active, subset_won, subset_recent) -> dict:
            # 1. Edad del pipeline — días abiertos de las activas.
            ages = [r["age_days"] for r in self._pipeline_age_rows(subset_active, today)]
            avg_age = round(sum(ages) / len(ages), 1) if ages else 0

            # 2. Tiempo de cierre — creación → cierre, sólo ganadas del año.
            closes = [r["days"] for r in self._close_time_rows(subset_won, today)]
            avg_close = round(sum(closes) / len(closes), 1) if closes else 0

            # 3. Estancadas — días desde el ÚLTIMO movimiento de etapa; sin
            # historial, desde la creación.
            stagnant = len(self._stagnant_rows(subset_active, transitions, today, STAGNANT_DAYS))

            # 4. Tiempo por etapa — se reconstruye cuándo entró y salió de
            # cada etapa. La primera transición siembra la etapa de origen en
            # la fecha de creación.
            per_stage = self._per_stage_elapsed(subset_active, subset_won, transitions, valid_ids)
            stage_avgs = [round(sum(v) / len(v), 1) for v in per_stage.values() if v]
            avg_stage = round(sum(stage_avgs) / len(stage_avgs), 1) if stage_avgs else None

            # 5. Conversión etapa a etapa — qué proporción de las que
            # ALCANZARON una etapa alcanzó también la siguiente. El titular es
            # la tasa MÍNIMA: el cuello de botella del embudo.
            reached = self._stage_reached_sets(subset_active, subset_won, transitions, valid_ids)
            rates = []
            for idx, st in enumerate(valid_stages[:-1]):
                entered_n = len(reached[st["id"]])
                if entered_n:
                    advanced = len(reached[st["id"]] & reached[valid_stages[idx + 1]["id"]])
                    rates.append(round(advanced / entered_n * 100, 1))
            min_rate = min(rates) if rates else None

            # 6. Creadas — entrada al PIPELINE (etapa 1), MISMA semántica que
            # el legacy: no es la fecha de alta si la oportunidad nació antes
            # como prospecto. Sin transición a etapa 1 registrada, se usa la
            # creación para las que ya están en 1–8. Conteo semanal sobre el
            # MISMO conjunto canónico que alimenta el drill (Creadas Legacy
            # Parity, ver HANDOFF) — nunca un segundo algoritmo de entrada.
            creadas_events = self._created_entry_events(subset_recent, transitions, stage_one_id, valid_ids)
            this_week = sum(1 for d in creadas_events.values() if d >= this_monday)
            last_week = sum(1 for d in creadas_events.values() if last_monday <= d < this_monday)

            return {
                "pipeline_age": {"avg_days": avg_age, "count": len(ages)},
                "stage_time": {"avg_days": avg_stage, "stages_with_data": len(stage_avgs)},
                "stage_conversion": {"min_rate": min_rate, "steps": len(rates)},
                "close_time": {"avg_days": avg_close, "count": len(closes)},
                "stagnant": {"count": stagnant, "threshold_days": STAGNANT_DAYS},
                "creadas": {"this_week": this_week, "last_week": last_week},
            }

        def _by(subset, tag_id):
            return [o for o in subset if self._in_tag(o, tag_id)]

        views = {
            "todos": _view(active, won, recent),
            "nn": _view(_by(active, nn_id), _by(won, nn_id), _by(recent, nn_id)),
            "bi": _view(_by(active, bi_id), _by(won, bi_id), _by(recent, bi_id)),
        }
        lead_conv = self._build_lead_conversion(nn_id, bi_id)
        for tipo in _TIPOS:
            views[tipo]["lead_conversion"] = lead_conv[tipo]
        return views

    # Conversión de Leads — el sexto titular. Mide sobre EMPRESAS únicas, no
    # sobre registros: el legacy deduplica por partner para no contar tres
    # veces al mismo cliente que entró por tres formularios.
    def _build_lead_conversion(self, nn_id, bi_id) -> dict:
        all_stages = self._client.search_read("crm.stage", [], fields=["id", "name"])
        lead_stage_ids = [st["id"] for st in all_stages
                          if (st.get("name") or "").strip().lower() == "lead"]
        fields = ["id", "partner_id", "partner_name", "stage_id", "active", "tag_ids"]

        leads = self._client.search_read(
            "crm.lead", domain=[["type", "=", "lead"], ["active", "in", [True, False]]], fields=fields
        )
        stuck = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", lead_stage_ids]],
            fields=fields,
        ) if lead_stage_ids else []

        # Convertidas: el campo `type` pasó de lead a oportunidad. Vive en el
        # historial, no en el registro.
        msgs = self._client.search_read(
            "mail.message",
            domain=[["model", "=", "crm.lead"], ["tracking_value_ids.field_id.name", "=", "type"]],
            fields=["id", "res_id"],
        )
        converted_ids: set = set()
        if msgs:
            by_id = {m["id"]: m for m in msgs}
            tvs = self._client.search_read(
                "mail.tracking.value",
                domain=[
                    ["mail_message_id", "in", list(by_id)],
                    ["field_id.name", "=", "type"],
                    ["old_value_char", "ilike", "lead"],
                    ["new_value_char", "ilike", "opor"],
                ],
                fields=["mail_message_id"],
            )
            for tv in tvs:
                raw = tv.get("mail_message_id")
                msg = by_id.get(raw[0] if isinstance(raw, (list, tuple)) else raw)
                if msg:
                    converted_ids.add(msg["res_id"])
        # Las que volvieron a etapa Lead no cuentan como convertidas.
        converted_ids -= {o["id"] for o in stuck}

        converted = self._client.search_read(
            "crm.lead",
            domain=[["id", "in", sorted(converted_ids)], ["active", "in", [True, False]]],
            fields=fields + ["probability"],
        ) if converted_ids else []

        prospect_ids = {st["id"] for st in all_stages if _stage_first_char(st) == "0"}
        opp_ids = {st["id"] for st in all_stages if _stage_first_char(st) in "12345678"}

        def _co(rec):
            partner = rec.get("partner_id")
            return (partner[0], partner[1]) if partner else (0, rec.get("partner_name") or "Sin empresa")

        def _view(sub_leads, sub_stuck, sub_conv) -> dict:
            universe = {_co(r) for r in list(sub_leads) + list(sub_stuck) + list(sub_conv)}
            any_conv, adv_opp = set(), set()
            for rec in sub_conv:
                key = _co(rec)
                any_conv.add(key)
                sid = rec["stage_id"][0] if isinstance(rec.get("stage_id"), (list, tuple)) else rec.get("stage_id")
                # "Avanzó" = superó la etapa de prospecto: está en 1–8, o ya
                # cerró (ganada o perdida) habiendo pasado por ahí.
                if sid in opp_ids or sid not in prospect_ids:
                    adv_opp.add(key)
            total = len(universe)
            pct = lambda n: round(n / total * 100, 1) if total else 0
            return {
                "companies": total,
                "any_conv": len(any_conv), "rate_any_conv": pct(len(any_conv)),
                "adv_opp": len(adv_opp), "rate_adv_opp": pct(len(adv_opp)),
            }

        def _by(subset, tag_id):
            return [o for o in subset if self._in_tag(o, tag_id)]

        return {
            "todos": _view(leads, stuck, converted),
            "nn": _view(_by(leads, nn_id), _by(stuck, nn_id), _by(converted, nn_id)),
            "bi": _view(_by(leads, bi_id), _by(stuck, bi_id), _by(converted, bi_id)),
        }

    def get_analytics(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:analytics", self._build_analytics)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Drill de Analítica del Pipeline — detalle compartido (ver HANDOFF) ───
    # LAZY, endpoint propio, MISMO criterio que Creadas/Prospectos/Ganadas/
    # Perdidas/Suspendidas — "el KPI debe seguir liviano": `_build_analytics`
    # (arriba, EAGER, parte de la hidratación inicial) sigue devolviendo
    # SÓLO agregados; este builder re-consulta el MISMO universo con más
    # campos de presentación y expone el detalle por fila.
    #
    # UN SOLO endpoint para CINCO sub-vistas (Edad/Tiempo por etapa/
    # Conversión etapa a etapa/Tiempo de cierre/Estancadas) — las cinco
    # comparten el MISMO cómputo de fondo (`active`+`won`+`_stage_transitions`,
    # MISMA fórmula que `_build_analytics` vía los helpers puros de arriba),
    # así que partirlas en cinco endpoints separados sería 5x el tráfico
    # Odoo por la MISMA data. "Conversión de leads" es la excepción: universo
    # totalmente distinto (`crm.lead type=lead` + `mail.tracking.value` sobre
    # `type`, no sobre `stage_id`) — vive en su propio builder, más abajo.
    #
    # `stage_conversion` preserva el contrato YA vigente del titular
    # (`min_rate`, en `_build_analytics`): SÓLO etapas 1-8, NUNCA la fila
    # "Prospecto → Etapa 1" que sí trae el legacy (`prsp_rate` en
    # `/pipeline-analytics`) — agregarla cambiaría qué valor puede ser el
    # mínimo del embudo, y `min_rate` YA está en producción con la semántica
    # actual. Deviation deliberada, documentada — no un byte-for-byte del
    # legacy (ver HANDOFF).
    def _build_pipeline_analytics_detail(self) -> dict:
        today = date.today()
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None

        all_stages = self._client.search_read(
            "crm.stage", [], fields=["id", "name", "sequence"], order="sequence asc"
        )
        valid_stages = sorted(
            [st for st in all_stages if _stage_first_char(st) in "12345678"],
            key=lambda st: (int(st["name"].strip()[0]), st.get("sequence", 99)),
        )
        valid_ids = [st["id"] for st in valid_stages]

        def _empty_views() -> dict:
            return {tipo: {
                "pipeline_age": {"opportunities": []},
                "stage_time": [],
                "stage_conversion": [],
                "close_time": {"opportunities": []},
                "stagnant": {"opportunities": [], "threshold_days": STAGNANT_DAYS},
            } for tipo in _TIPOS}

        if not valid_ids:
            return _empty_views()

        year = today.year
        active = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", valid_ids]],
            fields=["id", "name", "partner_id", "user_id", "stage_id", "create_date",
                     "priority", "tag_ids", "lead_properties"],
        )
        won = self._client.search_read(
            "crm.lead",
            domain=[
                ["type", "=", "opportunity"], ["stage_id.is_won", "=", True],
                ["date_closed", ">=", f"{year}-01-01"], ["date_closed", "<=", f"{year}-12-31"],
            ],
            fields=["id", "name", "partner_id", "user_id", "create_date", "date_closed", "tag_ids"],
        )
        transitions = self._stage_transitions(sorted({o["id"] for o in active} | {o["id"] for o in won}))

        def _by(subset, tag_id):
            return [o for o in subset if self._in_tag(o, tag_id)]

        def _client_of(o):
            return o["partner_id"][1] if o.get("partner_id") else ""

        def _seller_of(o):
            return o["user_id"][1] if o.get("user_id") else ""

        def _stage_of(o):
            return o["stage_id"][1] if o.get("stage_id") else ""

        def _view(subset_active, subset_won) -> dict:
            by_id_active = {o["id"]: o for o in subset_active}
            by_id_won = {o["id"]: o for o in subset_won}

            age_rows = []
            for r in self._pipeline_age_rows(subset_active, today):
                o = by_id_active[r["id"]]
                age_rows.append({
                    "opportunity": o.get("name") or "", "client": _client_of(o), "seller": _seller_of(o),
                    "stage": _stage_of(o), "create_date": o.get("create_date"), "age_days": r["age_days"],
                })
            age_rows.sort(key=lambda r: -r["age_days"])

            close_rows = []
            for r in self._close_time_rows(subset_won, today):
                o = by_id_won[r["id"]]
                close_rows.append({
                    "opportunity": o.get("name") or "", "client": _client_of(o), "seller": _seller_of(o),
                    "days": r["days"],
                })
            close_rows.sort(key=lambda r: -r["days"])

            stagnant_rows = []
            for r in self._stagnant_rows(subset_active, transitions, today, STAGNANT_DAYS):
                o = by_id_active[r["id"]]
                last_resp = _get_property_date(o.get("lead_properties"), _PROP_ULTIMA_RESPUESTA)
                stagnant_rows.append({
                    "opportunity": o.get("name") or "", "client": _client_of(o), "seller": _seller_of(o),
                    "stage": _stage_of(o), "priority": o.get("priority") or "0",
                    "days_stagnant": r["days_stagnant"], "last_move_date": r["last_move_date"].isoformat(),
                    "last_response": last_resp,
                })
            stagnant_rows.sort(key=lambda r: -r["days_stagnant"])

            per_stage = self._per_stage_elapsed(subset_active, subset_won, transitions, valid_ids)
            stage_time_rows = [{
                "stage": st["name"],
                "avg_days": round(sum(per_stage[st["id"]]) / len(per_stage[st["id"]]), 1) if per_stage[st["id"]] else None,
                "sample_count": len(per_stage[st["id"]]),
            } for st in valid_stages]

            reached = self._stage_reached_sets(subset_active, subset_won, transitions, valid_ids)
            stage_conversion_rows = []
            for idx, st in enumerate(valid_stages):
                entered_n = len(reached[st["id"]])
                if idx + 1 < len(valid_stages):
                    next_st = valid_stages[idx + 1]
                    advanced = len(reached[st["id"]] & reached[next_st["id"]])
                    rate = round(advanced / entered_n * 100, 1) if entered_n else 0
                    next_name = next_st["name"]
                else:
                    advanced, rate, next_name = None, None, None
                stage_conversion_rows.append({
                    "stage": st["name"], "next_stage": next_name,
                    "entered": entered_n, "advanced": advanced, "rate": rate,
                })

            return {
                "pipeline_age": {"opportunities": age_rows},
                "stage_time": stage_time_rows,
                "stage_conversion": stage_conversion_rows,
                "close_time": {"opportunities": close_rows},
                "stagnant": {"opportunities": stagnant_rows, "threshold_days": STAGNANT_DAYS},
            }

        return {
            "todos": _view(active, won),
            "nn": _view(_by(active, nn_id), _by(won, nn_id)),
            "bi": _view(_by(active, bi_id), _by(won, bi_id)),
        }

    def get_pipeline_analytics_detail(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:pipeline_analytics_detail", self._build_pipeline_analytics_detail)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Drill de "Conversión de Leads" — detalle (ver HANDOFF) ───────────────
    # LAZY, endpoint propio — universo TOTALMENTE distinto al resto de
    # Analítica del Pipeline (`crm.lead type=lead` + historial del campo
    # `type` vía `mail.tracking.value`, no de `stage_id`), así que nunca
    # comparte query con `_build_pipeline_analytics_detail`. Re-consulta el
    # MISMO universo que el titular EAGER (`_build_lead_conversion`, dentro
    # de `_build_analytics`) con más campos de presentación, y agrega el
    # desglose completo por resultado (Prospecto/Activa/Ganada/Perdida —
    # antes sólo existían "cualquier conversión"/"avanzó a oportunidad").
    #
    # "Ganada"/"Perdida" usan la MISMA regla YA cerrada que los tiles
    # Ganadas/Perdidas (`stage_id.is_won` / `active=False + probability=0`)
    # — NUNCA la clasificación propia del legacy (`probability==100`), que
    # sería una TERCERA definición de "ganada" que Hub no usa en ningún otro
    # lado (consume el contrato ya cerrado, no lo reinventa — ver HANDOFF).
    # Por construcción, cada convertida cae en EXACTAMENTE un bucket
    # (Prospecto si etapa 0; si no, Ganada/Perdida/Activa) — la unión
    # Prospecto∪Activa∪Ganada∪Perdida es la MISMA partición que ya usan
    # `any_conv`/`adv_opp` del titular (ver test de conservación).
    #
    # Alcance deliberadamente ACOTADO respecto del legacy `/lead-conversion`:
    # ese endpoint también arma un árbol UTM completo (campaña→medio→origen,
    # con gráficos por campaña) y un buscador con 5 facetas — una feature de
    # atribución de marketing aparte, no "conversión de leads" en sí, y Hub
    # ya tiene una vista de Origen (C8, dentro del análisis de Activas). Se
    # documenta como brecha de paridad intencional (ver reporte final), no
    # una omisión silenciosa.
    def _build_lead_conversion_detail(self) -> dict:
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None

        all_stages = self._client.search_read("crm.stage", [], fields=["id", "name", "is_won"])
        lead_stage_ids = [st["id"] for st in all_stages if (st.get("name") or "").strip().lower() == "lead"]
        prospect_ids = {st["id"] for st in all_stages if _stage_first_char(st) == "0"}
        won_stage_ids = {st["id"] for st in all_stages if st.get("is_won")}

        fields = ["id", "name", "partner_id", "partner_name", "user_id", "stage_id", "active",
                  "probability", "create_date", "tag_ids"]

        leads = self._client.search_read(
            "crm.lead", domain=[["type", "=", "lead"], ["active", "in", [True, False]]], fields=fields
        )
        stuck = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", lead_stage_ids]],
            fields=fields,
        ) if lead_stage_ids else []

        msgs = self._client.search_read(
            "mail.message",
            domain=[["model", "=", "crm.lead"], ["tracking_value_ids.field_id.name", "=", "type"]],
            fields=["id", "res_id", "date"],
        )
        converted_ids: set = set()
        conversion_dates: dict = {}
        if msgs:
            by_id = {m["id"]: m for m in msgs}
            tvs = self._client.search_read(
                "mail.tracking.value",
                domain=[
                    ["mail_message_id", "in", list(by_id)],
                    ["field_id.name", "=", "type"],
                    ["old_value_char", "ilike", "lead"],
                    ["new_value_char", "ilike", "opor"],
                ],
                fields=["mail_message_id"],
            )
            for tv in tvs:
                raw = tv.get("mail_message_id")
                msg = by_id.get(raw[0] if isinstance(raw, (list, tuple)) else raw)
                if msg:
                    oid = msg["res_id"]
                    converted_ids.add(oid)
                    d = str(msg.get("date") or "")
                    if oid not in conversion_dates or d < conversion_dates[oid]:
                        conversion_dates[oid] = d
        converted_ids -= {o["id"] for o in stuck}

        converted = self._client.search_read(
            "crm.lead",
            domain=[["id", "in", sorted(converted_ids)], ["active", "in", [True, False]]],
            fields=fields,
        ) if converted_ids else []

        def _co_key(rec):
            partner = rec.get("partner_id")
            return (partner[0], partner[1]) if partner else (0, rec.get("partner_name") or "Sin empresa")

        def _row(rec, extra=None) -> dict:
            row = {
                "opportunity": rec.get("name") or "", "client": _co_key(rec)[1],
                "seller": rec["user_id"][1] if rec.get("user_id") else "",
                "create_date": rec.get("create_date"),
            }
            if extra:
                row.update(extra)
            return row

        def _classify(rec) -> str:
            sid = rec["stage_id"][0] if isinstance(rec.get("stage_id"), (list, tuple)) else rec.get("stage_id")
            active = rec.get("active", True)
            if sid in prospect_ids:
                return "prospect"
            if sid in won_stage_ids:
                return "won"
            if not active and (rec.get("probability", 0) or 0) == 0:
                return "lost"
            return "opp"

        def _by(subset, tag_id):
            return [o for o in subset if self._in_tag(o, tag_id)]

        def _view(sub_leads, sub_stuck, sub_conv) -> dict:
            still_lead_recs = [r for r in sub_leads if r.get("active", True)]
            lost_lead_recs = [r for r in sub_leads if not r.get("active", True)]

            buckets: dict = {"prospect": [], "opp": [], "won": [], "lost": []}
            bucket_co: dict = {"prospect": set(), "opp": set(), "won": set(), "lost": set()}
            for rec in sub_conv:
                b = _classify(rec)
                stage_name = rec["stage_id"][1] if rec.get("stage_id") else ""
                row = _row(rec, {
                    "stage": stage_name,
                    "convert_date": (conversion_dates.get(rec["id"], "") or "")[:10] or None,
                })
                buckets[b].append(row)
                bucket_co[b].add(_co_key(rec))

            still_lead_rows = [_row(r) for r in still_lead_recs]
            lost_lead_rows = [_row(r) for r in lost_lead_recs]

            stuck_co = {_co_key(r) for r in sub_stuck}
            still_lead_co = {_co_key(r) for r in still_lead_recs}
            lost_lead_co = {_co_key(r) for r in lost_lead_recs}
            all_conv_co = bucket_co["prospect"] | bucket_co["opp"] | bucket_co["won"] | bucket_co["lost"]
            adv_co = bucket_co["opp"] | bucket_co["won"] | bucket_co["lost"]
            # MISMO universo que el titular (`_build_lead_conversion`):
            # leads + stuck (reversiones a "Lead") + convertidas — `stuck`
            # SÓLO cuenta para el denominador, nunca aparece como bucket
            # propio (MISMO criterio que el titular).
            universe_co = still_lead_co | lost_lead_co | stuck_co | all_conv_co
            total_co = len(universe_co)

            def pct(n):
                return round(n / total_co * 100, 1) if total_co else 0

            return {
                "companies": total_co,
                "still_lead": {"count": len(still_lead_recs), "companies": len(still_lead_co),
                                "opportunities": still_lead_rows},
                "lost_lead": {"count": len(lost_lead_recs), "companies": len(lost_lead_co),
                               "opportunities": lost_lead_rows},
                "prospect": {"count": len(buckets["prospect"]), "companies": len(bucket_co["prospect"]),
                             "opportunities": buckets["prospect"]},
                "opp": {"count": len(buckets["opp"]), "companies": len(bucket_co["opp"]),
                        "opportunities": buckets["opp"]},
                "won": {"count": len(buckets["won"]), "companies": len(bucket_co["won"]),
                        "opportunities": buckets["won"]},
                "lost_opp": {"count": len(buckets["lost"]), "companies": len(bucket_co["lost"]),
                             "opportunities": buckets["lost"]},
                "any_conv": {"companies": len(all_conv_co), "rate": pct(len(all_conv_co))},
                "adv_opp": {"companies": len(adv_co), "rate": pct(len(adv_co))},
            }

        return {
            "todos": _view(leads, stuck, converted),
            "nn": _view(_by(leads, nn_id), _by(stuck, nn_id), _by(converted, nn_id)),
            "bi": _view(_by(leads, bi_id), _by(stuck, bi_id), _by(converted, bi_id)),
        }

    def get_lead_conversion_detail(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:lead_conversion_detail", self._build_lead_conversion_detail)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Drill de "Creadas" (Creadas Legacy Parity, ver HANDOFF) ──────────
    # LAZY, endpoint propio — owner directive: "Do NOT put the full
    # enriched Creadas drill payload into the normal /comercial/data/
    # analytics response. The KPI must remain lightweight." Reusa
    # `_created_entry_events` (MISMO algoritmo que el KPI semanal de
    # `_build_analytics`) — nunca un segundo cómputo de entrada.
    #
    # UNIVERSO: a diferencia de `_build_analytics.recent` (ventana de 2
    # semanas, todo lo que el KPI necesita), acá NO hay ventana de fecha —
    # portado 1:1 del legacy (`/opp-by-month`, que tampoco la tenía): el
    # selector de Año del drill necesita el historial COMPLETO, no solo las
    # últimas 2 semanas. Incluye archivadas/perdidas (`active in [True,
    # False]`) — MISMO criterio que el KPI, "una oportunidad creada esta
    # semana y ya perdida se creó igual".
    #
    # ENRIQUECIMIENTO: UN solo batch adicional, acotado a los IDs del
    # conjunto canónico (`events`) — nunca todo el universo de crm.lead, y
    # nunca una consulta por oportunidad (owner directive — "No per-
    # opportunity Odoo calls. No N+1").
    def _build_creadas_detail(self, year: int) -> dict:
        def _empty_views() -> dict:
            return {tipo: {"months": []} for tipo in _TIPOS}

        all_stages = self._client.search_read(
            "crm.stage", [], fields=["id", "name", "sequence"], order="sequence asc"
        )
        valid_stages = sorted(
            [st for st in all_stages if _stage_first_char(st) in "12345678"],
            key=lambda st: (int(st["name"].strip()[0]), st.get("sequence", 99)),
        )
        valid_ids = [st["id"] for st in valid_stages]
        if not valid_ids:
            return _empty_views()
        stage_one_id = valid_stages[0]["id"]

        # HOT/COLD POR AÑO: el universo se acota ANTES de reconstruir nada. La
        # regla canónica (`_created_entry_events`) se aplica DESPUÉS y SIN
        # cambios sobre el historial completo de ese universo acotado — la
        # equivalencia con la versión full-history es estructural.
        opps, transitions = self._creadas_year_universe(year, valid_ids, stage_one_id)
        if not opps:
            return _empty_views()
        todos_los_eventos = self._created_entry_events(opps, transitions, stage_one_id, valid_ids)
        # Y recién acá se filtra por año. Una RE-ENTRADA queda afuera sola: su
        # evento canónico es la PRIMERA entrada, que cae en otro año.
        events = {oid: d for oid, d in todos_los_eventos.items() if d and d.year == year}
        if not events:
            return _empty_views()

        opp_by_id = {o["id"]: o for o in opps}
        enriched = self._client.search_read(
            "crm.lead",
            domain=[["id", "in", sorted(events)], ["active", "in", [True, False]]],
            fields=["id", "name", "partner_id", "user_id", "stage_id", "probability",
                     "active", "source_id", "lead_properties"],
        )
        enriched_by_id = {o["id"]: o for o in enriched}

        def _row(opp_id, entry) -> dict | None:
            rich = enriched_by_id.get(opp_id)
            if not rich:
                return None
            stage_name = rich["stage_id"][1] if rich.get("stage_id") else "Sin etapa"
            # Excluye etapa 0-Prospecto — MISMO guard que legacy: la etapa
            # ACTUAL puede haber revertido a prospecto pese a tener un
            # evento de entrada histórico.
            if stage_name.strip().startswith("0"):
                return None
            prob = rich.get("probability", 0) or 0
            active = rich.get("active", True)
            # MISMA clasificación que el legacy — nunca reinterpretada acá
            # (owner directive — "Do NOT independently reinterpret status").
            if not active and prob == 0:
                status = "Perdida"
            elif prob == 100:
                status = "Ganada"
            else:
                status = stage_name
            return {
                "id": opp_id,
                "name": rich.get("name") or "",
                "client": rich["partner_id"][1] if rich.get("partner_id") else "",
                "seller": rich["user_id"][1] if rich.get("user_id") else "",
                "stage": stage_name,
                "status": status,
                "origen": rich["source_id"][1] if rich.get("source_id") else "Sin definir",
                "entry_date": entry.isoformat(),
                "horas": get_property_num(rich.get("lead_properties"), "Horas") or None,
            }

        all_rows = [r for r in (_row(opp_id, entry) for opp_id, entry in events.items()) if r]

        def _group_by_month(rows: list) -> dict:
            by_month: dict = {}
            for r in rows:
                by_month.setdefault(r["entry_date"][:7], []).append(r)
            months = [
                {"month": month, "count": len(group), "opportunities": sorted(group, key=lambda r: r["entry_date"])}
                for month, group in by_month.items()
            ]
            months.sort(key=lambda m: m["month"], reverse=True)
            return {"months": months}

        def _partition(tag_id) -> dict:
            ids = {o["id"] for o in opps if self._in_tag(o, tag_id)}
            return _group_by_month([r for r in all_rows if r["id"] in ids])

        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        return {
            "todos": _group_by_month(all_rows),
            "nn": _partition(nn_id),
            "bi": _partition(bi_id),
        }

    def get_creadas_detail(self, tipo: str = "todos", year: int | None = None) -> dict:
        """Detalle canónico de Creadas de UN año. `year=None` resuelve al actual."""
        if year is None:
            year = date.today().year
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            # CACHE POR AÑO. La key vieja era `commercial:creadas` — una sola
            # entrada con TODO el historial. `tipo` sigue FUERA de la key: las
            # tres vistas (todos/nn/bi) se derivan del MISMO dataset anual, así
            # que duplicar por tipo serían tres reconstrucciones idénticas.
            views = _cached(f"commercial:creadas:{year}",
                            lambda: self._build_creadas_detail(year))
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Actividades pendientes ───────────────────────────────────────────
    # UNIVERSO (decisión de owner, Batch 3A): la capability representa
    # TRABAJO PENDIENTE, no el universo analítico de Oportunidades Activas.
    # Por eso NO se restringe a etapas 1–8: entra la actividad pendiente de
    # CUALQUIER `crm.lead` relevante — etapa 0, activas, ganadas, perdidas y
    # archivadas incluidas. Ocultar tareas reales para "cuadrar" con el
    # Dashboard sería una consistencia artificial. `mail.activity` sólo
    # contiene actividades PENDIENTES (Odoo borra las hechas), así que
    # "pendiente" es semántica del modelo, no un filtro que agreguemos.
    def _build_activities(self) -> dict:
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        acts = self._client.search_read(
            "mail.activity",
            domain=[["res_model", "=", "crm.lead"]],
            fields=["res_id", "date_deadline", "activity_type_id", "summary", "note", "user_id"],
            order="date_deadline asc",
        )
        # El lead se trae con `active in [True, False]` a propósito: si la
        # oportunidad fue archivada pero la actividad sigue pendiente, la
        # tarea existe igual y debe verse (ver UNIVERSO arriba). `tag_ids`
        # es lo que habilita el particionado NN/BI en memoria — el filtro
        # global aplica por los tags del LEAD asociado, no de la actividad.
        lead_ids = sorted({a["res_id"] for a in acts if a.get("res_id")})
        lead_map: dict = {}
        if lead_ids:
            leads = self._client.search_read(
                "crm.lead",
                domain=[["id", "in", lead_ids], ["active", "in", [True, False]]],
                fields=["id", "name", "partner_id", "tag_ids"],
            )
            lead_map = {lead["id"]: lead for lead in leads}

        rows = []
        for act in acts:
            lead = lead_map.get(act.get("res_id")) or {}
            rows.append({
                "opportunity": lead.get("name") or "",
                "client": lead["partner_id"][1] if lead.get("partner_id") else "",
                "user": act["user_id"][1] if act.get("user_id") else "",
                "type": act["activity_type_id"][1] if act.get("activity_type_id") else "",
                "summary": act.get("summary") or "",
                "note": strip_html_note(act.get("note")),
                "deadline": iso_deadline(act.get("date_deadline")),
                # Sólo para el particionado por tag; se descarta abajo.
                "_tag_ids": lead.get("tag_ids") or [],
            })

        def _group(subset: list) -> dict:
            # El orden por deadline lo fija Odoo (`order=`) y el
            # particionado lo preserva. Los BUCKETS (Retrasadas/Pendientes)
            # NO se calculan acá: dependen de `days_until`, que es del
            # cliente para no quedar congelado por el TTL del cache.
            return {
                "activities": [{k: v for k, v in row.items() if k != "_tag_ids"} for row in subset],
                "total": len(subset),
            }

        return {
            "todos": _group(rows),
            "nn": _group([r for r in rows if self._in_tag({"tag_ids": r["_tag_ids"]}, nn_id)]),
            "bi": _group([r for r in rows if self._in_tag({"tag_ids": r["_tag_ids"]}, bi_id)]),
        }

    def get_activities(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:activities", self._build_activities)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]
