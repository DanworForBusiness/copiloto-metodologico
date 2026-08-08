"""
catalog_reads.py — Lecturas cacheadas del catálogo local (feature COPILOT).

Extraído de ``webapp/main.py`` (Fase 2I) como el cierre mínimo de dependencia
que ``GET /modules/{stem}`` necesita para su pestaña "Catálogo": 4 loaders
cacheados + 5 funciones delgadas de lectura/consulta. Relocación verbatim —
NO redisexa el mecanismo de import dinámico (`sys.path.insert` +
``import catalog_service``) hacia ``tools/catalog/``, que sigue siendo deuda
explícita para una fase de Catalog/M365 futura.

Clasificación: COPILOT / CATALOG READ CAPABILITY. NOT PLATFORM / SHARED.
NO importa ``webapp.main``, NO define rutas, NO renderiza templates, NO
genera SVG/presentación, NO toca Graph/M365, NO mantiene estado mutable
por-request (solo memoización de proceso vía ``@lru_cache``).
"""
from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def _catalog_entries():
    """Carga el catálogo local (catalog_local) vía el servicio del piloto.
    Determinístico, sin IA. Devuelve (módulo, entradas) o (None, []).

    Cacheado: antes se releía y reparseaba TODO catalog_local en cada request
    a /modules/{stem} (aunque esa persona nunca abriera la pestaña Catálogo),
    ~230ms por click. Mismo criterio que load_module: contenido estático
    mientras el proceso corre, se invalida solo con un restart."""
    try:
        import sys as _sys
        from config import BASE_DIR
        svc_dir = str(BASE_DIR / "tools" / "catalog")
        if svc_dir not in _sys.path:
            _sys.path.insert(0, svc_dir)
        import catalog_service as mod
        cat_dir = os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
        return mod, mod.load_catalog(cat_dir)
    except Exception:
        return None, []


def _catalog_domains() -> list:
    mod, entries = _catalog_entries()
    return mod.domains(entries) if mod else []


@lru_cache(maxsize=1)
def _catalog_architecture_connections():
    try:
        import sys as _sys
        from config import BASE_DIR
        svc_dir = str(BASE_DIR / "tools" / "catalog")
        if svc_dir not in _sys.path:
            _sys.path.insert(0, svc_dir)
        import catalog_service as mod
        cat_dir = os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
        return mod, mod.load_architecture_connections(cat_dir)
    except Exception:
        return None, []


def _catalog_connection_types() -> list:
    mod, entries = _catalog_architecture_connections()
    return mod.connection_types(entries) if mod else []


@lru_cache(maxsize=1)
def _catalog_data_products_entries():
    """Cacheado: el parser de Data Products lee frontmatter YAML grande
    (ej. ~150+ campos embebidos) y era el costo dominante medido (~830ms
    por request, sin cachear)."""
    try:
        import sys as _sys
        from config import BASE_DIR
        svc_dir = str(BASE_DIR / "tools" / "catalog")
        if svc_dir not in _sys.path:
            _sys.path.insert(0, svc_dir)
        import catalog_service as mod
        cat_dir = os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
        return mod, mod.load_data_products(cat_dir)
    except Exception:
        return None, []


def _catalog_data_products_list() -> list:
    _mod, entries = _catalog_data_products_entries()
    return entries


@lru_cache(maxsize=1)
def _catalog_security_entries():
    try:
        import sys as _sys
        from config import BASE_DIR
        svc_dir = str(BASE_DIR / "tools" / "catalog")
        if svc_dir not in _sys.path:
            _sys.path.insert(0, svc_dir)
        import catalog_service as mod
        cat_dir = os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(BASE_DIR / "catalog_local")
        return mod, mod.load_security_catalog(cat_dir)
    except Exception:
        return None, []


def _catalog_security_families() -> list:
    mod, entries = _catalog_security_entries()
    return mod.security_families(entries) if mod else []


def _catalog_security_criteria() -> list:
    mod, entries = _catalog_security_entries()
    return mod.security_criteria(entries) if mod else []
