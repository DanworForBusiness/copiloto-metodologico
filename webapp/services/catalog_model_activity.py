"""catalog_model_activity.py — `catalog_model_viewed`: consulta SIGNIFICATIVA de un modelo del Catálogo (Herramientas de Decisión).

Se registra en el SERVIDOR (con identidad cliente + modelo + flujo), no desde el beacon del navegador: la `section_view` del cliente guarda la ruta
sin parámetros y no sabe qué modelo se miró. Deduplicación: una consulta por (usuario, cliente, modelo, vista) cada ``DEDUP_S``; un render parcial
(HTMX), un refresh o la navegación técnica dentro de esa ventana NO cuentan como otra consulta. Vistas: ``model`` (detalle del flujo),
``lineage`` (solo el diagrama de linaje), ``assessment`` (informe de Assessment del modelo). Nunca rompe la página que lo llama.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from webapp.services.activity_context import AREA_CATALOG, SCOPE_COPILOT
from webapp.services.activity_service import log_activity

log = logging.getLogger(__name__)

DEDUP_S = 600
VIEWS = frozenset({"model", "lineage", "assessment"})
_TERMINAL = re.compile(r"^(4?AM)_", re.I)
_seen: dict[tuple[str, str, str, str], float] = {}
_lock = threading.Lock()


def model_of(entry: Any) -> str:
    """Modelo terminal del flujo (artefacto de capa de consumo AM_/4AM_); sin él, el dominio."""
    for art in getattr(entry, "artefactos", None) or []:
        name = art if isinstance(art, str) else (art.get("nombre") or art.get("name") or art.get("technical_name") or "") if isinstance(art, dict) else ""
        if name and _TERMINAL.match(str(name)):
            return str(name)
    return str(getattr(entry, "dominio", "") or "")


def log_model_viewed(user: dict | None, entry: Any, view: str, *, model: str | None = None, now: float | None = None) -> bool:
    """True si se registró (no duplicada). ``model`` explícito cuando la vista ya lo conoce (p. ej. el AM terminal del Assessment)."""
    if not user or view not in VIEWS or entry is None:
        return False
    who = str(user.get("sub") or user.get("email") or "").strip().lower()
    client, mdl = str(getattr(entry, "cliente", "") or ""), (model or model_of(entry))
    key, now = (who, client.lower(), mdl.lower(), view), (time.time() if now is None else now)
    with _lock:
        last = _seen.get(key)
        if last is not None and now - last < DEDUP_S:
            return False
        _seen[key] = now
        if len(_seen) > 5000:                                            # acotado: se descartan las entradas vencidas
            for k in [k for k, t in _seen.items() if now - t >= DEDUP_S]:
                _seen.pop(k, None)
    try:
        log_activity("catalog_model_viewed", user=user, scope=SCOPE_COPILOT, area=AREA_CATALOG, client=client, model=mdl, view=view,
                     flow=str(getattr(entry, "entry_id", "") or ""))
    except Exception as exc:                                             # noqa: BLE001
        log.warning("catalog_model_viewed log failed exc=%s", type(exc).__name__)
    return True
