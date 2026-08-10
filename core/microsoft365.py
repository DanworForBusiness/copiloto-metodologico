"""
microsoft365.py — Primitivas de transporte Microsoft Graph (Fase de
aceleración post-Fase 2, Batch 1).

Cierre mínimo de dependencia compartida para que `core/`, `tools/` y
`webapp/` puedan consumir Microsoft Graph sin duplicar la construcción de
headers/GET/paginación en cada módulo. Extraído como boundary DELGADO, no
como consolidación M365-A: transporte HTTP únicamente vía `urllib.request`
(igual que los tres consumidores existentes) — sin retry/backoff, sin
jerarquía de errores, sin manejo de 429/Retry-After, sin autenticación
app-only ni refresh de tokens.

Clasificación: SHARED PLATFORM / MICROSOFT 365 INTEGRATION. Este módulo NO
posee ni recupera tokens — cada llamada recibe `access_token` explícito del
caller. NO persiste el token (ni en memoria de proceso, ni en cookie, ni en
`app.state`, ni en base de datos): el modelo vigente sigue siendo MSAL en el
browser → `sessionStorage` → el caller lo pasa explícito por request. NO
define política de cache — la memoización (TTL, `@lru_cache`, etc.) sigue
siendo responsabilidad de cada repositorio/consumidor
(`core/sharepoint_cases.py`, `webapp/services/catalog_reads.py`).

NO importa `webapp` ni `webapp.main` (evita crear una dependencia
`tools`/`core` → `webapp`). NO contiene lógica de negocio, rutas, templates
ni estado mutable de aplicación.
"""
from __future__ import annotations

import json
import urllib.request

DEFAULT_TIMEOUT = 12


def graph_headers(access_token: str) -> dict[str, str]:
    """Headers Graph estándar (Bearer + Accept JSON). No loguea ni persiste el token."""
    return {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}


def graph_get_json(
    url: str, access_token: str, *, timeout: int = DEFAULT_TIMEOUT, extra_headers: dict[str, str] | None = None,
) -> dict:
    """GET a una URL de Graph, devuelve el JSON decodificado. Los errores de
    `urllib` (HTTPError/URLError) y de decodificación se propagan al caller
    sin normalizar — igual que el comportamiento actual de los tres
    consumidores existentes.

    `extra_headers` (Staffing Director Priority V2, ver HANDOFF): merge
    explícito sobre los headers Graph estándar — hoy solo lo usa
    `StaffingRepository` para pedir `Prefer: HonorNonIndexedQueriesWarningMayFailRandomly`
    al filtrar por `$filter` sobre una columna Choice potencialmente sin
    indexar. `None` (default) preserva el comportamiento exacto de siempre."""
    headers = graph_headers(access_token)
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def graph_get_paged_values(
    url: str, access_token: str, *, timeout: int = DEFAULT_TIMEOUT, extra_headers: dict[str, str] | None = None,
) -> list:
    """Sigue `@odata.nextLink` acumulando `value` de cada página hasta que Graph
    no devuelve una página siguiente. Mismo patrón que
    `core/sharepoint_cases.py::_fetch_sap_cases_cached` — acá generalizado, sin
    cache (la cache sigue siendo responsabilidad del caller)."""
    items: list = []
    next_url = url
    while next_url:
        payload = graph_get_json(next_url, access_token, timeout=timeout, extra_headers=extra_headers)
        items.extend(payload.get("value") or [])
        next_url = payload.get("@odata.nextLink") or ""
    return items
