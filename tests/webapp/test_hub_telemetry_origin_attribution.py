"""
test_hub_telemetry_origin_attribution.py — HUB TELEMETRY: GENERIC ORIGIN
ATTRIBUTION GATE (owner gate, ver HANDOFF).

`webapp/routers/telemetry.py` atribuía CUALQUIER beacon compartido
(`nav_jump`/`module_continue`/etc, ver app.js) a `scope=copilot,
area=methodology` sin importar de qué página vino — Estrategia Comercial ya
dispara `nav_jump` (`.section-index a` es un selector TRANSVERSAL, ver
app.js), así que un click en su índice quedaba mal clasificado como
Copiloto.

Fix: el cliente manda `module_id` (identidad ESTABLE, `window.DW_MODULE_ID`,
ver base.html) — el servidor lo resuelve vía `module_registry`
(`telemetry._resolve_beacon_origin`), NUNCA confía en un scope/area que el
cliente pudiera mandar directo. Sin `module_id` resoluble, cae al
comportamiento HISTÓRICO exacto (Copiloto/Metodología, sin `module`).

Prueba en dos capas: (1) `_resolve_beacon_origin` en aislamiento total (sin
HTTP) para A-E, (2) el contrato HTTP end-to-end para F, (3) auditoría
estática de fuente para G."""
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.routers import telemetry as telemetry_router
from webapp.services import activity_analytics, activity_service, auth_service, module_registry
from tests.webapp.test_activity_routes import _hx, _owner_client

_SESSION = auth_service.create_session(
    {"email": "telemetry-test@danwor.com", "name": "Telemetry Test", "first_name": "Telemetry", "family_name": "Test"}
)
client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)


# ============================================================================
# A/B/C/D — `_resolve_beacon_origin` en aislamiento (sin HTTP).
# ============================================================================

def test_a_strategy_module_id_resolves_to_strategy_origin():
    scope, area, module = telemetry_router._resolve_beacon_origin("CM02")
    assert (scope, area, module) == ("commercial", "strategy", "CM02")


def test_b_copilot_module_id_resolves_to_methodology_origin():
    scope, area, module = telemetry_router._resolve_beacon_origin("CP01")
    assert (scope, area, module) == ("copilot", "methodology", "CP01")


def test_c_unknown_module_id_is_invalid_not_copilot():
    # Superseded by the Hub Telemetry Invalid Module Context gate: an
    # unresolvable module_id used to collapse into the SAME fallback as a
    # genuine legacy caller (Copilot/Methodology) -- that silently turned a
    # malformed MODERN beacon into legitimate Copilot activity. Now it is
    # `None` (invalid, caller must drop it), never Copilot.
    origin = telemetry_router._resolve_beacon_origin("totally-fake-spoofed-id")
    assert origin is None


def test_d_empty_module_id_retains_safe_historical_behavior():
    scope, area, module = telemetry_router._resolve_beacon_origin("")
    assert (scope, area, module) == ("copilot", "methodology", None)


def test_e_synthetic_registered_module_resolves_without_editing_telemetry_py(monkeypatch):
    # E: "otro módulo registrado puede usar el MISMO mecanismo sin editar
    # telemetry.py" -- probado literalmente: un ModuleDescriptor sintético
    # bajo un workspace nuevo ("widgets_workspace", ni copilot/operations/
    # commercial) resuelve al fallback histórico por workspace desconocido
    # (comportamiento seguro, documentado), y uno bajo "commercial" (YA
    # mapeado) resuelve de punta a punta -- CERO cambios a este archivo.
    # `resolve()` (usado por `_resolve_beacon_origin`) lee de `_registry()`
    # (lru_cache interno), NUNCA de `all_entries()` -- hay que parchear la
    # fuente REAL que `resolve()` consulta, no el wrapper público.
    real_entries = module_registry.all_entries()
    synthetic = module_registry.ModuleDescriptor(
        module_id="CM99", workspace="commercial", area="widgets", kind="module",
        content_key=None, visible_label="Widgets (synthetic test module)",
        display_order=99, legacy_aliases=("widgets_test",),
    )
    monkeypatch.setattr(module_registry, "_registry", lambda: real_entries + (synthetic,))

    scope, area, module = telemetry_router._resolve_beacon_origin("CM99")
    assert (scope, area, module) == ("commercial", "widgets", "CM99")


# ============================================================================
# F — end-to-end HTTP: la página expone DW_MODULE_ID, el beacon lo manda, el
# endpoint lo resuelve, Usage Metrics lo consume correctamente atribuido.
# ============================================================================

def test_f_strategy_page_exposes_dw_module_id_for_the_client_beacon(monkeypatch):
    _owner_client_c = _owner_client(monkeypatch)
    page = _owner_client_c.get("/comercial/estrategia")
    assert page.status_code == 200
    assert "window.DW_MODULE_ID=" in page.text
    assert '"CM02"' in page.text.split("window.DW_MODULE_ID=", 1)[1].split(";</script>", 1)[0]


