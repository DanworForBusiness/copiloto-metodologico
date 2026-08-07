"""
test_route_contract.py — Contrato de superficie de rutas (red de seguridad del
refactor de routers, Fase 0).

Congela el conjunto (MÉTODO(S) HTTP + PATH) de las rutas declaradas por la app y
lo compara contra un snapshot versionado (``tests/route_surface.json``). El
objetivo es demostrar, durante el refactor, que:

    route surface  ANTES  ==  route surface  DESPUÉS

Extraer endpoints de ``webapp/main.py`` hacia ``APIRouter`` NO debe cambiar la
superficie observable (mismas URLs, mismos métodos).

Alcance deliberadamente GRUESO — solo método + path. NO se acopla a:
  - la función handler,
  - el ``response_model``,
  - detalles de implementación,
  - el orden interno de FastAPI (se compara como conjunto, no como lista).

Regenerar el snapshot (SOLO cuando el cambio de superficie sea intencional y
haya sido revisado):

    UPDATE_ROUTE_SURFACE=1 pytest tests/test_route_contract.py

============================================================================
ROUTE CONTRACT RULE (regla operacional — leer antes de regenerar)
============================================================================
Durante un refactor estructural, un mismatch de este test debe tratarse PRIMERO
como una REGRESIÓN, no como un snapshot desactualizado.

NO regenerar route_surface.json automáticamente para "hacer pasar" el test.

UPDATE_ROUTE_SURFACE=1 se usa ÚNICAMENTE cuando existe un cambio de contrato HTTP
deliberado y aprobado previamente. Durante las Fases 1, 2 y 3 del refactor se
espera estrictamente:

    route surface  ANTES  ==  route surface  DESPUÉS

salvo decisión funcional explícita y revisada.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from starlette.routing import Mount, Route

from webapp.main import app

_SNAPSHOT = Path(__file__).parent / "route_surface.json"

# Métodos que Starlette/FastAPI agregan automáticamente a las rutas; no son parte
# del contrato declarado por la app, así que se descartan para no acoplar el test
# a ese comportamiento del framework.
_AUTO_METHODS = {"HEAD", "OPTIONS"}

# Rutas provistas por FastAPI (docs/openapi), no por la app. Se excluyen: son
# ruido dependiente de la versión del framework, no contrato propio.
_FRAMEWORK_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _current_surface() -> set[str]:
    """(método(s), path) de cada APIRoute como string 'GET,POST /path'."""
    surface: set[str] = set()
    for route in app.routes:
        # StaticFiles y cualquier sub-app montada no son endpoints de contrato.
        if isinstance(route, Mount):
            continue
        # Solo rutas HTTP (Route/APIRoute); descarta WebSocketRoute u otros.
        if not isinstance(route, Route):
            continue
        if route.path in _FRAMEWORK_PATHS:
            continue
        methods = sorted((route.methods or set()) - _AUTO_METHODS)
        surface.add(f"{','.join(methods)} {route.path}")
    return surface


def _load_snapshot() -> set[str]:
    return set(json.loads(_SNAPSHOT.read_text(encoding="utf-8")))


def _write_snapshot(surface: set[str]) -> None:
    _SNAPSHOT.write_text(
        json.dumps(sorted(surface), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_route_surface_matches_snapshot():
    current = _current_surface()

    if os.getenv("UPDATE_ROUTE_SURFACE") == "1":
        _write_snapshot(current)
        return

    assert _SNAPSHOT.exists(), (
        "Falta tests/route_surface.json. Generalo una vez con: "
        "UPDATE_ROUTE_SURFACE=1 pytest tests/test_route_contract.py"
    )

    expected = _load_snapshot()
    missing = expected - current   # rutas eliminadas o con método/path cambiado
    extra = current - expected     # rutas nuevas o con método/path cambiado

    assert not missing and not extra, (
        "La superficie de rutas cambió respecto del contrato congelado.\n"
        f"  Faltantes (eliminadas/modificadas): {sorted(missing)}\n"
        f"  Nuevas/modificadas:                 {sorted(extra)}\n"
        "Si el cambio es intencional y fue revisado, regenerá el snapshot con "
        "UPDATE_ROUTE_SURFACE=1 pytest tests/test_route_contract.py"
    )
