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

Capital Humano Phase 4.1 (Profile Documents) agregó `graph_post_json`/
`graph_put_bytes`; Phase 4.1.2 (delete) agregó `graph_delete` — MISMOS
primitivos de transporte, MISMA filosofía (sin retry/backoff, sin
jerarquía de errores propia, `HTTPError`/`URLError` se propagan tal cual).
Sigue sin existir PATCH genérico ni un cliente HTTP arbitrario — cada
capability nueva declara explícitamente qué verbos necesita.

NO importa `webapp` ni `webapp.main` (evita crear una dependencia
`tools`/`core` → `webapp`). NO contiene lógica de negocio, rutas, templates
ni estado mutable de aplicación.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Callable

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


def graph_get_bytes(
    url: str, access_token: str, *, timeout: int = 30,
) -> bytes:
    """GET binario de Graph (p. ej. `.../drive/root:/{path}:/content`) — devuelve
    los bytes crudos del recurso. `urllib` sigue por default el redirect 302 que
    Graph emite hacia la URL de descarga pre-autenticada (mismo opener que
    `graph_get_json`, sin construir ni hardcodear esa URL temporal). Errores de
    `urllib` (HTTPError/URLError) se propagan sin normalizar — el caller los
    envuelve (ver `_call_vacation_graph`). Timeout más alto que el default JSON:
    descargar un archivo puede tardar más que un GET de metadata. NO persiste el
    archivo ni el token."""
    request = urllib.request.Request(url, headers=graph_headers(access_token))
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def graph_post_json(
    url: str, access_token: str, body: dict, *, timeout: int = DEFAULT_TIMEOUT,
) -> dict:
    """POST JSON a Graph, devuelve el JSON decodificado de la respuesta.
    MISMO idioma que `graph_get_json` (urllib crudo, sin retry/backoff, sin
    normalizar errores — `HTTPError`/`URLError` se propagan al caller tal
    cual). Agregado para Capital Humano Phase 4.1 (Profile Documents): la
    ÚNICA operación de escritura mínima que necesita (crear la carpeta de
    un Profile bajo la raíz de CVs, ver
    `webapp/services/profile_document_service.py::ensure_profile_folder`).
    NO agrega PATCH (fuera de alcance) — DELETE vive en `graph_delete`, ver
    ese docstring."""
    payload = json.dumps(body).encode("utf-8")
    headers = graph_headers(access_token)
    headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=payload, headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def graph_put_bytes(
    url: str, access_token: str, data: bytes, *, content_type: str = "application/octet-stream",
    timeout: int = 30,
) -> dict:
    """PUT binario a Graph (upload simple de contenido, p. ej.
    `.../items/{id}:/{name}:/content` — válido para archivos chicos, sin
    sesión de upload resumable) — devuelve el JSON del `driveItem`
    resultante. MISMO idioma que `graph_get_bytes` (urllib crudo, timeout
    más alto que el default JSON). Sin retry/backoff acá — el contrato de
    reintento semántico (mismo `document_id`/path/bytes, un único
    reintento) vive en el caller, ver
    `webapp/services/profile_document_service.py::upload_document_content`."""
    headers = graph_headers(access_token)
    headers["Content-Type"] = content_type
    request = urllib.request.Request(url, data=data, headers=headers, method="PUT")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def graph_delete(
    url: str, access_token: str, *, timeout: int = DEFAULT_TIMEOUT, extra_headers: dict[str, str] | None = None,
) -> None:
    """DELETE a Graph -- no devuelve nada (Graph responde 204 sin body para
    un `driveItem` eliminado). MISMO idioma que el resto (urllib crudo, sin
    retry/backoff, `HTTPError`/`URLError` se propagan tal cual). Agregado
    para Capital Humano Phase 4.1.2 (Profile Documents delete): la ÚNICA
    operación DELETE que el Hub necesita hoy (borrar el `driveItem` físico
    de un ProfileDocument, ver
    `webapp/services/profile_document_service.py::delete_document`).

    `extra_headers` (opcional): p. ej. `If-Match: <eTag>` para un DELETE condicionado (412 si el elemento cambió). `None` (default) preserva el comportamiento exacto de siempre."""
    headers = graph_headers(access_token)
    if extra_headers:
        headers.update(extra_headers)
    request = urllib.request.Request(url, headers=headers, method="DELETE")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        response.read()


def graph_get_paged_values(
    url: str, access_token: str, *, timeout: int = DEFAULT_TIMEOUT, extra_headers: dict[str, str] | None = None,
    on_page: Callable[[int], None] | None = None,
) -> list:
    """Sigue `@odata.nextLink` acumulando `value` de cada página hasta que Graph
    no devuelve una página siguiente. Mismo patrón que
    `core/sharepoint_cases.py::_fetch_sap_cases_cached` — acá generalizado, sin
    cache (la cache sigue siendo responsabilidad del caller).

    `on_page` (Staffing Director Priority V2 pre-PR corrective, ver HANDOFF)
    — callback OPCIONAL invocado una vez por página con la cantidad de items
    de esa página; permite que el caller cuente páginas/filas sin que este
    módulo posea ninguna noción de diagnóstico/logging propia. `None`
    (default) preserva el comportamiento exacto de siempre."""
    items: list = []
    next_url = url
    while next_url:
        payload = graph_get_json(next_url, access_token, timeout=timeout, extra_headers=extra_headers)
        page_items = payload.get("value") or []
        items.extend(page_items)
        if on_page is not None:
            on_page(len(page_items))
        next_url = payload.get("@odata.nextLink") or ""
    return items
