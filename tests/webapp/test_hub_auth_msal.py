"""GRAPH TOKEN LIFECYCLE — FASE 1B: el artefacto de MSAL servido por la app.

Owner decision 2026-08-23: "Prefer a locally served pinned artifact if it can
be done without introducing a JS build pipeline". Se eligió A (local) sobre B
(CDN con integrity) porque esto es el código que maneja las credenciales, y un
CDN de terceros comprometido —o simplemente caído— deja al Hub sin
autenticación. Ver `webapp/static/vendor/README.md` para el rationale completo.

Estos tests protegen esa decisión de las tres formas en que se puede deshacer
sin darse cuenta: que alguien vuelva a apuntar a un CDN, que el bundle servido
deje de ser el declarado, o que queden dos versiones conviviendo y nadie sepa
cuál se carga.

CERO tooling agregado: el bundle es UMD y se carga con `<script src>`, igual
que los terceros de CDN que ya había. No hay npm, ni bundler, ni lockfile.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"
_VENDOR = _STATIC / "vendor"
_TEMPLATES = _ROOT / "webapp" / "templates"

# La versión pinneada. Cambiarla es un acto deliberado: hay que tocar esta
# línea, el archivo, los tres templates y el README.
MSAL_VERSION = "4.30.0"
MSAL_FILENAME = f"msal-browser-{MSAL_VERSION}.min.js"
MSAL_SHA256 = "2b580165064a6a4ec041ebd44832299172688a23a42c3b81d65314799dba04eb"

# Los tres documentos que cargan auth: `base.html` cubre el chrome del Hub;
# `landing.html` y `auth_callback.html` son independientes (no extienden base)
# y son donde ocurre el login.
AUTH_DOCUMENTS = ("base.html", "landing.html", "auth_callback.html")


def test_the_pinned_bundle_is_present_and_is_the_declared_version():
    path = _VENDOR / MSAL_FILENAME
    assert path.exists(), f"falta {MSAL_FILENAME}"
    head = path.read_text(encoding="utf-8", errors="replace")[:200]
    # El propio bundle declara su versión en el banner: se compara contra eso,
    # no contra el nombre del archivo (que cualquiera puede renombrar).
    assert f"@azure/msal-browser v{MSAL_VERSION}" in head, head[:100]


def test_the_bundle_content_matches_the_recorded_hash():
    """La integridad la garantiza el repo, y este test la hace explícita: si el
    archivo cambia sin que cambie el hash declarado, alguien lo editó o lo
    reemplazó sin pasar por el procedimiento del README."""
    import hashlib

    data = (_VENDOR / MSAL_FILENAME).read_bytes()
    assert hashlib.sha256(data).hexdigest() == MSAL_SHA256


def test_git_never_rewrites_the_vendored_bundle():
    """Sin esto, la verificación de hash de arriba es una garantía falsa.

    El repo tiene `core.autocrlf = true`, así que un checkout fresco en Windows
    reescribiría el bundle con CRLF: el archivo seguiría funcionando (JS tolera
    CRLF) pero su SHA-256 cambiaría y el test de integridad empezaría a fallar
    por una razón que no tiene nada que ver con la integridad. Peor todavía
    sería si alguien "arreglara" ese fallo actualizando el hash esperado — ahí
    la verificación quedaría midiendo el resultado de la conversión en vez del
    artefacto publicado.

    La regla de `.gitattributes` hace que el artefacto se guarde y se restaure
    byte a byte.
    """
    attrs = (_ROOT / ".gitattributes").read_text(encoding="utf-8")
    lines = [l.strip() for l in attrs.splitlines()
             if l.strip() and not l.strip().startswith("#")]
    assert any(
        l.startswith("webapp/static/vendor/") and "-text" in l for l in lines
    ), "falta la regla `-text` para webapp/static/vendor/ en .gitattributes"


def test_the_bundle_is_umd_so_no_build_pipeline_is_needed():
    """La razón por la que servirlo local no costó nada: es UMD y expone un
    global. Si una actualización trajera un ESM, `<script src>` dejaría de
    alcanzar y aparecería la tentación de meter un bundler — que es
    exactamente lo que el owner prohibió."""
    head = (_VENDOR / MSAL_FILENAME).read_text(encoding="utf-8", errors="replace")[:600]
    assert "define.amd" in head          # rama AMD del preámbulo UMD
    assert ".msal=" in head or "msal = " in head or "msal={}" in head


def test_the_bundle_exposes_every_api_the_authority_uses():
    """Si una actualización sacara cualquiera de estos símbolos, la autoridad
    fallaría en runtime y no en un test. Se verifican contra el bundle real."""
    src = (_VENDOR / MSAL_FILENAME).read_text(encoding="utf-8", errors="replace")
    for symbol in ("PublicClientApplication", "initialize", "loginPopup",
                   "acquireTokenSilent", "acquireTokenPopup", "getAllAccounts",
                   "setActiveAccount", "getActiveAccount",
                   "InteractionRequiredAuthError", "clearCache"):
        assert symbol in src, symbol


def test_msal_owns_offline_access_and_the_app_never_asks_for_it():
    """El punto que el owner corrigió: no se construye lógica propia alrededor
    de `offline_access`. MSAL lo trae en sus `defaultScopes`, y la app pide
    sólo permisos de RECURSO."""
    bundle = (_VENDOR / MSAL_FILENAME).read_text(encoding="utf-8", errors="replace")
    assert "OFFLINE_ACCESS_SCOPE" in bundle
    assert re.search(r"defaultScopes:\[[^\]]*OFFLINE_ACCESS_SCOPE", bundle), \
        "MSAL ya no trae offline_access en sus defaultScopes: revisar el contrato"
    authority = (_STATIC / "hub_auth.js").read_text(encoding="utf-8")
    code = "\n".join(
        l for l in re.sub(r"/\*.*?\*/", "", authority, flags=re.S).splitlines()
        if not l.strip().startswith("//"))
    for oidc in ("offline_access", "openid", "profile"):
        assert oidc not in code, f"la app no debe pedir {oidc} — es de MSAL"
    # Y los permisos de recurso son los mismos de siempre.
    assert '"User.Read"' in code and '"Sites.Read.All"' in code


def test_msal_is_served_by_the_app_and_never_from_a_cdn():
    """La decisión de distribución, protegida. Si alguien "simplifica" volviendo
    al CDN, este test lo encuentra."""
    for name in AUTH_DOCUMENTS:
        text = (_TEMPLATES / name).read_text(encoding="utf-8")
        assert f"vendor/{MSAL_FILENAME}" in text, name
        # Nada de MSAL desde afuera.
        for cdn in ("alcdn.msauth.net", "cdn.jsdelivr.net/npm/@azure",
                    "unpkg.com/@azure", "msauth.net"):
            assert cdn not in text, f"{name}: MSAL desde {cdn}"
    # Y en ningún otro lugar del proyecto.
    for path in sorted(_ROOT.glob("webapp/**/*")):
        if path.is_dir() or path.suffix not in (".html", ".js", ".py") or "vendor" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        assert "alcdn.msauth.net" not in text, path.name


def test_the_bundle_loads_before_the_authority_that_uses_it():
    """`hub_auth.js` usa `msal` al construir la instancia. El orden es del
    documento, no `defer` — y se compara la posición de los TAGS, no de las
    menciones en prosa (que ya me dio un falso negativo antes)."""
    def script_pos(text: str, fragment: str) -> int:
        m = re.search(r"<script[^>]*" + re.escape(fragment), text)
        assert m, f"no se encontró el tag de {fragment}"
        return m.start()

    for name in AUTH_DOCUMENTS:
        text = (_TEMPLATES / name).read_text(encoding="utf-8")
        assert script_pos(text, MSAL_FILENAME) < script_pos(text, "hub_auth.js"), name


def test_only_one_msal_version_is_vendored():
    """Dos versiones conviviendo es exactamente la ambigüedad que el pin en el
    nombre del archivo existe para evitar: nadie sabría cuál se carga."""
    bundles = sorted(p.name for p in _VENDOR.glob("msal-browser-*.js"))
    assert bundles == [MSAL_FILENAME], bundles


def test_the_vendor_directory_documents_its_own_provenance():
    """Un binario de 332 KB sin procedencia es deuda: dentro de seis meses nadie
    sabe de dónde salió ni cómo actualizarlo."""
    readme = (_VENDOR / "README.md").read_text(encoding="utf-8")
    assert MSAL_VERSION in readme
    assert MSAL_SHA256 in readme
    assert "unpkg.com/@azure/msal-browser" in readme     # el origen exacto
    assert "MIT" in readme                               # la licencia


# ── CUMPLIMIENTO DE LICENCIA DEL ARTEFACTO VENDORIZADO ────────────────────
# MIT pide que el aviso de copyright y el de permiso acompañen a la copia. El
# bundle minificado trae SOLO un banner de versión, así que el aviso vive al
# lado. Este guard es genérico a propósito: la próxima librería que se
# vendorice hereda la obligación sin que nadie tenga que acordarse.

_VENDOR = Path(__file__).resolve().parents[2] / "webapp" / "static" / "vendor"


def test_todo_artefacto_vendorizado_tiene_su_aviso_de_licencia():
    artefactos = sorted(_VENDOR.glob("*.min.js"))
    assert artefactos, "no hay artefactos vendorizados: el guard quedaría vacío"
    for artefacto in artefactos:
        aviso = artefacto.with_suffix("").with_suffix(".LICENSE")
        assert aviso.exists(), (
            f"{artefacto.name} se redistribuye sin su aviso de licencia "
            f"(se esperaba {aviso.name})"
        )
        texto = aviso.read_text(encoding="utf-8")
        # Las dos cláusulas que MIT exige que viajen con la copia.
        assert "Copyright (c)" in texto, f"{aviso.name} sin línea de copyright"
        assert "shall be included in all" in texto, f"{aviso.name} sin aviso de permiso"


def test_el_aviso_no_se_inyecta_dentro_del_bundle():
    """Meter prosa en el `.min.js` cambiaría su SHA-256 y rompería la
    verificación de integridad: el artefacto tiene que quedar idéntico a lo que
    publica el registry."""
    bundle = (_VENDOR / "msal-browser-4.30.0.min.js").read_text(encoding="utf-8", errors="ignore")
    assert "shall be included in all" not in bundle
    assert bundle.startswith("/*! @azure/msal-browser v4.30.0"), \
        "el banner original del registry tiene que quedar tal cual"
