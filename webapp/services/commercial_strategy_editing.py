"""commercial_strategy_editing.py — Validación server-side del payload de
edición de Estrategia Comercial (Commercial Strategy — real editing pass).

Frontera de confianza: el cliente NUNCA es la fuente de verdad de qué se
persiste. `merge_editable_fields(payload)` es el ÚNICO punto que traduce un
JSON crudo llegado por HTTP en algo que
`commercial_strategy_repository.save_strategy()` puede escribir — devuelve
SOLO las claves conocidas (`EDITABLE_TOP_KEYS`), cada una validada por forma/
longitud/enum, o levanta `ValidationError` (rechazo atómico: nada parcial se
persiste nunca, ver el router).

Alcance editable de ESTA fase (ver el reporte del owner para la justificación
completa de qué queda de solo lectura y por qué): `hero`, `objectives`
(KPIs + notas + supuestos pendientes) y `next_steps` (tareas — CRUD +
reorden). El resto del documento (segmentos/sub-tabs, generación de demanda,
foco, capacidad, cadencia, exclusiones, los 4 anexos) es CÓDIGO+SEED en esta
fase — ninguna clave fuera de `EDITABLE_TOP_KEYS` puede llegar a persistirse
sin importar qué mande el cliente."""
from __future__ import annotations

EDITABLE_TOP_KEYS = ("hero", "objectives", "next_steps")

_Q_ENUM = {"Q1", "Q2", "Q3", "Q4", "a_definir"}
_MAX_TEXT = 4000
_MAX_SHORT = 300
_MAX_VALUE = 60
_MAX_ID = 40
_MAX_TASKS = 60
_MAX_LIST_ITEMS = 20


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


def _kpi_list(raw, *, field: str, max_items: int) -> list[dict]:
    if not isinstance(raw, list) or not raw or len(raw) > max_items:
        raise ValidationError(f"{field}: lista inválida (1 a {max_items} elementos)")
    out = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValidationError(f"{field}[{i}]: forma inválida")
        out.append({
            "label": _text(item.get("label", ""), max_len=200, field=f"{field}[{i}].label"),
            "value": _text(item.get("value", ""), max_len=_MAX_VALUE, field=f"{field}[{i}].value"),
            "detail": _text(item.get("detail", ""), max_len=_MAX_TEXT, field=f"{field}[{i}].detail"),
        })
    return out


def _string_list(raw, *, field: str, max_items: int = _MAX_LIST_ITEMS, max_len: int = 800) -> list[str]:
    if not isinstance(raw, list) or len(raw) > max_items:
        raise ValidationError(f"{field}: lista inválida (máx {max_items} elementos)")
    return [_text(v, max_len=max_len, field=f"{field}[{i}]") for i, v in enumerate(raw)]


def _validate_hero(raw) -> dict:
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


def _validate_objectives(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("objectives: forma inválida")
    return {
        "result_targets": _kpi_list(raw.get("result_targets"), field="objectives.result_targets", max_items=6),
        "demand_targets": _kpi_list(raw.get("demand_targets"), field="objectives.demand_targets", max_items=6),
        "calc_note": _text(raw.get("calc_note", ""), max_len=_MAX_TEXT, field="objectives.calc_note"),
        "coverage_note": _text(raw.get("coverage_note", ""), max_len=_MAX_TEXT, field="objectives.coverage_note"),
        "sensitivity_note": _text(raw.get("sensitivity_note", ""), max_len=_MAX_TEXT, field="objectives.sensitivity_note"),
        "assumptions_pending": _string_list(raw.get("assumptions_pending"), field="objectives.assumptions_pending"),
    }


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


def _validate_next_steps(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValidationError("next_steps: forma inválida")
    tasks_raw = raw.get("tasks")
    if not isinstance(tasks_raw, list) or len(tasks_raw) > _MAX_TASKS:
        raise ValidationError(f"next_steps.tasks: lista inválida (máx {_MAX_TASKS} elementos)")
    seen_ids: set[str] = set()
    tasks = [_validate_task(t, index=i, seen_ids=seen_ids) for i, t in enumerate(tasks_raw)]
    return {"tasks": tasks}


_VALIDATORS = {
    "hero": _validate_hero,
    "objectives": _validate_objectives,
    "next_steps": _validate_next_steps,
}


def merge_editable_fields(payload) -> dict:
    """`payload` crudo (ya deserializado de JSON) → dict con SOLO las claves
    editables reconocidas, cada una validada. Rechazo atómico: la primera
    `ValidationError` aborta TODO el payload — nunca se persiste una mitad
    válida de un envío inválido."""
    if not isinstance(payload, dict):
        raise ValidationError("payload: se esperaba un objeto JSON")
    out: dict = {}
    for key, validator in _VALIDATORS.items():
        if key in payload:
            out[key] = validator(payload[key])
    if not out:
        raise ValidationError("payload: no contiene ningún campo editable reconocido")
    return out
