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


def catalog_clean_label(raw, fallback: str) -> str:
    """Descarta un label que en realidad es un placeholder i18n sin resolver (p. ej. '{i18n>Remote.X@ENDUSERTEXT.LABEL}')
    y cae al fallback (nombre técnico). Cubre .md viejos sin regenerar."""
    s = str(raw or "").strip()
    if not s:
        return fallback
    low = s.lower()
    if ("{i18n" in low or "@enduserext.label" in low
            or (s.startswith("{") and s.endswith("}") and "@" in s)):
        return fallback
    return s


def catalog_display_title(name: str, label: str) -> str:
    """Parte "descripción" del título `Descripción · NOMBRE` de un artefacto: la descripción, o el nombre técnico si no hay."""
    return label if (label and label != name) else name


def consumption_artifacts(entry) -> list[str]:
    """Nombres técnicos de los artefactos de capa Consumo del flujo (sin duplicados, en orden): el MISMO criterio de capa que `CatalogEntry.consumo`."""
    mod, _ = _catalog_entries()
    if mod is None:
        return []
    return list(dict.fromkeys(str(a) for a in entry.artefactos if mod._capa_of_name(str(a)) == "Consumo"))


def artifact_title(entry, name: str) -> tuple[str, str]:
    """(descripción, nombre técnico) de un artefacto del flujo, leída del MISMO `auto.label` de su vista que usa el detalle del
    flujo para su heading. Fail-soft: sin vista o sin label devuelve el nombre técnico en ambas partes."""
    try:
        from pathlib import Path
        from webapp.services.content_service import split_front_matter
        vpath = Path(entry.path).parent / "vistas" / f"{name}.md"
        meta, _body = split_front_matter(vpath.read_text(encoding="utf-8"))
        label = catalog_clean_label(((meta or {}).get("auto") or {}).get("label"), name)
    except Exception:                                   # noqa: BLE001 — el título nunca debe romper el informe
        label = name
    return catalog_display_title(name, label), name


@lru_cache(maxsize=1)
def _catalog_entries_cached():
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
        from webapp.services import catalog_governance
        return mod, catalog_governance.visible_entries(mod.load_catalog(cat_dir), cat_dir)      # sin los tiles retirados lógicamente
    except Exception:
        return None, []


def _catalog_entries():
    """Lectura cacheada del catálogo con la COMPUERTA DE LECTORES de la publicación (P1): una lectura en FRÍO (caché vacía) espera a que termine la activación de una publicación —o
    se rechaza si está sin resolver—, para no cargar un Catálogo a medio escribir. Con la caché caliente se sirve la última versión CONFIRMADA (la publicación invalida la caché recién
    cuando su versión nueva está confirmada)."""
    if _catalog_entries_cached.cache_info().currsize == 0:
        from core import publication_gate
        publication_gate.check(os.getenv("DANWOR_CATALOG_DIR", "").strip() or None)
    return _catalog_entries_cached()


_catalog_entries.cache_clear = _catalog_entries_cached.cache_clear
_catalog_entries.cache_info = _catalog_entries_cached.cache_info


def _catalog_domains() -> list:
    mod, entries = _catalog_entries()
    return mod.domains(entries) if mod else []


def _catalog_clients() -> list:
    """Opciones del selector "Clientes" del Catálogo (ver ``catalog_navigation.clients``)."""
    from webapp.services import catalog_navigation
    mod, entries = _catalog_entries()
    return catalog_navigation.clients(mod, entries) if mod else []


def _catalog_model_domains() -> list:
    """Opciones del selector "Modelos" del Catálogo: solo modelos genuinos/desagrupados (ver ``catalog_navigation.model_domains``)."""
    from webapp.services import catalog_navigation
    mod, entries = _catalog_entries()
    return catalog_navigation.model_domains(mod, entries) if mod else []


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
