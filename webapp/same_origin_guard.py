"""
same_origin_guard.py — AUTORIDAD ÚNICA sobre si un POST/PUT/DELETE llegó del
MISMO origen que sirvió la página (protección anti-CSRF mínima, ver storage
gate: "production session cookies may use SameSite=None and current POST
routes have no explicit CSRF contract").

Clasificación: PLATFORM / WRITE SAFETY. NO importa nada de ``webapp`` (mismo
criterio que ``safe_redirect.py``): cualquier router puede usarlo sin
arrastrar dependencias ni riesgo de ciclo. NO decide AUTORIZACIÓN (quién
puede escribir qué) — eso vive en cada access module (p. ej.
``webapp.commercial_access.can_edit_strategy_user``). Esto SOLO contesta:
"¿este request específico vino del mismo sitio que lo sirvió?".

Por qué esto y no un framework CSRF completo (owner directive: "prefer a
simple reusable same-origin write guard over introducing a large CSRF
framework solely for this route"):

  - `SameSite=None` (necesario en producción por el iframe enmascarado de
    danwor.com, ver `config.SESSION_COOKIE_SAMESITE`) desactiva la
    protección CSRF que `SameSite=Lax/Strict` daría gratis — la cookie de
    sesión viaja en CUALQUIER request cross-site, incluida una llamada
    forjada desde un `<form>`/`fetch` en un sitio ajeno.
  - Un token CSRF de doble-submit (o server-side) es la defensa "correcta"
    de propósito general, pero exige nueva infraestructura (generar/rotar/
    validar el token, inyectarlo en cada template) — sobredimensionado para
    UNA ruta de escritura nueva cuando el Hub entero no lo tiene todavía.
  - `Origin`/`Referer` son headers que el BROWSER adjunta y que JavaScript
    de un sitio ajeno NO puede falsificar (a diferencia de un header
    inventado a mano) — exactamente la garantía que necesitamos: que la
    petición salió de un documento cargado desde ESTE origen.

Qué se chequea exactamente:

  1. Header `Origin` si está presente (fetch/XHR modernos SIEMPRE lo mandan
     en un POST cross-origin Y same-origin) — su host debe matchear el host
     del request. Mismatch ⇒ rechazado.
  2. Si `Origin` está ausente, fallback a `Referer` (algunos navegadores/
     clientes viejos no mandan Origin pero sí Referer) — mismo chequeo de
     host.
  3. Si NINGUNO de los dos está presente: RECHAZADO. Un fetch/XHR real desde
     una página cargada en un navegador moderno siempre manda al menos uno
     en un POST — su ausencia total es la firma de un cliente no-browser
     forjando el request a mano (curl, un `<form>` legacy con
     `referrerpolicy="no-referrer"`, etc.), nunca un falso positivo legítimo
     que valga la pena aceptar.

Deliberadamente NO valida: método HTTP, Content-Type, cuerpo del request,
autenticación — eso es responsabilidad del caller. Este módulo contesta UNA
sola pregunta booleana.
"""
from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Request


def _host_of(raw: str) -> str:
    """`host:port` de una URL absoluta cruda ("" si no parsea) — comparación
    case-insensitive, el puerto SÍ importa (localhost:8099 != localhost:8080)."""
    try:
        parsed = urlparse(raw)
    except ValueError:
        return ""
    if not parsed.netloc:
        return ""
    return parsed.netloc.lower()


def is_same_origin_write(request: Request) -> bool:
    """True si `Origin` (o, en su ausencia, `Referer`) del request declara el
    MISMO host que sirvió la página. Fail-closed: sin ninguno de los dos
    headers, o con un host distinto, devuelve False."""
    own_host = (request.url.hostname or "").lower()
    if request.url.port:
        own_host = f"{own_host}:{request.url.port}"

    origin = request.headers.get("origin", "")
    if origin:
        return _host_of(origin) == own_host

    referer = request.headers.get("referer", "")
    if referer:
        return _host_of(referer) == own_host

    return False
