"""
timenotes_exceptions.py — Excepciones MANUALES de Carga de Horas.

Lista DURABLE de colaboradores excluidos de los recordatorios en TODAS las semanas (decisión del owner 2026-11-11: exclusión PERMANENTE por
colaborador, hasta quitarlos manualmente). SEPARADA de las excepciones automáticas de Ausencias (SharePoint), que siguen marcándose como «Revisar»
en la tabla. «Mover a excepciones» = agregar; «Devolver a colaboradores» = quitar.

Persistencia fail-closed (MISMO patrón que la auditoría de envío y `institutional_mail.write_audit`): `operations/timenotes/exceptions.json`
vía `remote_store.write_through` (local + Spaces). Sin datos sensibles: email + nombre visible + actor + timestamp. NO toca el servicio de cálculo
(`timenotes_service`): la partición del resultado se hace acá (`apply`), en la capa de presentación.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import config
from core import remote_store


class ExceptionsUnavailable(RuntimeError):
    """No se pudo confirmar la escritura durable (fail-closed)."""


def _path() -> Path:
    return Path(config.BASE_DIR) / "operations" / "timenotes" / "exceptions.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(*, strict: bool = False) -> dict:
    """Lectura del registro. `strict` (camino de ESCRITURA): un archivo existente pero ilegible NO se trata como vacío —se aborta en vez de pisarlo
    y perder las excepciones vigentes (fail-closed). En lectura (listar/aplicar) un archivo ilegible se informa vacío, sin romper la vista."""
    p = _path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (OSError, ValueError):
        if strict:
            raise ExceptionsUnavailable("el registro de excepciones existe pero no se pudo leer") from None
        return {}


def list_emails() -> set[str]:
    """Conjunto de emails (minúsculas) excluidos."""
    return {str(e).strip().lower() for e in (_read().get("emails") or []) if e}


def entries() -> list[dict]:
    """[{email, nombre, added_by, added_at}] ordenados por nombre, para el contenedor «Excepciones»."""
    d = _read()
    meta = d.get("entries") or {}
    rows = []
    for e in (d.get("emails") or []):
        key = str(e).strip().lower()
        m = meta.get(key, {})
        rows.append({"email": key, "nombre": m.get("nombre") or key, "added_by": m.get("added_by") or "", "added_at": m.get("added_at") or ""})
    return sorted(rows, key=lambda r: (r["nombre"] or r["email"]).lower())


def _write(emails: set[str], meta: dict, history: list) -> None:
    if remote_store.read_only_mode():
        raise ExceptionsUnavailable("modo de solo lectura")
    payload = json.dumps({"schema": 1, "emails": sorted(emails), "entries": meta, "history": history, "updated_at": _now()},
                         ensure_ascii=False, sort_keys=True, indent=1)
    try:
        outcome = remote_store.write_through(_path(), payload)
    except OSError:
        raise ExceptionsUnavailable("no se pudo escribir el registro") from None
    if outcome != remote_store.DURABLE_REMOTE and not remote_store.local_only():
        raise ExceptionsUnavailable("registro no confirmado en Spaces")


def history() -> list[dict]:
    """Trazabilidad append-only de altas/bajas: [{op: add|remove, email, actor, at}] (la baja NO borra el rastro del alta)."""
    return list(_read().get("history") or [])


def add(items: list[dict], *, actor: str) -> list[str]:
    """Agrega colaboradores a la lista. `items`: [{email, nombre}]. Idempotente (no duplica ni pisa el alta previa). Devuelve los emails nuevos."""
    d = _read(strict=True)
    emails = {str(e).strip().lower() for e in (d.get("emails") or [])}
    meta = dict(d.get("entries") or {})
    hist = list(d.get("history") or [])
    added = []
    for it in items or []:
        em = str(it.get("email") or "").strip().lower()
        if not em or em in emails:
            continue
        now = _now()
        meta[em] = {"nombre": str(it.get("nombre") or em), "added_by": actor, "added_at": now}
        emails.add(em)
        hist.append({"op": "add", "email": em, "actor": actor, "at": now})
        added.append(em)
    if added:
        _write(emails, meta, hist)
    return added


def remove(emails: list[str], *, actor: str = "") -> list[str]:
    """Quita colaboradores de la lista (devolver a Colaboradores). Deja constancia de la baja en `history`. Devuelve los emails quitados."""
    d = _read(strict=True)
    current = {str(e).strip().lower() for e in (d.get("emails") or [])}
    drop = {str(e).strip().lower() for e in (emails or [])} & current
    if not drop:
        return []
    hist = list(d.get("history") or [])
    now = _now()
    hist.extend({"op": "remove", "email": em, "actor": actor, "at": now} for em in sorted(drop))
    meta = {k: v for k, v in (d.get("entries") or {}).items() if k not in drop}
    _write(current - drop, meta, hist)
    return sorted(drop)


def apply(data: dict, excluded: set[str]) -> dict:
    """Mueve del resultado de cumplimiento (`timenotes_service.build_compliance`) los colaboradores manualmente exceptuados: los saca de
    `data['colaboradores']`, los deja en `data['excepciones_manuales_rows']` y recalcula el `resumen` sobre los que quedan. NO altera el cálculo
    de horas (solo reparte filas ya calculadas). Idempotente."""
    if data.get("status") != "ok" or not excluded:
        data.setdefault("excepciones_manuales_rows", [])
        return data
    excluded = {str(e).strip().lower() for e in excluded}
    cols = data.get("colaboradores") or []
    kept = [c for c in cols if c["email"] not in excluded]
    data["colaboradores"] = kept
    data["excepciones_manuales_rows"] = [c for c in cols if c["email"] in excluded]
    r = dict(data.get("resumen") or {})
    r["esperados"] = len(kept)
    r["cumplen"] = sum(1 for c in kept if c["cumple"])
    r["con_faltantes"] = sum(1 for c in kept if c["faltantes"] > 0)
    r["sin_registros"] = sum(1 for c in kept if c["sin_registros"])
    r["con_excepcion"] = sum(1 for c in kept if c["revisar_excepcion"])
    r["notificables"] = sum(1 for c in kept if c["notificable"])
    r["excepciones_manuales"] = len(data["excepciones_manuales_rows"])
    data["resumen"] = r
    return data
