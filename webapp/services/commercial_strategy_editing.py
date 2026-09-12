"""commercial_strategy_editing.py — Validación server-side del payload de
edición de Estrategia Comercial (Commercial Strategy — real editing pass).

Frontera de confianza: el cliente NUNCA es la fuente de verdad de qué se
persiste. `merge_editable_fields(payload)` es el ÚNICO punto que traduce un
JSON crudo llegado por HTTP en algo que
`commercial_strategy_repository.save_strategy()` puede escribir — devuelve
SOLO las claves conocidas (`EDITABLE_TOP_KEYS`), cada una validada por forma/
longitud/enum, o levanta `ValidationError` (rechazo atómico: nada parcial se
persiste nunca, ver el router).

Alcance editable ("COMPLETE VISIBLE BUSINESS VALUE EDITABILITY" gate, ver
HANDOFF): TODO valor de negocio autorado y visible en la Estrategia. Lo
único que queda código+seed es lo genuinamente ESTRUCTURAL:
  - eyebrow/title de cada sección (encabezado — MISMO criterio que el
    título de módulo de Copilot, nunca editable ahí tampoco)
  - ids técnicos (`hero.nav[].id`, `segments.items[].id`, `funnel[].level`,
    `annex_process.stages[].n`, `annex_rules.rules[].n`) — se derivan de la
    POSICIÓN en el array, nunca se confía en lo que mande el cliente
  - la forma/cardinalidad de `hero.bignums` (siempre 4) y `funnel` (siempre
    4, visualización de embudo — ver gate item 7)
  - `access.kind` de un segmento/subtab (list vs table) — no se puede
    cambiar el TIPO de bloque de acceso desde el cliente, solo editar su
    contenido si ya existe

Ningún valor visible de negocio se clasificó como DERIVADO: se auditaron
`ticket_table`/`sensitivity_table` contra `objectives.calc_note` y no hay
una fórmula implementada en código en ningún lado del Hub — son 3 columnas
independientes que un humano mantiene sincronizadas a mano en el seed. Por
el propio criterio del gate ("si no existe una fórmula real, no es
derivado — debe ser editable"), las 3 columnas de esas tablas son
CANONICAL INPUT, no DERIVED — inventar un motor de parseo/cálculo de
strings tipo "USD 20K" sería una pieza de lógica nueva y frágil que
ningún otro lugar del Hub tiene, desproporcionada frente al problema real
("ningún valor visible sin vía de edición")."""
from __future__ import annotations

EDITABLE_TOP_KEYS = (
    "hero", "objectives", "demand_generation", "segments", "focus",
    "capacity", "cadence", "exclusions", "next_steps", "annex_map",
    "annex_roles", "annex_process", "annex_rules",
)

_Q_ENUM = {"Q1", "Q2", "Q3", "Q4", "a_definir"}
_MAX_TEXT = 4000
_MAX_SHORT = 300
_MAX_VALUE = 60
_MAX_ID = 40
_MAX_TASKS = 60
_MAX_LIST_ITEMS = 20
_MAX_ROWS = 40
_MAX_COLS = 8
_MAX_TREE_DEPTH = 6  # generoso frente a la profundidad real (3 niveles bajo root)
_MAX_SEGMENTS = 12
_MAX_SUBTABS = 8


class ValidationError(Exception):
    """Payload rechazado — mensaje SEGURO para mostrar al usuario (nunca
    incluye datos sensibles, solo el nombre del campo y la regla violada)."""


def _text(value, *, max_len: int, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field}: se esperaba texto")
    cleaned = value.strip()
    if len(cleaned) > max_len:
        raise ValidationError(f"{field}: excede el máximo de {max_len} caracteres")
    return cleaned


def _bool(value) -> bool:
    return bool(value)


def _kpi(raw, *, field: str) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError(f"{field}: forma inválida")
    return {
        "label": _text(raw.get("label", ""), max_len=200, field=f"{field}.label"),
        "value": _text(raw.get("value", ""), max_len=_MAX_VALUE, field=f"{field}.value"),
        "detail": _text(raw.get("detail", ""), max_len=_MAX_TEXT, field=f"{field}.detail"),
    }


