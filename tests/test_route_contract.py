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

El collector recorre la estructura de routing de forma recursiva y reconoce una
hoja HTTP por sus atributos públicos (`path` + `methods`), NO por su clase. Esto
lo hace robusto a cómo FastAPI represente las rutas incluidas vía ``APIRouter``:
aplanadas en ``app.routes`` (versiones viejas) o anidadas en un contenedor de
router incluido (versiones modernas). Ver ``_iter_leaves``.

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

from webapp.main import app

_SNAPSHOT = Path(__file__).parent / "route_surface.json"

# Métodos que Starlette/FastAPI agregan automáticamente a las rutas; no son parte
# del contrato declarado por la app, así que se descartan para no acoplar el test
# a ese comportamiento del framework.
_AUTO_METHODS = {"HEAD", "OPTIONS"}

# Rutas provistas por FastAPI (docs/openapi), no por la app. Se excluyen: son
# ruido dependiente de la versión del framework, no contrato propio.
_FRAMEWORK_PATHS = {"/openapi.json", "/docs", "/docs/oauth2-redirect", "/redoc"}


def _iter_leaves(routes, seen):
    """Recorre recursivamente la estructura de routing y produce (methods, path)
    de cada HOJA HTTP: un objeto que expone `path` y `methods`. NO se depende de
    la clase interna de la ruta (`isinstance`), sino de esos atributos públicos.

    Por qué recursivo: FastAPI representa las rutas incluidas vía `APIRouter` de
    forma distinta según su versión — en versiones viejas las APLANA en
    `app.routes`; en versiones modernas las ANIDA en un contenedor de router
    incluido que NO expone sus rutas por `.routes` sino por `.original_router.routes`.
    Por eso descendemos por `.routes` y, como fallback de compatibilidad con el
    FastAPI moderno, por `.original_router.routes` (introspección defensiva por
    atributo: no se importa ni se referencia ninguna clase interna del framework).
    El contrato protege MÉTODO+PATH observable, no la representación interna.
    """
    for route in routes:
        rid = id(route)
        if rid in seen:  # evita revisitar/duplicar/ciclos si un contenedor expone
            continue      # el mismo contenido por más de una vía
        seen.add(rid)

        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None)
        if path and methods:  # hoja HTTP utilizable para el contrato
            yield tuple(sorted(set(methods) - _AUTO_METHODS)), path

        children = getattr(route, "routes", None)
        if not children:  # fallback: router incluido de FastAPI moderno
            children = getattr(getattr(route, "original_router", None), "routes", None)
        if children:
            yield from _iter_leaves(children, seen)


def _current_surface() -> set[str]:
    """Conjunto de 'MÉTODO(S) PATH' de todas las hojas HTTP de la app (aplanadas
    o anidadas), excluyendo las rutas del framework (docs/openapi)."""
    surface: set[str] = set()
    for methods, path in _iter_leaves(app.routes, set()):
        if path in _FRAMEWORK_PATHS:
            continue
        surface.add(f"{','.join(methods)} {path}")
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
