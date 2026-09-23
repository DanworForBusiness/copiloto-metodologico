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

# ── Account Licensing Profile (ver HANDOFF — Commercial Licensing) ─────────
# A DIFERENCIA de `_get_prop_label`/`get_property_num` (arriba, resuelven
# `lead_properties` por LABEL solamente — la convención ya establecida para
# esas dos), estas SIETE properties viven en `res.partner.properties` y se
# resuelven por su NOMBRE TÉCNICO — verificado manualmente contra Odoo,
# nunca por label solo. Motivo del desvío deliberado: estas son properties
# tipadas (selection/tags/monetary/date) cuyo mal-parseo silencioso sería
# mucho más caro que el de un label simple de `lead_properties` (un monto o
# una fecha mal resueltos contaminan decisiones de negocio reales, no solo
# un rótulo visual) — así que acá el nombre técnico es la clave primaria y
# el `string`/tipo esperado son un GUARD: si el nombre técnico existe pero
# su metadata no matchea lo esperado, se trata como ausente (nunca se
# inventa un valor), ver `_resolve_partner_property`.
_LICENSING_PROPERTIES = {
    "partner_de_licencia": ("85e69c44e445398f", "Partner de Licencia", "selection"),
    "ae_sap": ("e85a4881542a93b0", "AE SAP", "selection"),
    "arquitectura_actual": ("8bcae38dc5e849eb", "Arquitectura Actual", "tags"),
    "monto_contrato": ("4495d8705b9b212c", "Monto Contrato", "monetary"),
    "vencimiento_contrato": ("c084bf66dd492576", "Vencimiento Contrato", "date"),
    "tipo_contrato_sap": ("148e42f95b7b9dc8", "Tipo de Contrato SAP", "selection"),
    "segmento_cuenta": ("dd859ad74685c28e", "Segmento de la Cuenta", "selection"),
}


def _find_partner_property(properties, technical_name: str, expected_label: str, expected_type: str):
    """`None` si la property técnica no existe O si existe pero su
    `string`/tipo no matchea lo esperado (metadata drift — ver comentario de
    arriba: fallar observable, nunca interpretar metadata incompatible).
    Nunca por posición: recorre TODA la lista buscando el `name` exacto."""
    if not isinstance(properties, list):
        return None
    for prop in properties:
        if not isinstance(prop, dict) or prop.get("name") != technical_name:
            continue
        if (prop.get("string") or "").strip() != expected_label:
            return None
        if prop.get("type") and prop.get("type") != expected_type:
            return None
        return prop
    return None


def _selection_value(properties, technical_name: str, expected_label: str) -> str | None:
    prop = _find_partner_property(properties, technical_name, expected_label, "selection")
    if not prop or not prop.get("value"):
        return None
    return next((opt[1] for opt in (prop.get("selection") or []) if opt[0] == prop["value"]), None)


def _tags_value(properties, technical_name: str, expected_label: str) -> list[str]:
    prop = _find_partner_property(properties, technical_name, expected_label, "tags")
    if not prop or not prop.get("value"):
        return []
    # `prop["tags"]` en Odoo real viene como `(key, label, color)` —
    # TRES elementos, no dos (descubierto en vivo contra Odoo real: `dict()`
    # sobre la lista cruda revienta con "sequence element has length 3").
    # Se toman sólo los primeros DOS por posición, ignorando cualquier
    # elemento extra (color u otro) — nunca se asume una longitud fija.
    tag_map = {entry[0]: entry[1] for entry in (prop.get("tags") or [])}
    return [tag_map[key] for key in prop["value"] if key in tag_map]


def _monetary_value(properties, technical_name: str, expected_label: str) -> float | None:
    prop = _find_partner_property(properties, technical_name, expected_label, "monetary")
    if not prop or prop.get("value") in (None, False, ""):
        return None
    try:
        return float(prop["value"])
    except (ValueError, TypeError):
        return None