def _kpi_list(raw, *, field: str, max_items: int) -> list[dict]:
    if not isinstance(raw, list) or not raw or len(raw) > max_items:
        raise ValidationError(f"{field}: lista inválida (1 a {max_items} elementos)")
    return [_kpi(item, field=f"{field}[{i}]") for i, item in enumerate(raw)]


def _string_list(raw, *, field: str, max_items: int = _MAX_LIST_ITEMS, max_len: int = 800) -> list[str]:
    if not isinstance(raw, list) or len(raw) > max_items:
        raise ValidationError(f"{field}: lista inválida (máx {max_items} elementos)")
    return [_text(v, max_len=max_len, field=f"{field}[{i}]") for i, v in enumerate(raw)]


def _table_rows(raw, *, field: str) -> list[list[str]]:
    if not isinstance(raw, list) or len(raw) > _MAX_ROWS:
        raise ValidationError(f"{field}: lista inválida (máx {_MAX_ROWS} filas)")
    out = []
    for i, row in enumerate(raw):
        if not isinstance(row, list) or not row or len(row) > _MAX_COLS:
            raise ValidationError(f"{field}[{i}]: fila inválida")
        out.append([_text(c, max_len=_MAX_SHORT, field=f"{field}[{i}][{j}]") for j, c in enumerate(row)])
    return out


def _table(raw, *, field: str, current_table) -> dict:
    """Tabla genérica `{headers, rows}` — `headers` es ESTRUCTURAL (rótulos
    de columna, nunca contenido de negocio propio de una fila) y se toma
    SIEMPRE de `current_table` (el documento canónico ya cargado), nunca
    del payload del cliente — evita depender de que `save()` haga un merge
    recursivo dentro de listas para no perder esta clave en cada edición.
    `rows` es 100% lo que mande el cliente (agregar/eliminar fila
    soportado; cada celda es texto de negocio)."""
    if not isinstance(current_table, dict) or not isinstance(current_table.get("headers"), list):
        raise ValidationError(f"{field}: tabla canónica ausente o inválida")
    rows_raw = raw.get("rows") if isinstance(raw, dict) else None
    return {"headers": current_table["headers"], "rows": _table_rows(rows_raw, field=f"{field}.rows")}


def _validate_hero(raw, current) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("hero: forma inválida")
    bignums_raw = raw.get("bignums")
    if not isinstance(bignums_raw, list) or len(bignums_raw) != 4:
        raise ValidationError("hero.bignums: se esperan exactamente 4 elementos")
    bignums = []
    for i, bn in enumerate(bignums_raw):
        if not isinstance(bn, dict):
            raise ValidationError(f"hero.bignums[{i}]: forma inválida")
        bignums.append({
            "value": _text(bn.get("value", ""), max_len=_MAX_VALUE, field=f"hero.bignums[{i}].value"),
            "label": _text(bn.get("label", ""), max_len=200, field=f"hero.bignums[{i}].label"),
        })
    return {
        "kicker": _text(raw.get("kicker", ""), max_len=200, field="hero.kicker"),
        "title_prefix": _text(raw.get("title_prefix", ""), max_len=200, field="hero.title_prefix"),
        "title_accent": _text(raw.get("title_accent", ""), max_len=200, field="hero.title_accent"),
        "lead": _text(raw.get("lead", ""), max_len=_MAX_TEXT, field="hero.lead"),
        "bignums": bignums,
    }


def _validate_funnel(raw) -> list[dict]:
    """`level` es posicional/estructural (gobierna el orden visual del
    embudo, ver gate item 7: "technical stage IDs/topology may remain
    fixed") — SIEMPRE se deriva del índice, nunca del payload. Cardinalidad
    fija en 4 (la visualización del embudo, ver commercial_strategy.css
    `.cs-frow`), sin agregar/eliminar filas."""
    if not isinstance(raw, list) or len(raw) != 4:
        raise ValidationError("objectives.funnel: se esperan exactamente 4 elementos")
    out = []
    for i, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValidationError(f"objectives.funnel[{i}]: forma inválida")
        out.append({
            "level": i + 1,
            "label": _text(row.get("label", ""), max_len=200, field=f"objectives.funnel[{i}].label"),
            "value": _text(row.get("value", ""), max_len=_MAX_VALUE, field=f"objectives.funnel[{i}].value"),
            "sub": _text(row.get("sub", ""), max_len=300, field=f"objectives.funnel[{i}].sub"),
        })
    return out


