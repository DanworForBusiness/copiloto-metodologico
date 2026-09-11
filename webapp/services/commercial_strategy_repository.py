"""commercial_strategy_repository.py — READ CONTRACT del documento
"Estrategia Comercial 2026" (Commercial Strategy — Phase 1, read-only).

Fase 1 es deliberadamente de SOLO LECTURA (owner decision, ver storage gate):
el gate de durabilidad/CSRF/concurrencia distribuida NO está resuelto
todavía para escritura de usuario — este módulo expone ÚNICAMENTE
`load_strategy()`. NO existen (ni deben agregarse "para después")
`save_strategy`/`update_strategy`/`delete_strategy` en esta fase.

Fuente actual: un YAML COMMITTED al repo (`commercial_strategy_data/
strategy.yaml`), generado una única vez por `scripts/
import_commercial_strategy.py` a partir del artefacto standalone del owner.
Deliberadamente NO vive bajo `config.PERSIST_PATHS` — ese mecanismo es
eventual (mirror cada `SPACES_SYNC_INTERVAL`, nunca síncrono con un write) y
pensado para caches/logs regenerables, no para el único documento canónico
de una feature todavía sin contrato de escritura (ver storage gate, Gate A).
Un archivo COMMITTED es simplemente parte del código desplegado — mismo
ciclo de vida que cualquier otro asset estático del repo.

Seam de reemplazo (Phase 2): el consumidor (`webapp.routers.
commercial_strategy`) solo conoce `load_strategy() -> dict`, nunca la ruta
del YAML ni su mecanismo de lectura — el día que exista un store editable
real (con su propio contrato de durabilidad/CSRF/concurrencia, ver storage
gate), esta función puede reapuntar a él sin tocar el router ni los
templates. `@lru_cache` es intencionalmente el ÚNICO "estado": un proceso
lee el YAML una vez; no hay invalidación porque no hay escritura que
invalidar en esta fase.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

_DATA_PATH = Path(__file__).parent / "commercial_strategy_data" / "strategy.yaml"


class CommercialStrategyUnavailable(Exception):
    """El documento no se pudo cargar (archivo ausente o YAML inválido) —
    nunca se fabrica un documento vacío en su lugar (ver `webapp.routers.
    commercial_strategy`, que traduce esto al mismo contrato fail-soft
    `{"state": ...}` que el resto de Comercial, en vez de un 500 crudo)."""


@lru_cache(maxsize=1)
def load_strategy() -> dict:
    """Documento canónico completo, tal cual lo produjo el import. Sin
    transformación de presentación acá — eso es responsabilidad exclusiva
    del template/renderer (ver `webapp/templates/commercial/strategy.html`),
    nunca de la capa de datos."""
    try:
        raw = _DATA_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise CommercialStrategyUnavailable(f"No se pudo leer {_DATA_PATH}: {exc}") from exc
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise CommercialStrategyUnavailable(f"YAML inválido en {_DATA_PATH}: {exc}") from exc
    if not isinstance(document, dict) or not document.get("version"):
        raise CommercialStrategyUnavailable(f"Documento sin forma válida en {_DATA_PATH}")
    return document
