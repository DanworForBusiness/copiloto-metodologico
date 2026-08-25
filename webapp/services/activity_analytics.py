"""
activity_analytics.py
======================
Resumen de adopción/uso a partir de los JSONL diarios que escribe
activity_service.log_activity/log_verbose en ACTIVITY_LOG_DIR. Un solo pase de
lectura de archivos, sin dependencias externas (stdlib) — el volumen esperado
en el lanzamiento reducido es chico, no hace falta pandas/duckdb ni caché.

No reemplaza auditoría formal: es una vista de lectura sobre el mismo log
liviano de adopción (ver activity_service.py), acotada por rango de fechas y,
opcionalmente, por usuario y/o módulo.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

import yaml

from config import ACTIVITY_LOG_DIR, FEEDBACK_LOG_DIR
from webapp.services import module_registry
from webapp.services.activity_context import (
    area_label, context_of, scope_label, module_label,
    normalize_event, event_class as _classify_event,
    EVENT_CLASS_USAGE, EVENT_MODULE_VIEWED, EVENT_LABELS,
)

# Misma convención de nombre/rango que admin_tools._file_date /
# runtime_purge: activity_YYYYMMDD.jsonl, bounds YYYYMMDD, vacío = sin límite.
_FILE_DATE_RE = re.compile(r"activity_(\d{8})\.jsonl$")

# route_heading llega con el título crudo de la sección, que puede traer la sintaxis
# de dos tonos del contenido (p. ej. "{dw-black}Tipos de {dw-gold}Vistas CDS") — la
# misma que content_service.TONE_RE sabe convertir a <span> al renderizar Markdown.
# Acá no renderizamos nada, así que directamente se descarta para mostrar texto plano.
_TONE_TAG_RE = re.compile(r"\{dw-(black|gold|white|green|red)\}", re.I)

# Eventos que llevan una referencia de módulo/stem en el payload (module_selected
# usa "module", el resto usa "stem"). El resto de los eventos (login, catalog_view,
# tree_*) no son atribuibles a un módulo puntual.
def _answer_html(text: str) -> str:
    """Renderiza la respuesta (markdown) a HTML para mostrarla en la historia con
    el MISMO formato que el panel del asistente (negritas, listas), no texto plano
    con asteriscos. Import lazy para evitar ciclos en el import de arranque."""
    if not text:
        return ""
    try:
        from webapp.services.section_analysis import render_llm_html
        return render_llm_html(text)
    except Exception:
        return ""


_MODULE_KEY_EVENTS = {"module_selected", "section_analyze", "assistant_query", "tree_started"}
_TOP_N = 10
_RECENT_QUESTIONS_N = 20

# Paginación de "Historia de preguntas al asistente": el listado completo (no
# solo un cap fijo) se pagina server-side para que no se vuelva pesado de
# escanear a medida que se acumula actividad.
_STORY_PAGE_SIZES = (10, 15, 25, 50)
_DEFAULT_STORY_PAGE_SIZE = 10


def _page_window(page: int, total_pages: int, radius: int = 2) -> list[int | None]:
    """Números de página a mostrar en el paginador: siempre primera/última,
    +/- `radius` alrededor de la actual, con `None` como separador "…" en los
    huecos. Ej. page=4, total=10, radius=2 → [1, None, 2,3,4,5,6, None, 10]."""
    if total_pages <= 1:
        return [1]
    window = {1, total_pages}
    for p in range(page - radius, page + radius + 1):
        if 1 <= p <= total_pages:
            window.add(p)
    ordered = sorted(window)
    result: list[int | None] = []
    prev = None
    for p in ordered:
        if prev is not None and p - prev > 1:
            result.append(None)
        result.append(p)
        prev = p
    return result

# Qué mide cada evento — se muestra como tooltip sobre el nombre del evento en el
# dashboard, para que quien lee los números sepa exactamente qué está contando
# (y por qué, p. ej., tree_step > tree_started: un recorrido tiene varios nodos).
# Fuente de la verdad de los nombres: los log_activity/log_verbose en main.py
# (+ el beacon /telemetry/event y su _TELEMETRY_EVENTS).
_EVENT_DESCRIPTIONS = {
    "login": "Inicios de sesión (una vez por login).",
    # `module_viewed` (corrective A2 — Transversal Module + Event Semantics):
    # reemplaza al histórico `module_selected` de Copiloto Y al histórico
    # `staffing_overview_viewed` de Operations/Staffing — mismo hecho (vista
    # de un módulo), ahora un solo evento genérico y reusable en vez de
    # repetir la ubicación en el nombre.
    "module_viewed": "Vista de módulo: se entró a un módulo navegable (Copiloto o, p. ej., Asignaciones en Operaciones). Una por cada vez que se entra.",
    "module_continue": "Veces que se usó el cue de “continuar” para avanzar dentro de un módulo.",
    "module_reached_end": "Veces que se llegó al final de un módulo (lectura completa).",
    "academy_selected": "Veces que se abrió una página de Academy.",
    "assistant_query": "Preguntas/acciones enviadas al asistente (chat o acciones de la tab Sección). Cada envío suma una.",
    "catalog_view": "Fichas del catálogo abiertas (dominio, flujo, data product, CDS o guía). El desglose es por tipo de ficha.",
    "catalog_regenerate": "Veces que se regeneró el catálogo.",
    "tree_started": "Árboles de decisión iniciados: cada apertura es un recorrido nuevo. (Ver detalle en la sección “Árboles de decisión”.)",
    "tree_step": "Respuestas a nodos INTERMEDIOS de un árbol (cada opción elegida que todavía NO cierra en un resultado). Un recorrido de varios nodos suma varias — por eso suele ser mayor que tree_started.",
    "tree_outcome": "Respuestas que cerraron un árbol en un RESULTADO (hoja). Una por recorrido que llegó al final; los recorridos abandonados no suman acá.",
    "nav_jump": "Saltos de navegación interna (ir a otra sección/módulo desde un enlace).",
    "link_external": "Clics en enlaces externos (fuera del copiloto).",
    "disclosure_toggle": "Veces que se abrió/cerró un desplegable de contenido.",
    "theme_change": "Veces que se cambió el tema (claro/oscuro).",
    "section_analyze": "(Histórico) Acción de análisis de sección — reemplazado por assistant_query; puede aparecer en datos previos.",
    # Diagnóstico (Batch A1/A2 corrective) — transversal, NUNCA cuenta como
    # adopción/uso (ver event_class en activity_context.py). Reemplazan a los
    # históricos con la ubicación repetida en el nombre (`staffing_overview_
    # error`, `staffing_data_diagnostics`, etc.) — la fase queda en
    # `payload.operation` ("initial"/"history"), no en el nombre del evento.
    "data_load_failed": "(Diagnóstico) Error de Graph/transporte al cargar datos de un módulo (ver payload.operation: initial/history).",
    "data_load_diagnostics": "(Diagnóstico) Métricas de la carga de datos de un módulo — filas/páginas/tiempo/fallback (ver payload.operation), nunca token/payload/PII.",
    "configuration_incomplete": "(Diagnóstico) La configuración del módulo (ver payload.operation) estaba incompleta al pedir datos — no es un fallo de Graph/transporte.",
}


def _bounds(date_from: str, date_to: str) -> tuple[str, str]:
    low = (date_from or "").replace("-", "").strip() or "00000000"
    high = (date_to or "").replace("-", "").strip() or "99999999"
    return low, high


def _log_files(low: str, high: str) -> list[tuple[str, Path]]:
    if not ACTIVITY_LOG_DIR.exists():
        return []
    hits = []
    for path in ACTIVITY_LOG_DIR.glob("activity_*.jsonl"):
        match = _FILE_DATE_RE.search(path.name)
        if not match:
            continue
        day = match.group(1)
        if low <= day <= high:
            hits.append((day, path))
    return sorted(hits)


def _legacy_payload_module(raw_event: str, payload: dict[str, Any]) -> str:
    """Fallback LEGACY para eventos que nunca tuvieron `module` de primera
    clase en el envelope y lo llevaban dentro de `payload` (`module_selected`
    → payload.module; `section_analyze`/`assistant_query`/`tree_started` →
    payload.stem). Se evalúa sobre el RAW event name (antes de
    `normalize_event`) — el set no cambia por el corrective A2, los eventos
    NUEVOS ya traen `module` como campo top-level y nunca llegan acá."""
    if raw_event not in _MODULE_KEY_EVENTS:
        return ""
    return str(payload.get("module") or payload.get("stem") or "").strip()


def _read_feedback(date_from: str, date_to: str) -> dict[str, dict]:
    """Feedback (👍/👎 + comentario) de respuestas del asistente, indexado por
    reply_id — ver AssistantFeedbackRepository.save (assistant_service.py), que
    ahora graba reply_id/conversation_id para poder correlacionar acá. YAML
    previos a ese cambio no tienen reply_id: se ignoran para la historia (se
    siguen viendo igual en los reportes de analyze_feedback.py, sin cambios)."""
    low, high = _bounds(date_from, date_to)
    by_reply: dict[str, dict] = {}
    if not FEEDBACK_LOG_DIR.exists():
        return by_reply
    for path in FEEDBACK_LOG_DIR.glob("*.yaml"):
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        if not isinstance(data, dict) or data.get("type") != "assistant_response":
            continue
        reply_id = str(data.get("reply_id") or "").strip()
        if not reply_id:
            continue
        day = str(data.get("ts") or "")[:10].replace("-", "")
        if day and not (low <= day <= high):
            continue
        by_reply[reply_id] = {
            "rating": str(data.get("rating") or ""),
            "comment": str(data.get("comment") or ""),
        }
    return by_reply


def _read_events(date_from: str, date_to: str) -> list[dict]:
    """Parsea los JSONL del rango a una lista de eventos normalizados (día,
    tipo, usuario, nombre, ts, payload, module, raw_module, event_class).
    Líneas corruptas se descartan silenciosamente, igual que log_activity
    nunca rompe la app.

    Normalización de NOMBRE de evento + module + event_class (corrective A2 —
    Transversal Module + Event Semantics) es semántica de READ-MODEL: el
    JSONL persistido NUNCA se reescribe. `event` queda con el nombre
    CANÓNICO (post-`normalize_event`); `raw_event` preserva el nombre
    realmente persistido, para que el owner pueda seguir inspeccionando qué
    se guardó de verdad.

    MODULE IDENTIFIER CONVENTION V2 — Fase 2 (Canonical Module Adoption):
    mismo criterio para `module`. La extracción histórica (campo explícito →
    module inyectado por `normalize_event` → `payload.module`/`payload.stem`)
    NO cambia — sigue produciendo el mejor valor histórico disponible, ahora
    llamado `raw_module`. Ese valor se resuelve contra `module_registry`
    (dueño único de la identidad canónica — dirección de dependencia:
    `activity_analytics` DEPENDE de `module_registry`, nunca al revés): si
    resuelve, `module` queda con el `module_id` canónico (`CP01`/`OP01`/...);
    si no (valor desconocido, p. ej. contenido todavía sin ID asignado),
    `module` queda IGUAL a `raw_module`, sin fabricar un ID — nunca se
    pierde silenciosamente un módulo no reconocido."""
    low, high = _bounds(date_from, date_to)
    events: list[dict] = []
    for day, path in _log_files(low, high):
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                raw = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            raw_event = str(raw.get("event") or "").strip()
            if not raw_event:
                continue
            payload = raw.get("payload") or {}
            if not isinstance(payload, dict):
                payload = {}
            user = str(raw.get("user") or "").strip()
            name = str(raw.get("name") or "").strip()
            # Seam de normalización de contexto (Batch A1): scope/area para
            # registros nuevos (campos explícitos) Y viejos (clasificados por
            # event) vía la ÚNICA función canónica context_of — no se esparce
            # lógica event→scope/area por las métricas del dashboard. Sigue
            # evaluándose sobre el RAW event name (`_HISTORICAL` está keyed
            # por nombres crudos, p. ej. "staffing_overview_viewed").
            scope, area = context_of(raw)
            canonical_event, injected_module, injected_operation = normalize_event(raw_event)
            if injected_operation and not payload.get("operation"):
                payload = {**payload, "operation": injected_operation}
            explicit_module = str(raw.get("module") or "").strip()
            raw_module = explicit_module or injected_module or _legacy_payload_module(raw_event, payload)
            # MODULE IDENTIFIER CONVENTION V2 — Fase 2: `raw_module` preserva
            # el mejor valor histórico disponible (igual que antes, sin
            # tocar la extracción); `module` es su resolución canónica si
            # `module_registry` la reconoce, o el mismo valor sin cambios si
            # no — nunca se fabrica un ID para un módulo desconocido.
            resolved_module = module_registry.resolve(raw_module) if raw_module else None
            module = resolved_module.module_id if resolved_module else raw_module
            events.append({
                "day": day,
                "event": canonical_event,
                "raw_event": raw_event,
                "user": user,
                "name": name,
                "ts_utc": str(raw.get("ts_utc") or ""),
                "payload": payload,
                "module": module,
                "raw_module": raw_module,
                "scope": scope,
                "area": area,
                "event_class": _classify_event(canonical_event),
            })
    return events


def _user_facets(events: list[dict]) -> list[dict]:
    """Usuarios distintos en el rango de fechas (ANTES de aplicar el filtro de
    usuario), para que el <select> no se vacíe al filtrar. Módulo se resuelve
    aparte, en build_summary — es transversal (Copilot Y Operations, ver
    corrective A2) y necesita facetear contra Ámbito/Área, no solo fecha."""
    users: dict[str, str] = {}
    for ev in events:
        if ev["user"]:
            users.setdefault(ev["user"], ev["name"] or ev["user"])
    return [
        {"value": user, "label": label}
        for user, label in sorted(users.items(), key=lambda kv: kv[1].lower())
    ]


def _top(counter: Counter, n: int = _TOP_N) -> list[dict]:
    total_max = counter.most_common(1)[0][1] if counter else 0
    rows = []
    for key, count in counter.most_common(n):
        rows.append({
            "label": key,
            "count": count,
            "pct": round(100 * count / total_max) if total_max else 0,
        })
    return rows


def _breakdown(counter: Counter, n: int | None = None) -> dict:
    """Desglose de un DRILL-DOWN: por default COMPLETO.

    `_top` recorta a 10 sin decirlo. Para el leaderboard de la vista general
    eso esta bien —es explicitamente un "top"—, pero un drill-down que
    aparenta ser exhaustivo y omite filas en silencio miente sobre el
    universo que dice desglosar. Aca las filas CONSERVAN por default.

    `total` (suma del universo) y `usuarios` (cardinalidad) viajan siempre,
    aun si algun consumidor decide recortar: la conservacion se puede
    verificar sin depender de cuantas filas se pintaron.
    """
    total = sum(counter.values())
    maximo = counter.most_common(1)[0][1] if counter else 0
    filas = counter.most_common(n) if n else counter.most_common()
    return {
        "rows": [{"label": k, "count": c,
                  "pct": round(100 * c / maximo) if maximo else 0}
                 for k, c in filas],
        "total": total,
        "usuarios": len(counter),
        "shown": len(filas),
        "truncated": bool(n) and len(counter) > len(filas),
    }


def _top_modules(counter: Counter, n: int = _TOP_N) -> list[dict]:
    """Como _top() pero con `key` (identificador estable) Y `label` (visible,
    ver module_label) por separado — el template usa `label` tanto para
    mostrar como para el cross-reference clickeable con
    `module_event_breakdowns` (data-module / data-module-breakdown), así que
    ambos DEBEN construirse juntos acá para no desincronizarse. Para módulos
    de Copiloto (sin entrada en MODULE_LABELS) label == key, sin cambios."""
    total_max = counter.most_common(1)[0][1] if counter else 0
    rows = []
    for key, count in counter.most_common(n):
        rows.append({
            "key": key,
            "label": module_label(key),
            "count": count,
            "pct": round(100 * count / total_max) if total_max else 0,
        })
    return rows


def _event_breakdown_rows(counter: Counter) -> list[dict]:
    """Igual que _top() pero con clave `event` en vez de `label` (y sin tope N:
    la cantidad de tipos de evento distintos es chica) — mismo shape para el
    panel global de "Eventos por tipo" y cada panel module-scoped, así el
    template comparte un solo bloque Jinja para los dos casos. Incluye
    `event_class` (corrective A2) para que el template module-scoped
    (module_event_breakdowns, que SÍ mezcla uso+diagnóstico de un módulo) pueda
    separarlos con `selectattr`/`rejectattr` sin duplicar la lógica de
    clasificación."""
    total_max = counter.most_common(1)[0][1] if counter else 0
    return [
        {
            "event": event, "count": count,
            "pct": round(100 * count / total_max) if total_max else 0,
            "event_class": _classify_event(event),
        }
        for event, count in counter.most_common()
    ]


_TREE_START_EVENT = "tree_started"
_TREE_PATH_EVENTS = {"tree_step", "tree_outcome"}


def _tree_summary(
    all_events: list[dict], user: str, module: str,
    run_page: int = 1, run_page_size: int = _DEFAULT_STORY_PAGE_SIZE,
) -> dict:
    """Reconstruye la actividad de árboles de decisión a partir de dos eventos:
    - `tree_started` (BASIC-VERBOSE): trae `tree_id`, `tree_title`, `panel`, `stem`
      → sabemos QUÉ árbol se abrió y desde dónde.
    - `tree_step`/`tree_outcome` (VERBOSE): traen `run_id`, `answer` (opción
      elegida en cada nodo), `leaf`/`score` en el outcome → reconstruimos el
      RECORRIDO (camino de respuestas + resultado).

    Los eventos de recorrido NO llevan módulo en el payload (solo `tree_started`
    lo tiene, vía `stem`), así que la atribución por módulo de un recorrido se
    hace mapeando su `tree_id` al módulo del `tree_started` correspondiente. Por
    eso este resumen se calcula sobre `all_events` (pre-filtro) y aplica los
    filtros de usuario/módulo acá adentro, no sobre la lista ya filtrada (donde
    los step/outcome se caerían al filtrar por módulo)."""
    title_by_id: dict[str, str] = {}
    module_by_id: dict[str, str] = {}
    for ev in all_events:
        if ev["event"] != _TREE_START_EVENT:
            continue
        payload = ev["payload"]
        tid = str(payload.get("tree_id") or "").strip()
        if not tid:
            continue
        title_by_id.setdefault(tid, str(payload.get("tree_title") or "").strip() or tid)
        if ev["module"]:
            module_by_id.setdefault(tid, ev["module"])

    started: dict[str, dict] = {}
    for ev in all_events:
        if ev["event"] != _TREE_START_EVENT:
            continue
        if (user and ev["user"] != user) or (module and ev["module"] != module):
            continue
        payload = ev["payload"]
        tid = str(payload.get("tree_id") or "").strip() or "—"
        row = started.setdefault(tid, {"tree_id": tid, "title": title_by_id.get(tid, tid), "panels": set(), "count": 0})
        row["count"] += 1
        panel = str(payload.get("panel") or "").strip()
        if panel:
            row["panels"].add(panel)
    started_rows = sorted(started.values(), key=lambda r: r["count"], reverse=True)
    max_start = started_rows[0]["count"] if started_rows else 0
    for row in started_rows:
        row["panels"] = ", ".join(sorted(row["panels"]))
        row["pct"] = round(100 * row["count"] / max_start) if max_start else 0

    runs: dict[str, dict] = {}
    order: list[str] = []
    for ev in all_events:
        if ev["event"] not in _TREE_PATH_EVENTS:
            continue
        payload = ev["payload"]
        run_id = str(payload.get("run_id") or "").strip()
        if not run_id:
            continue
        tid = str(payload.get("tree_id") or "").strip()
        run_module = module_by_id.get(tid, "")
        if (user and ev["user"] != user) or (module and run_module != module):
            continue
        if run_id not in runs:
            runs[run_id] = {
                "tree_id": tid,
                "title": title_by_id.get(tid, tid or "—"),
                "module": run_module,
                "user": ev["name"] or ev["user"],
                "ts_utc": ev["ts_utc"],
                "steps": [],
                "outcome": None,
            }
            order.append(run_id)
        run = runs[run_id]
        answer = _TONE_TAG_RE.sub("", str(payload.get("answer") or "")).strip()
        if answer:
            run["steps"].append(answer)
        if ev["event"] == "tree_outcome" or payload.get("reached_leaf"):
            score = payload.get("score")
            score_display = str(score) if isinstance(score, (int, float, str)) and str(score).strip() else ""
            run["outcome"] = {
                "leaf": _TONE_TAG_RE.sub("", str(payload.get("leaf") or "").strip()),
                "score": score_display,
            }
    run_rows = [runs[k] for k in order]
    run_rows.sort(key=lambda r: r["ts_utc"] or "", reverse=True)

    # Misma paginación server-side que "Historia de preguntas" (activity_pager):
    # los recorridos crecen igual que las conversaciones, un cap fijo se vuelve
    # inmanejable con actividad real.
    runs_total = len(run_rows)
    page_size = run_page_size if run_page_size in _STORY_PAGE_SIZES else _DEFAULT_STORY_PAGE_SIZE
    total_pages = max(1, -(-runs_total // page_size)) if runs_total else 1
    page = max(1, min(run_page or 1, total_pages))
    start = (page - 1) * page_size
    run_page_rows = run_rows[start:start + page_size]

    return {
        "tree_started": started_rows,
        "tree_runs": run_page_rows,
        "tree_runs_total": runs_total,
        "tree_runs_page": page,
        "tree_runs_page_size": page_size,
        "tree_runs_total_pages": total_pages,
        "tree_runs_page_sizes": _STORY_PAGE_SIZES,
        "tree_runs_page_window": _page_window(page, total_pages),
    }


def build_summary(
    date_from: str, date_to: str, user: str = "", module: str = "",
    story_page: int = 1, story_page_size: int = _DEFAULT_STORY_PAGE_SIZE,
    run_page: int = 1, run_page_size: int = _DEFAULT_STORY_PAGE_SIZE,
    scopes: list[str] | None = None, areas: list[str] | None = None,
) -> dict:
    # Danwor Hub-wide (Batch A2): el dashboard opera sobre TODO el stream
    # normalizado — la exclusión interina de Operations (A1) fue RETIRADA. Filtros
    # explícitos: date (a nivel de archivo en _read_events) + user, luego
    # scope (Ámbito) / area (Área) con faceting UNIDIRECCIONAL, y por último
    # module (transversal, corrective A2) — ver abajo por qué module se
    # resuelve DESPUÉS de scope/area en vez de junto con user.
    all_events = _read_events(date_from, date_to)
    available_users = _user_facets(all_events)
    feedback_by_reply = _read_feedback(date_from, date_to)

    user = (user or "").strip()
    module = (module or "").strip()
    # Ámbito/Área derivan de fecha+usuario — NUNCA de Módulo. Antes Módulo
    # SÍ influía qué Ámbitos/Áreas se ofrecían (via el viejo `base`, que ya
    # incluía el filtro de módulo) — backwards: una dimensión de más abajo en
    # la jerarquía (scope→area→module→event) no debería re-facetear a las de
    # arriba. Mismo principio ya aplicado a Ámbito/Área entre sí.
    user_filtered = [ev for ev in all_events if not user or ev["user"] == user]

    # Ámbito/Área con faceting UNIDIRECCIONAL (Ámbito → Área), derivado del
    # universo filtrado (nunca hardcodeado). Selección vacía/None = TODO
    # (default Hub-wide). Una selección de Área que queda inválida al cambiar
    # Ámbito se descarta determinísticamente (intersección con lo disponible).
    # No se fabrican opciones ausentes del dataset.
    #
    # Ámbito NO se re-facetea por la selección de Área: si lo hiciera (como
    # antes), destildar un Ámbito angosta Área, lo que a su vez podía angostar
    # Ámbito de vuelta y hacer desaparecer su propia opción — sin forma de
    # volver a tildarla (reportado por el owner). `scope_facets` usa siempre
    # `present_scopes` (el universo COMPLETO, pre-cualquier selección) para
    # quedar estable; solo Área reacciona al Ámbito elegido.
    # Área tampoco cambia de CANTIDAD de opciones al cambiar Ámbito — listarla
    # siempre completa (como Ámbito) y marcar `disabled` en vez de sacar del
    # DOM las que no aplican evita que la grilla salte de alto/columna cada
    # vez que se toca Ámbito (reportado por el owner). `disabled` (no solo
    # visual): un checkbox disabled no dispara `change` ni se manda en el
    # submit, así que la opción incompatible queda de hecho excluida sin
    # necesitar lógica extra.
    requested_scopes = {s for s in (scopes or []) if s}
    requested_areas = {a for a in (areas or []) if a}
    present_scopes = {ev["scope"] for ev in user_filtered}
    present_areas = {ev["area"] for ev in user_filtered}
    sel_scopes = (requested_scopes & present_scopes) or present_scopes
    areas_for_scopes = {ev["area"] for ev in user_filtered if ev["scope"] in sel_scopes}
    sel_areas = (requested_areas & areas_for_scopes) or areas_for_scopes
    scope_facets = [
        {"key": k, "label": scope_label(k), "checked": k in sel_scopes}
        for k in sorted(present_scopes)
    ]
    area_facets = [
        {"key": k, "label": area_label(k), "checked": k in sel_areas, "disabled": k not in areas_for_scopes}
        for k in sorted(present_areas)
    ]

    scope_area_user_filtered = [
        ev for ev in user_filtered if ev["scope"] in sel_scopes and ev["area"] in sel_areas
    ]
    # Módulo (dimensión TRANSVERSAL opcional, corrective A2 — Copilot Y
    # Operations por igual): universo faceteado por fecha+usuario+Ámbito+Área,
    # nunca se fabrica una opción ausente del dataset. Si la selección pedida
    # ya no aplica tras un cambio de Ámbito/Área (mismo criterio determinístico
    # que Área con Ámbito), se descarta en vez de dejar el dashboard en un
    # estado "0 eventos" silencioso.
    module_universe = sorted({ev["module"] for ev in scope_area_user_filtered if ev["module"]})
    if module and module not in module_universe:
        module = ""
    available_modules = [{"key": m, "label": module_label(m)} for m in module_universe]

    events = [ev for ev in scope_area_user_filtered if not module or ev["module"] == module]
    # Universo scope/area-filtrado SIN usuario (pre user/module) para lentes
    # Copilot con filtrado interno propio (árboles, que resuelven título/
    # módulo de CUALQUIER usuario antes de aplicar su propio filtro de
    # usuario/módulo) — mismo criterio que antes de este corrective, sin
    # cambios: así se vacían solas cuando el Ámbito seleccionado excluye al
    # Copiloto.
    scoped_all_events = [
        ev for ev in all_events if ev["scope"] in sel_scopes and ev["area"] in sel_areas
    ]

    total_events = 0
    users_seen: set[str] = set()
    usage_areas_seen: set[str] = set()
    # "USAGE DASHBOARD SEMANTIC CLEANUP": este dashboard responde "quién usa
    # Danwor Hub, qué usa, cómo evoluciona" — NUNCA "¿falló Graph?" o "¿faltó
    # config?". Por eso el read-model completo de acá abajo (event_counts,
    # métricas de adopción, desgloses por usuario/módulo) filtra a
    # event_class == usage, con un solo `continue` temprano en el loop — no
    # dos counters ni un split visual. `unknown` tampoco entra: un evento sin
    # clasificar nunca contamina adopción en silencio. El diagnóstico sigue
    # 100% persistido/normalizado/clasificable (activity_service/
    # activity_context) para una futura herramienta owner-only — este batch
    # no la crea, solo saca el diagnóstico de ESTE dashboard.
    event_counts: Counter = Counter()
    daily_counts: Counter = Counter()
    module_counts: Counter = Counter()
    user_counts: Counter = Counter()
    catalog_kind_counts: Counter = Counter()
    # Desglose por evento para el panel derecho interactivo: por cada tipo de
    # evento, quién lo generó cuántas veces (catalog_view usa su propio desglose
    # por `kind`, ver más abajo). Usage-only, igual que el resto de este loop.
    event_user_counts: dict[str, Counter] = {}
    # Desglose de "Acciones de usuario" POR MÓDULO, para conectar el clic en
    # un módulo (sección "Módulos y usuarios") con un filtro de esta lista —
    # mismo patrón client-side que event_user_counts, ver initModuleFilter en
    # app.js. Usage-only: un data_load_failed dentro de Asignaciones no debe
    # aparecer al clickear "Asignaciones" en este dashboard.
    module_event_counts: dict[str, Counter] = {}

    # CASCADE INTEGRITY (corrective de confianza). El desglose por usuario del
    # panel derecho tiene que resolverse por (modulo, evento), NUNCA por evento
    # solo. `event_user_counts` esta llaveado por NOMBRE de evento, y nombres
    # como `module_viewed` existen a proposito en Copiloto, Operaciones y
    # Comercial: al filtrar por modulo a la izquierda, la derecha seguia
    # mostrando el universo entero del nombre. La izquierda decia 175 y la
    # derecha sumaba 978 — una falla de conservacion que ademas insinuaba que
    # usuarios sin autorizacion habian entrado a un modulo sensible.
    module_event_user_counts: dict[str, dict[str, Counter]] = {}
    # Conversaciones del asistente agrupadas por conversation_id: cada "turno" es un
    # assistant_query (router, o el bypass de "Explícame vos"), con su feedback si
    # hubo. Eventos viejos sin conversation_id quedan como conversación de un solo
    # turno (mismo comportamiento que antes de esta correlación).
    assistant_conversations: dict[str, dict] = {}
    assistant_order: list[str] = []
    ts_min: str | None = None
    ts_max: str | None = None
    files_scanned = len({ev["day"] for ev in all_events})

    for ev in events:
        # Boundary del dashboard de USO (ver comentario arriba de los
        # counters): todo lo que sigue en este loop — event_counts, métricas
        # de adopción, event_user_counts, module_event_counts — es
        # exclusivamente event_class == usage. `diagnostic` y `unknown`
        # simplemente nunca entran acá; siguen existiendo en `events`/
        # `all_events` para quien los necesite (árboles vía scoped_all_events,
        # o una futura herramienta owner-only).
        if ev["event_class"] != EVENT_CLASS_USAGE:
            continue

        event_counts[ev["event"]] += 1
        total_events += 1
        daily_counts[ev["day"]] += 1
        usage_areas_seen.add(ev["area"])
        if ev["user"]:
            users_seen.add(ev["user"])
            label = ev["name"] or ev["user"]
            user_counts[label] += 1
            event_user_counts.setdefault(ev["event"], Counter())[label] += 1

        if ev["ts_utc"]:
            ts_min = ev["ts_utc"] if ts_min is None else min(ts_min, ev["ts_utc"])
            ts_max = ev["ts_utc"] if ts_max is None else max(ts_max, ev["ts_utc"])

        # Top Modules: una VISITA de módulo es `event == module_viewed`, no
        # cualquier evento de uso que ocurra adentro del módulo.
        if ev["module"] and ev["event"] == EVENT_MODULE_VIEWED:
            module_counts[ev["module"]] += 1
        if ev["module"]:
            module_event_counts.setdefault(ev["module"], Counter())[ev["event"]] += 1
            # El cubo de la cascada. MISMA identidad de display que
            # `event_user_counts` (`name or user`) para que la comparacion
            # contra el oraculo server-side sea de igual a igual.
            if ev["user"]:
                (module_event_user_counts
                 .setdefault(ev["module"], {})
                 .setdefault(ev["event"], Counter())[ev["name"] or ev["user"]]) += 1

        if ev["event"] == "catalog_view":
            kind = str(ev["payload"].get("kind") or "").strip()
            if kind:
                catalog_kind_counts[kind] += 1
        elif ev["event"] == "assistant_query":
            question = str(ev["payload"].get("question") or "").strip()
            if question:
                conv_id = str(ev["payload"].get("conversation_id") or "").strip()
                reply_id = str(ev["payload"].get("reply_id") or "").strip()
                key = conv_id or f"_solo:{len(assistant_order)}"
                if key not in assistant_conversations:
                    assistant_conversations[key] = {
                        "question": question[:200],
                        "user": ev["name"] or ev["user"],
                        "turns": [],
                    }
                    assistant_order.append(key)
                assistant_conversations[key]["turns"].append({
                    "ts_utc": ev["ts_utc"],
                    # Cada turno guarda SU propia pregunta: dentro de una misma
                    # conversación puede repetirse (router → "Explícame vos" reenvía
                    # el mismo texto) o ser una pregunta nueva (el usuario siguió
                    # charlando) — el template decide si la vuelve a mostrar.
                    "question": question[:200],
                    "routed": bool(ev["payload"].get("routed")),
                    "route_heading": _TONE_TAG_RE.sub("", str(ev["payload"].get("route_heading") or "")).strip(),
                    "bypass": bool(ev["payload"].get("bypass")),
                    "llm": bool(ev["payload"].get("llm")),
                    "api_cost": bool(ev["payload"].get("api_cost")),
                    "answer": (_ans := _TONE_TAG_RE.sub("", str(ev["payload"].get("answer") or "")).strip()),
                    "answer_html": _answer_html(_ans),
                    "feedback": feedback_by_reply.get(reply_id),
                })

    daily_series = [
        {"day": day, "count": count, "pct": round(100 * count / max(daily_counts.values())) if daily_counts else 0}
        for day, count in sorted(daily_counts.items())
    ]
    total_days = len(daily_counts)

    # Resumen precalculado por conversación (fila colapsada del dashboard) + KPI de
    # costo: cuántas conversaciones tuvieron AL MENOS un turno con llamada real a
    # OpenAI (no todas las "preguntas al asistente" cuestan — router/simulación no).
    assistant_api_cost_count = 0
    for conv in assistant_conversations.values():
        turns = conv["turns"]
        conv["turn_count"] = len(turns)
        up_count = sum(1 for t in turns if t["feedback"] and t["feedback"]["rating"] == "up")
        down_count = sum(1 for t in turns if t["feedback"] and t["feedback"]["rating"] == "down")
        # Un solo ícono de feedback por conversación (no uno por turno): si todas las
        # reacciones coinciden (o hay una sola), se muestra esa; si hay opiniones
        # encontradas dentro del mismo hilo, se muestra un ícono neutral distinto
        # ("mixed") en vez de mostrar 👍 y 👎 juntos, que sería confuso.
        if up_count and down_count:
            conv["feedback_summary"] = "mixed"
        elif up_count:
            conv["feedback_summary"] = "up"
        elif down_count:
            conv["feedback_summary"] = "down"
        else:
            conv["feedback_summary"] = ""
        conv["has_api_cost"] = any(t["api_cost"] for t in turns)
        if conv["has_api_cost"]:
            assistant_api_cost_count += 1

    # Paginación real sobre TODA la conversación filtrada (no un cap fijo): más
    # reciente primero (assistant_order queda en orden de PRIMERA aparición,
    # se invierte acá).
    all_keys_desc = list(reversed(assistant_order))
    assistant_stories_total = len(all_keys_desc)
    page_size = story_page_size if story_page_size in _STORY_PAGE_SIZES else _DEFAULT_STORY_PAGE_SIZE
    total_pages = max(1, -(-assistant_stories_total // page_size)) if assistant_stories_total else 1
    page = max(1, min(story_page or 1, total_pages))
    start = (page - 1) * page_size
    assistant_stories = [assistant_conversations[k] for k in all_keys_desc[start:start + page_size]]

    trees = _tree_summary(scoped_all_events, user, module, run_page, run_page_size)

    # Desglose interactivo: al clickear un evento en el panel izquierdo, el
    # derecho muestra su detalle. catalog_view abre por tipo de vista (`kind`);
    # el resto abre por usuario (quién lo generó). Se precalculan todos y el
    # front togglea con JS (sin request extra) — ver initEventBreakdown en app.js.
    event_breakdowns: dict[str, dict] = {}
    for event, _count in event_counts.most_common():
        if event == "catalog_view":
            event_breakdowns[event] = {"dim": "tipo de vista", **_breakdown(catalog_kind_counts)}
        else:
            event_breakdowns[event] = {"dim": "usuario",
                                       **_breakdown(event_user_counts.get(event, Counter()))}

    return {
        "date_from": date_from,
        "date_to": date_to,
        "selected_user": user,
        "selected_module": module,
        "available_users": available_users,
        "available_modules": available_modules,
        "files_scanned": files_scanned,
        "total_events": total_events,
        "unique_users": len(users_seen),
        "total_days": total_days,
        "avg_per_day": round(total_events / total_days) if total_days else 0,
        # Hub-wide (Batch A2): facets de Ámbito/Área + KPI genérico cross-scope +
        # flag para gatear las lentes Copilot-específicas.
        "scope_facets": scope_facets,
        "area_facets": area_facets,
        # Áreas activas: uso-driven (mismo criterio que el resto de las
        # métricas de adopción, corrective A2) — un área que solo generó
        # diagnóstico no cuenta como "activa".
        "areas_active": len(usage_areas_seen),
        "copilot_present": any(ev["scope"] == "copilot" for ev in events),
        "assistant_query_count": event_counts.get("assistant_query", 0),
        "assistant_api_cost_count": assistant_api_cost_count,
        "ts_min": ts_min,
        "ts_max": ts_max,
        # "Eventos" (USAGE DASHBOARD SEMANTIC CLEANUP): usage-only — ver el
        # `continue` temprano del loop de arriba. Un solo set de filas, sin
        # split visual de uso/diagnóstico.
        "event_counts": _event_breakdown_rows(event_counts),
        # Label visible por evento canónico (p. ej. module_viewed → "Vista de
        # módulo") — mismo seam que event_descriptions, fallback seguro al
        # nombre crudo si no hay entrada (ver EVENT_LABELS/event_label en
        # activity_context.py). Sin framework de localización.
        "event_labels": EVENT_LABELS,
        "top_modules": _top_modules(module_counts),
        "top_users": _top(user_counts),
        # Mismo tope que top_modules (los módulos que realmente aparecen como fila
        # clickeable): no tiene sentido precalcular el desglose de un módulo que
        # ni se muestra. Keyed por LABEL visible (no por key interno) — así
        # coincide con `data-module="{{ row.label }}"` en el template; lookup
        # por `row["key"]` (interno) contra module_event_counts.
        "module_event_breakdowns": {
            row["label"]: _event_breakdown_rows(module_event_counts.get(row["key"], Counter()))
            for row in _top_modules(module_counts)
        },
        # CASCADE INTEGRITY: el desglose por usuario DENTRO de un modulo. Es lo
        # que le faltaba a la cascada — con esto la etapa de la derecha se
        # resuelve por (modulo, evento) y hereda el filtro de la izquierda en
        # vez de descartarlo. Mismo keyed-por-LABEL que `module_event_breakdowns`
        # (coincide con `data-module="{{ row.label }}"` del template), y COMPLETO:
        # un drill-down no puede omitir filas en silencio.
        "module_event_user_breakdowns": {
            row["label"]: {
                evento: {"dim": "usuario", **_breakdown(contador)}
                for evento, contador in module_event_user_counts.get(row["key"], {}).items()
            }
            for row in _top_modules(module_counts)
        },
        "catalog_kind_counts": [
            {"kind": kind, "count": count}
            for kind, count in catalog_kind_counts.most_common()
        ],
        "daily_series": daily_series,
        "assistant_stories": assistant_stories,
        "assistant_stories_total": assistant_stories_total,
        "assistant_stories_page": page,
        "assistant_stories_page_size": page_size,
        "assistant_stories_total_pages": total_pages,
        "assistant_stories_page_sizes": _STORY_PAGE_SIZES,
        "assistant_stories_page_window": _page_window(page, total_pages),
        "tree_started": trees["tree_started"],
        "tree_runs": trees["tree_runs"],
        "tree_runs_total": trees["tree_runs_total"],
        "tree_runs_page": trees["tree_runs_page"],
        "tree_runs_page_size": trees["tree_runs_page_size"],
        "tree_runs_total_pages": trees["tree_runs_total_pages"],
        "tree_runs_page_sizes": trees["tree_runs_page_sizes"],
        "tree_runs_page_window": trees["tree_runs_page_window"],
        "event_breakdowns": event_breakdowns,
        "event_descriptions": _EVENT_DESCRIPTIONS,
    }