def test_f_copilot_module_page_exposes_its_own_dw_module_id(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    page = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert page.status_code == 200
    snippet = page.text.split("window.DW_MODULE_ID=", 1)[1].split(";</script>", 1)[0]
    assert '"CP01"' in snippet


def test_f_strategy_nav_jump_reaches_usage_metrics_attributed_as_strategy(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE", raising=False)
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    empty_feedback = tmp_path / "_no_feedback"
    empty_feedback.mkdir()
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback)

    c = _owner_client(monkeypatch)
    # Simula EXACTAMENTE lo que dwBeacon() manda al hacer click en
    # `.section-index a` dentro de la página de Estrategia (module_id =
    # window.DW_MODULE_ID, ya probado presente arriba).
    res = c.post("/telemetry/event", json={"event": "nav_jump", "stem": "", "module_id": "CM02", "href": "#p1"})
    assert res.status_code == 204

    saved = list(tmp_path.glob("activity_*.jsonl"))
    assert len(saved) == 1
    record = json.loads(saved[0].read_text(encoding="utf-8").strip())
    assert record["event"] == "nav_jump"
    assert record["scope"] == "commercial"
    assert record["area"] == "strategy"
    assert record["module"] == "CM02"

    t = _hx(c, "/admin/activity").text
    assert 'name="area" value="strategy"' in t
    assert "1 evento(s)" in t


def test_f_copilot_nav_jump_still_reaches_usage_metrics_as_methodology(monkeypatch, tmp_path):
    # Regresión: el MISMO endpoint, para Copiloto, sigue atribuyendo igual
    # que siempre.
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE", raising=False)
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    empty_feedback = tmp_path / "_no_feedback"
    empty_feedback.mkdir()
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback)

    c = _owner_client(monkeypatch)
    res = c.post("/telemetry/event", json={"event": "nav_jump", "stem": "01_Arquitectura_y_Conectividad", "module_id": "CP01", "href": "#s1"})
    assert res.status_code == 204

    saved = list(tmp_path.glob("activity_*.jsonl"))
    record = json.loads(saved[0].read_text(encoding="utf-8").strip())
    assert record["scope"] == "copilot"
    assert record["area"] == "methodology"
    assert record["module"] == "CP01"


def test_f_legacy_beacon_without_module_id_still_attributes_to_copilot(monkeypatch, tmp_path):
    # D/F combinado a nivel HTTP: un caller legacy (versión vieja de
    # app.js, o cualquier otro futuro consumidor que aún no mande
    # `module_id`) sigue rindiendo EXACTAMENTE el envelope histórico —
    # sin la clave "module" en absoluto (nunca `module: null`).
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE", raising=False)
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)

    c = _owner_client(monkeypatch)
    res = c.post("/telemetry/event", json={"event": "module_continue", "stem": "01"})
    assert res.status_code == 204

    saved = list(tmp_path.glob("activity_*.jsonl"))
    record = json.loads(saved[0].read_text(encoding="utf-8").strip())
    assert record["scope"] == "copilot"
    assert record["area"] == "methodology"
    assert "module" not in record


# ============================================================================
# E (Hub Telemetry Invalid Module Context gate) — HTTP end-to-end: un
# module_id EXPLÍCITO pero no reconocido no debe generar NINGÚN registro de
# actividad (ni Copiloto ni ningún otro), y la respuesta sigue siendo la
# 204 normal (sin UI de error para telemetría).
# ============================================================================

def test_e_invalid_module_id_is_dropped_not_attributed_to_copilot(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE", raising=False)
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)

    c = _owner_client(monkeypatch)
    res = c.post(
        "/telemetry/event",
        json={"event": "nav_jump", "stem": "", "module_id": "totally-fake-spoofed-id", "href": "#p1"},
    )
    assert res.status_code == 204
    assert not list(tmp_path.glob("activity_*.jsonl"))


def test_e_invalid_module_id_does_not_leak_client_scope_area(monkeypatch, tmp_path):
    # F: aun mandando scope/area/module directo en el body (campos NO
    # aceptados, ver el filtro de `payload` en telemetry.py), un
    # module_id inválido sigue sin generar registro -- el cliente no puede
    # ni forzar un origen inventado ni forzar Copiloto por defecto.
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE", raising=False)
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)

    c = _owner_client(monkeypatch)
    res = c.post(
        "/telemetry/event",
        json={
            "event": "nav_jump", "stem": "", "module_id": "totally-fake-spoofed-id",
            "scope": "commercial", "area": "strategy", "module": "CM02",
        },
    )
    assert res.status_code == 204
    assert not list(tmp_path.glob("activity_*.jsonl"))


# ============================================================================
# G — auditoría estática: cero condicional Strategy-específico en telemetry.py.
# ============================================================================

def test_g_no_strategy_specific_conditional_exists_in_telemetry_py():
    # Auditoría de CÓDIGO real, no de prosa explicativa: los docstrings/
    # comentarios de este archivo SÍ mencionan "Estrategia"/"CM02" (explican
    # POR QUÉ existe el fix) -- lo que se prueba acá es que ningún nodo AST
    # de CÓDIGO (comparación, valor de dict/lista, argumento de llamada)
    # sea un id/área/scope de un módulo Comercial específico. La ÚNICA
    # tabla de traducción permitida (`_WORKSPACE_SCOPE`) es a nivel
    # WORKSPACE (copilot/operations/commercial) -- nunca por módulo.
    import ast
    from pathlib import Path
    source = (Path(__file__).resolve().parents[2] / "webapp" / "routers" / "telemetry.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned = {"cm01", "cm02", "cm03", "crm", "estrategia", "strategy"}
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
                docstring_nodes.add(id(body[0].value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_nodes:
            assert node.value.strip().lower() not in banned, node.value