def _date_value(properties, technical_name: str, expected_label: str) -> str | None:
    prop = _find_partner_property(properties, technical_name, expected_label, "date")
    val = prop.get("value") if prop else None
    if not val or val is True:
        return None
    try:
        return date.fromisoformat(str(val)[:10]).isoformat()
    except (ValueError, TypeError):
        return None


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

    # ── Detalle de Oportunidades (Bucket 1, ver HANDOFF) ─────────────────────
    # Universo: stages 0-8 (`_stage_first_char in "012345678"`) — recuperado
    # de `crm-comercial`/`backend/routers/crm.py::get_relevamiento`, llamado
    # por el legacy tab "Detalle Oportunidades" con `stage_ids=<0-8>` (TODAS
    # las etapas cuyo nombre matchea `/^[0-8]/`, previa consulta a
    # `/crm/stages`) — EL MISMO universo "0-8" que ya usa Forecast acá arriba
    # (`_build_forecast`, ver su propio comentario: "Forecast agrupa... sobre
    # TODO el pipeline, prospectos incluidos" — semántica legacy INTENCIONAL,
    # no un bug, y NO la de Activas — que es sólo 1-8, `_build_opportunities`
    # — así que se reusa el MISMO patrón de filtro de `_build_forecast`, no
    # el de Activas). `active=True` explícito, MISMO criterio que
    # Activas/Prospectos/Suspendidas — excluye Perdidas (`active=False`) a
    # propósito: el legacy nunca las trae acá (`get_relevamiento` siempre
    # tiene `active=True` en su dominio, sea cual sea la rama de stage_id/
    # stage_ids), Perdidas vive en su propio universo (Historial Perdidas,
    # ver más abajo).
    #
    # LICENCIAS (ver HANDOFF §3/§4) — auditado línea por línea contra
    # `frontend/js/dashboard.js::fetchAllActiveOpps/loadLicenciasTab`: pide
    # `/crm/relevamiento?stage_id=0&tag_id=...` (sentinel "sin stage_id" —
    # `stage_id=0` no filtra por la etapa 0, cae al branch `else` del backend
    # que trae TODO el 0-8 sin restricción) y filtra CLIENT-SIDE
    # `.filter(o => o.licencias === "Si")`. Es decir: Licencias y Detalle
    # Oportunidades usan el MISMO endpoint/dominio en el legacy — la única
    # diferencia es un filtro posterior, nunca una consulta separada. Por
    # eso comparten ESTE MISMO método/endpoint acá — no porque se haya
    # decidido fusionarlos por conveniencia, sino porque el propio legacy ya
    # los trata como la MISMA fuente. La hipótesis "Licencias incluye
    # Perdidas" NO se sostiene: `get_relevamiento` siempre exige
    # `active=True`, sea cual sea la rama de stage — así que Licencias NUNCA
    # incluyó oportunidades perdidas en el legacy alcanzable.
    #
    # Fila = MISMA fórmula que `_build_suspendidas_detail`/`_build_opportunities`
    # (Hs Pond./Últ. Resp./Días resp./actividades, ya auditadas y en
    # producción) — CON "stage"/"tags" (multi-etapa genuino, MISMO criterio
    # que Suspendidas) y SIN "source"/"revenue": el legacy `renderTable`
    # (compartido por Licencias Y Detalle en el propio `dashboard.js`) nunca
    # imprime esas dos columnas para este contrato — no son un dead field
    # accidental, es que el contrato real no las tiene.
    def _build_opportunities_detail(self) -> dict:
        all_stages = self._client.search_read("crm.stage", [], fields=["id", "name"])
        stage_ids = [s["id"] for s in all_stages if _stage_first_char(s) in "012345678"]
        if not stage_ids:
            return {tipo: {"opportunities": [], "total": 0} for tipo in _TIPOS}
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        opps = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", True], ["stage_id", "in", stage_ids]],
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

        # `account_id` (Account Licensing Profile, ver HANDOFF): grano
        # canónico de cuenta (`commercial_partner_id`) — MISMO colapso
        # contacto→empresa que Clientes/Ex Clientes, resuelto acá con UN
        # batch read adicional (nunca N+1). Aditivo: Detalle Oportunidades
        # sigue mostrando `client` (nombre crudo) tal cual — este campo
        # nuevo sólo lo consume el cross-filter de Licencias.
        raw_partner_ids = {o["partner_id"][0] for o in opps if o.get("partner_id")}
        account_id_by_partner: dict[int, int] = {}
        if raw_partner_ids:
            for p in self._client.search_read(
                "res.partner", domain=[["id", "in", list(raw_partner_ids)]],
                fields=["id", "commercial_partner_id"],
            ):
                account_id_by_partner[p["id"]] = (
                    p["commercial_partner_id"][0] if p.get("commercial_partner_id") else p["id"]
                )

        today = date.today()

        def _row(opp: dict) -> dict:
            props = opp.get("lead_properties") or []
            hours_raw = get_property_num(props, "Horas")
            weighted_hours = round((opp.get("probability", 0) or 0) / 100 * hours_raw, 1)
            last_response = _get_property_date(props, _PROP_ULTIMA_RESPUESTA)
            days_elapsed = (today - date.fromisoformat(last_response)).days if last_response else None
            raw_pid = opp["partner_id"][0] if opp.get("partner_id") else None
            return {
                "opportunity": opp.get("name") or "",
                "client": opp["partner_id"][1] if opp.get("partner_id") else "",
                "account_id": account_id_by_partner.get(raw_pid) if raw_pid else None,
                "seller": opp["user_id"][1] if opp.get("user_id") else "",
                "stage": opp["stage_id"][1] if opp.get("stage_id") else "",
                "tags": [tag_names[tid] for tid in (opp.get("tag_ids") or []) if tid in tag_names],
                "licencias": _get_prop_label(props, "Licencias"),
                "hours": hours_raw,
                "weighted_hours": weighted_hours,
                # Ingreso esperado (ver HANDOFF — Commercial Licensing):
                # trivial ahora que `expected_revenue` ya viaja en el fetch
                # de arriba — SÓLO Licencias lo muestra (owner decisión),
                # pero el dato viaja para las dos tabs, MISMO criterio que
                # `weighted_hours` (calculado siempre, mostrado donde aplica).
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

    def get_opportunities_detail(self, tipo: str = "todos") -> dict:
        """Universo canónico compartido por "Detalle Oportunidades" Y
        "Licencias" (ver comentario de `_build_opportunities_detail` — el
        legacy ya los trata como la MISMA fuente, filtrada distinto en cada
        tab). UN solo endpoint/cache — Licencias filtra `licencias === "Si"`
        client-side, cero fetch propio."""
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:opportunities-detail", self._build_opportunities_detail)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Historial de Perdidas (Bucket 1, ver HANDOFF) ────────────────────────
    # Universo: `type=opportunity, active=False, probability=0` — MISMO
    # dominio crudo que ya usan `_build_summary`/`_build_perdidas_detail`
    # para Perdidas — recuperado de `crm-comercial`/
    # `backend/routers/crm.py::get_lost_history`. Reusa LITERAL
    # `_resolve_lost_motivos`/`_filter_excluded_lost` (nunca una segunda
    # implementación del cascade de motivo ni de la exclusión administrativa
    # — eso rompería la conservación KPI↔Historial en silencio).
    #
    # DIFERENCIA DELIBERADA con `_build_perdidas_detail` (preservada a
    # propósito, ver HANDOFF §5): ESE método filtra `date_closed` en el
    # DOMINIO de Odoo (`>= year_start, <= year_end`) porque es año-scoped por
    # diseño (un año a la vez, con cache propio). Historial necesita
    # exactamente lo opuesto — TODOS los años a la vez, MENOS el actual — y
    # el legacy es explícito sobre por qué eso exige traer TODO sin filtro
    # de fecha y resolver el año en Python: los operadores de comparación de
    # fecha de Odoo EXCLUYEN silenciosamente los registros con
    # `date_closed` nulo/False, así que un dominio con `>=`/`<=` perdería
    # esas filas para siempre. Se preserva ese comportamiento 1:1: sin
    # cláusula de fecha en el dominio, exclusión de año actual en Python, y
    # "Sin fecha" es un bucket de historial VÁLIDO (nunca se descarta).
    #
    # Fila: SIN "stage"/"revenue" — MISMO criterio "sin dead fields" que ya
    # aplica `_build_perdidas_detail` (ninguna de las dos existe en el
    # contrato real que el legacy `renderLostHistoryTable` imprime).
    def _build_lost_history(self) -> dict:
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        lost_raw = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", False], ["probability", "=", 0]],
            fields=["name", "partner_id", "user_id", "date_closed", "lead_properties", "tag_ids"],
        )

        current_year = date.today().year

        def _is_history(opp: dict) -> bool:
            dc = (opp.get("date_closed") or "")[:4]
            if dc.isdigit() and int(dc) >= current_year:
                return False  # año actual → vive en el dashboard principal (Perdidas)
            return True  # sin fecha, o año anterior → historial

        lost_raw = [o for o in lost_raw if _is_history(o)]

        lost_motivos = self._resolve_lost_motivos(lost_raw)
        opps = self._filter_excluded_lost(lost_raw, lost_motivos)

        def _row(opp: dict) -> dict:
            dc = (opp.get("date_closed") or "")[:10]
            return {
                "opportunity": opp.get("name") or "",
                "client": opp["partner_id"][1] if opp.get("partner_id") else "",
                "seller": opp["user_id"][1] if opp.get("user_id") else "",
                "date_closed": dc,
                "year": dc[:4] if dc else "Sin fecha",
                "motivo": lost_motivos.get(opp["id"]) or "Sin definir",
                "hours": get_property_num(opp.get("lead_properties"), "Horas") or None,
            }

        def _partition(subset: list) -> dict:
            return {"opportunities": [_row(o) for o in subset], "total": len(subset)}

        return {
            "todos": _partition(opps),
            "nn": _partition([o for o in opps if self._in_tag(o, nn_id)]),
            "bi": _partition([o for o in opps if self._in_tag(o, bi_id)]),
        }

    def get_lost_history(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:lost-history", self._build_lost_history)
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
    # Clasificación de madurez para Pipeline Forecast — MODELO A / "Pipeline
    # Maturity Forecast" (owner GO final, ver HANDOFF — semantic audit):
    # PROSPECTO/ACTIVA es PURA ETAPA, exactamente el MISMO criterio que ya
    # gobierna los tiles "Prospectos"/"Oportunidades Activas" del Dashboard
    # (`_stage_partition`, arriba en este mismo archivo) — reusado tal cual
    # vía `_stage_first_char`, nunca reimplementado. `res.partner`/"Es
    # Cliente?" NO participa acá: un modelo anterior (revertido, ver git
    # history) mezclaba etapa y relación de cuenta — dos dimensiones
    # ortogonales del CRM (auditado con datos reales: "Base Instalada" ≈
    # 98% Cliente pero "New Name" NO correlaciona con "No" explícito en
    # absoluto) — y esa mezcla rompía la reconciliación con el Dashboard en
    # cuanto existiera una cuenta "No" con pipeline fuera de la etapa 0. La
    # property de cuenta sigue siendo la fuente correcta para Clientes/Ex-
    # Clientes en OTRAS superficies del CRM (`_partner_ids_by_cliente_value`,
    # sin tocar) — simplemente no es la dimensión de ESTE forecast.
    _FORECAST_CLASS_PROSPECTO = "PROSPECTO"
    _FORECAST_CLASS_ACTIVA = "ACTIVA"

    def _classify_opportunity_maturity(self, opp: dict) -> str:
        """Etapa "0-..." -> PROSPECTO, cualquier otra etapa del universo
        0-8 -> ACTIVA. `_stage_first_char` espera un dict con "name" — acá
        sólo se tiene el nombre de etapa ya resuelto en la propia
        oportunidad (`stage_id` es `[id, display_name]`), así que se envuelve
        en un dict de un campo para reusar la MISMA función que
        `_stage_partition`, en vez de reimplementar el chequeo del primer
        carácter."""
        stage_name = opp["stage_id"][1] if opp.get("stage_id") else ""
        if _stage_first_char({"name": stage_name}) == "0":
            return self._FORECAST_CLASS_PROSPECTO
        return self._FORECAST_CLASS_ACTIVA

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
                classification = self._classify_opportunity_maturity(opp)
                bucket["opportunities"].append({
                    "opportunity": opp.get("name") or "",
                    "client": opp["partner_id"][1] if opp.get("partner_id") else "",
                    "seller": opp["user_id"][1] if opp.get("user_id") else "",
                    "stage": opp["stage_id"][1] if opp.get("stage_id") else "",
                    "deadline": dd,
                    "revenue": revenue,
                    "hours": hours,
                    "probability": opp.get("probability"),
                    "classification": classification,
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
                    "stage": _stage_of(o),
                    # `create_date` es datetime en Odoo ("YYYY-MM-DD HH:MM:SS")
                    # — `commercialFmtDate` (frontend) asume fecha pura y
                    # parte por "-", así que sin truncar acá la hora quedaba
                    # DENTRO del tercer segmento y el resultado salía
                    # ilegible (owner corrective, ver HANDOFF — captura en
                    # vivo de "Edad del Pipeline"). MISMO patrón `[:10]` ya
                    # usado en el resto del archivo (`convert_date`, etc.).
                    "create_date": (o.get("create_date") or "")[:10] or None,
                    "age_days": r["age_days"],
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
    # desglose completo por resultado + la atribución UTM — antes sólo
    # existían "cualquier conversión"/"avanzó a oportunidad".
    #
    # OWNER DECISION (corrective post-trust-audit, ver HANDOFF): "Ganada"/
    # "Perdida"/"Archivada"/"Activa" usan la regla PROPIA del legacy
    # `/lead-conversion` (`probability==100` / `active=False+probability=0`
    # / `active=False` / resto) — NUNCA `stage_id.is_won` (el intento
    # anterior, revertido). El legacy usa DOS definiciones de "ganada"
    # distintas a propósito en dos features distintas (`/summary`'s
    # Ganadas KPI = is_won; `/lead-conversion` = probability==100) — Lead
    # Conversion preserva la SUYA, no la de Ganadas/Perdidas.
    #
    # RUTEO A BUCKET — MISMA prioridad que legacy: la etapa 0 (Prospecto)
    # SIEMPRE gana el bucket, sea cual sea el resultado calculado (incluso
    # una convertida con probability=100 que sigue en etapa 0 cae en
    # "prospect", no en "won" — quirk real del legacy, preservado a
    # propósito). Fuera de eso, Ganada→"won", Perdida→"lost", Activa Y
    # Archivada COMPARTEN el bucket "opp" (MISMO agrupamiento que
    # `conv_to_opp` en el legacy — Archivada nunca tuvo bucket propio ahí).
    # El campo `outcome` de cada fila SÍ distingue Activa de Archivada — es
    # una etiqueta nueva de ESTA UI (el legacy no la muestra por fila, sólo
    # por pestaña), así que "prospect" muestra `outcome="Prospecto"` para
    # que la tabla combinada sea legible (decisión de UI propia, no
    # reinterpretación del ruteo).
    #
    # ATRIBUCIÓN — Campaña→Medio→Origen (owner §4/§9): MISMOS campos que el
    # legacy (`campaign_id`/`medium_id`/`source_id`, ya vienen con nombre
    # resuelto en el many2one — CERO consultas extra), MISMO universo
    # (`all_conv` — las CUATRO poblaciones convertidas, nunca still_lead/
    # lost_lead), MISMA medida (conteo crudo por nodo, sin empresas/%), y
    # MISMOS placeholders de ausencia ("Sin campaña"/"Sin medio"/"Sin
    # origen") aplicados SÓLO al agregar — la fila cruda guarda "" si falta
    # (MISMO criterio que el legacy: los selects de filtro excluyen los
    # valores vacíos de sus opciones vía `.filter(Boolean)`, nunca ofrecen
    # un "(Sin definir)" que el legacy tampoco ofrece).
    def _build_lead_conversion_detail(self) -> dict:
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None

        all_stages = self._client.search_read("crm.stage", [], fields=["id", "name"])
        lead_stage_ids = [st["id"] for st in all_stages if (st.get("name") or "").strip().lower() == "lead"]
        prospect_ids = {st["id"] for st in all_stages if _stage_first_char(st) == "0"}

        fields = ["id", "name", "partner_id", "partner_name", "user_id", "stage_id", "active",
                  "probability", "create_date", "tag_ids", "campaign_id", "medium_id", "source_id"]

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

        def _m2o_name(rec, field):
            val = rec.get(field)
            return val[1] if isinstance(val, (list, tuple)) else ""

        def _row(rec, extra=None) -> dict:
            row = {
                "opportunity": rec.get("name") or "", "client": _co_key(rec)[1],
                "seller": rec["user_id"][1] if rec.get("user_id") else "",
                # MISMO fix que `pipeline_age` (ver HANDOFF): `create_date`
                # es datetime en Odoo, `commercialFmtDate` asume fecha pura.
                # No se muestra hoy en la tabla combinada, pero SÍ viaja en
                # el payload (búsqueda por substring sobre todos los campos)
                # — truncar acá evita el mismo landmine si algún día se
                # muestra, y mantiene el campo consistente con `convert_date`.
                "create_date": (rec.get("create_date") or "")[:10] or None,
                "campana": _m2o_name(rec, "campaign_id"),
                "medio": _m2o_name(rec, "medium_id"),
                "origen": _m2o_name(rec, "source_id"),
            }
            if extra:
                row.update(extra)
            return row

        # Regla PROPIA de Lead Conversion (owner decision) — legacy
        # `get_lead_conversion`, `outcome = ...`. NUNCA is_won.
        def _outcome(rec) -> str:
            prob = rec.get("probability", 0) or 0
            active = rec.get("active", True)
            if prob == 100:
                return "Ganada"
            if not active and prob == 0:
                return "Perdida"
            if not active:
                return "Archivada"
            return "Activa"

        def _bucket_for(rec, outcome) -> str:
            sid = rec["stage_id"][0] if isinstance(rec.get("stage_id"), (list, tuple)) else rec.get("stage_id")
            if sid in prospect_ids:
                return "prospect"
            if outcome == "Ganada":
                return "won"
            if outcome == "Perdida":
                return "lost"
            return "opp"  # Activa Y Archivada comparten bucket — MISMO que legacy conv_to_opp

        def _by(subset, tag_id):
            return [o for o in subset if self._in_tag(o, tag_id)]

        def _attribution(rows: list) -> list:
            tree: dict = {}
            for r in rows:
                ca = r["campana"] or "Sin campaña"
                me = r["medio"] or "Sin medio"
                orig = r["origen"] or "Sin origen"
                tree.setdefault(ca, {}).setdefault(me, {}).setdefault(orig, 0)
                tree[ca][me][orig] += 1
            out = []
            for ca, medios in sorted(tree.items(), key=lambda kv: -sum(sum(v.values()) for v in kv[1].values())):
                ca_count = sum(sum(v.values()) for v in medios.values())
                medio_list = []
                for me, origs in sorted(medios.items(), key=lambda kv: -sum(kv[1].values())):
                    me_count = sum(origs.values())
                    origen_list = [{"origen": o, "count": c}
                                   for o, c in sorted(origs.items(), key=lambda kv: -kv[1])]
                    medio_list.append({"medio": me, "count": me_count, "origenes": origen_list})
                out.append({"campana": ca, "count": ca_count, "medios": medio_list})
            return out

        def _view(sub_leads, sub_stuck, sub_conv) -> dict:
            still_lead_recs = [r for r in sub_leads if r.get("active", True)]
            lost_lead_recs = [r for r in sub_leads if not r.get("active", True)]

            buckets: dict = {"prospect": [], "opp": [], "won": [], "lost": []}
            bucket_co: dict = {"prospect": set(), "opp": set(), "won": set(), "lost": set()}
            all_conv_rows: list = []
            for rec in sub_conv:
                outcome = _outcome(rec)
                b = _bucket_for(rec, outcome)
                stage_name = rec["stage_id"][1] if rec.get("stage_id") else ""
                row = _row(rec, {
                    "stage": stage_name,
                    "outcome": "Prospecto" if b == "prospect" else outcome,
                    "convert_date": (conversion_dates.get(rec["id"], "") or "")[:10] or None,
                })
                buckets[b].append(row)
                bucket_co[b].add(_co_key(rec))
                all_conv_rows.append(row)

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

            # `total` — owner corrective (mini KPI tiles, ver HANDOFF §2):
            # MISMA fórmula que el legacy (`total = n_still_lead + n_lost_lead
            # + n_any_conv`, TODOS conteos crudos, nunca empresas) — la
            # cabecera de "Total leads" usa este campo, NUNCA `companies`
            # (que es la línea SECUNDARIA "empresas únicas · base para los
            # %"). Suma de conteos YA calculados — cero query nueva.
            total_raw = (len(still_lead_recs) + len(lost_lead_recs) + len(buckets["prospect"])
                         + len(buckets["opp"]) + len(buckets["won"]) + len(buckets["lost"]))

            return {
                "companies": total_co,
                "total": total_raw,
                "still_lead": {"count": len(still_lead_recs), "companies": len(still_lead_co),
                                "opportunities": still_lead_rows},
                "lost_lead": {"count": len(lost_lead_recs), "companies": len(lost_lead_co),
                               "opportunities": lost_lead_rows},
                "prospect": {"count": len(buckets["prospect"]), "companies": len(bucket_co["prospect"]),
                             "opportunities": buckets["prospect"]},
                "opp": {"count": len(buckets["opp"]), "companies": len(bucket_co["opp"]),
                        "opportunities": buckets["opp"]},
                # `rate` en won/lost_opp — owner corrective (mini KPI tiles):
                # el legacy también muestra "Ganadas"/"Perdidas" con %, MISMO
                # denominador `total_co` que any_conv/adv_opp (`pct()`, ya
                # usada acá) — no era necesario para el detalle por fila,
                # pero SÍ para la tarjeta.
                "won": {"count": len(buckets["won"]), "companies": len(bucket_co["won"]), "rate": pct(len(bucket_co["won"])),
                        "opportunities": buckets["won"]},
                "lost_opp": {"count": len(buckets["lost"]), "companies": len(bucket_co["lost"]), "rate": pct(len(bucket_co["lost"])),
                             "opportunities": buckets["lost"]},
                "any_conv": {"companies": len(all_conv_co), "rate": pct(len(all_conv_co))},
                "adv_opp": {"companies": len(adv_co), "rate": pct(len(adv_co))},
                "attribution": _attribution(all_conv_rows),
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

    # ── Bucket 2 — Leads (ver HANDOFF) ───────────────────────────────────────
    # Universo: `type=lead, active=True` — recuperado de
    # `crm-comercial`/`backend/routers/crm.py::get_leads`. NUNCA
    # `type=opportunity` (ese universo es Detalle Oportunidades/Prospectos/
    # Licencias, ya cerrados) — un lead convertido cambia de `type` a
    # "opportunity" y por eso desaparece SOLO de acá, sin ningún filtro
    # adicional: `type=lead` ya lo excluye. Un lead perdido (marcado
    # perdido sin convertir) queda `active=False`, así que también sale
    # del dominio — MISMO criterio "activo" que el resto de Comercial.
    #
    # Agrupado por EMPRESA — `partner_id` si existe, si no el texto libre
    # `partner_name` (el legacy nunca fuerza un partner en un lead sin
    # vincular), con "Sin empresa" como fallback final y SIEMPRE última en
    # el orden (nunca alfabético con el resto). Fila = SOLO los campos que
    # el legacy realmente imprime en `renderLeadsTable` (lead/contact/
    # seller/email/create_date) — `phone`/`stage`/`revenue`/`priority` se
    # piden en el legacy pero NUNCA se muestran (dead fields, mismo
    # criterio "no se portan" ya aplicado a Perdidas/Historial) — así que
    # acá ni se piden.
    #
    # Tipo: filtra CADA lead por su propio `tag_ids` (MISMO criterio que
    # el resto de Comercial — partition post-fetch, nunca un segundo
    # dominio Odoo por tipo) — una empresa con leads de varios segmentos
    # sólo muestra los que matchean el tipo pedido, y desaparece del todo
    # si ninguno matchea. El legacy resuelve esto distinto (un `tag_id`
    # server-side por request), pero el resultado observable es el mismo:
    # "los leads de este segmento, agrupados por empresa".
    def _build_leads(self) -> dict:
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        leads = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "lead"], ["active", "=", True]],
            fields=["name", "partner_name", "contact_name", "partner_id", "user_id",
                     "email_from", "create_date", "tag_ids"],
            order="partner_name asc, name asc",
        )
        companies: dict = {}
        for lead in leads:
            if lead.get("partner_id"):
                pid, pname = lead["partner_id"][0], lead["partner_id"][1]
            else:
                pid, pname = 0, lead.get("partner_name") or "Sin empresa"
            key = (pid, pname)
            if key not in companies:
                companies[key] = {"id": pid, "name": pname, "leads": []}
            companies[key]["leads"].append({
                "lead": lead.get("name") or "",
                "contact": lead.get("contact_name") or "",
                "seller": lead["user_id"][1] if lead.get("user_id") else "",
                "email": lead.get("email_from") or "",
                "create_date": (lead.get("create_date") or "")[:10],
                "_tag_ids": lead.get("tag_ids") or [],
            })

        def _partition(tag_id) -> dict:
            result = []
            for c in companies.values():
                rows = c["leads"] if tag_id is None else [l for l in c["leads"] if tag_id in l["_tag_ids"]]
                if not rows:
                    continue
                result.append({
                    "id": c["id"], "name": c["name"],
                    "leads": [{k: v for k, v in l.items() if k != "_tag_ids"} for l in rows],
                })
            result.sort(key=lambda c: (c["name"].lower() if c["name"] != "Sin empresa" else "zzz"))
            total_leads = sum(len(c["leads"]) for c in result)
            return {"companies": result, "total_companies": len(result), "total_leads": total_leads}

        return {"todos": _partition(None), "nn": _partition(nn_id), "bi": _partition(bi_id)}

    def get_leads(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:leads", self._build_leads)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Bucket 2 — Clientes / Ex Clientes (ver HANDOFF) ──────────────────────
    # "Cliente" NO es una noción derivada de crm.lead — es una property
    # PROPIA de `res.partner` ("Es Cliente?" = "Si"/"Ex", una selection
    # property configurable en Odoo, resuelta por LABEL — nunca por
    # posición, exactamente como `lead_properties` en `crm.lead`, pero acá
    # sobre el modelo de partner). Recuperado 1:1 de
    # `crm-comercial`/`backend/routers/crm.py::get_clients`/`get_ex_clients`
    # (`_get_cliente_partner_ids`/`_get_ex_cliente_partner_ids`/
    # `_expand_partner_ids_with_contacts`/`_resolve_opp_partner`).
    #
    # Contactos (personas) se COLAPSAN a su empresa vía
    # `commercial_partner_id` — una oportunidad vinculada a un CONTACTO
    # cuenta para la EMPRESA, nunca aparece como una fila propia. Identidad
    # ESTABLE por `partner_id` (nunca por nombre de display — dos partners
    # con el mismo nombre quedan DISTINTOS).
    #
    # FALLBACK deliberado del legacy (preservado 1:1, nunca "simplificado"
    # a un solo camino): si la property "Es Cliente?" no existe en esta
    # instancia de Odoo (`_partner_ids_by_cliente_value` devuelve `None`,
    # no `[]` — la ausencia de la property es un caso DISTINTO de "nadie
    # matchea"), el universo cae a TODAS las oportunidades activas
    # (mismo patrón fail-soft que el resto de Comercial ante un dato no
    # configurado, nunca una lista vacía disfrazada de "sin clientes").
    _CLIENTE_PROP_LABELS = {"es cliente?", "es cliente", "¿es cliente?", "¿es cliente"}
    _CLIENTE_SI_LABELS = {"si", "sí", "yes", "true"}
    _CLIENTE_EX_LABELS = {"ex", "ex cliente", "ex-cliente", "ex clientes", "ex-clientes", "excliente", "exclientes"}

    def _partner_ids_by_cliente_value(self, target_labels: set) -> list | None:
        """`None` si la property "Es Cliente?" no existe en esta instancia
        (el caller debe caer al fallback de dominio completo) — `[]` si la
        property SÍ existe pero ningún partner matchea `target_labels`
        (universo legítimamente vacío). Dos resultados observables
        distintos por diseño, MISMO criterio que el legacy."""
        all_partners = self._client.search_read("res.partner", domain=[], fields=["id", "properties"])
        prop_internal_name = None
        target_key = None
        for p in all_partners:
            props = p.get("properties")
            if not isinstance(props, list):
                continue
            for prop in props:
                label = (prop.get("string") or "").strip().lower()
                if label not in self._CLIENTE_PROP_LABELS:
                    continue
                prop_internal_name = prop.get("name")
                for key, display in (prop.get("selection") or []):
                    if (display or "").strip().lower() in target_labels:
                        target_key = key
                        break
                break
            if prop_internal_name:
                break
        if not prop_internal_name or not target_key:
            return None
        result = []
        for p in all_partners:
            props = p.get("properties")
            if not isinstance(props, list):
                continue
            for prop in props:
                if prop.get("name") == prop_internal_name and prop.get("value") == target_key:
                    result.append(p["id"])
                    break
        return result

    def _expand_partner_ids_with_contacts(self, company_ids: list) -> tuple[list, dict, dict]:
        if not company_ids:
            return [], {}, {}
        child_partners = self._client.search_read(
            "res.partner",
            domain=[["commercial_partner_id", "in", company_ids], ["id", "not in", company_ids]],
            fields=["id", "commercial_partner_id"],
        )
        contact_to_company = {
            p["id"]: p["commercial_partner_id"][0] for p in child_partners if p.get("commercial_partner_id")
        }
        all_ids = company_ids + list(contact_to_company.keys())
        company_ids_needed = set(contact_to_company.values())
        company_names: dict = {}
        if company_ids_needed:
            recs = self._client.search_read(
                "res.partner", domain=[["id", "in", list(company_ids_needed)]], fields=["id", "name"]
            )
            company_names = {r["id"]: r["name"] for r in recs}
        return all_ids, contact_to_company, company_names

    @staticmethod
    def _resolve_opp_partner(opp: dict, contact_to_company: dict, company_names: dict) -> tuple:
        if opp.get("partner_id"):
            raw_pid, raw_pname = opp["partner_id"][0], opp["partner_id"][1]
            if raw_pid in contact_to_company:
                pid = contact_to_company[raw_pid]
                pname = company_names.get(pid, raw_pname)
            else:
                pid, pname = raw_pid, raw_pname
        else:
            pid, pname = 0, "Sin cliente"
        return pid, pname

    def _client_opp_row(self, opp: dict) -> dict:
        props = opp.get("lead_properties") or []
        return {
            "opportunity": opp.get("name") or "",
            "seller": opp["user_id"][1] if opp.get("user_id") else "",
            "stage": opp["stage_id"][1] if opp.get("stage_id") else "",
            "probability": opp.get("probability"),
            "hours": get_property_num(props, "Horas"),
            "_tag_ids": opp.get("tag_ids") or [],
        }

    def _build_clients(self) -> dict:
        valid_ids, prospect_ids, _suspended_ids = self._stage_partition()
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        cliente_ids = self._partner_ids_by_cliente_value(self._CLIENTE_SI_LABELS)
        if cliente_ids is not None:
            all_partner_ids, contact_to_company, company_names = self._expand_partner_ids_with_contacts(cliente_ids)
            partner_domain = [["partner_id", "in", all_partner_ids]] if all_partner_ids else [["id", "=", -1]]
        else:
            contact_to_company, company_names = {}, {}
            partner_domain = []

        opps = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", True]] + partner_domain,
            fields=["id", "name", "partner_id", "user_id", "stage_id", "tag_ids",
                     "probability", "lead_properties"],
            order="partner_id asc, name asc",
        )
        clients: dict = {}
        for opp in opps:
            pid, pname = self._resolve_opp_partner(opp, contact_to_company, company_names)
            sid = opp["stage_id"][0] if opp.get("stage_id") else None
            if pid not in clients:
                clients[pid] = {"id": pid, "name": pname, "active": [], "prospects": [],
                                 "_sellers": set(), "_has_licencias": False}
            rec = self._client_opp_row(opp)
            if rec["seller"]:
                clients[pid]["_sellers"].add(rec["seller"])
            props = opp.get("lead_properties") or []
            if _get_prop_label(props, "Licencias") == "Si":
                clients[pid]["_has_licencias"] = True
            if sid in valid_ids:
                clients[pid]["active"].append(rec)
            elif sid in prospect_ids:
                clients[pid]["prospects"].append(rec)

        # Partners marcados "Cliente" SIN ninguna oportunidad abierta —
        # igual aparecen en la lista (0 activas, 0 prospectos). Sólo en el
        # camino primario: el fallback no tiene una lista de partners
        # "Cliente" propia de la que tomar estos huérfanos.
        if cliente_ids is not None:
            missing_ids = [pid for pid in cliente_ids if pid not in clients and pid != 0]
            if missing_ids:
                for p in self._client.search_read("res.partner", domain=[["id", "in", missing_ids]], fields=["id", "name"]):
                    clients[p["id"]] = {"id": p["id"], "name": p["name"], "active": [], "prospects": [],
                                          "_sellers": set(), "_has_licencias": False}

        def _finalize(subset: dict) -> dict:
            result = []
            for c in subset.values():
                result.append({
                    "id": c["id"], "name": c["name"],
                    "active": [{k: v for k, v in r.items() if k != "_tag_ids"} for r in c["active"]],
                    "prospects": [{k: v for k, v in r.items() if k != "_tag_ids"} for r in c["prospects"]],
                    "sellers": sorted(c["_sellers"]), "has_licencias": c["_has_licencias"],
                })
            result.sort(key=lambda c: (-len(c["active"]), -len(c["prospects"]), c["name"].lower()))
            return {"clients": result, "total": len(result)}

        # Tipo (ver HANDOFF §13 — PROBADO desde el propio legacy, no
        # inventado): en el camino PRIMARIO (property resuelta), `tag_id`
        # está LITERALMENTE sin usar en `get_clients` — "Cliente" es una
        # clasificación a nivel EMPRESA, independiente del segmento NN/BI
        # de sus oportunidades. `todos`/`nn`/`bi` devuelven el MISMO
        # universo completo. Sólo el camino de FALLBACK (dominio sin
        # `partner_id`, universo = TODAS las oportunidades activas)
        # necesita algo que filtrar por Tipo — ahí SÍ se particiona cada
        # cliente por sus propias oportunidades con ese tag (MISMO criterio
        # de filtrado por-fila que Leads), preservando los partners
        # "Cliente" sin oportunidades sin importar el tipo (no son
        # oportunidades, no tienen tag que filtrar).
        if cliente_ids is not None:
            todos_view = _finalize(clients)
            return {"todos": todos_view, "nn": todos_view, "bi": todos_view}

        def _tag_filtered(tag_id) -> dict:
            if tag_id is None:
                return _finalize(clients)
            subset = {}
            for pid, c in clients.items():
                active = [r for r in c["active"] if tag_id in r["_tag_ids"]]
                prospects = [r for r in c["prospects"] if tag_id in r["_tag_ids"]]
                if not active and not prospects:
                    continue
                subset[pid] = {**c, "active": active, "prospects": prospects}
            return _finalize(subset)

        return {"todos": _finalize(clients), "nn": _tag_filtered(nn_id), "bi": _tag_filtered(bi_id)}

    def get_clients(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:clients", self._build_clients)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # Ex Clientes — MISMA mecánica que Clientes, filtrando "Es Cliente?" =
    # "Ex" (`_CLIENTE_EX_LABELS`) en vez de "Si". DOS diferencias reales
    # sobre Clientes (owner decisión del propio legacy, preservadas 1:1):
    #   1. TAMBIÉN trae oportunidades INACTIVAS (`active=False` —
    #      ganadas/perdidas), en un TERCER bucket "history" — un ex
    #      cliente sin nada activo hoy puede tener historial real.
    #   2. Orden simple alfabético (`name.lower()`), NUNCA por cantidad de
    #      oportunidades — un ex cliente casi por definición tiene poco o
    #      nada abierto, así que ordenar por ese conteo sería casi
    #      constante.
    # SIN fallback a "todas las oportunidades" si la property no resuelve
    # (a diferencia de Clientes): sin la property no hay forma de saber
    # quién es "Ex" — universo vacío, no una lista completa mal
    # etiquetada.
    def _build_ex_clients(self) -> dict:
        valid_ids, prospect_ids, _suspended_ids = self._stage_partition()
        nn_tag, bi_tag = self._nn_bi_tags()
        nn_id = nn_tag["id"] if nn_tag else None
        bi_id = bi_tag["id"] if bi_tag else None
        ex_ids = self._partner_ids_by_cliente_value(self._CLIENTE_EX_LABELS) or []
        if not ex_ids:
            empty = {"clients": [], "total": 0}
            return {"todos": empty, "nn": empty, "bi": empty}

        all_ex_ids, contact_to_company, company_names = self._expand_partner_ids_with_contacts(ex_ids)
        partner_domain = [["partner_id", "in", all_ex_ids]] if all_ex_ids else [["id", "=", -1]]

        all_active = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", True]] + partner_domain,
            fields=["id", "name", "partner_id", "user_id", "stage_id", "tag_ids",
                     "probability", "lead_properties"],
            order="partner_id asc, name asc",
        )
        all_inactive = self._client.search_read(
            "crm.lead",
            domain=[["type", "=", "opportunity"], ["active", "=", False]] + partner_domain,
            fields=["id", "name", "partner_id", "user_id", "stage_id", "tag_ids",
                     "probability", "lead_properties"],
            order="partner_id asc, name asc",
        )

        clients: dict = {}

        def _add(opp: dict, bucket: str):
            pid, pname = self._resolve_opp_partner(opp, contact_to_company, company_names)
            if pid not in clients:
                clients[pid] = {"id": pid, "name": pname, "active": [], "prospects": [], "history": [],
                                 "_sellers": set(), "_has_licencias": False}
            rec = self._client_opp_row(opp)
            if rec["seller"]:
                clients[pid]["_sellers"].add(rec["seller"])
            props = opp.get("lead_properties") or []
            if _get_prop_label(props, "Licencias") == "Si":
                clients[pid]["_has_licencias"] = True
            clients[pid][bucket].append(rec)

        for opp in all_active:
            sid = opp["stage_id"][0] if opp.get("stage_id") else None
            bucket = "active" if sid in valid_ids else ("prospects" if sid in prospect_ids else "history")
            _add(opp, bucket)
        for opp in all_inactive:
            _add(opp, "history")

        missing_ids = [pid for pid in ex_ids if pid not in clients and pid != 0]
        if missing_ids:
            for p in self._client.search_read("res.partner", domain=[["id", "in", missing_ids]], fields=["id", "name"]):
                clients[p["id"]] = {"id": p["id"], "name": p["name"], "active": [], "prospects": [], "history": [],
                                      "_sellers": set(), "_has_licencias": False}

        def _finalize(subset: dict) -> dict:
            result = []
            for c in subset.values():
                result.append({
                    "id": c["id"], "name": c["name"],
                    "active": [{k: v for k, v in r.items() if k != "_tag_ids"} for r in c["active"]],
                    "prospects": [{k: v for k, v in r.items() if k != "_tag_ids"} for r in c["prospects"]],
                    "history": [{k: v for k, v in r.items() if k != "_tag_ids"} for r in c["history"]],
                    "sellers": sorted(c["_sellers"]), "has_licencias": c["_has_licencias"],
                })
            result.sort(key=lambda c: c["name"].lower())
            return {"clients": result, "total": len(result)}

        def _tag_filtered(tag_id) -> dict:
            if tag_id is None:
                return _finalize(clients)
            subset = {}
            for pid, c in clients.items():
                active = [r for r in c["active"] if tag_id in r["_tag_ids"]]
                prospects = [r for r in c["prospects"] if tag_id in r["_tag_ids"]]
                history = [r for r in c["history"] if tag_id in r["_tag_ids"]]
                if not active and not prospects and not history:
                    continue
                subset[pid] = {**c, "active": active, "prospects": prospects, "history": history}
            return _finalize(subset)

        return {"todos": _finalize(clients), "nn": _tag_filtered(nn_id), "bi": _tag_filtered(bi_id)}

    def get_ex_clients(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:ex-clients", self._build_ex_clients)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]

    # ── Account Licensing Profile (ver HANDOFF — Commercial Licensing,
    # owner ticket "Final Business Semantics Realignment") ──────────────────
    # Grain: `commercial_partner_id` (canonical account identity, MISMO
    # criterio que Clientes/Ex Clientes — ver `_expand_partner_ids_with_
    # contacts`). Universo ÚNICO y PERMANENTE:
    #
    #   toda cuenta canónica con `Es Cliente? = Sí`
    #
    # — punto. Ya NO deriva de oportunidades/tags en absoluto (owner: "Do
    # not infer client status from opportunity tags/Licenciamiento/presence
    # of opportunities/account name/Base instalada"). `tipo` (Todos/New
    # Name/Base instalada) es una clasificación de OPORTUNIDAD — el Perfil
    # es explícitamente account-native y NO varía con `tipo` (owner §6:
    # "This is intentional, not a bug"), MISMO criterio que YA usa
    # `get_clients` en su camino primario (`_build_clients`, arriba: las
    # tres vistas devuelven el MISMO `todos_view` — acá replicamos ESE
    # patrón ya aceptado, en vez de inventar uno nuevo).
    def _licensing_account_property_row(self, partner: dict) -> dict:
        props = partner.get("properties")
        currency = partner.get("currency_id")
        row = {"id": partner["id"], "name": partner.get("name") or "",
               "seller": partner["user_id"][1] if partner.get("user_id") else "",
               "currency": currency[1] if currency else None}
        for key, (technical_name, label, kind) in _LICENSING_PROPERTIES.items():
            if kind == "selection":
                row[key] = _selection_value(props, technical_name, label)
            elif kind == "tags":
                row[key] = _tags_value(props, technical_name, label)
            elif kind == "monetary":
                row[key] = _monetary_value(props, technical_name, label)
            elif kind == "date":
                row[key] = _date_value(props, technical_name, label)
        return row

    def _build_licensing_profile(self) -> dict:
        # `Es Cliente? = Sí` — MISMO helper que Clientes, cero segundo
        # parser. `None` (property no configurada en esta instancia) es un
        # universo vacío acá (a diferencia de Clientes, que cae a "todas las
        # oportunidades activas" — ese fallback no tiene sentido para un
        # Perfil explícitamente account-native, sin ninguna noción de
        # oportunidad de la que caer).
        es_cliente_si_ids = self._partner_ids_by_cliente_value(self._CLIENTE_SI_LABELS) or []
        if not es_cliente_si_ids:
            empty = {"accounts": [], "total": 0}
            return {"todos": empty, "nn": empty, "bi": empty}

        # UN solo batch read — sin oportunidades/tags de por medio, nunca
        # N+1 (una única consulta `res.partner`, MISMO patrón que el resto
        # de Comercial).
        partners_by_id: dict[int, dict] = {}
        for p in self._client.search_read(
            "res.partner", domain=[["id", "in", es_cliente_si_ids]],
            fields=["id", "name", "user_id", "commercial_partner_id", "properties", "currency_id"],
        ):
            partners_by_id[p["id"]] = p

        def _resolve_account_id(raw_id: int) -> int:
            p = partners_by_id.get(raw_id)
            if p and p.get("commercial_partner_id"):
                return p["commercial_partner_id"][0]
            return raw_id

        account_ids = {_resolve_account_id(pid) for pid in es_cliente_si_ids}

        # Segunda pasada, acotada: cuentas resueltas por `commercial_partner_id`
        # cuyo PROPIO registro todavía no está en el batch de arriba — sólo
        # ocurre si el id crudo de origen era un CONTACTO cuya empresa no
        # tenía `Es Cliente?` propio. En el caso común esto no agrega
        # ninguna consulta.
        missing_account_ids = [aid for aid in account_ids if aid not in partners_by_id]
        if missing_account_ids:
            for p in self._client.search_read(
                "res.partner", domain=[["id", "in", missing_account_ids]],
                fields=["id", "name", "user_id", "commercial_partner_id", "properties", "currency_id"],
            ):
                partners_by_id[p["id"]] = p

        rows = sorted(
            (self._licensing_account_property_row(partners_by_id.get(aid, {"id": aid, "name": "Sin nombre"}))
             for aid in account_ids),
            key=lambda r: r["name"].lower(),
        )
        view = {"accounts": rows, "total": len(rows)}
        return {"todos": view, "nn": view, "bi": view}

    def get_licensing_profile(self, tipo: str = "todos") -> dict:
        if tipo not in _TIPOS:
            tipo = "todos"
        if not self._client.is_configured():
            raise CommercialConfigurationError("Odoo no está configurado (ODOO_URL/ODOO_DB/ODOO_USERNAME/ODOO_API_KEY)")
        try:
            views = _cached("commercial:licensing-profile", self._build_licensing_profile)
        except OdooTransportError as exc:
            raise CommercialDataError(str(exc), stage=exc.stage) from exc
        return views[tipo]