def _validate_objectives(raw, current: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("objectives: forma inválida")
    cur = current.get("objectives", {})
    return {
        "intro": _text(raw.get("intro", ""), max_len=_MAX_TEXT, field="objectives.intro"),
        "result_targets": _kpi_list(raw.get("result_targets"), field="objectives.result_targets", max_items=6),
        "calc_note": _text(raw.get("calc_note", ""), max_len=_MAX_TEXT, field="objectives.calc_note"),
        "ticket_table": _table(raw.get("ticket_table"), field="objectives.ticket_table", current_table=cur.get("ticket_table")),
        "funnel": _validate_funnel(raw.get("funnel")),
        "demand_targets": _kpi_list(raw.get("demand_targets"), field="objectives.demand_targets", max_items=6),
        "coverage_note": _text(raw.get("coverage_note", ""), max_len=_MAX_TEXT, field="objectives.coverage_note"),
        "sensitivity_note": _text(raw.get("sensitivity_note", ""), max_len=_MAX_TEXT, field="objectives.sensitivity_note"),
        "sensitivity_table": _table(raw.get("sensitivity_table"), field="objectives.sensitivity_table", current_table=cur.get("sensitivity_table")),
        "assumptions_pending": _string_list(raw.get("assumptions_pending"), field="objectives.assumptions_pending"),
    }


def _validate_calendar(raw) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > 12:
        raise ValidationError("demand_generation.calendar: lista inválida (máx 12 meses)")
    out = []
    for i, month in enumerate(raw):
        if not isinstance(month, dict):
            raise ValidationError(f"demand_generation.calendar[{i}]: forma inválida")
        items_raw = month.get("items")
        if not isinstance(items_raw, list) or len(items_raw) > _MAX_LIST_ITEMS:
            raise ValidationError(f"demand_generation.calendar[{i}].items: lista inválida")
        items = []
        for j, it in enumerate(items_raw):
            if not isinstance(it, dict):
                raise ValidationError(f"demand_generation.calendar[{i}].items[{j}]: forma inválida")
            items.append({
                "name": _text(it.get("name", ""), max_len=200, field=f"demand_generation.calendar[{i}].items[{j}].name"),
                "is_date": _bool(it.get("is_date")),
                "muted": _bool(it.get("muted")),
                "sub": _text(it.get("sub", ""), max_len=300, field=f"demand_generation.calendar[{i}].items[{j}].sub"),
            })
        out.append({
            "month": _text(month.get("month", ""), max_len=60, field=f"demand_generation.calendar[{i}].month"),
            "items": items,
        })
    return out


def _validate_clusters(raw) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > _MAX_LIST_ITEMS:
        raise ValidationError("demand_generation.clusters: lista inválida")
    out = []
    for i, c in enumerate(raw):
        if not isinstance(c, dict):
            raise ValidationError(f"demand_generation.clusters[{i}]: forma inválida")
        out.append({
            "tag": _text(c.get("tag", ""), max_len=200, field=f"demand_generation.clusters[{i}].tag"),
            "name": _text(c.get("name", ""), max_len=200, field=f"demand_generation.clusters[{i}].name"),
            "bullets": _string_list(c.get("bullets"), field=f"demand_generation.clusters[{i}].bullets"),
            "message": _text(c.get("message", ""), max_len=_MAX_TEXT, field=f"demand_generation.clusters[{i}].message"),
        })
    return out


def _validate_channel_box(raw, *, field: str) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError(f"{field}: forma inválida")
    return {
        "title": _text(raw.get("title", ""), max_len=200, field=f"{field}.title"),
        "items": _string_list(raw.get("items"), field=f"{field}.items"),
    }


def _validate_demand_generation(raw, current: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("demand_generation: forma inválida")
    channels_raw = raw.get("channels")
    if not isinstance(channels_raw, dict):
        raise ValidationError("demand_generation.channels: forma inválida")
    cur = current.get("demand_generation", {})
    return {
        "intro": _text(raw.get("intro", ""), max_len=_MAX_TEXT, field="demand_generation.intro"),
        "demand_target": _kpi(raw.get("demand_target"), field="demand_generation.demand_target"),
        "clusters": _validate_clusters(raw.get("clusters")),
        "channels": {
            "inbound": _validate_channel_box(channels_raw.get("inbound"), field="demand_generation.channels.inbound"),
            "outbound": _validate_channel_box(channels_raw.get("outbound"), field="demand_generation.channels.outbound"),
        },
        "events_catalog": _table(raw.get("events_catalog"), field="demand_generation.events_catalog", current_table=cur.get("events_catalog")),
        "calendar": _validate_calendar(raw.get("calendar")),
        "owner_note": _text(raw.get("owner_note", ""), max_len=_MAX_TEXT, field="demand_generation.owner_note"),
    }


def _validate_pending(raw, *, field: str):
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError(f"{field}: forma inválida")
    return {
        "title": _text(raw.get("title", ""), max_len=200, field=f"{field}.title"),
        "items": _string_list(raw.get("items"), field=f"{field}.items"),
    }


def _validate_access(raw, *, field: str, current_access):
    """`access.kind` NUNCA se acepta del cliente como cambio de tipo — el
    tipo (table/list) se toma SIEMPRE de `current_access` (estructural,
    MISMO criterio que headers de tabla, ver `_table`); el validador
    valida SOLO el CONTENIDO del tipo que ya existía. `access: null` en
    el payload significa "sin bloque de acceso", igual que el seed — un
    segmento sin acceso hoy no puede ganar uno nuevo desde el cliente
    (agregar un bloque de acceso es topología, no valor de negocio)."""
    if current_access is None:
        return None
    if not isinstance(raw, dict):
        raise ValidationError(f"{field}: forma inválida")
    kind = current_access.get("kind")
    if kind == "table":
        table_raw = raw.get("table")
        return {"kind": "table", "table": _table(table_raw, field=f"{field}.table", current_table=current_access.get("table"))}
    if kind == "list":
        return {"kind": "list", "items": _string_list(raw.get("items"), field=f"{field}.items")}
    raise ValidationError(f"{field}: tipo de acceso canónico desconocido ({kind!r})")


def _validate_extra_table(raw, *, field: str, current_extra_table):
    if current_extra_table is None:
        return None
    return _table(raw, field=field, current_table=current_extra_table)


def _validate_segment_body(raw, *, field: str, current: dict) -> dict:
    """Forma compartida por un segmento de nivel superior Y cada uno de sus
    subtabs (ver strategy.yaml: `notes`/`access`/`strategy`/`pending`/
    `extra_table` idénticos en ambos niveles) — `tab_label` es lo único que
    varía en presencia/nombre según el nivel, así que vive en el caller.
    `current`: el mismo nodo (segmento o subtab) en el documento canónico
    — de ahí sale `access.kind`/headers de tabla, nunca del payload."""
    if not isinstance(raw, dict):
        raise ValidationError(f"{field}: forma inválida")
    return {
        "notes": _string_list(raw.get("notes"), field=f"{field}.notes"),
        "access": _validate_access(raw.get("access"), field=f"{field}.access", current_access=current.get("access")),
        "strategy": _string_list(raw.get("strategy"), field=f"{field}.strategy"),
        "pending": _validate_pending(raw.get("pending"), field=f"{field}.pending"),
        "extra_table": _validate_extra_table(raw.get("extra_table"), field=f"{field}.extra_table", current_extra_table=current.get("extra_table")),
    }


def _validate_subtab(raw, *, field: str, current: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError(f"{field}: forma inválida")
    body = _validate_segment_body(raw, field=field, current=current)
    body["id"] = current["id"]
    body["tab_label"] = _text(raw.get("tab_label", ""), max_len=200, field=f"{field}.tab_label")
    return body


def _validate_segment_item(raw, *, field: str, current: dict) -> dict:
    """`subtabs` es cardinalidad FIJA (ver docstring del módulo — topología
    estructural, MISMO criterio que `hero.bignums`/`funnel`): el payload
    debe traer exactamente los mismos subtabs (mismo largo, mismo orden)
    que el documento canónico — agregar/quitar un subtab no está soportado
    (son sub-vistas de negocio predefinidas, no una colección genérica)."""
    if not isinstance(raw, dict):
        raise ValidationError(f"{field}: forma inválida")
    body = _validate_segment_body(raw, field=field, current=current)
    body["id"] = current["id"]
    body["tab_label"] = _text(raw.get("tab_label", ""), max_len=200, field=f"{field}.tab_label")
    body["title"] = _text(raw.get("title", ""), max_len=200, field=f"{field}.title")
    current_priority = current.get("priority")
    if current_priority is None:
        body["priority"] = None
    else:
        priority_raw = raw.get("priority") if isinstance(raw.get("priority"), dict) else {}
        # `code` es estructural (gobierna la clase CSS del badge —
        # `.cs-priority--p2`/`.cs-priority--new`, ver priority_badge()) —
        # se toma SIEMPRE de `current`. `text` ("PRIORIDAD 1", etc.) es el
        # valor de negocio visible, 100% editable.
        body["priority"] = {
            "code": current_priority.get("code"),
            "text": _text(priority_raw.get("text", ""), max_len=100, field=f"{field}.priority.text"),
        }
    current_subtabs = current.get("subtabs")
    if current_subtabs is None:
        body["subtabs"] = None
    else:
        subtabs_raw = raw.get("subtabs")
        if not isinstance(subtabs_raw, list) or len(subtabs_raw) != len(current_subtabs):
            raise ValidationError(f"{field}.subtabs: se esperaban exactamente {len(current_subtabs)} elementos")
        body["subtabs"] = [
            _validate_subtab(s, field=f"{field}.subtabs[{i}]", current=current_subtabs[i])
            for i, s in enumerate(subtabs_raw)
        ]
    return body


def _validate_segments(raw, current: dict) -> dict:
    """`items` es cardinalidad FIJA — los 6 segmentos de negocio son una
    taxonomía predefinida (ver strategy.yaml), no una colección genérica
    tipo tareas/KPIs; agregar/quitar/reordenar segmentos no está
    soportado. Cada VALOR dentro de cada segmento sí es 100% editable."""
    if not isinstance(raw, dict):
        raise ValidationError("segments: forma inválida")
    current_segments = current.get("segments", {})
    current_items = current_segments.get("items", [])
    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or len(items_raw) != len(current_items):
        raise ValidationError(f"segments.items: se esperaban exactamente {len(current_items)} elementos")
    return {
        "intro": _text(raw.get("intro", ""), max_len=_MAX_TEXT, field="segments.intro"),
        "new_name_note": _text(raw.get("new_name_note", ""), max_len=_MAX_TEXT, field="segments.new_name_note"),
        "items": [
            _validate_segment_item(it, field=f"segments.items[{i}]", current=current_items[i])
            for i, it in enumerate(items_raw)
        ],
    }


def _validate_focus(raw, current) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("focus: forma inválida")
    questions_raw = raw.get("questions")
    if not isinstance(questions_raw, list) or len(questions_raw) != 5:
        raise ValidationError("focus.questions: se esperan exactamente 5 elementos")
    questions = []
    for i, q in enumerate(questions_raw):
        if not isinstance(q, dict):
            raise ValidationError(f"focus.questions[{i}]: forma inválida")
        questions.append({"n": i + 1, "text": _text(q.get("text", ""), max_len=300, field=f"focus.questions[{i}].text")})
    return {
        "intro": _text(raw.get("intro", ""), max_len=_MAX_TEXT, field="focus.intro"),
        "questions": questions,
        "rules": _string_list(raw.get("rules"), field="focus.rules"),
        "coherence_note": _text(raw.get("coherence_note", ""), max_len=_MAX_TEXT, field="focus.coherence_note"),
    }


def _validate_capacity(raw, current: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("capacity: forma inválida")
    cur = current.get("capacity", {})
    return {
        "assignments_table": _table(raw.get("assignments_table"), field="capacity.assignments_table", current_table=cur.get("assignments_table")),
        "priority_rules": _string_list(raw.get("priority_rules"), field="capacity.priority_rules"),
    }


def _validate_meetings(raw) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > _MAX_LIST_ITEMS:
        raise ValidationError("cadence.meetings: lista inválida")
    out = []
    for i, m in enumerate(raw):
        if not isinstance(m, dict):
            raise ValidationError(f"cadence.meetings[{i}]: forma inválida")
        out.append({
            "freq": _text(m.get("freq", ""), max_len=200, field=f"cadence.meetings[{i}].freq"),
            "title": _text(m.get("title", ""), max_len=200, field=f"cadence.meetings[{i}].title"),
            "participants": _text(m.get("participants", ""), max_len=300, field=f"cadence.meetings[{i}].participants"),
            "bullets": _string_list(m.get("bullets"), field=f"cadence.meetings[{i}].bullets"),
        })
    return out


def _validate_cadence(raw, current) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("cadence: forma inválida")
    return {
        "intro": _text(raw.get("intro", ""), max_len=_MAX_TEXT, field="cadence.intro"),
        "meetings": _validate_meetings(raw.get("meetings")),
        "note": _text(raw.get("note", ""), max_len=_MAX_TEXT, field="cadence.note"),
    }


def _validate_exclusions(raw, current) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("exclusions: forma inválida")
    items_raw = raw.get("items")
    if not isinstance(items_raw, list) or len(items_raw) > _MAX_LIST_ITEMS:
        raise ValidationError("exclusions.items: lista inválida")
    out = []
    for i, it in enumerate(items_raw):
        if not isinstance(it, dict):
            raise ValidationError(f"exclusions.items[{i}]: forma inválida")
        out.append({"text": _text(it.get("text", ""), max_len=_MAX_TEXT, field=f"exclusions.items[{i}].text")})
    return {"items": out}


def _validate_task(raw, *, index: int, seen_ids: set[str]) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError(f"next_steps.tasks[{index}]: forma inválida")
    task_id = _text(raw.get("id", ""), max_len=_MAX_ID, field=f"next_steps.tasks[{index}].id")
    if not task_id:
        raise ValidationError(f"next_steps.tasks[{index}].id: requerido")
    if task_id in seen_ids:
        raise ValidationError(f"next_steps.tasks[{index}].id: duplicado ({task_id!r})")
    seen_ids.add(task_id)
    q = str(raw.get("q", "")).strip()
    if q not in _Q_ENUM:
        raise ValidationError(f"next_steps.tasks[{index}].q: valor no permitido ({q!r}) — debe ser uno de {sorted(_Q_ENUM)}")
    return {
        "id": task_id,
        "tarea": _text(raw.get("tarea", ""), max_len=_MAX_TEXT, field=f"next_steps.tasks[{index}].tarea"),
        "origen": _text(raw.get("origen", ""), max_len=_MAX_SHORT, field=f"next_steps.tasks[{index}].origen"),
        "responsable": _text(raw.get("responsable", ""), max_len=_MAX_SHORT, field=f"next_steps.tasks[{index}].responsable"),
        "q": q,
    }


def _validate_next_steps(raw, current) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("next_steps: forma inválida")
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or len(tasks_raw) > _MAX_TASKS:
        raise ValidationError(f"next_steps.tasks: lista inválida (máx {_MAX_TASKS} elementos)")
    seen_ids: set[str] = set()
    tasks = [_validate_task(t, index=i, seen_ids=seen_ids) for i, t in enumerate(tasks_raw)]
    return {
        "intro": _text(raw.get("intro", ""), max_len=_MAX_TEXT, field="next_steps.intro"),
        "tasks": tasks,
        "closing_note": _text(raw.get("closing_note", ""), max_len=_MAX_TEXT, field="next_steps.closing_note"),
    }


def _validate_tree_node(raw, *, field: str, depth: int) -> dict:
    """Nodo del árbol de contactos (`sap_tree`/`lob_tree`) — recursivo,
    `children` es OPCIONAL (los nodos hoja del seed no traen la clave).
    `depth` acotado (`_MAX_TREE_DEPTH`) para que un payload adversarial no
    pueda forzar una recursión arbitrariamente profunda."""
    if not isinstance(raw, dict):
        raise ValidationError(f"{field}: forma inválida")
    if depth > _MAX_TREE_DEPTH:
        raise ValidationError(f"{field}: profundidad máxima excedida")
    node = {
        "name": _text(raw.get("name", ""), max_len=200, field=f"{field}.name"),
        "role": _text(raw.get("role", ""), max_len=200, field=f"{field}.role"),
        "desc": _text(raw.get("desc", ""), max_len=_MAX_TEXT, field=f"{field}.desc"),
        "sellers": _text(raw.get("sellers", ""), max_len=_MAX_TEXT, field=f"{field}.sellers"),
        "highlight": bool(raw.get("highlight", False)),
    }
    children_raw = raw.get("children")
    if children_raw is not None:
        if not isinstance(children_raw, list) or len(children_raw) > _MAX_LIST_ITEMS:
            raise ValidationError(f"{field}.children: lista inválida (máx {_MAX_LIST_ITEMS} elementos)")
        node["children"] = [
            _validate_tree_node(c, field=f"{field}.children[{i}]", depth=depth + 1)
            for i, c in enumerate(children_raw)
        ]
    return node


def _validate_tree(raw, *, field: str) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError(f"{field}: forma inválida")
    root_raw = raw.get("root")
    if not isinstance(root_raw, dict):
        raise ValidationError(f"{field}.root: forma inválida")
    root = _validate_tree_node(root_raw, field=f"{field}.root", depth=0)
    root.pop("children", None)  # root nunca tiene children propio (ver strategy.yaml)
    branches_raw = raw.get("branches")
    if not isinstance(branches_raw, list) or len(branches_raw) > _MAX_LIST_ITEMS:
        raise ValidationError(f"{field}.branches: lista inválida (máx {_MAX_LIST_ITEMS} elementos)")
    branches = [_validate_tree_node(b, field=f"{field}.branches[{i}]", depth=0) for i, b in enumerate(branches_raw)]
    return {"root": root, "branches": branches}


def _validate_annex_notes(raw, *, field: str) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > _MAX_LIST_ITEMS:
        raise ValidationError(f"{field}: lista inválida (máx {_MAX_LIST_ITEMS} elementos)")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValidationError(f"{field}[{i}]: forma inválida")
        out.append({
            "title": _text(item.get("title", ""), max_len=200, field=f"{field}[{i}].title"),
            "text": _text(item.get("text", ""), max_len=_MAX_TEXT, field=f"{field}[{i}].text"),
        })
    return out


def _validate_annex_map(raw, current) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("annex_map: forma inválida")
    return {
        "sap_tree": _validate_tree(raw.get("sap_tree"), field="annex_map.sap_tree"),
        "lob_tree": _validate_tree(raw.get("lob_tree"), field="annex_map.lob_tree"),
        "notes": _validate_annex_notes(raw.get("notes"), field="annex_map.notes"),
    }


def _validate_role(raw, *, field: str) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError(f"{field}: forma inválida")
    return {
        "name": _text(raw.get("name", ""), max_len=200, field=f"{field}.name"),
        "who": _text(raw.get("who", ""), max_len=200, field=f"{field}.who"),
        "mision": _text(raw.get("mision", ""), max_len=_MAX_TEXT, field=f"{field}.mision"),
        "responsibilities": _string_list(raw.get("responsibilities"), field=f"{field}.responsibilities"),
        "not_responsible": _string_list(raw.get("not_responsible"), field=f"{field}.not_responsible"),
    }


def _validate_annex_roles(raw, current) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("annex_roles: forma inválida")
    roles_raw = raw.get("roles")
    if not isinstance(roles_raw, list) or len(roles_raw) > _MAX_LIST_ITEMS:
        raise ValidationError("annex_roles.roles: lista inválida")
    return {
        "intro": _text(raw.get("intro", ""), max_len=_MAX_TEXT, field="annex_roles.intro"),
        "roles": [_validate_role(r, field=f"annex_roles.roles[{i}]") for i, r in enumerate(roles_raw)],
    }


def _validate_stages(raw) -> list[dict]:
    """`n` es estructural/posicional (la numeración canónica 0–8 del
    pipeline, referenciada por texto en otras secciones — ver gate item 3
    ejemplo "funnel stage technical ID → structural") — se deriva SIEMPRE
    del índice, nunca del payload."""
    if not isinstance(raw, list) or not raw or len(raw) > _MAX_LIST_ITEMS:
        raise ValidationError("annex_process.stages: lista inválida")
    out = []
    for i, s in enumerate(raw):
        if not isinstance(s, dict):
            raise ValidationError(f"annex_process.stages[{i}]: forma inválida")
        out.append({
            "n": str(i),
            "name": _text(s.get("name", ""), max_len=200, field=f"annex_process.stages[{i}].name"),
            "meta": _text(s.get("meta", ""), max_len=300, field=f"annex_process.stages[{i}].meta"),
            "gate": _text(s.get("gate", ""), max_len=_MAX_TEXT, field=f"annex_process.stages[{i}].gate"),
        })
    return out


def _validate_annex_process(raw, current) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("annex_process: forma inválida")
    return {
        "intro": _text(raw.get("intro", ""), max_len=_MAX_TEXT, field="annex_process.intro"),
        "stages": _validate_stages(raw.get("stages")),
        "cooling_note": _text(raw.get("cooling_note", ""), max_len=_MAX_TEXT, field="annex_process.cooling_note"),
    }


def _validate_golden_rules(raw) -> list[dict]:
    """`n` (numeración "01".."10") es estructural/posicional, MISMO
    criterio que `annex_process.stages[].n`."""
    if not isinstance(raw, list) or not raw or len(raw) > _MAX_LIST_ITEMS:
        raise ValidationError("annex_rules.rules: lista inválida")
    out = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict):
            raise ValidationError(f"annex_rules.rules[{i}]: forma inválida")
        out.append({
            "n": f"{i + 1:02d}",
            "title": _text(r.get("title", ""), max_len=200, field=f"annex_rules.rules[{i}].title"),
            "detail": _text(r.get("detail", ""), max_len=_MAX_TEXT, field=f"annex_rules.rules[{i}].detail"),
        })
    return out


def _validate_annex_rules(raw, current) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("annex_rules: forma inválida")
    return {
        "intro": _text(raw.get("intro", ""), max_len=_MAX_TEXT, field="annex_rules.intro"),
        "rules": _validate_golden_rules(raw.get("rules")),
    }


_VALIDATORS = {
    "hero": _validate_hero,
    "objectives": _validate_objectives,
    "demand_generation": _validate_demand_generation,
    "segments": _validate_segments,
    "focus": _validate_focus,
    "capacity": _validate_capacity,
    "cadence": _validate_cadence,
    "exclusions": _validate_exclusions,
    "next_steps": _validate_next_steps,
    "annex_map": _validate_annex_map,
    "annex_roles": _validate_annex_roles,
    "annex_process": _validate_annex_process,
    "annex_rules": _validate_annex_rules,
}


def merge_editable_fields(payload, current: dict) -> dict:
    """`payload` crudo (ya deserializado de JSON) → dict con SOLO las claves
    editables reconocidas, cada una validada. Rechazo atómico: la primera
    `ValidationError` aborta TODO el payload — nunca se persiste una mitad
    válida de un envío inválido.

    `current`: el documento canónico YA CARGADO (ver router) — cada
    validador lo usa para recuperar valores ESTRUCTURALES que el propio
    payload no trae (headers de tabla, `access.kind`, ids de segmento/
    subtab, cardinalidad fija de colecciones estructurales) en vez de
    depender de que `save()` haga un merge recursivo dentro de listas
    para no perderlos — mucho más simple y explícito que esa alternativa."""
    if not isinstance(payload, dict):
        raise ValidationError("payload: se esperaba un objeto JSON")
    if not isinstance(current, dict):
        raise ValidationError("current: documento canónico ausente")
    out: dict = {}
    for key, validator in _VALIDATORS.items():
        if key in payload:
            out[key] = validator(payload[key], current)
    if not out:
        raise ValidationError("payload: no contiene ningún campo editable reconocido")
    return out
