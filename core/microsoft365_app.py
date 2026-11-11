"""
microsoft365_app.py — token de APLICACIÓN de Microsoft Entra ID (OAuth 2.0 client credentials) para integraciones app-only del Hub.

Separado a propósito de MSAL (tokens DELEGADOS del usuario en el navegador, ver `core/microsoft365.py`): acá no hay usuario, ni
sesión, ni `sessionStorage`. Lo usa hoy solo `webapp/services/notifications.py` (identidad `Hub-Danwor-Notifications`, `Mail.Send`
restringido por Exchange RBAC). Mismo transporte que el resto de Microsoft 365 del Hub: `urllib`, sin dependencias nuevas.

Contrato de seguridad: el secreto y el token NUNCA se loguean, persisten ni se devuelven en errores; el cuerpo de una respuesta de
error del endpoint de token no se propaga (puede describir la aplicación). El llamador decide la política de reintentos con la
clasificación que expone `AppTokenError.transient`. No hay caché: cada llamada pide un token (volumen bajo, sin estado mutable).
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
GRAPH_SCOPE = "https://graph.microsoft.com/.default"
DEFAULT_TIMEOUT = 12


class AppTokenError(RuntimeError):
    """Fallo al obtener el token de aplicación. `transient`: reintentar más tarde no es inútil (red, 429, 5xx); si no, es de
    configuración/credenciales (400/401: cliente o secreto inválido, tenant inexistente). El mensaje nunca contiene secretos."""

    def __init__(self, message: str, *, transient: bool, status: int | None = None):
        super().__init__(message)
        self.transient = transient
        self.status = status


def acquire_app_token(tenant_id: str, client_id: str, client_secret: str, *, scope: str = GRAPH_SCOPE, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Token de acceso de aplicación (client credentials) para `scope`. Lanza `AppTokenError` ante cualquier fallo."""
    if not (tenant_id and client_id and client_secret):
        raise AppTokenError("configuración incompleta", transient=False)
    data = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": client_id,
                                   "client_secret": client_secret, "scope": scope}).encode("utf-8")
    request = urllib.request.Request(TOKEN_URL.format(tenant=urllib.parse.quote(tenant_id, safe="")), data=data,
                                     headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        transient = exc.code == 429 or exc.code >= 500
        raise AppTokenError(f"el endpoint de token respondió HTTP {exc.code}", transient=transient, status=exc.code) from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        raise AppTokenError(f"sin conexión con el endpoint de token ({type(exc).__name__})", transient=True) from None
    except ValueError:
        raise AppTokenError("respuesta de token ilegible", transient=True) from None
    token = payload.get("access_token") if isinstance(payload, dict) else None
    if not token or not isinstance(token, str):
        raise AppTokenError("respuesta de token sin access_token", transient=False)
    return token
