"""GRAPH TOKEN LIFECYCLE — FASE 1A: la autoridad única del Hub.

Owner decision 2026-08-23. Este archivo es el GUARD ARQUITECTÓNICO del
increment: 25 consumidores leían la credencial de Graph directo del storage
—17 en Reporting, 3 en app.js, 4 en atributos htmx de templates—, cada uno
con su propio `|| ""`. Ahora hay UNO.

Por qué importa, más allá de la prolijidad: con 25 lecturas directas,
*ningún* consumidor podía RENOVAR nada (leer un string de storage no es una
operación que pueda esperar), y cambiar el proveedor de tokens obligaba a
tocar 25 lugares sin poder garantizar que ninguno quedara con la lectura
vieja. La Fase 1B (MSAL) tiene que poder reemplazar el proveedor tocando UNA
función.

CERO cambio de comportamiento de auth pretendido en este commit: la
autoridad resuelve contra el mismo artefacto de siempre.

El guard es de PROPIEDAD, no de estilo: lo que se prohíbe es que un feature
vuelva a poseer la credencial.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"
_TEMPLATES = _ROOT / "webapp" / "templates"
_AUTHORITY = _STATIC / "hub_auth.js"
_HARNESS = Path(__file__).with_name("hub_auth_authority.node.js")

# El nombre del artefacto. Aparece acá y en la autoridad, en ningún otro lado.
ARTIFACT = "dw-graph-token"
# El marcador declarativo de htmx NO es la credencial: dice "este request
# necesita Graph", no la lleva. Se excluye explícitamente de los conteos.
MARKER = "data-dw-graph-token"


def _js_code(raw: str) -> str:
    """JS sin comentarios —de línea Y de bloque—: los comentarios que
    documentan este contrato citan a propósito los literales que estos tests
    cuentan. `auth.js` explica en su cabecera `/* */` justamente el PKCE que
    dejó de tener, así que quedarse sólo con `//` deja pasar todo eso."""
    sin_bloque = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    return "\n".join(
        l for l in sin_bloque.splitlines() if not l.strip().startswith("//"))


def _without_marker(text: str) -> str:
    return text.replace(MARKER, "")


# --- A: EL GUARD -----------------------------------------------------------

def test_no_code_anywhere_owns_a_graph_credential_key():
    """EL GUARD CENTRAL, actualizado a la Fase 1B.

    En la Fase 1A la regla era "sólo la autoridad nombra `dw-graph-token`".
    Ahora esa clave NO EXISTE en ninguna parte: MSAL es dueño completo de sus
    artefactos y usa sus propias claves. Así que la regla se vuelve más
    fuerte, no más débil — nadie, ni la autoridad, tiene una clave propia de
    credenciales.

    Si este test falla, alguien reintrodujo storage de credenciales a mano, que
    es exactamente lo que la decisión del owner prohíbe.
    """
    offenders = []
    for path in sorted(_ROOT.glob("webapp/**/*")):
        if path.is_dir() or path.suffix not in (".js", ".html", ".py", ".css"):
            continue
        if "vendor" in path.parts:      # MSAL es de MSAL
            continue
        text = _without_marker(path.read_text(encoding="utf-8"))
        if ARTIFACT in text:
            offenders.append(str(path.relative_to(_ROOT)))
    assert offenders == [], (
        "estos archivos vuelven a nombrar una clave de credencial propia: "
        + ", ".join(offenders)
    )


def test_the_application_never_touches_a_refresh_token():
    """El límite explícito de la aprobación del owner: MSAL puede usar su cache
    de SPA —refresh token incluido— pero la app NUNCA lo lee, escribe, rota,
    serializa ni exporta, y no hay implementación propia ni clave propia.

    Se cuenta sobre código sin comentarios a propósito: los bloques que
    documentan este contrato mencionan "refresh token" justamente para
    explicar de quién es.
    """
    for path in sorted(_ROOT.glob("webapp/**/*.js")):
        if "vendor" in path.parts:
            continue
        code = _js_code(path.read_text(encoding="utf-8"))
        low = code.lower()
        for banned in ("refresh_token", "refreshtoken"):
            assert banned not in low, f"{path.name}: {banned}"


def test_no_application_code_defines_its_own_graph_token_accessor():
    """El guard del artefacto (arriba) cubre el nombre. Este cubre la otra
    forma de volver a poseer la credencial: que una vertical defina su propio
    accesor y lo resuelva por su cuenta.

    OJO con lo que NO se prohíbe: `sessionStorage` en general. app.js lo usa
    legítimamente para el orden de las secciones del dashboard, que no es una
    credencial. La regla es sobre el RECURSO, no sobre el mecanismo — confundir
    los dos fue mi primer intento y prohibía código correcto."""
    for name in ("reporting.js", "commercial.js", "tools.js", "auth.js", "app.js"):
        path = _STATIC / name
        if not path.exists():
            continue
        code = _js_code(path.read_text(encoding="utf-8"))
        # Nadie define un `dwGraphToken`/`dwClearGraphAuth`/`dwRememberGraphAuth`:
        # esas son de la autoridad. Invocarlas sí, definirlas no.
        #
        # El `=(?!=)` no es cosmético: `auth.js` comprueba la disponibilidad de
        # la autoridad con `typeof window.dwRememberGraphAuth === "function"`, y
        # un `=` pelado matchea el primero de ese `===`. O sea que la primera
        # versión de este test declaraba "define" a una invocación defensiva.
        for owned in ("dwGraphToken", "dwClearGraphAuth", "dwRememberGraphAuth"):
            defines = re.compile(
                r"(?:window\.)?" + owned + r"\s*=(?!=)"      # asignación
                r"|function\s+" + owned + r"\b"              # declaración
            )
            assert not defines.search(code), f"{name}: define {owned}"


def test_templates_never_carry_the_credential():
    """Los 4 `hx-vals` llevaban la credencial en un atributo HTML. Ahora sólo
    DECLARAN que la necesitan — el valor lo inyecta el hook transversal."""
    for path in sorted(_TEMPLATES.glob("**/*.html")):
        text = path.read_text(encoding="utf-8")
        assert "sessionStorage.getItem" not in text, path.name
        assert "graph_token:" not in text, path.name


def test_the_authority_is_loaded_before_every_consumer():
    """Un guard sin carga no sirve: si `hub_auth.js` no está en la página, los
    consumidores fallan en runtime en vez de en el test."""
    def script_pos(text: str, filename: str) -> int:
        """Posición del TAG que carga el archivo, no de cualquier mención.
        `base.html` nombra "app.js" en un comentario de la línea 5, y
        landing/auth_callback nombran "auth.js" en prosa — comparar índices de
        menciones daba un falso negativo (mi primer intento)."""
        m = re.search(r"<script[^>]*path='" + re.escape(filename) + r"'", text)
        assert m, f"no se encontró el tag de {filename}"
        return m.start()

    hub = "hub_auth.js"
    # `base.html` cubre todo el chrome del Hub, y la autoridad va ANTES de app.js.
    base = (_TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert script_pos(base, hub) < script_pos(base, "app.js"),         "la autoridad debe cargar antes del chrome"
    # `landing.html` / `auth_callback.html` son documentos INDEPENDIENTES (no
    # extienden base.html) y son justo donde el login guarda el token.
    for name in ("landing.html", "auth_callback.html"):
        text = (_TEMPLATES / name).read_text(encoding="utf-8")
        assert script_pos(text, hub) < script_pos(text, "auth.js"), name


# --- B: el contrato de la autoridad ---------------------------------------

def test_the_contract_is_async_from_day_one():
    """`async` aunque hoy resuelva de inmediato: es lo que permite que la Fase
    1B renueve de verdad sin tocar ninguno de los 25 consumidores. Si alguien
    la vuelve síncrona "porque no hace falta", la migración se cae."""
    code = _js_code(_AUTHORITY.read_text(encoding="utf-8"))
    assert "window.dwGraphToken = async function ()" in code


def test_the_authority_is_not_a_graph_client():
    """Responde por la credencial y nada más: no arma requests ni sabe qué
    endpoint necesita qué. Mezclar las dos cosas es lo que convierte un
    módulo de auth en un God Module."""
    code = _js_code(_AUTHORITY.read_text(encoding="utf-8"))
    for leaked in ("graph.microsoft.com", "/operaciones/", "/admin/", "/catalog/"):
        assert leaked not in code, leaked


def test_every_graph_consumer_goes_through_the_authority():
    """Cobertura, no muestreo: los 20 sitios de JS (17 Reporting + 3 app.js)
    piden el token a la autoridad, y ninguno se quedó con la lectura vieja."""
    reporting = _js_code((_STATIC / "reporting.js").read_text(encoding="utf-8"))
    app = _js_code((_STATIC / "app.js").read_text(encoding="utf-8"))
    assert reporting.count("await dwGraphToken()") == 17
    assert app.count("await dwGraphToken()") == 3
    # Y todo sitio que manda `graph_token` en un body lo obtuvo de la autoridad.
    for name, code in (("reporting.js", reporting), ("app.js", app)):
        for m in re.finditer(r"graph_token", code):
            window = code[max(0, m.start() - 400):m.start()]
            assert "await dwGraphToken()" in window, f"{name}: graph_token sin la autoridad"


def test_login_no_longer_writes_a_credential_at_all():
    """En la Fase 1A el login ENTREGABA el token a la autoridad para que lo
    guardara. En la 1B ni eso: MSAL lo guarda en su cache al resolver el
    popup, así que el paso de guardado desaparece. `auth.js` queda como la
    capa de UI del login —botón, overlay, mensajes— y el protocolo entero se
    fue.
    """
    code = _js_code((_STATIC / "auth.js").read_text(encoding="utf-8"))
    # El protocolo ya no vive acá.
    for gone in ("code_challenge", "code_verifier", "crypto.subtle", "oauth2/v2.0",
                 "grant_type", "dwRememberGraphAuth", "sessionStorage", "localStorage"):
        assert gone not in code, f"auth.js todavía tiene {gone}"
    # Y el login se pide a la autoridad.
    assert "dwHubLogin()" in code


def test_the_backend_still_creates_the_hub_session_from_the_id_token():
    """Lo que NO cambia: la sesión del Hub la crea el server con el `id_token`
    validado. MSAL no sabe nada de la cookie del Hub, y el Hub no sabe nada de
    la cache de MSAL."""
    code = _js_code(_AUTHORITY.read_text(encoding="utf-8"))
    assert '"/api/auth/microsoft/token"' in code
    assert "id_token: res.idToken" in code
    # Y NUNCA se le manda un access token al backend.
    assert "accessToken" not in code.split("/api/auth/microsoft/token")[1][:400]


def test_logout_hygiene_still_goes_through_the_authority():
    """La limpieza se movió a la autoridad (sabe QUÉ se limpia) pero el chrome
    sigue decidiendo CUÁNDO. Fase 0 sigue valiendo, sólo cambió de archivo."""
    authority = _js_code(_AUTHORITY.read_text(encoding="utf-8"))
    app = _js_code((_STATIC / "app.js").read_text(encoding="utf-8"))
    assert authority.count("window.dwClearGraphAuth = function ()") == 1
    assert "window.dwClearGraphAuth = function ()" not in app  # ya no vive acá
    assert "dwClearGraphAuth()" in app                          # pero se invoca


# --- C: htmx — diferir sin crear una segunda autoridad --------------------

def test_htmx_has_no_synchronous_token_authority():
    """La salida fácil era exponer un getter síncrono para los templates. Eso
    sería una segunda autoridad, capaz de servir un token vencido justo cuando
    la única que sabe renovar no fue consultada."""
    code = _js_code(_AUTHORITY.read_text(encoding="utf-8"))
    # Una sola función pública devuelve el token, y es la async.
    publics = re.findall(r"window\.(dw\w*)\s*=", code)
    token_getters = [p for p in publics if "GraphToken" in p]
    assert token_getters == ["dwGraphToken"], token_getters


def test_htmx_defers_the_request_until_the_authority_answers():
    """El mecanismo verificado contra el artefacto pinneado htmx 2.0.4:
    `htmx:confirm` expone `issueRequest` y `preventDefault()` aborta la
    pasada; `hx-vals`/parámetros se arman después. Reemitir con `true` saltea
    el confirm, así que no hay loop."""
    code = _js_code(_AUTHORITY.read_text(encoding="utf-8"))
    assert "htmx:confirm" in code
    assert "preventDefault()" in code
    assert "issueRequest(true)" in code          # el `true` es lo que corta el loop
    assert "htmx:configRequest" in code          # inyecta el parámetro
    assert "dwGraphToken()" in code


def test_the_marker_is_declarative_and_carries_no_credential():
    """Cada sitio DECLARA que el request necesita Graph; el valor lo inyecta el
    hook (hub_auth.js), nunca viaja en el markup. Se enumeran los sitios
    APROBADOS por archivo (no un número mágico suelto) para que sumar/quitar un
    consumidor sea una decisión explícita, no un conteo silencioso:
      - operations/staffing.html    -> asignaciones (1)
      - partials/admin_panel.html   -> diagnóstico/refresh (3)
      - partials/human_capital_catalog_refresh_topbar.html -> "Actualizar
        catálogos" en un click (1), promovido al topbar compartido de
        Capital Humano (owner corrective: antes vivía sólo en
        human_capital/profiles.html; ahora un único partial incluido desde
        el slot `caller()` de CUALQUIER página CCHH, MISMO hook, sin poseer
        la credencial).
    """
    approved = {
        "operations/staffing.html": 1,
        "partials/admin_panel.html": 3,
        "partials/human_capital_catalog_refresh_topbar.html": 1,
    }
    found = {}
    for path in sorted(_TEMPLATES.glob("**/*.html")):
        n = path.read_text(encoding="utf-8").count(MARKER)
        if n:
            found[path.relative_to(_TEMPLATES).as_posix()] = n
    assert found == approved, found
    # El marcador es SIEMPRE un atributo booleano declarativo: NUNCA lleva un
    # valor (no existe `data-dw-graph-token="<credencial>"`) -- el valor lo pone
    # el hook, nunca el template.
    for path in sorted(_TEMPLATES.glob("**/*.html")):
        assert MARKER + "=" not in path.read_text(encoding="utf-8"), path


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_hub_auth_authority_invariants_hold():
    """El comportamiento REAL: la autoridad y el hook de htmx ejecutados
    contra un DOM y un htmx simulados. Un test de archivo no puede probar que
    el request se difiera de verdad ni que dos requests concurrentes no se
    pisen el token."""
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Hub auth authority failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUB AUTH AUTHORITY INVARIANTS PASSED" in result.stdout
