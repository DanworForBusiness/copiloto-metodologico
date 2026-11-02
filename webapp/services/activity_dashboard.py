"""
activity_dashboard.py
=====================
Métricas de Uso v2 — fase 3: modelo de vista del tablero `/admin/activity`
(y `/operaciones/metricas`, que renderiza el MISMO parcial).

Arma, a partir de los query params, todo lo que el tablero v2 necesita:

- Filtros dinámicos: período (presets + rango), vertical, módulo, sección,
  usuario y tipo de actividad. Las opciones salen de los DATOS del período
  (`activity_aggregates.query`) unidos al INVENTARIO real del Hub
  (`module_registry`, módulos del content pack y las secciones declaradas con
  `data-dw-section` en los templates) — siempre dentro de lo que el viewer
  puede ver.
- KPIs v2 (visitas, usuarios únicos, sesiones, acciones, resultados).
- Drill-down vertical → módulo → sección → eventos: cada nivel es la MISMA
  ruta con más filtros; el último nivel lista eventos paginados
  (`activity_aggregates.events`, attrs ya filtrados por la whitelist).
- Inventario sin actividad: módulos/secciones/verticales visibles con 0.
- Tendencia v2 (visitas/usuarios/acciones por día).

Visibilidad: `visible_verticals`/`allowed_areas` (de
`hub_metrics_access.metrics_visibility_for`) se pasan a CADA consulta de
agregados/detalle y recortan opciones e inventario — nunca se confía en los
query params. Este módulo no decide SI se entra (eso es `_require_hub_metrics`).

Los paneles históricos (`activity_analytics.build_summary`) siguen intactos;
el router les pasa el período/vertical/módulo/usuario resueltos acá
(`legacy_args`), así toda la página responde al mismo filtro.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlencode

from webapp.services import activity_aggregates, activity_catalog, content_service, module_registry
from webapp.services.activity_context import module_label, scope_label
from webapp.services.pagination import paginate

BASE_URL = "/admin/activity"

# ── vocabulario ──────────────────────────────────────────────────────────────

PERIOD_ALL = ""
PERIOD_CUSTOM = "custom"
PERIOD_OPTIONS: tuple[tuple[str, str], ...] = (
    (PERIOD_ALL, "Todo el historial"),
    ("today", "Hoy"),
    ("7d", "Últimos 7 días"),
    ("30d", "Últimos 30 días"),
    ("90d", "Últimos 90 días"),
    ("month", "Este mes"),
    (PERIOD_CUSTOM, "Rango personalizado"),
)
_PRESET_DAYS = {"today": 1, "7d": 7, "30d": 30, "90d": 90}

# Tipos de actividad ofrecidos. El diagnóstico NUNCA fue visible en este
# tablero (es de uso, ver activity_analytics), así que tampoco se ofrece acá.
KIND_OPTIONS: tuple[tuple[str, str], ...] = (
    (activity_catalog.KIND_NAVIGATION, "Navegación"),
    (activity_catalog.KIND_ACTION, "Acción"),
    (activity_catalog.KIND_OUTCOME, "Resultado"),
)
KIND_LABELS = {**dict(KIND_OPTIONS), activity_catalog.KIND_SESSION: "Sesión", activity_catalog.KIND_UNKNOWN: "Sin clasificar"}

# Estado de un `outcome` (ver activity_aggregates._status) → (label, tono de .status-badge).
STATUS_BADGES = {
    "success": ("Éxito", "activo"),
    "failed": ("Falla", "inactivo"),
    "rejected": ("Rechazado", "inactivo"),
    "partial": ("Parcial", "tentativo"),
    "done": ("Completado", "otro"),
}

# Orden de las verticales (el mismo de SCOPE_LABELS). `unknown` solo aparece si hay datos.
_VERTICAL_ORDER = (
    activity_catalog.SCOPE_PLATFORM, activity_catalog.SCOPE_COPILOT, activity_catalog.SCOPE_OPERATIONS,
    activity_catalog.SCOPE_COMMERCIAL, activity_catalog.SCOPE_HUMAN_CAPITAL,
)

# Sentinela de drill-down para «sin módulo» / «sin sección» (eventos que no
# llevan esa dimensión, p. ej. el inicio del Hub). Empieza con `_`: nunca
# colisiona con un module_id ni con un slug de sección (regex del catálogo).
NONE_KEY = "_none"

# Inventario de secciones con `section_view`: los slugs REALES declarados con
# `data-dw-section` en los templates (grep), con su vertical y un rótulo.
# `catalog_cds`/`catalog_sql` salen de `catalog_{{ catalog_kind }}` en
# catalog_cds_content.html (los dos `catalog_kind` que renderizan ese parcial).
# tests/webapp/test_activity_dashboard_v2.py verifica que esta lista y los
# templates no se desalineen. No es un mapa sección→módulo: el Catálogo se
# abre dentro de varios módulos del Copiloto, así que la sección pertenece a
# la VERTICAL y el módulo lo dice el dato.
SECTION_INVENTORY: dict[str, tuple[str, str]] = {
    "catalog_connections": (activity_catalog.SCOPE_COPILOT, "Catálogo · Conexiones"),
    "catalog_connection_detail": (activity_catalog.SCOPE_COPILOT, "Catálogo · Detalle de conexión"),
    "connectivity_review": (activity_catalog.SCOPE_COPILOT, "Catálogo · Revisión de conectividad"),
    "catalog_connectivity_registry": (activity_catalog.SCOPE_COPILOT, "Catálogo · Tenants y ambientes"),
    "catalog_security": (activity_catalog.SCOPE_COPILOT, "Catálogo · Seguridad"),
    "catalog_security_detail": (activity_catalog.SCOPE_COPILOT, "Catálogo · Detalle de seguridad"),
    "catalog_domain": (activity_catalog.SCOPE_COPILOT, "Catálogo · Dominio"),
    "catalog_lob": (activity_catalog.SCOPE_COPILOT, "Catálogo · Línea de negocio"),
    "catalog_client": (activity_catalog.SCOPE_COPILOT, "Catálogo · Cliente"),
    "catalog_client_assessment": (activity_catalog.SCOPE_COPILOT, "Catálogo · Informe de cliente"),
    "catalog_assessment": (activity_catalog.SCOPE_COPILOT, "Catálogo · Assessment"),
    "catalog_data_product": (activity_catalog.SCOPE_COPILOT, "Catálogo · Data Product"),
    "catalog_flow": (activity_catalog.SCOPE_COPILOT, "Catálogo · Flujo"),
    "catalog_sap_types": (activity_catalog.SCOPE_COPILOT, "Catálogo · Disponibilidad SAP"),
    "catalog_cds": (activity_catalog.SCOPE_COPILOT, "Catálogo · CDS View"),
    "catalog_sql": (activity_catalog.SCOPE_COPILOT, "Catálogo · Vista SQL"),
    "governed_contracts_list": (activity_catalog.SCOPE_COPILOT, "Contratos gobernados · Listado"),
    "governed_contracts_detail": (activity_catalog.SCOPE_COPILOT, "Contratos gobernados · Detalle"),
    "cchh_candidate_contact": (activity_catalog.SCOPE_HUMAN_CAPITAL, "CCHH · Contacto de candidato"),
}

# Secciones POR RUTA (pantallas completas sin módulo: CCHH, Búsquedas,
# Reporting, Plataforma) — vienen de `activity_catalog.PAGE_SECTIONS`, la MISMA
# tabla con la que telemetry.py deriva la sección del `page_view`. Van aparte
# de SECTION_INVENTORY (que es el espejo exacto de `data-dw-section`).
PAGE_SECTION_INVENTORY: dict[str, tuple[str, str]] = activity_catalog.page_section_inventory()
_ALL_SECTIONS: dict[str, tuple[str, str]] = {**PAGE_SECTION_INVENTORY, **SECTION_INVENTORY}

_EVENT_PAGE_SIZES = (25, 50, 100)
_TREND_MAX_DAYS = 25
_MAX_ATTRS = 3


# ── helpers ──────────────────────────────────────────────────────────────────

def _today() -> date:
    return datetime.now(timezone.utc).date()


def _parse_iso(value: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text) if "-" in text else datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None


def section_label(slug: str) -> str:
    if not slug:
        return "Sin sección (vista general)"
    return _ALL_SECTIONS.get(slug, (None, slug))[1]


def _module_label(key: str) -> str:
    if not key:
        return "Sin módulo (vistas generales)"
    return module_label(key) or key


def _status_badge(status: str) -> dict | None:
    if not status:
        return None
    label, tone = STATUS_BADGES.get(status, (status, "otro"))
    return {"label": label, "tone": tone}


def resolve_period(period: str, date_from: str, date_to: str, *, today: date | None = None) -> dict:
    """Período efectivo. Presets relativos a HOY (UTC). Si llegan fechas que
    no coinciden con el preset (p. ej. un clic en una barra de la tendencia,
    que fija Desde/Hasta), manda el rango: queda «personalizado»."""
    today = today or _today()
    period = (period or "").strip()
    given_from, given_to = _parse_iso(date_from), _parse_iso(date_to)
    if period == "month":
        rng = (today.replace(day=1), today)
    elif period in _PRESET_DAYS:
        rng = (today - timedelta(days=_PRESET_DAYS[period] - 1), today)
    else:
        rng = None
    if rng is not None and (given_from or given_to) and (given_from, given_to) != rng:
        rng = None
        period = PERIOD_CUSTOM
    if rng is not None:
        low, high = rng
    elif given_from or given_to:
        period = PERIOD_CUSTOM
        low, high = given_from, given_to
    else:
        period = PERIOD_ALL
        low = high = None
    return {
        "period": period,
        # Lo que viaja a los paneles históricos y a los inputs Desde/Hasta.
        "date_from": low.isoformat() if low else "",
        "date_to": high.isoformat() if high else "",
        "low": low, "high": high,
    }


def _qs(state: dict, **override) -> str:
    merged = {**state, **override}
    return urlencode([(k, v) for k, v in merged.items() if v not in (None, "")])


def _url(state: dict, **override) -> str:
    qs = _qs(state, **override)
    return f"{BASE_URL}?{qs}" if qs else BASE_URL


# ── inventario ───────────────────────────────────────────────────────────────

def _visible_vertical_inventory(visible: frozenset[str]) -> list[str]:
    return [v for v in _VERTICAL_ORDER if v in visible]


def _module_inventory(visible: frozenset[str], allowed_areas: frozenset[str] | None) -> list[dict]:
    """Módulos REALES del Hub visibles para el viewer: `module_registry`
    (Copiloto/Operaciones/Comercial) + módulos del content pack que todavía no
    tienen ID canónico (se listan por su stem, igual que los registra el log)."""
    out = []
    seen = set()
    for entry in sorted(module_registry.all_entries(), key=lambda e: (e.workspace, e.kind != "module", e.display_order)):
        if entry.workspace not in visible or (allowed_areas is not None and entry.area not in allowed_areas):
            continue
        out.append({"key": entry.module_id, "label": entry.visible_label, "vertical": entry.workspace})
        seen.add(entry.module_id)
        seen.update(entry.legacy_aliases)
    if activity_catalog.SCOPE_COPILOT in visible:
        try:
            pack_modules = list(content_service.list_modules())
        except Exception:  # noqa: BLE001 — el inventario es complementario: sin pack, solo el registro
            pack_modules = []
        for m in pack_modules:
            if m.stem not in seen:
                out.append({"key": m.stem, "label": m.title, "vertical": activity_catalog.SCOPE_COPILOT})
                seen.add(m.stem)
    return out


def _section_inventory(visible: frozenset[str]) -> list[dict]:
    return [{"key": slug, "label": label, "vertical": vertical}
            for slug, (vertical, label) in _ALL_SECTIONS.items() if vertical in visible]


# ── construcción ─────────────────────────────────────────────────────────────

def build(params: dict, *, summary: dict, visible_verticals: Iterable[str], allowed_areas: Iterable[str] | None,
          log_dir=None, today: date | None = None) -> dict:
    """Modelo de vista del tablero v2. `params`: query params crudos
    (period, date_from, date_to, vertical, module, section, kind, user,
    scope[], area[], epage, epage_size). `summary`: el de `build_summary`
    (para las facetas Ámbito/Área y las etiquetas de usuario, ya recortadas
    por visibilidad)."""
    today = today or _today()
    visible = frozenset(str(v) for v in visible_verticals) - {""}
    areas_allowed = frozenset(allowed_areas) if allowed_areas is not None else None
    per = resolve_period(params.get("period", ""), params.get("date_from", ""), params.get("date_to", ""), today=today)

    vertical = str(params.get("vertical") or "").strip()
    if vertical not in visible:
        vertical = ""
    module = str(params.get("module") or "").strip()[:64]
    section = str(params.get("section") or "").strip()
    if section and section != NONE_KEY and not activity_catalog.is_valid_section_slug(section):
        section = ""
    # Una sección inventariada de una vertical que el viewer no ve (p. ej. CCHH forzada por URL) se ignora:
    # ni su rótulo llega al subhead del desglose.
    if section in _ALL_SECTIONS and _ALL_SECTIONS[section][0] not in visible:
        section = ""
    kind = str(params.get("kind") or "").strip()
    if kind not in dict(KIND_OPTIONS):
        kind = ""
    user = str(params.get("user") or "").strip()

    # Ámbito/Área (checkboxes históricos): solo restringen si el viewer
    # destildó algo — todo tildado (o nada enviado) = sin filtro, así la
    # vista general también muestra verticales/módulos sin actividad.
    scope_req = {s for s in params.get("scope") or [] if s}
    area_req = {a for a in params.get("area") or [] if a}
    enabled_scopes = {f["key"] for f in summary.get("scope_facets") or [] if not f.get("disabled")}
    enabled_areas = {f["key"] for f in summary.get("area_facets") or [] if not f.get("disabled")}
    vertical_filter: set[str] | None = None
    if vertical:
        vertical_filter = {vertical}
    elif scope_req and enabled_scopes and not enabled_scopes <= scope_req:
        vertical_filter = (scope_req & visible) or None
    area_filter: set[str] | None = set(areas_allowed) if areas_allowed is not None else None
    if area_req and enabled_areas and not enabled_areas <= area_req:
        area_filter = area_req if area_filter is None else (area_req & area_filter)
        if not area_filter:
            area_filter = set(areas_allowed) if areas_allowed is not None else None

    with activity_aggregates.reading_from(log_dir):
        earliest = activity_aggregates.earliest_day()
        low = per["low"] or (datetime.strptime(earliest, "%Y%m%d").date() if earliest else today)
        high = per["high"] or today
        if low > high:
            low, high = high, low
        start, end = low.strftime("%Y%m%d"), high.strftime("%Y%m%d")
        today_key = today.strftime("%Y%m%d")

        base_filters: dict = {}
        if vertical_filter:
            base_filters["vertical"] = sorted(vertical_filter)
        if area_filter is not None:
            base_filters["area"] = sorted(area_filter) or [""]
        if user:
            base_filters["user"] = user
        # Opciones: datos del período (sin módulo/sección/tipo) ∪ inventario.
        options_data = activity_aggregates.query(start, end, base_filters, visible_verticals=visible, today=today_key)
        filters = dict(base_filters)
        if module:
            filters["module"] = [""] if module == NONE_KEY else module
        if section:
            filters["section"] = [""] if section == NONE_KEY else section
        if kind:
            filters["kind"] = kind
        data = activity_aggregates.query(start, end, filters, visible_verticals=visible, today=today_key)

        level = "section" if section else "module" if module else "vertical" if vertical else "overview"
        events_block = None
        if level == "section":
            total = activity_aggregates.events(start, end, filters, visible_verticals=visible, page=1, page_size=1)["total"]
            state_for_pager = _state(per, vertical, module, section, kind, user)
            pg = paginate(range(total), page=params.get("epage") or 1, page_size=params.get("epage_size"),
                          base_url=BASE_URL, params=state_for_pager, sizes=_EVENT_PAGE_SIZES, default_size=50,
                          page_param="epage", size_param="epage_size")
            detail = activity_aggregates.events(start, end, filters, visible_verticals=visible,
                                                page=pg["page"], page_size=pg["page_size"])
            events_block = {"rows": [_event_row(i, summary) for i in detail["items"]], "total": detail["total"],
                            "pager": pg}

    state = _state(per, vertical, module, section, kind, user)
    vertical_inv = _visible_vertical_inventory(visible)
    module_inv = _module_inventory(visible, areas_allowed)
    section_inv = _section_inventory(visible)
    inferred_vertical = vertical or _infer_vertical(module, module_inv, data)

    crumbs = _breadcrumb(state, vertical or inferred_vertical, module, section, module_inv)
    rows, rows_title, zero_count = _drill_rows(level, data, state, vertical_inv, module_inv, section_inv,
                                               vertical_filter, inferred_vertical, module)
    kpis = data["kpis"]
    outcomes = kpis.get("outcomes") or {}
    ok = int(outcomes.get("success", 0))
    fail = int(outcomes.get("failed", 0)) + int(outcomes.get("rejected", 0))
    partial = int(outcomes.get("partial", 0))
    graded = ok + fail + partial
    return {
        **state,
        "period_options": [{"value": v, "label": lbl} for v, lbl in PERIOD_OPTIONS],
        "kind_options": [{"value": v, "label": lbl} for v, lbl in KIND_OPTIONS],
        "vertical_options": [{"value": v, "label": scope_label(v)} for v in vertical_inv],
        "module_options": _module_options(options_data, module_inv, vertical_filter, module),
        "section_options": _section_options(options_data, section_inv, vertical_filter, inferred_vertical, module, section),
        "start": start, "end": end,
        "level": level,
        "breadcrumb": crumbs,
        # Subhead del desglose (mismo patrón que «Eventos»): «Filtrado por <ruta>» + ✕ que sube UN nivel.
        "filter_label": " › ".join(c["label"] for c in crumbs[1:]),
        "parent_url": crumbs[-2]["url"] if len(crumbs) > 1 else "",
        "rows": rows, "rows_title": rows_title, "zero_count": zero_count,
        # Paneles históricos por vertical (ver admin_activity_content.html).
        "vertical_label": scope_label(vertical) if vertical else "",
        "nav_panel": _nav_panel(summary, vertical),
        "show_copilot_panels": not vertical or vertical == activity_catalog.SCOPE_COPILOT,
        "show_reactions": not vertical or vertical in activity_catalog.feedback_verticals(),
        "events": events_block,
        "kpis": {
            "visits": kpis["visits"], "unique_users": kpis["unique_users"], "sessions": kpis["sessions"],
            "actions": kpis["actions"], "events": kpis["events"],
            "outcomes_total": sum(int(v) for v in outcomes.values()),
            "ok": ok, "fail": fail, "partial": partial,
            "success_rate": round(100 * ok / graded) if graded else None,
        },
        "trend": _trend(data["trend"]),
        "retention_months": data["retention_months"],
        "has_drill": bool(vertical or module or section),
        "has_v2_filters": bool(per["period"] or vertical or module or section or kind),
    }


def legacy_args(params: dict, *, visible_verticals: Iterable[str], today: date | None = None) -> dict:
    """Argumentos para `activity_analytics.build_summary` (paneles históricos)
    con el MISMO período/vertical/módulo/usuario que resuelve `build` — así
    Tendencia, Eventos, Reacciones, Historia de preguntas y Árboles responden
    al mismo filtro. Sección y tipo de actividad no existen en ese motor."""
    per = resolve_period(params.get("period", ""), params.get("date_from", ""), params.get("date_to", ""), today=today)
    vertical = str(params.get("vertical") or "").strip()
    if vertical not in {str(v) for v in visible_verticals}:
        vertical = ""
    module = str(params.get("module") or "").strip()[:64]
    return {
        "date_from": per["date_from"], "date_to": per["date_to"],
        "user": str(params.get("user") or "").strip(),
        "module": "" if module == NONE_KEY else module,
        "scopes": [vertical] if vertical else list(params.get("scope") or []),
        # Vertical ESTRICTA: los paneles históricos nunca vuelven a «todo» si la
        # vertical elegida no tiene actividad (ver build_summary).
        "vertical": vertical,
        "areas": list(params.get("area") or []),
    }


def _state(per: dict, vertical: str, module: str, section: str, kind: str, user: str) -> dict:
    """Estado de URL (round-trip): lo que reconstruye exactamente esta vista."""
    custom = per["period"] == PERIOD_CUSTOM
    return {
        "period": per["period"],
        "date_from": per["date_from"] if custom else "",
        "date_to": per["date_to"] if custom else "",
        "vertical": vertical, "module": module, "section": section, "kind": kind, "user": user,
    }


def _infer_vertical(module: str, module_inv: list[dict], data: dict) -> str:
    if not module:
        return ""
    for m in module_inv:
        if m["key"] == module:
            return m["vertical"]
    for v in data["breakdown"]:
        for m in v["modules"]:
            if (m["module"] or NONE_KEY) == module:
                return v["vertical"]
    return ""


def _breadcrumb(state: dict, vertical: str, module: str, section: str, module_inv: list[dict]) -> list[dict]:
    keep = {k: state[k] for k in ("period", "date_from", "date_to", "kind", "user")}
    crumbs = [{"label": "Todas las verticales", "url": _url(keep)}]
    if vertical:
        crumbs.append({"label": scope_label(vertical), "url": _url(keep, vertical=vertical)})
    if module:
        label = next((m["label"] for m in module_inv if m["key"] == module), None) or _module_label("" if module == NONE_KEY else module)
        crumbs.append({"label": label, "url": _url(keep, vertical=vertical, module=module)})
    if section:
        crumbs.append({"label": section_label("" if section == NONE_KEY else section),
                       "url": _url(keep, vertical=vertical, module=module, section=section)})
    crumbs[-1]["current"] = True
    return crumbs


def _row(key: str, label: str, node: dict | None, url: str) -> dict:
    node = node or {}
    return {"key": key, "label": label, "visits": node.get("visits", 0), "users": node.get("users", 0),
            "events": node.get("events", 0), "url": url, "zero": not node.get("events")}


def _drill_rows(level, data, state, vertical_inv, module_inv, section_inv, vertical_filter, vertical, module):
    keep = {k: state[k] for k in ("period", "date_from", "date_to", "kind", "user")}
    breakdown = data["breakdown"]
    rows: list[dict] = []
    if level == "overview":
        seen = set()
        for v in breakdown:
            seen.add(v["vertical"])
            rows.append(_row(v["vertical"], v["label"], v, _url(keep, vertical=v["vertical"])))
        for v in vertical_inv:
            if v not in seen and (not vertical_filter or v in vertical_filter):
                rows.append(_row(v, scope_label(v), None, _url(keep, vertical=v)))
        title = "Verticales"
    elif level == "vertical" and vertical in activity_catalog.SECTION_VERTICALS:
        # Vertical estructurada por pantallas (CCHH, Plataforma): sin módulos,
        # el desglose baja directo a sus SECCIONES (nunca un único «sin módulo»).
        node = next((v for v in breakdown if v["vertical"] == vertical), None)
        merged: dict[str, dict] = {}
        for m in (node or {}).get("modules", []):
            for s in m["sections"]:
                acc = merged.setdefault(s["section"], {"visits": 0, "users": 0, "events": 0})
                for k in ("visits", "users", "events"):
                    acc[k] += s.get(k, 0)
        for slug, sec in sorted(merged.items(), key=lambda kv: -kv[1]["events"]):
            key = slug or NONE_KEY
            rows.append(_row(key, section_label(slug), sec, _url(keep, vertical=vertical, section=key)))
        for s in section_inv:
            if s["vertical"] == vertical and s["key"] not in merged:
                rows.append(_row(s["key"], s["label"], None, _url(keep, vertical=vertical, section=s["key"])))
        title = "Secciones"
    elif level == "vertical":
        node = next((v for v in breakdown if v["vertical"] == vertical), None)
        seen = set()
        for m in (node or {}).get("modules", []):
            key = m["module"] or NONE_KEY
            seen.add(key)
            rows.append(_row(key, _module_label(m["module"]), m, _url(keep, vertical=vertical, module=key)))
        for m in module_inv:
            if m["vertical"] == vertical and m["key"] not in seen:
                rows.append(_row(m["key"], m["label"], None, _url(keep, vertical=vertical, module=m["key"])))
        title = "Módulos"
    elif level == "module":
        merged: dict[str, dict] = {}
        for v in breakdown:
            for m in v["modules"]:
                for s in m["sections"]:
                    acc = merged.setdefault(s["section"], {"visits": 0, "users": 0, "events": 0})
                    for k in ("visits", "users", "events"):
                        acc[k] += s.get(k, 0)
        for slug, node in sorted(merged.items(), key=lambda kv: -kv[1]["events"]):
            key = slug or NONE_KEY
            rows.append(_row(key, section_label(slug), node, _url(keep, vertical=vertical, module=module, section=key)))
        for s in section_inv:
            if s["key"] not in merged and (not vertical or s["vertical"] == vertical):
                rows.append(_row(s["key"], s["label"], None, _url(keep, vertical=vertical, module=module, section=s["key"])))
        title = "Secciones"
    else:
        return [], "Eventos", 0
    return rows, title, sum(1 for r in rows if r["zero"])


def _nav_panel(summary: dict, vertical: str) -> dict:
    """«Módulos y usuarios» según la vertical elegida. Sin vertical o con una
    vertical de módulos (Copiloto, Operaciones, Comercial): Top módulos, que
    `build_summary` ya calcula SOLO sobre esa vertical (estricta). Con una
    vertical de pantallas (CCHH, Plataforma): «Secciones y usuarios» con SUS
    secciones (page_view por ruta + section_view); nunca módulos de otra."""
    if vertical in activity_catalog.SECTION_VERTICALS:
        rows = [{"key": r["label"], "label": section_label(r["label"]), "count": r["count"], "pct": r["pct"]}
                for r in summary.get("top_sections") or []
                if _ALL_SECTIONS.get(r["label"], (vertical, ""))[0] == vertical]
        return {"mode": "sections", "title": "Secciones y usuarios", "items_title": "Top secciones",
                "noun": "sección(es)", "rows": rows}
    return {"mode": "modules", "title": "Módulos y usuarios", "items_title": "Top módulos",
            "noun": "módulo(s)", "rows": list(summary.get("top_modules") or [])}


def _module_options(options_data, module_inv, vertical_filter, selected) -> list[dict]:
    out: dict[str, str] = {}
    for v in options_data["breakdown"]:
        for m in v["modules"]:
            if m["module"]:
                out.setdefault(m["module"], _module_label(m["module"]))
    for m in module_inv:
        if not vertical_filter or m["vertical"] in vertical_filter:
            out.setdefault(m["key"], m["label"])
    if selected == NONE_KEY:
        out[NONE_KEY] = _module_label("")
    return [{"value": k, "label": lbl} for k, lbl in sorted(out.items(), key=lambda kv: kv[1].lower())]


def _section_options(options_data, section_inv, vertical_filter, vertical, module, selected) -> list[dict]:
    out: dict[str, str] = {}
    for v in options_data["breakdown"]:
        for m in v["modules"]:
            if module and (m["module"] or NONE_KEY) != module:
                continue
            for s in m["sections"]:
                if s["section"]:
                    out.setdefault(s["section"], section_label(s["section"]))
    for s in section_inv:
        if (not vertical_filter or s["vertical"] in vertical_filter) and (not vertical or s["vertical"] == vertical):
            out.setdefault(s["key"], s["label"])
    if selected == NONE_KEY:
        out[NONE_KEY] = section_label("")
    return [{"value": k, "label": lbl} for k, lbl in sorted(out.items(), key=lambda kv: kv[1].lower())]


def _event_row(item: dict, summary: dict) -> dict:
    users = {u["value"]: u["label"] for u in summary.get("available_users") or []}
    attrs = [(k, str(v)[:60]) for k, v in sorted((item.get("attrs") or {}).items())
             if k not in ("view_id", "reply_id", "conversation_id", "run_id") and v not in (None, "")][:_MAX_ATTRS]
    return {
        "ts": item["ts_utc"][:19].replace("T", " "),
        "user": item["user"], "user_label": users.get(item["user"]) or item["user"] or "—",
        "label": item["label"], "event": item["event"],
        "kind": KIND_LABELS.get(item["kind"], item["kind"]),
        "module_label": _module_label(item["module"]) if item["module"] else "",
        "status": _status_badge(item["status"]),
        "attrs": attrs,
    }


def _trend(trend: list[dict]) -> dict:
    """Días CON actividad (como la tendencia histórica), los últimos 25."""
    active = [t for t in trend if t["events"] or t["visits"]]
    truncated = len(active) > _TREND_MAX_DAYS
    active = active[-_TREND_MAX_DAYS:]
    series = {}
    for key in ("visits", "users", "actions"):
        peak = max((t[key] for t in active), default=0)
        series[key] = [{"day": t["day"], "value": t[key], "pct": round(100 * t[key] / peak) if peak else 0}
                       for t in active]
    return {"series": series, "days": len(active), "truncated": truncated}
