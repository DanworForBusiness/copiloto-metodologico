"""GRAPH TOKEN LIFECYCLE — la interacción de MSAL requiere un GESTO del usuario.

Owner corrective 2026-08-23, antes del smoke real.

EL DEFECTO. La Fase 1B había separado bien un caso —sin cuenta MSAL, no se
abre nada— pero dejaba vivo el otro: con cuenta presente y
`InteractionRequiredAuthError`, `dwGraphToken()` escalaba a
`acquireTokenPopup()`. Y `dwGraphToken()` lo invoca la HIDRATACIÓN AUTOMÁTICA
de los tabs, así que ese popup podía ejecutarse sin ningún gesto detrás: el
browser lo bloquearía, y si no lo bloqueara sería peor —un loader de datos
abriendo la ventana de Microsoft por su cuenta—.

LA SEPARACIÓN. Dos contratos, y la frontera es el punto:

    await dwGraphToken()          → silencioso SIEMPRE, nunca abre nada
    await dwGraphReauthenticate() → interactivo, SÓLO desde un gesto

`dwGraphReauthenticate` es el único dueño de `acquireTokenPopup` en todo el
proyecto, y se dispara desde el botón del cuadro de la Fase 0.

LA RECUPERACIÓN. Tras reautenticar se reintenta el componente útil más chico
—los datasets del tab que el usuario está mirando— preservando vertical,
perspectivas y filtros. Sin forzar logout y sin recargar el Hub: cerrar sesión
cobraba de más (el usuario perdía ubicación y todo lo cargado) por algo que
sólo necesitaba renovar la autorización de datos. El logout sigue siendo otra
intención, con su propio botón en el topbar.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"
_AUTHORITY = _STATIC / "hub_auth.js"
_HARNESS = Path(__file__).with_name("reporting_reauth_recovery.node.js")

# Las APIs interactivas de MSAL: abren una ventana y le roban el foco al
# usuario. Sólo la autoridad puede nombrarlas, y sólo desde un gesto.
INTERACTIVE_MSAL_APIS = ("acquireTokenPopup", "loginPopup", "acquireTokenRedirect",
                         "loginRedirect", "ssoSilent")


def _js_code(raw: str) -> str:
    """JS sin comentarios —de línea Y de bloque—. Los comentarios de este
    contrato nombran a propósito las APIs que estos tests prohíben, para
    explicar de quién son: contar sobre prosa da falsos positivos."""
    sin_bloque = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    return "\n".join(
        l for l in sin_bloque.splitlines() if not l.strip().startswith("//"))


def _fn_body(code: str, header: str) -> str:
    """El cuerpo de una función, delimitado por su llave de cierre REAL — nunca
    por una ventana de N caracteres, que se rompe en silencio en cuanto la
    función crece.

    El delimitador depende de la forma: una asignación a `window.*` vive dentro
    del IIFE de `hub_auth.js` y cierra con `\\n  };` (dos espacios); una
    declaración en columna 0 cierra con `\\n}`. Detectar eso por el texto del
    header es frágil —mi primer intento buscaba `"= function"`, que NO es
    substring de `"= async function"`, así que delimitaba por la rama
    equivocada y devolvía media autoridad—; se decide por el prefijo, que es
    inequívoco.
    """
    start = code.index(header)
    close = "\n  };" if header.startswith("window.") else "\n}"
    return code[start:code.index(close, start) + len(close)]


# --- A: el invariante central ---------------------------------------------

def test_dwgraphtoken_never_opens_an_interactive_window():
    """GUARD 1. `dwGraphToken` es el camino de la hidratación automática: no
    puede abrir NADA. Si este test falla, un loader de datos volvió a poder
    abrir Microsoft por su cuenta."""
    code = _js_code(_AUTHORITY.read_text(encoding="utf-8"))
    body = _fn_body(code, "window.dwGraphToken = async function ()")
    for api in INTERACTIVE_MSAL_APIS:
        assert api not in body, f"dwGraphToken llama {api}"
    # Y tampoco delega en la puerta interactiva.
    assert "dwGraphReauthenticate" not in body
    # Lo que SÍ hace: intentar en silencio y señalar.
    assert "acquireTokenSilent" in body
    assert "needsInteraction = true" in body


def test_only_the_shared_reauthentication_owns_the_interactive_api():
    """GUARD 5. `acquireTokenPopup` aparece EXACTAMENTE una vez en todo el
    proyecto, y es dentro de `dwGraphReauthenticate`."""
    code = _js_code(_AUTHORITY.read_text(encoding="utf-8"))
    assert code.count("acquireTokenPopup") == 1, "acquireTokenPopup debe estar en un solo lugar"
    reauth = _fn_body(code, "window.dwGraphReauthenticate = async function ()")
    assert "acquireTokenPopup" in reauth
    # `loginPopup` es del login (otra intención, también con gesto detrás).
    assert code.count("loginPopup") == 1
    login = _fn_body(code, "window.dwHubLogin = async function ()")
    assert "loginPopup" in login


def test_no_vertical_calls_an_interactive_msal_api_directly():
    """GUARD 9. Ninguna vertical instancia MSAL ni llama a sus APIs
    interactivas: pueden INVOCAR la acción compartida, nada más. Dos dueños de
    la interacción es cómo vuelven las tormentas de popups."""
    for name in ("reporting.js", "commercial.js", "tools.js", "app.js", "auth.js"):
        path = _STATIC / name
        if not path.exists():
            continue
        code = _js_code(path.read_text(encoding="utf-8"))
        assert "PublicClientApplication" not in code, name
        assert "new msal" not in code, name
        for api in INTERACTIVE_MSAL_APIS:
            assert api not in code, f"{name}: {api}"


def test_the_interactive_path_is_serialized_against_popup_storms():
    """GUARDS 6 y 7. Un doble click en "Renovar sesión", o dos tabs ofreciendo
    el botón y ambos clickeados, tienen que producir UN popup. El candado es
    compartido con el login a propósito: son las dos únicas interacciones."""
    code = _js_code(_AUTHORITY.read_text(encoding="utf-8"))
    reauth = _fn_body(code, "window.dwGraphReauthenticate = async function ()")
    assert "runInteractive(" in reauth
    # Y el candado se libera siempre, incluso si el popup falla.
    assert "finally" in _fn_body(code, "function runInteractive(fn)")


# --- B: la recuperación ---------------------------------------------------

def test_the_phase0_action_reauthenticates_instead_of_logging_out():
    """La acción del cuadro dejó de ser el logout. Cerrar sesión funcionaba,
    pero cobraba de más: el usuario perdía la ubicación, los filtros y todo lo
    ya cargado para resolver algo que sólo necesitaba renovar la autorización
    de datos."""
    code = _js_code((_STATIC / "reporting.js").read_text(encoding="utf-8"))
    box = _fn_body(code, "function reportingAuthRequiredHtml()")
    assert "reportingReauthenticate()" in box
    assert "dwLogout" not in box


def test_logout_remains_a_separate_intent(monkeypatch):
    """GUARD 10. "Quiero terminar mi sesión del Hub" sigue siendo otra cosa,
    con su propio botón en el topbar transversal, y sigue bajando las DOS
    cosas: la sesión del Hub y el contexto de auth de MSAL."""
    app_code = _js_code((_STATIC / "app.js").read_text(encoding="utf-8"))
    # El logout sigue existiendo, uno solo, y no se confunde con reautenticar.
    assert app_code.count("window.dwLogout = function ()") == 1
    logout = _fn_body(app_code, "window.dwLogout = function ()")
    assert "/api/auth/logout" in logout          # termina la sesión del Hub
    assert "dwClearGraphAuth()" in logout        # y baja el contexto de MSAL
    assert "dwGraphReauthenticate" not in logout, "el logout no reautentica"
    # Y el botón del topbar sigue siendo el del logout, no el de reautenticar.
    topbar = (_ROOT / "webapp" / "templates" / "partials" / "platform_topbar.html") \
        .read_text(encoding="utf-8")
    assert topbar.count("dwLogout()") == 1
    assert "dwGraphReauthenticate" not in topbar
    # La limpieza baja cuenta Y cache de MSAL (las dos, o algo queda vivo).
    authority = _js_code(_AUTHORITY.read_text(encoding="utf-8"))
    clear = _fn_body(authority, "window.dwClearGraphAuth = function ()")
    assert "setActiveAccount(null)" in clear
    assert "clearCache()" in clear


def test_recovery_never_reloads_or_navigates():
    """Owner: "Do NOT require a full Hub reload if the component can retry."
    Un reload perdería la ubicación y no renovaría nada — es justamente lo que
    el usuario venía haciendo a mano antes de todo este trabajo."""
    code = _js_code((_STATIC / "reporting.js").read_text(encoding="utf-8"))
    fn = _fn_body(code, "async function reportingReauthenticate()")
    for banned in ("location.reload", "location.href", "window.location", "dwLogout"):
        assert banned not in fn, banned


def test_every_tab_can_recover():
    """Un tab sin datasets declarados reautenticaría y después no reintentaría
    nada: el usuario vería el mismo cuadro para siempre. Se compara contra los
    triggers de hidratación, que son la lista real de tabs."""
    code = (_STATIC / "reporting.js").read_text(encoding="utf-8")
    triggers = re.search(r"const TAB_HYDRATION_TRIGGERS = \{(.*?)\n\};", code, re.S)
    datasets = re.search(r"const REPORTING_TAB_DATASETS = \{(.*?)\n\};", code, re.S)
    assert triggers and datasets
    tabs = set(re.findall(r"^\s{2}(\w+):", triggers.group(1), re.M))
    cubiertos = set(re.findall(r"^\s{2}(\w+):", datasets.group(1), re.M))
    assert tabs and tabs <= cubiertos, f"tabs sin recuperación: {tabs - cubiertos}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_reporting_reauth_recovery_invariants_hold():
    """GUARD 8 y el resto del comportamiento: ejecuta `reportingReauthenticate`
    REAL contra un `dwGraphReauthenticate` controlado y verifica sobre el estado
    lo que un test de archivo no puede — que se reintente sólo el tab activo,
    que no se pierdan filtros ni perspectiva, que un dataset ya listo no se
    vuelva a pedir, y que cancelar no cambie nada."""
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Reporting reauth recovery failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL REPORTING REAUTH RECOVERY INVARIANTS PASSED" in result.stdout
