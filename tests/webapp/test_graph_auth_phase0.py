"""GRAPH TOKEN LIFECYCLE — FASE 0 (higiene de logout + semántica de fallas).

Owner decision 2026-08-23. Fija el contrato que la Fase 1 (MSAL Browser) va a
apoyarse encima, y que hasta ahora no existía: **la sesión del Hub y la
autorización delegada de Graph son dos cosas con vidas distintas**, y el Hub
tiene que decirlas distinto.

Lo que se prueba acá, en el orden del pedido del owner:

* el logout limpia la cookie Y el artefacto de Graph del browser (el lado
  cliente vive en `test_hub_logout_next.py` / `hub_logout_next.node.js`, acá
  va el lado server + la ausencia del artefacto huérfano);
* falta de token  != falla de fuente;
* HTTP 401        != falla de fuente;
* una falla de fuente REAL sigue siendo falla de fuente (la parte que se
  rompe sola si alguien "arregla" esto mandando todo a `auth_required`);
* Commercial no cambia de comportamiento — no consume Graph y no se le
  inventa acoplamiento.

Nota sobre `missing_token`: ya existía en el server desde antes. El defecto
era del CLIENTE, que lo colapsaba con `source_error` al renderizar. Estos
tests lo fijan de los dos lados para que no vuelva a colapsarse.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp.graph_auth_state import (
    GRAPH_AUTH_REQUIRED,
    GRAPH_SOURCE_ERROR,
    graph_error_state,
)
from webapp.main import app
from webapp.services.reporting_repository import (
    ReportingGraphError,
    ReportingSourceError,
)

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"
_ROUTERS = _ROOT / "webapp" / "routers"
_HARNESS = Path(__file__).with_name("graph_auth_client.node.js")

# El artefacto que el browser guarda con la autorización delegada. Un solo
# nombre, en un solo lugar: si cambia, cambia acá y los tests siguen valiendo.
GRAPH_ARTIFACT = "dw-graph-token"


# --- A: la decisión, aislada ------------------------------------------------

def test_only_401_means_the_delegated_authorization_died():
    assert graph_error_state(401) == GRAPH_AUTH_REQUIRED
    # Todo lo demás es la fuente, no la autorización.
    for status in (400, 404, 429, 500, 502, 503, 504):
        assert graph_error_state(status) == GRAPH_SOURCE_ERROR, status


def test_403_is_deliberately_not_auth_required():
    # Un 403 con token delegado es scope/consentimiento: renovar la sesión NO
    # lo desbloquea, necesita un admin. Prometerle "Renovar sesión" al usuario
    # sería mandarlo a un loop. Si alguien agrega 403 acá, este test explica
    # por qué no.
    assert graph_error_state(403) == GRAPH_SOURCE_ERROR


def test_transport_failure_without_an_http_status_is_not_an_auth_problem():
    # DNS, timeout, conexión cortada: no hubo respuesta, no hubo 401.
    assert graph_error_state(None) == GRAPH_SOURCE_ERROR


def test_each_vertical_keeps_its_own_production_vocabulary_for_source_failure():
    # Lo que se comparte es la decisión de AUTORIZACIÓN. El nombre del otro
    # estado sigue siendo de cada vertical (Reporting: source_error;
    # Staffing: graph_error) — renombrarlos no es el trabajo de este fix.
    assert graph_error_state(500, source_state="graph_error") == "graph_error"
    assert graph_error_state(401, source_state="graph_error") == GRAPH_AUTH_REQUIRED


def test_the_module_is_a_pure_leaf():
    # Sin imports de webapp: cualquier router puede usarlo sin riesgo de ciclo,
    # y la decisión no arrastra HTTP ni templates.
    src = (_ROOT / "webapp" / "graph_auth_state.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", src, flags=re.S)
    assert "import" in code  # el __future__, para que el assert de abajo valga
    assert "webapp" not in code
    for leaked in ("JSONResponse", "fastapi", "Request", "template"):
        assert leaked not in code, leaked


# --- B: los routers, end to end --------------------------------------------

client = TestClient(app)

# Un endpoint por vertical alcanza para el contrato: la clasificación es
# compartida, no per-endpoint (y eso lo prueba el test de cobertura de abajo).
REPORTING_ENDPOINT = "/operaciones/reporting/integral/data/rentabilidad"


@pytest.fixture()
def authorized(monkeypatch):
    """Sesión + autorización de Operations, sin tocar credenciales reales.

    Se parchea a través del namespace del ROUTER — mismo patrón que
    `test_reporting_feedback.py`: el router hace `from webapp.services import
    auth_service`, así que parchear el módulo original no alcanzaría si otro
    archivo de test ya lo pisó.
    """
    import webapp.routers.reporting as rr

    monkeypatch.setattr(rr.auth_service, "session_user", lambda request: {"mail": "x@danwor.com"})
    monkeypatch.setattr(rr.operations_access, "is_operations_authorized", lambda request: True)
    return rr


def _post(endpoint, **data):
    return client.post(endpoint, data={"graph_token": "t", **data})


def test_http_401_from_graph_is_reported_as_auth_required(authorized, monkeypatch):
    def boom(self, *a, **k):
        raise ReportingGraphError("rentabilidad", 401)

    monkeypatch.setattr(authorized.ReportingService, "get_rentabilidad", boom)
    body = _post(REPORTING_ENDPOINT).json()
    assert body["state"] == GRAPH_AUTH_REQUIRED
    # Y NO el estado de fuente: ese era el defecto exacto que se cierra.
    assert body["state"] != GRAPH_SOURCE_ERROR
    # El status viaja para diagnóstico, sin filtrar nada de la respuesta cruda.
    assert body["http_status"] == 401
    assert body["stage"] == "rentabilidad"


def test_a_real_source_failure_stays_a_source_failure(authorized, monkeypatch):
    # La mitad que se rompe si alguien manda todo a `auth_required`: un 500 de
    # SharePoint NO se arregla renovando la autorización.
    def boom(self, *a, **k):
        raise ReportingGraphError("rentabilidad", 500)

    monkeypatch.setattr(authorized.ReportingService, "get_rentabilidad", boom)
    assert _post(REPORTING_ENDPOINT).json()["state"] == GRAPH_SOURCE_ERROR


def test_a_parsing_or_content_failure_stays_a_source_failure(authorized, monkeypatch):
    # `ReportingSourceError` no es de transporte: no tiene status y nunca fue
    # candidata a `auth_required`. Se fija para que el refactor no la arrastre.
    def boom(self, *a, **k):
        raise ReportingSourceError("rentabilidad")

    monkeypatch.setattr(authorized.ReportingService, "get_rentabilidad", boom)
    body = _post(REPORTING_ENDPOINT).json()
    assert body["state"] == GRAPH_SOURCE_ERROR
    assert "http_status" not in body  # no hay status que reportar


def test_a_missing_token_is_not_a_source_failure(authorized):
    # Sin token no se llegó a hablar con SharePoint: decir "no pudimos cargar
    # desde SharePoint" sería falso.
    body = client.post(REPORTING_ENDPOINT, data={"graph_token": ""}).json()
    assert body["state"] == "missing_token"
    assert body["state"] != GRAPH_SOURCE_ERROR


def test_staffing_401_is_auth_required_with_its_own_source_vocabulary(monkeypatch):
    import webapp.routers.staffing as rs

    monkeypatch.setattr(rs.auth_service, "session_user", lambda request: {"mail": "x@danwor.com"})
    monkeypatch.setattr(rs.operations_access, "is_operations_authorized", lambda request: True)
    src = (_ROUTERS / "staffing.py").read_text(encoding="utf-8")
    # El único sitio de Staffing que tiene el status a mano ahora lo clasifica.
    assert 'graph_error_state(exc.status_code, source_state="graph_error")' in src
    # Y el vocabulario de fuente de Staffing sigue intacto donde corresponde.
    assert '"state": "graph_error"' in src


def test_every_graph_transport_failure_in_reporting_goes_through_the_shared_decision():
    # Cobertura, no muestreo: si mañana alguien agrega un endpoint con su
    # propio `except ReportingGraphError`, este test lo encuentra.
    src = (_ROUTERS / "reporting.py").read_text(encoding="utf-8")
    handlers = re.findall(r"except ReportingGraphError[^\n]*\n(\s+)([^\n]+)", src)
    assert handlers, "no se encontró ningún handler de ReportingGraphError"
    for _indent, first_line in handlers:
        assert "_graph_error_response(exc)" in first_line, first_line
    # Y la decisión se toma en un solo lugar.
    assert src.count("def _graph_error_response") == 1
    assert "graph_error_state" in src


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_graph_auth_client_invariants_hold():
    """El clasificador y los dos cuadros, ejecutados de verdad. Un test de
    archivo no puede probar que un payload `auth_required` PRODUZCA el cuadro
    de renovación y no el de fuente — eso era exactamente el defecto.
    """
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Graph auth client failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL GRAPH AUTH CLIENT INVARIANTS PASSED" in result.stdout


# --- C: el lado cliente — el artefacto no sobrevive al logout --------------

def test_logout_clears_the_graph_artifact_through_a_single_authority():
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    # Una sola función sabe cómo se borra el artefacto. En la FASE 1A se mudó
    # de app.js a la autoridad compartida: sigue habiendo UNA, y ahora está
    # donde vive el resto del ciclo de vida de la credencial.
    authority = (_STATIC / "hub_auth.js").read_text(encoding="utf-8")
    assert authority.count("window.dwClearGraphAuth = function () {") == 1
    assert authority.count(f'sessionStorage.removeItem(ARTIFACT)') == 1
    assert "window.dwClearGraphAuth = function" not in app_js
    assert GRAPH_ARTIFACT not in app_js  # el chrome ya no nombra la credencial
    # ...y el logout la usa en vez de reimplementarla.
    logout = app_js[app_js.index("window.dwLogout = function () {"):]
    logout = logout[:logout.index("\n};") + 3]
    assert "dwClearGraphAuth()" in logout
    assert GRAPH_ARTIFACT not in logout, "el logout no debe conocer el nombre del artefacto"


def test_no_vertical_clears_the_graph_artifact_on_its_own():
    # Higiene de auth es del Hub. Una vertical borrando el token por su cuenta
    # es exactamente el acoplamiento que la Fase 1 viene a eliminar.
    for vertical in ("reporting.js", "commercial.js", "tools.js"):
        path = _STATIC / vertical
        if not path.exists():
            continue
        src = path.read_text(encoding="utf-8")
        assert "removeItem" not in src, vertical


def test_reauthentication_is_offered_without_a_full_page_reload():
    # Owner: "Do NOT implement full-page reload as recovery". La acción es el
    # logout compartido (que preserva `next`), no un reload que perdería la
    # ubicación y no renovaría nada.
    src = (_STATIC / "reporting.js").read_text(encoding="utf-8")
    start = src.index("function reportingAuthRequiredHtml(")
    fn = src[start:src.index("\n}", start) + 2]
    assert "dwLogout()" in fn
    for banned in ("location.reload", "location.href", "window.location"):
        assert banned not in fn, banned


# --- D: Commercial no cambia ----------------------------------------------

def test_commercial_is_untouched_by_the_graph_auth_contract():
    # Commercial va por una cuenta de servicio de Odoo del lado server: no
    # tiene autorización delegada que renovar. No se le crea acoplamiento.
    commercial_py = (_ROUTERS / "commercial.py")
    if commercial_py.exists():
        src = commercial_py.read_text(encoding="utf-8")
        assert "graph_auth_state" not in src
        assert GRAPH_AUTH_REQUIRED not in src
    commercial_js = _STATIC / "commercial.js"
    if commercial_js.exists():
        src = commercial_js.read_text(encoding="utf-8")
        assert GRAPH_ARTIFACT not in src
        assert GRAPH_AUTH_REQUIRED not in src
