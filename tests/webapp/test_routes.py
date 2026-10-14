import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from webapp.main import app, templates
import webapp.main as web_main
from webapp import access
from webapp import copilot_runtime
from webapp.services import auth_service
from webapp.services.section_analysis import SectionAnalysisService
from webapp.services.content_service import load_module

# El modo diagnóstico solo lo habilita el dueño (APP_OWNER). En tests, el usuario
# de prueba ES el owner para poder ejercitar el grupo de gestión / selector. El
# seam vive ahora en webapp.access (access policy), no en webapp.main.
access.APP_OWNER = "test@danwor.com"
# Baseline de perfil: full para todos (el default de config es ACADEMY_USERS=ALL →
# solo lectura). Los tests de perfil read-only monkeypatchean estas listas.
auth_service.ALLOWED_USERS = "ALL"

_SESSION = auth_service.create_session(
    {"email": "test@danwor.com", "name": "Test User", "first_name": "Test", "family_name": "User"}
)

client = TestClient(app)
# Las páginas de módulo están protegidas por sesión: el cliente lleva una cookie válida.
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)

# Cliente en modo diagnóstico (habilita selector de marco + pestaña Analítica).
diag_client = TestClient(app)
diag_client.cookies.set(auth_service.COOKIE_NAME, _SESSION)
diag_client.cookies.set("dw-diagnostic", "on")

def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["architecture"] == "fastapi-ssr-htmx"

def test_favicon():
    # /health y /favicon.ico se sirven desde webapp/routers/system.py (Fase 1).
    # Regresión mínima: sirve el asset .ico (status + tipo de imagen + no vacío),
    # sin acoplar al mimetype exacto que infiere Starlette.
    response = client.get("/favicon.ico")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/")
    assert len(response.content) > 0

def test_telemetry_event_noop_when_not_verbose():
    # /telemetry/event se sirve desde webapp/routers/telemetry.py (Fase 2A).
    # LOG_ACTIVITY OFF (default del conftest) -> is_verbose False -> no-op 204.
    response = client.post("/telemetry/event", json={"event": "module_continue"})
    assert response.status_code == 204

def test_telemetry_event_logs_whitelisted_event_when_verbose(monkeypatch):
    # Con VERBOSE + sesión, un evento del whitelist registra vía log_verbose;
    # uno fuera del whitelist responde 204 pero NO registra (preserva el gate).
    from webapp.services import activity_service
    from webapp.routers import telemetry as telemetry_router
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE")
    calls = []
    monkeypatch.setattr(telemetry_router, "log_verbose", lambda event, **kw: calls.append(event))

    ok = client.post("/telemetry/event", json={"event": "module_continue", "stem": "01"})
    assert ok.status_code == 204
    assert calls == ["module_continue"]

    calls.clear()
    other = client.post("/telemetry/event", json={"event": "not_whitelisted"})
    assert other.status_code == 204
    assert calls == []

# ── Auth/session API (Fase 2B: webapp/routers/auth.py) ─────────────────────
# /api/diagnostic ya está cubierto más abajo. Estas protegen el contrato
# observable de las rutas sin cobertura HTTP previa (config/me/logout).

def test_auth_config_returns_azure_ids():
    response = client.get("/api/auth/microsoft/config")
    assert response.status_code == 200
    body = response.json()
    # `hc_document_write_scope` (Fase 4.1.1 -- Capital Humano Profile
    # Documents, SharePoint write corrective, §5): el scope Selected
    # ADICIONAL que la escritura de documentos de Capital Humano pide,
    # expuesto acá porque un scope string no es un secreto y `hub_auth.js`
    # (estático, sin backend propio) no puede leer un env var directamente.
    assert set(body) == {"enabled", "tenant_id", "client_id", "hc_document_write_scope"}

def test_auth_me_authenticated():
    response = client.get("/api/auth/me")
    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Test User" and body["first_name"] == "Test"

def test_auth_me_anonymous_401():
    response = TestClient(app).get("/api/auth/me")
    assert response.status_code == 401

def test_auth_logout_clears_session_cookie():
    # Cliente propio para no limpiar la cookie del `client` compartido del módulo.
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, _SESSION)
    response = c.post("/api/auth/logout")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert auth_service.COOKIE_NAME in response.headers.get("set-cookie", "")

def test_module_page_renders_server_side():
    response = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert response.status_code == 200
    assert "Arquitectura y Conectividad" in response.text
    assert "Topología de Espacios" in response.text
    assert "hx-post=" in response.text
    assert "1 árbol" in response.text
    assert "/sections/analyze" in response.text
    assert "Salida completa (operativa)" in response.text
    assert "¿Qué hace cada acción?" in response.text


def test_module_page_anonymous_redirects_to_login():
    # Gap real (Fase 2J-D): module_page nunca tuvo un test de redirect anónimo
    # propio (a diferencia de /whats-new y /academy/{stem}).
    c = TestClient(app)  # sin cookie de sesión
    r = c.get("/modules/01_Arquitectura_y_Conectividad", follow_redirects=False)
    # Comportamiento actual de nav.login_redirect: 307 a /?pack=…&next=…#acceso.
    assert r.status_code == 307
    location = r.headers["location"]
    assert location.startswith("/?")
    assert location.endswith("#acceso")
    assert "next=%2Fmodules%2F01_Arquitectura_y_Conectividad" in location
    assert "pack=datasphere_core" in location


def test_section_analysis_endpoint_returns_htmx_fragment(monkeypatch):
    # Seam vive ahora en webapp.routers.assistant (Fase 2J-B), no en web_main.
    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(
        assistant_router,
        "section_analysis_service",
        SectionAnalysisService(execution_mode="Simulación"),
    )
    document = load_module("01_Arquitectura_y_Conectividad")
    response = client.post(
        "/modules/01_Arquitectura_y_Conectividad/sections/analyze",
        data={
            "pack": "datasphere_core",
            "section_slug": document.sections[0].slug,
            "action": "Checklist ejecutable",
        },
    )

    assert response.status_code == 200
    assert "Simulación activa" in response.text
    assert "Checklist ejecutable" in response.text


def test_module_level_analysis_uses_module_target(monkeypatch):
    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(
        assistant_router,
        "section_analysis_service",
        SectionAnalysisService(execution_mode="Simulación"),
    )
    response = client.post(
        "/modules/01_Arquitectura_y_Conectividad/sections/analyze",
        data={
            "pack": "datasphere_core",
            "section_slug": "__module__",
            "action": "Guía operativa del módulo",
        },
    )

    assert response.status_code == 200
    assert "Simulación activa" in response.text
    assert "Guía operativa del módulo" in response.text


def test_module_page_offers_module_target_and_nav_toggles():
    response = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert response.status_code == 200
    # Target módulo (opción con value="__module__", card "Módulo activo") +
    # acciones módulo-nivel. (Los labels con acento viajan json-escapados en
    # x-data; "Errores frecuentes" es ASCII y aparece literal.)
    assert 'value="__module__"' in response.text
    assert "Módulo activo" in response.text
    assert "Errores frecuentes" in response.text
    # Hero como target del scroll-spy + nav izquierdo colapsable.
    assert 'id="__module__"' in response.text
    assert "navCore" in response.text
    assert "navAppendix" in response.text


def test_section_analysis_rejects_same_section_and_action_twice(monkeypatch):
    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(
        assistant_router,
        "section_analysis_service",
        SectionAnalysisService(execution_mode="Simulación"),
    )
    document = load_module("01_Arquitectura_y_Conectividad")
    conversation = copilot_runtime.assistant_store.start(
        document.summary.stem, "datasphere_core"
    )
    payload = {
        "pack": "datasphere_core",
        "conversation_id": conversation.id,
        "section_slug": document.sections[0].slug,
        "action": "Checklist ejecutable",
    }

    first = client.post(
        f"/modules/{document.summary.stem}/sections/analyze", data=payload,
    )
    duplicate = client.post(
        f"/modules/{document.summary.stem}/sections/analyze", data=payload,
    )

    assert first.status_code == 200
    assert "assistant-feedback" in first.text
    assert duplicate.status_code == 200
    assert "Ya analizaste esta acción" in duplicate.text
    assert "assistant-feedback" not in duplicate.text
    assert duplicate.headers["hx-retarget"] == "#section-analysis-warning"
    assert duplicate.headers["x-danwor-duplicate"] == "true"
    assert len(copilot_runtime.assistant_store.history(
        conversation.id, conversation.module, conversation.pack,
    )) == 2


def test_assistant_feedback_rejects_an_older_reply():
    conversation = copilot_runtime.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )
    first = copilot_runtime.assistant_store.append(
        conversation.id, conversation.module, conversation.pack,
        "Primera pregunta", "Primera respuesta", "<p>Primera respuesta</p>",
        topic="Pregunta libre", action="Asistente",
    )
    copilot_runtime.assistant_store.append(
        conversation.id, conversation.module, conversation.pack,
        "Pregunta siguiente", "Respuesta siguiente", "<p>Respuesta siguiente</p>",
        topic="Pregunta libre", action="Asistente",
    )

    response = client.post(
        f"/assistant/responses/{first.id}/feedback", data={"rating": "up"}
    )

    assert response.status_code == 409
    assert "respuesta más reciente" in response.text


def test_assistant_feedback_up_reaction_persists_and_thanks(monkeypatch, tmp_path):
    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(assistant_router.assistant_feedback_repository, "root", tmp_path)
    conversation = copilot_runtime.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )
    reply = copilot_runtime.assistant_store.append(
        conversation.id, conversation.module, conversation.pack,
        "una pregunta", "una respuesta", "<p>una respuesta</p>",
        topic="Pregunta libre", action="Asistente",
    )

    response = client.post(
        f"/assistant/responses/{reply.id}/feedback", data={"rating": "up"}
    )

    assert response.status_code == 200
    assert "assistant-feedback-thanks" in response.text
    assert "Gracias, registramos tu feedback." in response.text
    saved = list(tmp_path.glob("feedback_*.yaml"))
    assert len(saved) == 1
    import yaml
    payload = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert payload["type"] == "assistant_response"
    assert payload["rating"] == "up"
    assert payload["reply_id"] == reply.id
    assert payload["conversation_id"] == conversation.id


def test_assistant_feedback_down_opens_form_then_submit_persists_comment(monkeypatch, tmp_path):
    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(assistant_router.assistant_feedback_repository, "root", tmp_path)
    conversation = copilot_runtime.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )
    reply = copilot_runtime.assistant_store.append(
        conversation.id, conversation.module, conversation.pack,
        "otra pregunta", "otra respuesta", "<p>otra respuesta</p>",
        topic="Pregunta libre", action="Asistente",
    )

    # 👎 → despliega el formulario de comentario, todavía no persiste.
    down = client.post(
        f"/assistant/responses/{reply.id}/feedback", data={"rating": "down"}
    )
    assert down.status_code == 200
    assert "assistant-feedback-expanded" in down.text
    assert "¿Qué podríamos mejorar?" in down.text
    assert not list(tmp_path.glob("feedback_*.yaml"))

    # Enviar el 👎 con comentario → guarda con el texto.
    submit = client.post(
        f"/assistant/responses/{reply.id}/feedback/submit", data={"comment": "faltó un ejemplo"}
    )
    assert submit.status_code == 200
    assert "assistant-feedback-thanks" in submit.text
    saved = list(tmp_path.glob("feedback_*.yaml"))
    assert len(saved) == 1
    import yaml
    payload = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert payload["rating"] == "down"
    assert payload["comment"] == "faltó un ejemplo"
    assert payload["reply_id"] == reply.id


def test_assistant_feedback_submit_without_comment_reshows_form_with_error(monkeypatch, tmp_path):
    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(assistant_router.assistant_feedback_repository, "root", tmp_path)
    conversation = copilot_runtime.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )
    reply = copilot_runtime.assistant_store.append(
        conversation.id, conversation.module, conversation.pack,
        "pregunta sin comentario", "respuesta", "<p>respuesta</p>",
        topic="Pregunta libre", action="Asistente",
    )

    response = client.post(f"/assistant/responses/{reply.id}/feedback/submit", data={})

    assert response.status_code == 200
    assert "assistant-feedback-expanded" in response.text
    assert "feedback-error" in response.text
    assert not list(tmp_path.glob("feedback_*.yaml"))


def test_assistant_feedback_cancel_returns_to_reaction_bar_without_persisting(monkeypatch, tmp_path):
    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(assistant_router.assistant_feedback_repository, "root", tmp_path)
    conversation = copilot_runtime.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )
    reply = copilot_runtime.assistant_store.append(
        conversation.id, conversation.module, conversation.pack,
        "pregunta a cancelar", "respuesta", "<p>respuesta</p>",
        topic="Pregunta libre", action="Asistente",
    )

    response = client.post(f"/assistant/responses/{reply.id}/feedback/cancel")

    assert response.status_code == 200
    assert "¿Te resultó útil esta respuesta?" in response.text
    assert "assistant-feedback-expanded" not in response.text
    assert not list(tmp_path.glob("feedback_*.yaml"))


def test_assistant_chat_routes_to_curated_asset_without_calling_llm(monkeypatch):
    conversation = copilot_runtime.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )

    def unexpected_llm(*args, **kwargs):
        raise AssertionError("El LLM no debe ejecutarse cuando hay routing curado")

    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(assistant_router.assistant_chat_service, "ask", unexpected_llm)
    response = client.post(
        "/modules/01_Arquitectura_y_Conectividad/assistant/chat",
        data={
            "pack": "datasphere_core",
            "conversation_id": conversation.id,
            "question": "tenemos algun template CDS?",
        },
    )

    assert response.status_code == 200
    assert "Template CDS" in response.text
    assert "Abrir recurso" in response.text
    assert "Explicame vos" in response.text
    assert "assistant-feedback" in response.text
    assert "¿Te resultó útil esta respuesta?" in response.text
    assert "assistant-llm-disclaimer" not in response.text


def test_local_pack_resource_is_served_and_traversal_is_rejected():
    resource = client.get(
        "/pack-resources/datasphere_core/templates/cds_extractor_template.abap"
    )
    traversal = client.get(
        "/pack-resources/datasphere_core/templates/../config.py"
    )

    assert resource.status_code == 200
    assert traversal.status_code in {400, 404}


def test_pack_asset_traversal_is_rejected():
    traversal = client.get(
        "/pack-assets/datasphere_core/academy/../config.py"
    )

    assert traversal.status_code in {400, 404}


def test_explain_route_appends_only_the_assistant_answer(monkeypatch):
    conversation = copilot_runtime.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )
    copilot_runtime.assistant_store.append(
        conversation.id, conversation.module, conversation.pack,
        "tenemos algun template CDS?", "Encontré un recurso curado.",
        "<p>Encontré un recurso curado.</p>", topic="Routing", action="Routing",
    )
    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(
        assistant_router.assistant_chat_service,
        "ask",
        lambda *args, **kwargs: ("Explicación contextual", "<p>Explicación contextual</p>"),
    )

    response = client.post(
        "/modules/01_Arquitectura_y_Conectividad/assistant/chat",
        data={
            "pack": "datasphere_core",
            "conversation_id": conversation.id,
            "question": "tenemos algun template CDS?",
            "routing_bypass": "true",
            "route_module": "03_Integracion_y_Estrategia_de_Datos",
        },
    )

    assert response.status_code == 200
    assert "Explicación contextual" in response.text
    assert "assistant-message--user" not in response.text
    assert "assistant-llm-disclaimer" in response.text
    assert copilot_runtime.assistant_store.history(
        conversation.id, conversation.module, conversation.pack,
    ).count(("user", "tenemos algun template CDS?")) == 1


def test_no_local_evidence_answer_offers_explicame_vos_instead_of_dead_end(monkeypatch):
    # Forensic (Case A, TERMINAL_BRANCH_BUG): una consulta sin match local en
    # ningún módulo (route() -> None) llegaba a una respuesta grounded del LLM
    # sin NINGÚN affordance posterior -- ni siquiera se renderizaba el bloque
    # de acciones (gateado antes por `{% if route %}`, y route es None acá).
    conversation = copilot_runtime.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )
    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(
        assistant_router.assistant_chat_service,
        "ask",
        lambda *args, **kwargs: (
            "No está cubierto en el contexto de este módulo.",
            "<p>No está cubierto en el contexto de este módulo.</p>",
        ),
    )

    response = client.post(
        "/modules/01_Arquitectura_y_Conectividad/assistant/chat",
        data={
            "pack": "datasphere_core",
            "conversation_id": conversation.id,
            "question": "mejores practicas para variables MTD",
        },
    )

    assert response.status_code == 200
    assert "No está cubierto en el contexto de este módulo." in response.text
    assert "Explicame vos" in response.text


def test_explicame_vos_after_no_local_evidence_requests_general_mode(monkeypatch):
    # El "Explicame vos" del caso anterior debe pedir una respuesta GENERAL
    # (sin el scope restrictivo del módulo activo) -- a diferencia del
    # "Explicame vos" de un lead de catálogo, que sí reancla a un módulo cuyo
    # scope cubre el tema. Ver AssistantChatService.ask(general=True).
    conversation = copilot_runtime.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )
    captured = {}

    def _capture_ask(*args, **kwargs):
        captured["general"] = kwargs.get("general")
        captured["directive"] = kwargs.get("directive")
        return "Respuesta general del LLM.", "<p>Respuesta general del LLM.</p>"

    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(assistant_router.assistant_chat_service, "ask", _capture_ask)

    response = client.post(
        "/modules/01_Arquitectura_y_Conectividad/assistant/chat",
        data={
            "pack": "datasphere_core",
            "conversation_id": conversation.id,
            "question": "mejores practicas para variables MTD",
            "routing_bypass": "true",
            "route_module": "01_Arquitectura_y_Conectividad",
            "route_general": "true",
            "explain_prompt": "explicá en general",
        },
    )

    assert response.status_code == 200
    assert "Respuesta general del LLM." in response.text
    assert captured["general"] is True
    assert captured["directive"] == "explicá en general"


def test_no_local_evidence_answer_does_not_chain_a_second_explicame_vos(monkeypatch):
    # Al hacer clic en "Explicame vos" (routing_bypass=true), la respuesta
    # general resultante NO debe volver a ofrecer otro "Explicame vos" (evita
    # un loop de UX).
    conversation = copilot_runtime.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )
    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(
        assistant_router.assistant_chat_service,
        "ask",
        lambda *args, **kwargs: ("Respuesta general.", "<p>Respuesta general.</p>"),
    )

    response = client.post(
        "/modules/01_Arquitectura_y_Conectividad/assistant/chat",
        data={
            "pack": "datasphere_core",
            "conversation_id": conversation.id,
            "question": "mejores practicas para variables MTD",
            "routing_bypass": "true",
            "route_module": "01_Arquitectura_y_Conectividad",
            "route_general": "true",
            "explain_prompt": "explicá en general",
        },
    )

    assert response.status_code == 200
    assert "Explicame vos" not in response.text


def test_readonly_profile_does_not_offer_explicame_vos_without_local_evidence(monkeypatch):
    # El perfil solo-lectura ya bloquea la salida LLM sin ruta curada (ver
    # test_readonly_assistant_router_works_but_llm_output_is_blocked); debe
    # seguir sin ofrecer el nuevo affordance tampoco (no tiene sentido pedir
    # una respuesta general si el perfil no puede consumir IA).
    ro = _readonly_client(monkeypatch)
    from webapp.routers import assistant as assistant_router
    monkeypatch.setattr(
        assistant_router.assistant_chat_service, "ask",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe consumir OpenAI en solo-lectura")),
    )
    conv = copilot_runtime.assistant_store.start("01_Arquitectura_y_Conectividad", "datasphere_core")

    response = ro.post(
        "/modules/01_Arquitectura_y_Conectividad/assistant/chat",
        data={
            "pack": "datasphere_core",
            "conversation_id": conv.id,
            "question": "mejores practicas para variables MTD",
        },
    )

    assert response.status_code == 200
    assert "IA no disponible para Danwor Academy" in response.text
    assert "Explicame vos" not in response.text


def test_pack_selector_only_in_diagnostic_mode():
    # Fuera de diagnóstico no hay selector y el marco se fuerza al productivo.
    standard = client.get("/modules/01_Arquitectura_y_Conectividad?pack=bdc_next")
    assert "pack-select" not in standard.text

    # En diagnóstico aparece el selector y se puede ver el contenido BDC.
    response = diag_client.get("/modules/01_Estrategia_y_Readiness_BDC?pack=bdc_next")
    assert response.status_code == 200
    assert "Estrategia y Readiness BDC" in response.text
    assert "pack-select" in response.text
    assert "selected>BDC</option>" in response.text


def test_diagnostic_mode_adds_gestion_group():
    standard = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert "Herramientas de gestión" not in standard.text
    diag = diag_client.get("/modules/01_Arquitectura_y_Conectividad")
    assert "Herramientas de gestión" in diag.text
    assert "gestionOpen" in diag.text


def test_module_page_has_theme_navigation_and_resize_controls():
    response = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert "theme-toggle" in response.text
    assert "module-pager" in response.text
    assert response.text.count("data-resize=") == 2
    assert "/brand/dancing/datasphere_core" in response.text
    assert "tools.css?v=" in response.text
    assert "app.js?v=" in response.text


def test_static_assets_include_reversible_content_reveal():
    script = client.get("/static/app.js")
    styles = client.get("/static/content.css")

    assert script.status_code == 200
    assert styles.status_code == 200
    assert "function initContentReveals" in script.text
    assert 'classList.toggle("is-visible", entry.isIntersecting)' in script.text
    assert "root: container" in script.text
    assert ".content-reveal.is-visible" in styles.text
    assert "prefers-reduced-motion: reduce" in styles.text


def test_inherited_pack_asset_is_served():
    response = client.get("/pack-assets/bdc_next/danwor-punto-de-partida.html")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_nested_pack_html_asset_is_served_inline():
    response = client.get("/pack-assets/datasphere_core/apendices/danwor-arquitectura-referencia-aws.html")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_html_embed_props_are_injected_for_legacy_visuals():
    response = client.get(
        "/pack-assets/datasphere_core/danwor-modelo-integracion-dataproduct.html?default_mode=modelo"
    )
    assert response.status_code == 200
    assert "window.DANWOR_EMBED_PROPS" in response.text
    assert '"defaultMode": "modelo"' in response.text


def test_dancing_logo_uses_dark_shell_palette():
    response = client.get("/brand/dancing/datasphere_core")
    assert response.status_code == 200
    assert "#e4e4ec" in response.text
    assert "#8b8b96" in response.text
    assert "#363636" not in response.text


def test_case_library_path_is_only_visible_in_diagnostic_mode():
    template = templates.env.get_template("partials/tree_feedback.html")
    run = SimpleNamespace(feedback_rating="up", case_id="case_example")

    regular = template.render(run=run, diagnostic_mode=False)
    diagnostic = template.render(run=run, diagnostic_mode=True)

    assert "case_library/" not in regular
    assert "case_library/case_example.yaml" in diagnostic


def test_module_03_renders_tabs_and_tree_selector_without_scope():
    response = client.get("/modules/03_Integracion_y_Estrategia_de_Datos")
    document = load_module("03_Integracion_y_Estrategia_de_Datos")
    assert response.status_code == 200
    assert 'role="tab"' in response.text
    # Dos opciones con id "m03_": el árbol de decisión (m03_data_integration_strategy)
    # y el de diagnóstico (selector separado de "Herramientas de diagnóstico").
    assert response.text.count('<option value="m03_') == 2
    assert 'value="extraction_options"' in response.text
    assert 'value="resolucion_contrato"' in response.text
    assert "3 árboles" in response.text
    assert document.scope not in response.text
    assert "{{" not in response.text
    assert 'onchange="if(this.value) this.form.requestSubmit()"' in response.text
    assert ">Abrir</button>" not in response.text


def test_selected_tree_starts_via_htmx_fragment():
    response = client.post(
        "/modules/03_Integracion_y_Estrategia_de_Datos/trees/start",
        data={"tree_id": "extraction_options", "pack": "datasphere_core"},
    )
    assert response.status_code == 200
    assert "Opciones de Extracción" in response.text
    assert "rbol YAML" in response.text  # eyebrow "Árbol YAML" (Á como entidad &Aacute;)
    assert 'id="wizard-shell"' in response.text
    assert 'hx-trigger="change"' in response.text
    assert "Continuar" not in response.text


def test_wizard_answer_back_restart_lifecycle():
    # Interactúa con el store compartido real (webapp.copilot_runtime.store) —
    # sin monkeypatch de identidad, run_id fresco por corrida (uuid4 en .start()).
    from webapp import copilot_runtime
    tree = copilot_runtime.store.tree_by_id("extraction_options", "datasphere_core")
    run = copilot_runtime.store.start(tree, "datasphere_core")

    answered = client.post(f"/wizards/{run.id}/answer", data={"answer": "ERP"})
    assert answered.status_code == 200
    assert "¿Qué versión?" in answered.text  # avanzó a q2_erp_version

    back = client.post(f"/wizards/{run.id}/back")
    assert back.status_code == 200
    assert "¿Cuál es el sistema fuente?" in back.text  # volvió a q1

    # Reavanzar antes de restart para probar que restart limpia el historial.
    client.post(f"/wizards/{run.id}/answer", data={"answer": "ERP"})
    restart = client.post(f"/wizards/{run.id}/restart")
    assert restart.status_code == 200
    assert "¿Cuál es el sistema fuente?" in restart.text
    assert copilot_runtime.store.get(run.id).history == []


def test_diagnostics_orchestrator_starts_framework_assessment():
    response = client.post(
        "/modules/01_Arquitectura_y_Conectividad/diagnostics/orchestrator",
        data={"pack": "datasphere_core"},
    )
    assert response.status_code == 200
    assert 'id="wizard-shell"' in response.text
    assert "Diagn" in response.text  # eyebrow "Diagnóstico YAML" (run.is_assessment)


def test_wizard_feedback_up_persists_case_and_down_then_submit_persists_lesson(monkeypatch, tmp_path):
    from webapp import copilot_runtime
    monkeypatch.setattr(copilot_runtime.store._case_repository, "root", tmp_path)
    tree = copilot_runtime.store.tree_by_id("extraction_options", "datasphere_core")

    def _drive_to_leaf():
        run = copilot_runtime.store.start(tree, "datasphere_core")
        copilot_runtime.store.answer(run.id, "ERP")
        copilot_runtime.store.answer(run.id, "ECC")
        run = copilot_runtime.store.answer(run.id, "No sé")
        assert run.leaf is not None
        return run

    import yaml

    # 👍 → persiste directo.
    run_up = _drive_to_leaf()
    up = client.post(f"/wizards/{run_up.id}/feedback", data={"rating": "up"})
    assert up.status_code == 200
    saved_up = list(tmp_path.glob(f"case_*_{run_up.id[:8]}.yaml"))
    assert len(saved_up) == 1
    payload_up = yaml.safe_load(saved_up[0].read_text(encoding="utf-8"))
    assert payload_up["rating"] == "up"

    # 👎 → despliega el formulario (todavía no persiste el rating). El caso ya
    # existe (create_for_run se dispara al llegar a la hoja, dentro de .answer()).
    run_down = _drive_to_leaf()
    down = client.post(f"/wizards/{run_down.id}/feedback", data={"rating": "down"})
    assert down.status_code == 200
    case_before = yaml.safe_load(
        next(tmp_path.glob(f"case_*_{run_down.id[:8]}.yaml")).read_text(encoding="utf-8")
    )
    assert case_before.get("rating") is None

    # Enviar el 👎 con lección → persiste con los campos del formulario.
    submit = client.post(
        f"/wizards/{run_down.id}/feedback/submit",
        data={"rating": "down", "lesson": "faltó documentación del sistema fuente"},
    )
    assert submit.status_code == 200
    saved_down = list(tmp_path.glob(f"case_*_{run_down.id[:8]}.yaml"))
    assert len(saved_down) == 1
    payload_down = yaml.safe_load(saved_down[0].read_text(encoding="utf-8"))
    assert payload_down["rating"] == "down"
    assert payload_down["lessons"] == ["faltó documentación del sistema fuente"]


def test_wizard_feedback_submit_without_lesson_reshows_form_with_error(monkeypatch, tmp_path):
    # Monkeypatch root aunque el ValueError se dispare antes de escribir: llegar
    # a la hoja ya dispara create_for_run() dentro de .answer(), que sí escribe.
    from webapp import copilot_runtime
    monkeypatch.setattr(copilot_runtime.store._case_repository, "root", tmp_path)
    tree = copilot_runtime.store.tree_by_id("extraction_options", "datasphere_core")
    run = copilot_runtime.store.start(tree, "datasphere_core")
    copilot_runtime.store.answer(run.id, "ERP")
    copilot_runtime.store.answer(run.id, "ECC")
    copilot_runtime.store.answer(run.id, "No sé")

    response = client.post(f"/wizards/{run.id}/feedback/submit", data={"rating": "down"})

    assert response.status_code == 200
    assert "lección clave" in response.text or "obligatoria" in response.text


def test_wizard_feedback_cancel_shows_thanks_without_recording_rating(monkeypatch, tmp_path):
    # Llegar a la hoja ya dispara create_for_run() (dentro de .answer()) — el
    # caso existe de antemano; lo que cancel NO debe hacer es setear rating.
    from webapp import copilot_runtime
    monkeypatch.setattr(copilot_runtime.store._case_repository, "root", tmp_path)
    tree = copilot_runtime.store.tree_by_id("extraction_options", "datasphere_core")
    run = copilot_runtime.store.start(tree, "datasphere_core")
    copilot_runtime.store.answer(run.id, "ERP")
    copilot_runtime.store.answer(run.id, "ECC")
    copilot_runtime.store.answer(run.id, "No sé")

    response = client.post(f"/wizards/{run.id}/feedback/cancel")

    assert response.status_code == 200
    import yaml
    case = yaml.safe_load(
        next(tmp_path.glob(f"case_*_{run.id[:8]}.yaml")).read_text(encoding="utf-8")
    )
    assert case.get("rating") is None


def test_trees_suggest_persists_and_confirms(monkeypatch, tmp_path):
    from webapp.routers import wizard as wizard_router
    monkeypatch.setattr(wizard_router, "FEEDBACK_LOG_DIR", tmp_path)

    response = client.post(
        "/trees/suggest",
        data={
            "title": "¿Falta un árbol de X?", "module": "03_Integracion_y_Estrategia_de_Datos",
            "pack": "datasphere_core", "kind": "tree",
        },
    )

    assert response.status_code == 200
    assert "Registramos tu sugerencia" in response.text  # partials/tree_suggest.html, suggested=True
    saved = list(tmp_path.glob("tree_suggestion_*.yaml"))
    assert len(saved) == 1
    import yaml
    payload = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert payload["type"] == "tree_suggestion"
    assert payload["title"] == "¿Falta un árbol de X?"
    assert payload["module"] == "03_Integracion_y_Estrategia_de_Datos"


def test_academy_page_embeds_asset_full_height_without_panel():
    response = client.get("/academy/ACAD01_Paradigmas")
    assert response.status_code == 200
    # Iframe inmersivo al HTML de storytelling, con branding danwor + embed=1.
    assert 'class="content-embed academy-embed"' in response.text
    assert "/pack-assets/datasphere_core/academy/paradigmas.html" in response.text
    assert "branding=danwor" in response.text
    assert "embed=1" in response.text
    # Sin panel de IA (se ve como el módulo 00): shell no-copilot, sin panel derecho.
    assert "copilot-panel" not in response.text
    assert "no-copilot" in response.text
    # El grupo Academy y sus 4 entradas están en el nav.
    assert "Danwor Academy" in response.text
    assert '/academy/ACAD02_Datasphere' in response.text
    assert '/academy/ACAD04_SAC' in response.text


def test_academy_group_present_on_module_pages_but_out_of_pager():
    response = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert response.status_code == 200
    # El grupo Academy aparece en el nav (links a /academy/…, ruta distinta de /modules/).
    assert "Danwor Academy" in response.text
    assert 'href="/academy/ACAD01_Paradigmas' in response.text
    # Desconexión: el pager superior nunca apunta a Academy.
    pager = response.text.split('class="module-pager"', 1)[1].split("</div>", 1)[0]
    assert "/academy/" not in pager


def test_academy_page_has_standalone_button_pointing_to_non_embedded_asset():
    response = client.get("/academy/ACAD01_Paradigmas")
    assert response.status_code == 200
    assert 'class="academy-standalone-btn"' in response.text
    button = response.text.split('class="academy-standalone-btn"', 1)[1].split("</a>", 1)[0]
    assert 'target="_blank"' in button
    assert "/pack-assets/datasphere_core/academy/" in button
    assert "branding=danwor" in button
    # Standalone = SIN embed=1 (así conserva su propio topbar); solo el iframe
    # embebido lo lleva.
    assert "embed=1" not in button
    # El theme viaja vivo por query, atado al contentTheme de Alpine (no hardcodeado).
    assert "theme=' + contentTheme" in button
    # El asset objetivo es DINÁMICO (academyAsset, Alpine — actualizado por el
    # postMessage "danwor-academy-nav" cuando se navega dentro del iframe a otro
    # asset, p. ej. paradigmas→datasphere), con fallback al asset de aterrizaje:
    # nunca queda fijo en el de la carga inicial de la página.
    assert "(academyAsset || 'paradigmas.html')" in button


def test_academy_page_has_embedded_back_button_hidden_on_landing_asset():
    # Sin "Volver" no hay forma de salir de un asset al que se navegó DENTRO del
    # iframe (paradigmas→datasphere): el historial que se mueve es el del iframe,
    # no el del copiloto. Se muestra SOLO cuando academyAsset (el asset que el
    # iframe está mostrando AHORA, vía postMessage) difiere del de aterrizaje de
    # esta página — nunca en el landing (ni bien entrás, ni si volviste a él).
    response = client.get("/academy/ACAD01_Paradigmas")
    assert response.status_code == 200
    assert 'class="academy-back-btn"' in response.text
    button = response.text.split('class="academy-back-btn"', 1)[1].split("</a>", 1)[0]
    assert "x-show=\"academyAsset && academyAsset !== 'paradigmas.html'\"" in button
    assert '@click.prevent="academyGoBack()"' in button


def test_academy_standalone_button_absent_on_module_pages():
    # El botón es exclusivo de Danwor Academy: no debe aparecer en módulos
    # (ni siquiera los inmersivos, que reusan el mismo .academy-stage/iframe).
    response = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert response.status_code == 200
    assert "academy-standalone-btn" not in response.text
    assert "academy-back-btn" not in response.text


def test_pack_asset_and_pack_resource_require_session():
    # Joe: si abrís el contenido standalone de Academy, copiás el link, volvés al
    # copiloto y te desloguéas, ese link seguía sirviendo contenido sin sesión
    # (pack_asset/pack_resource no chequeaban auth — a diferencia de /academy/{stem}
    # y el resto de las rutas protegidas). Ahora deben redirigir a /?...#acceso,
    # preservando el destino en `next` (mismo mecanismo que las demás rutas).
    anon = TestClient(app)
    asset = anon.get(
        "/pack-assets/datasphere_core/academy/paradigmas.html?branding=danwor", follow_redirects=False
    )
    assert asset.status_code == 307
    assert "acceso" in asset.headers["location"]
    assert "next=" in asset.headers["location"]
    resource = anon.get(
        "/pack-resources/datasphere_core/assets/danwor-animated-dancing.html", follow_redirects=False
    )
    assert resource.status_code == 307
    assert "acceso" in resource.headers["location"]
    # Con sesión, sigue sirviendo el contenido real (sin regresión).
    authed = client.get("/pack-assets/datasphere_core/academy/paradigmas.html?branding=danwor")
    assert authed.status_code == 200
    assert "window.DANWOR_EMBED_PROPS" in authed.text


def test_academy_asset_standalone_route_respects_theme_query_with_cookie_fallback():
    # Sin cookie ni query: fallback a "light" (default de pack_asset).
    default = client.get("/pack-assets/datasphere_core/academy/paradigmas.html?branding=danwor")
    assert default.status_code == 200
    assert '"theme": "light"' in default.text
    # Query param explícito manda por sobre la cookie.
    dark = client.get(
        "/pack-assets/datasphere_core/academy/paradigmas.html?branding=danwor&theme=dark",
        cookies={"dw-content-theme": "light"},
    )
    assert dark.status_code == 200
    assert '"theme": "dark"' in dark.text
    # Sin query, cae a la cookie.
    from_cookie = client.get(
        "/pack-assets/datasphere_core/academy/paradigmas.html?branding=danwor",
        cookies={"dw-content-theme": "dark"},
    )
    assert '"theme": "dark"' in from_cookie.text
    # Standalone (sin embed=1 en la query) conserva su propio topbar: el prop
    # "embed" no viaja, así que el script nunca setea el atributo data-dw-embed.
    assert '"embed"' not in default.text


def test_academy_asset_injects_embed_flag_and_theme():
    response = client.get("/pack-assets/datasphere_core/academy/paradigmas.html?branding=danwor&embed=1")
    assert response.status_code == 200
    assert "window.DANWOR_EMBED_PROPS" in response.text
    assert "data-dw-embed" in response.text          # el script marca el modo embebido
    assert "data-dw-theme" in response.text          # theme del copiloto inyectado


def test_academy_asset_dark_theme_keeps_opaque_background_when_standalone():
    # Bug real: el CSS "en dark, fondo transparente" (pensado para que se vea el
    # canvas oscuro del copiloto DETRÁS del iframe embebido) estaba sin scopear a
    # [data-dw-embed] → en standalone (sin canvas detrás, pestaña propia) el body
    # quedaba transparente y el navegador mostraba blanco pese a theme=dark.
    standalone = client.get(
        "/pack-assets/datasphere_core/academy/paradigmas.html?branding=danwor&theme=dark"
    )
    assert standalone.status_code == 200
    assert '"theme": "dark"' in standalone.text
    assert 'html[data-dw-embed][data-dw-theme="dark"]' in standalone.text
    # La regla vieja (sin scopear a [data-dw-embed]) ya no debe existir tal cual.
    assert 'html[data-dw-theme="dark"],html[data-dw-theme="dark"] body{background:transparent' not in standalone.text
    # Embebido sigue igual que siempre: transparente en dark (se ve el canvas del copiloto).
    embedded = client.get(
        "/pack-assets/datasphere_core/academy/paradigmas.html?branding=danwor&embed=1&theme=dark"
    )
    assert 'html[data-dw-embed][data-dw-theme="dark"]' in embedded.text
    assert "background:transparent!important" in embedded.text


def test_academy_shared_stylesheet_is_served():
    response = client.get("/pack-assets/datasphere_core/academy/dw-academy.css")
    assert response.status_code == 200
    assert 'branding-danwor' in response.text


def test_unknown_academy_page_returns_404():
    response = client.get("/academy/ACAD99_NoExiste")
    assert response.status_code == 404


# ── Invitación al Desafío (dialog one-off vía /desafio) ───────────────────

def test_desafio_sets_invite_cookie_and_redirects_home():
    try:
        response = client.get("/desafio", follow_redirects=False)
        assert response.status_code == 307
        assert "/home" in response.headers["location"]
        assert "dw-desafio=1" in response.headers.get("set-cookie", "")
    finally:
        # El TestClient es compartido a nivel módulo: sin esta limpieza la cookie
        # de invitación contamina los tests siguientes (dialog siempre visible).
        client.cookies.delete("dw-desafio")


def test_desafio_cerrar_deletes_cookie_and_redirects_to_wrapper():
    # El cierre pasa por el server: navegación top-level (first-party) que borra
    # la cookie de invitación de forma confiable y recién entonces vuelve al
    # wrapper limpio de danwor.com.
    # Seam vive ahora en webapp.routers.landing (Fase 2G), no en web_main.
    from webapp.routers import landing as landing_router
    client.cookies.set("dw-desafio", "1")
    try:
        response = client.get("/desafio/cerrar", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == landing_router.DESAFIO_CLOSE_URL
        set_cookie = response.headers.get("set-cookie", "")
        assert "dw-desafio=" in set_cookie and ('Max-Age=0' in set_cookie or "expires" in set_cookie.lower())
    finally:
        client.cookies.delete("dw-desafio")


def test_root_forwards_logged_users_into_the_app():
    # El wrapper /copiloto/ embebe la raíz: con sesión vigente no debe mostrar el
    # landing con la sección de acceso, sino seguir de largo al copiloto.
    logged = client.get("/", follow_redirects=False)
    assert logged.status_code == 307
    # Un solo salto al destino final: el MISMO que decide /home (antes la raíz reenviaba a /home y este volvía a redirigir).
    assert logged.headers["location"] == client.get("/home?pack=datasphere_core", follow_redirects=False).headers["location"]
    assert "/modules/" in logged.headers["location"]
    # Sin sesión, el landing se sirve normal.
    anonymous = TestClient(app).get("/", follow_redirects=False)
    assert anonymous.status_code == 200
    assert "acceso" in anonymous.text.lower()


def test_root_authenticated_follows_safe_next_param():
    # Con sesión vigente y un `next` propio (p. ej. viniendo de un link directo
    # sin sesión), la raíz sigue de largo a ese destino en vez del home por defecto.
    response = client.get("/?next=/academy/ACAD01_Paradigmas", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/academy/ACAD01_Paradigmas"


def test_root_rejects_open_redirect_next_param():
    # `next` que no es un path propio (protocol-relative a un host externo) se
    # descarta: cae al home por defecto en vez de seguir a `next`.
    response = client.get("/?next=//evil.com", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == client.get("/home?pack=datasphere_core", follow_redirects=False).headers["location"]
    assert "evil.com" not in response.headers["location"]


def test_login_redirects_to_landing_access_section():
    response = client.get("/login?pack=datasphere_core", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/?pack=datasphere_core#acceso"


def test_landing_versions_its_auth_and_brand_scripts():
    # Mismo mecanismo `?v={{ static_version }}` que ya usa base.html — sin
    # esto, un browser con auth.js cacheado puede seguir ejecutando lógica
    # vieja del popup de login tras un deploy (bug real observado en el
    # smoke manual de One Danwor).
    anon = TestClient(app)
    response = anon.get("/")
    assert response.status_code == 200
    assert "/static/auth.js?v=" in response.text
    assert "/static/brand-hero.js?v=" in response.text


# ── /copilot: alias explícito de / (batch de transición multi-workspace) ──

def test_copilot_alias_renders_the_same_experience_as_root():
    root = TestClient(app).get("/")
    copilot = TestClient(app).get("/copilot")
    assert copilot.status_code == root.status_code == 200
    assert copilot.text == root.text


def test_copilot_alias_forwards_logged_users_into_the_app():
    logged = client.get("/copilot", follow_redirects=False)
    assert logged.status_code == 307
    assert logged.headers["location"] == client.get("/home?pack=datasphere_core", follow_redirects=False).headers["location"]


def test_copilot_alias_preserves_next_like_root():
    response = client.get("/copilot?next=/academy/ACAD01_Paradigmas", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/academy/ACAD01_Paradigmas"


# ── Auth-popup/callback: documento neutro separado (corrective pass) ──────
# (el popup de auth.js abre la landing con `?auth_popup=1`, y Microsoft
# vuelve con `?code=...` — un overlay NO alcanza, porque el marcado de la
# landing con marca seguía existiendo debajo; ambos casos deben servir
# `auth_callback.html`, que nunca incluye el copy RICO de ninguna landing
# específica. Corrective posterior (Danwor Hub Login Identity Alignment,
# ver HANDOFF): ese documento neutro SÍ dice "Danwor Hub" en su <title> —
# el paraguas de plataforma que cubre este paso intermedio sin importar
# si el login arrancó en Copiloto u Operaciones, nunca un producto
# específico ni el histórico "One Danwor". La razón por la que el batch
# anterior no detectó este hueco: los tests de este bloque solo
# afirmaban la AUSENCIA de copy rico (Copiloto), nunca la PRESENCIA de
# una identidad Hub — la doc neutra pasaba esos tests con o sin "Danwor
# Hub" en el título).

def test_normal_root_still_contains_copilot_identity():
    anon = TestClient(app)
    response = anon.get("/")
    assert response.status_code == 200
    assert "Copiloto Metodológico" in response.text
    assert "Arquitecto Danwor" in response.text


def test_auth_popup_state_serves_neutral_document_not_copilot_landing():
    anon = TestClient(app)
    response = anon.get("/?auth_popup=1")
    assert response.status_code == 200
    assert "Copiloto Metodológico" not in response.text
    assert "Arquitecto Danwor" not in response.text
    assert "Danwor Hub" in response.text
    assert "One Danwor" not in response.text
    assert 'class="auth-completing"' in response.text
    assert "Completando acceso" in response.text
    assert "/static/auth.js?v=" in response.text


def test_code_callback_state_serves_neutral_document_not_copilot_landing():
    anon = TestClient(app)
    response = anon.get("/?code=fake-oauth-code&state=fake-state")
    assert response.status_code == 200
    assert "Copiloto Metodológico" not in response.text
    assert "Arquitecto Danwor" not in response.text
    assert "Danwor Hub" in response.text
    assert "One Danwor" not in response.text
    assert 'class="auth-completing"' in response.text
    assert "Completando acceso" in response.text


def test_copilot_alias_auth_popup_also_serves_neutral_document():
    anon = TestClient(app)
    response = anon.get("/copilot?auth_popup=1")
    assert response.status_code == 200
    assert "Copiloto Metodológico" not in response.text
    assert "Arquitecto Danwor" not in response.text
    assert "Danwor Hub" in response.text
    assert 'class="auth-completing"' in response.text


def test_copilot_alias_normal_still_unchanged():
    anon = TestClient(app)
    response = anon.get("/copilot")
    assert response.status_code == 200
    assert "Copiloto Metodológico" in response.text


def test_auth_callback_preserves_home_url_for_no_opener_fallback():
    # auth.js lee `data-home` de este botón oculto en el raro caso sin
    # `window.opener` (fallback en handleRedirect) — sin esto, ese camino
    # perdería el destino real (next/pack) y caería siempre a "/".
    anon = TestClient(app)
    response = anon.get("/?auth_popup=1&next=/academy/ACAD01_Paradigmas")
    assert response.status_code == 200
    assert 'data-home="/academy/ACAD01_Paradigmas"' in response.text


def test_root_still_serves_module_flow_untouched():
    # Guard de no-regresión: /modules/{stem}?pack=... sigue funcionando
    # exactamente igual tras el batch de transición multi-workspace (pack
    # nunca se tocó).
    response = client.get("/modules/01_Arquitectura_y_Conectividad?pack=datasphere_core")
    assert response.status_code == 200
    assert "Arquitectura y Conectividad" in response.text


# ── Modo diagnóstico y actividad ──────────────────────────────────────────

def test_diagnostic_toggle_sets_cookie_server_side():
    # La cookie la emite el server (con los atributos de la de sesión): desde el
    # JS del iframe enmascarado una cookie Lax no viaja y el modo se apagaba solo.
    try:
        response = client.post("/api/diagnostic", data={"on": "on"})
        assert response.status_code == 200
        assert "dw-diagnostic=on" in response.headers.get("set-cookie", "")
        off = client.post("/api/diagnostic", data={"on": "off"})
        assert "dw-diagnostic=off" in off.headers.get("set-cookie", "")
        anonymous = TestClient(app).post("/api/diagnostic", data={"on": "on"})
        assert anonymous.status_code == 401
    finally:
        client.cookies.delete("dw-diagnostic")


def test_activity_log_records_module_and_academy_views(monkeypatch, tmp_path):
    from webapp.services import activity_service
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "ON")
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    client.get("/modules/01_Arquitectura_y_Conectividad")
    client.get("/academy/ACAD01_Paradigmas")
    lines = []
    for path in tmp_path.glob("activity_*.jsonl"):
        lines += [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    events = {entry["event"] for entry in lines}
    # `module_viewed` (corrective A2 — Transversal Module + Event Semantics):
    # reemplaza al histórico `module_selected` como nombre de evento persistido.
    assert "module_viewed" in events and "academy_selected" in events
    assert all(entry["user"] == "test@danwor.com" for entry in lines)
    # test 14 (MODULE IDENTIFIER CONVENTION V2 — Fase 2): el productor NUEVO
    # de Copiloto persiste el module_id CANÓNICO (CP01), no el stem legacy.
    module_viewed = next(e for e in lines if e["event"] == "module_viewed")
    assert module_viewed["module"] == "CP01"


def test_activity_log_disabled_by_default(monkeypatch, tmp_path):
    from webapp.services import activity_service
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    # Forzar OFF explícitamente: no depender del LOG_ACTIVITY del entorno (en
    # dev/prod suele estar en VERBOSE), si no el test es frágil y falla ahí.
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "OFF")
    client.get("/modules/01_Arquitectura_y_Conectividad")
    assert not list(tmp_path.glob("activity_*.jsonl"))


def test_desafio_is_reinvocable():
    # El link del mail debe volver a invitar SIEMPRE, incluso si quedó una cookie
    # vieja de un cierre anterior (el "no reaparece" lo resuelve el cierre del
    # dialog navegando el top a la URL limpia del wrapper, no esta ruta).
    client.cookies.set("dw-desafio", "0")
    try:
        response = client.get("/desafio", follow_redirects=False)
        assert response.status_code == 307
        assert "dw-desafio=1" in response.headers.get("set-cookie", "")
    finally:
        client.cookies.delete("dw-desafio")


def test_invite_dialog_renders_only_with_cookie():
    # Sin cookie: el shell no arma el dialog.
    clean = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert "dw-invite-overlay" not in clean.text
    # Con cookie (la arma /desafio): dialog con el iframe del Desafío, en módulos
    # y también en Academy (donde aterriza el perfil solo-lectura).
    client.cookies.set("dw-desafio", "1")
    try:
        invited = client.get("/modules/01_Arquitectura_y_Conectividad")
        assert "dw-invite-overlay" in invited.text
        assert "/pack-assets/datasphere_core/academy/desafio.html" in invited.text
        assert "academy_feedback=ACAD05_Desafio" in invited.text
        # La cruz es un link target=_top a /desafio/cerrar: enmascarada en el
        # frame de danwor.com, navega la ventana completa al endpoint que borra
        # la cookie en first-party y redirige a la URL limpia del wrapper.
        assert 'target="_top"' in invited.text
        assert 'href="/desafio/cerrar"' in invited.text
        academy = client.get("/academy/ACAD01_Paradigmas")
        assert "dw-invite-overlay" in academy.text
    finally:
        client.cookies.delete("dw-desafio")


def test_academy_feedback_reuses_module_feedback_classes_and_stylesheet():
    # Joe: los cierres de módulo y de Academy se veían distintos (tipografía,
    # tamaños, padding) pese a decir lo mismo — doble mantenimiento. Fix real
    # (no un mirror manual): el fragmento de Academy reusa las MISMAS clases
    # module-feedback__* que partials/module_feedback.html, estilizadas por la
    # ÚNICA fuente feedback.css (mismo patrón ya probado en
    # catalog_guide_slides.html para el how-to). Nada de dw-af-* debe quedar.
    response = client.get("/pack-assets/datasphere_core/academy/paradigmas.html?academy_feedback=ACAD01_Paradigmas")
    assert response.status_code == 200
    assert '<link rel="stylesheet" href="/static/feedback.css" />' in response.text
    assert 'class="module-feedback"' in response.text
    assert 'class="module-feedback__inner"' in response.text
    assert 'module-feedback__reactions' in response.text
    assert 'module-feedback__react' in response.text
    assert "dw-af-" not in response.text
    assert "dw-academy-feedback" not in response.text
    # Shim de variables (feedback.css usa --card/--soft; dw-academy.css no los
    # define con ese nombre) para que resuelva igual que en el copiloto.
    assert "--card: var(--bg-card)" in response.text
    assert "--soft: var(--ink-soft)" in response.text
    # feedback.css pone border-top:0 a propósito (en módulo esa línea la aporta
    # el border-bottom de la última sección de contenido); el último .stop/.topic
    # de Academy no tiene ese border (":last-of-type"), así que acá se repone
    # localmente para no perder el separador antes del cierre.
    assert "#academy-feedback { border-top: 1px solid var(--line); }" in response.text


def test_desafio_hidden_from_nav_but_direct_url_and_feedback_work(monkeypatch, tmp_path):
    # nav_hidden: la página no se lista en el nav de Academy…
    response = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert "ACAD05_Desafio" not in response.text
    # …pero la URL directa sigue viva (fallback del mail) y el feedback también.
    direct = client.get("/academy/ACAD05_Desafio")
    assert direct.status_code == 200
    assert "desafio.html" in direct.text
    # El save de Academy vive ahora en el router (Fase 2E); su seam es
    # academy_router.FEEDBACK_LOG_DIR (no web_main).
    from webapp.routers import academy as academy_router
    monkeypatch.setattr(academy_router, "FEEDBACK_LOG_DIR", tmp_path)  # no ensuciar runtime real
    thanks = client.post("/academy/ACAD05_Desafio/feedback", data={"rating": "up", "pack": "datasphere_core"})
    assert thanks.status_code == 200
    assert list(tmp_path.glob("academy_feedback_*.yaml"))


def test_module_renders_per_section_feedback_widgets():
    response = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert response.status_code == 200
    # Cada sección cierra con su micro-botonera (id propio para el swap HTMX).
    assert "section-feedback-espacios-transportes" in response.text
    assert 'class="section-feedback__bar"' in response.text
    assert "te pareci" in response.text  # prompt (entidades HTML: te pareci&oacute;)
    # Y el feedback general de módulo sigue existiendo (son momentos distintos).
    assert 'class="module-feedback' in response.text


def test_section_feedback_up_saves_and_down_opens_optional_comment(monkeypatch, tmp_path):
    # Seam vive ahora en webapp.routers.feedback (Fase 2H), no en web_main.
    from webapp.routers import feedback as feedback_router
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    base = "/modules/01_Arquitectura_y_Conectividad/sections/espacios-transportes/feedback"

    # 👍 → guarda al toque y agradece.
    up = client.post(base, data={"rating": "up", "pack": "datasphere_core"})
    assert up.status_code == 200 and "Gracias" in up.text
    saved = list(tmp_path.glob("section_feedback_*.yaml"))
    assert len(saved) == 1
    import yaml
    payload = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert payload["type"] == "section_content"
    assert payload["section_slug"] == "espacios-transportes"
    assert payload["rating"] == "up"

    # 👎 → NO guarda todavía; despliega la línea de comentario opcional.
    down = client.post(base, data={"rating": "down", "pack": "datasphere_core"})
    assert down.status_code == 200
    assert "opcional" in down.text and "<textarea" in down.text
    assert len(list(tmp_path.glob("section_feedback_*.yaml"))) == 1  # sigue habiendo 1

    # Enviar el 👎 con comentario → guarda con el texto.
    submit = client.post(base + "/submit",
                         data={"rating": "down", "comment": "confuso", "pack": "datasphere_core"})
    assert submit.status_code == 200 and "Gracias" in submit.text
    downs = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in tmp_path.glob("section_feedback_*.yaml")]
    down_entry = next(e for e in downs if e["rating"] == "down")
    assert down_entry["comment"] == "confuso"


def test_section_feedback_unknown_section_404(monkeypatch, tmp_path):
    from webapp.routers import feedback as feedback_router
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    response = client.post(
        "/modules/01_Arquitectura_y_Conectividad/sections/no-existe/feedback",
        data={"rating": "up", "pack": "datasphere_core"},
    )
    assert response.status_code == 404


def test_section_feedback_cancel_renders_bar_without_persisting(monkeypatch, tmp_path):
    from webapp.routers import feedback as feedback_router
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    response = client.post(
        "/modules/01_Arquitectura_y_Conectividad/sections/espacios-transportes/feedback/cancel",
        data={"pack": "datasphere_core"},
    )
    assert response.status_code == 200
    assert "<textarea" not in response.text
    assert not list(tmp_path.glob("section_feedback_*.yaml"))


def test_module_feedback_reaction_submit_and_cancel(monkeypatch, tmp_path):
    import yaml
    from webapp.routers import feedback as feedback_router
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    base = "/modules/01_Arquitectura_y_Conectividad/feedback"

    # 👍 → guarda al toque y agradece.
    up = client.post(base, data={"rating": "up", "pack": "datasphere_core"})
    assert up.status_code == 200 and "Gracias" in up.text
    saved = list(tmp_path.glob("module_feedback_*.yaml"))
    assert len(saved) == 1
    payload = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert payload["type"] == "module_content"
    # MODULE IDENTIFIER CONVENTION V2 — Fase 2: escrituras nuevas persisten
    # el module_id canónico (CP01), no el stem legacy.
    assert payload["module"] == "CP01"
    assert payload["rating"] == "up"

    # 👎 → despliega el formulario de comentario, todavía no persiste.
    down = client.post(base, data={"rating": "down", "pack": "datasphere_core"})
    assert down.status_code == 200 and "<textarea" in down.text
    assert len(list(tmp_path.glob("module_feedback_*.yaml"))) == 1  # sigue habiendo 1

    # Enviar el 👎 sin comentario → re-muestra el formulario con error (a diferencia
    # de section/catalog, module exige comentario no vacío al enviar el 👎).
    empty_submit = client.post(base + "/submit", data={"rating": "down", "pack": "datasphere_core"})
    assert empty_submit.status_code == 200
    assert "module-feedback__error" in empty_submit.text
    assert len(list(tmp_path.glob("module_feedback_*.yaml"))) == 1  # no se agregó nada

    # Enviar el 👎 con comentario → guarda con el texto.
    submit = client.post(base + "/submit", data={"rating": "down", "comment": "confuso", "pack": "datasphere_core"})
    assert submit.status_code == 200 and "Gracias" in submit.text
    downs = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in tmp_path.glob("module_feedback_*.yaml")]
    down_entry = next(e for e in downs if e["rating"] == "down")
    assert down_entry["comment"] == "confuso"

    # Cancelar → vuelve a la barra de reacción, sin persistir nada nuevo.
    cancel = client.post(base + "/cancel", data={"pack": "datasphere_core"})
    assert cancel.status_code == 200
    assert "<textarea" not in cancel.text
    assert len(list(tmp_path.glob("module_feedback_*.yaml"))) == 2  # igual que antes del cancel


def test_catalog_feedback_reaction_submit_and_cancel(monkeypatch, tmp_path):
    import yaml
    from webapp.routers import feedback as feedback_router
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    fields = {"catalog_kind": "cds", "catalog_ref": "Z_CDS_VIEW", "pack": "datasphere_core"}

    # 👍 → guarda al toque y agradece.
    up = client.post("/catalog/feedback", data={"rating": "up", **fields})
    assert up.status_code == 200 and "Gracias" in up.text
    saved = list(tmp_path.glob("catalog_feedback_*.yaml"))
    assert len(saved) == 1
    payload = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert payload["type"] == "catalog"
    assert payload["catalog_kind"] == "cds"
    assert payload["catalog_ref"] == "Z_CDS_VIEW"
    assert payload["rating"] == "up"

    # 👎 → despliega el formulario de comentario, todavía no persiste.
    down = client.post("/catalog/feedback", data={"rating": "down", **fields})
    assert down.status_code == 200 and "<textarea" in down.text
    assert len(list(tmp_path.glob("catalog_feedback_*.yaml"))) == 1

    # Enviar el 👎 con comentario → guarda con el texto (catalog no exige comentario
    # no vacío, a diferencia de module).
    submit = client.post("/catalog/feedback/submit", data={"comment": "faltan ejemplos", **fields})
    assert submit.status_code == 200 and "Gracias" in submit.text
    downs = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in tmp_path.glob("catalog_feedback_*.yaml")]
    down_entry = next(e for e in downs if e["rating"] == "down")
    assert down_entry["comment"] == "faltan ejemplos"

    # Cancelar → vuelve a la barra de reacción, sin persistir nada nuevo.
    cancel = client.post("/catalog/feedback/cancel", data=fields)
    assert cancel.status_code == 200
    assert "<textarea" not in cancel.text
    assert len(list(tmp_path.glob("catalog_feedback_*.yaml"))) == 2


def test_catalog_section_feedback_reaction_submit_and_cancel(monkeypatch, tmp_path):
    import yaml
    from webapp.routers import feedback as feedback_router
    monkeypatch.setattr(feedback_router, "FEEDBACK_LOG_DIR", tmp_path)
    fields = {"cliente": "AcmeCorp", "dominio": "Finanzas", "slug": "resumen", "pack": "datasphere_core"}

    # 👍 → guarda al toque y agradece.
    up = client.post("/catalog/section-feedback", data={"rating": "up", **fields})
    assert up.status_code == 200 and "Gracias" in up.text
    saved = list(tmp_path.glob("catalog_section_*.yaml"))
    assert len(saved) == 1
    payload = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert payload["type"] == "catalog_section"
    assert payload["cliente"] == "AcmeCorp"
    assert payload["dominio"] == "Finanzas"
    assert payload["section_slug"] == "resumen"
    assert payload["rating"] == "up"

    # 👎 → despliega el formulario de comentario, todavía no persiste.
    down = client.post("/catalog/section-feedback", data={"rating": "down", **fields})
    assert down.status_code == 200 and "<textarea" in down.text
    assert len(list(tmp_path.glob("catalog_section_*.yaml"))) == 1

    # Enviar el 👎 con comentario → guarda con el texto.
    submit = client.post("/catalog/section-feedback/submit", data={"comment": "poco claro", **fields})
    assert submit.status_code == 200 and "Gracias" in submit.text
    downs = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in tmp_path.glob("catalog_section_*.yaml")]
    down_entry = next(e for e in downs if e["rating"] == "down")
    assert down_entry["comment"] == "poco claro"

    # Cancelar → vuelve a la barra de reacción, sin persistir nada nuevo.
    cancel = client.post("/catalog/section-feedback/cancel", data=fields)
    assert cancel.status_code == 200
    assert "<textarea" not in cancel.text
    assert len(list(tmp_path.glob("catalog_section_*.yaml"))) == 2


def test_intro_module_renders_immersive_embed():
    response = client.get("/modules/00_Intro")
    assert response.status_code == 200
    # Cuerpo inmersivo: stage full-height con el iframe a intro.html (no secciones markdown).
    assert 'class="academy-stage"' in response.text
    assert "/pack-assets/datasphere_core/intro.html" in response.text
    assert "branding=danwor" in response.text
    assert "embed=1" in response.text
    assert 'class="section-index"' not in response.text
    assert 'class="module-feedback' not in response.text
    # El cue "scroll para continuar" del HTML embebido apunta al módulo 01.
    assert "continue_url=" in response.text
    assert "01_Arquitectura_y_Conectividad" in response.text
    # Sigue siendo módulo Core: conserva el pager (link al 01) y el nav.
    assert 'href="/modules/01_Arquitectura_y_Conectividad' in response.text
    assert "CORE" in response.text


def test_intro_asset_has_rail_and_embeds_diagrams():
    response = client.get("/pack-assets/datasphere_core/intro.html")
    assert response.status_code == 200
    assert 'id="rail"' in response.text
    assert "danwor-for-business.html" in response.text
    assert "danwor-punto-de-partida.html" in response.text
    assert "danwor-roadmap-implementacion.html" in response.text


def test_user_tier_resolution(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "senior@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "junior@danwor.com")
    assert auth_service.user_tier("senior@danwor.com") == "full"
    assert auth_service.user_tier("junior@danwor.com") == "academy"
    assert auth_service.user_tier("otro@danwor.com") is None          # ninguna lista → denegado
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")
    assert auth_service.user_tier("otro@danwor.com") == "academy"     # ACADEMY=ALL → resto read-only
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    assert auth_service.user_tier("otro@danwor.com") == "full"        # FULL=ALL gana (post-lanzamiento)


def _readonly_client(monkeypatch, email="junior@danwor.com"):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "senior@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")
    session = auth_service.create_session(
        {"email": email, "name": "Junior", "first_name": "Junior", "family_name": "X"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    return ro


def test_readonly_profile_reads_content_with_assistant_but_no_llm(monkeypatch):
    ro = _readonly_client(monkeypatch)
    page = ro.get("/modules/05_Reporting_y_Consumo")
    assert page.status_code == 200
    # Lee el contenido completo (no se restringe).
    assert "Estrategia de" in page.text
    # Badge de perfil + Árbol + Asistente (router determinista) disponibles.
    assert "Modo lectura" in page.text
    assert "Árbol" in page.text
    assert ">Asistente</button>" in page.text
    assert "/assistant/chat" in page.text
    # Pero la pestaña Sección (análisis puramente IA) queda oculta.
    assert ">Sección</button>" not in page.text
    assert "/sections/analyze" not in page.text


def test_readonly_assistant_router_works_but_llm_output_is_blocked(monkeypatch):
    ro = _readonly_client(monkeypatch)
    from webapp.routers import assistant as assistant_router
    # El router NO debe llamar a OpenAI para este perfil: si algo intenta ask(), falla.
    monkeypatch.setattr(
        assistant_router.assistant_chat_service, "ask",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no debe consumir OpenAI en solo-lectura")),
    )
    conv = copilot_runtime.assistant_store.start("01_Arquitectura_y_Conectividad", "datasphere_core")
    # Pregunta que resuelve el router (contenido curado, determinista) → responde igual.
    routed = ro.post(
        "/modules/01_Arquitectura_y_Conectividad/assistant/chat",
        data={"pack": "datasphere_core", "question": "tenemos algun template CDS?", "conversation_id": conv.id},
    )
    assert routed.status_code == 200
    assert "IA no disponible para Danwor Academy" not in routed.text
    # "Explicame vos" (fuerza salida LLM) → aviso, sin consumir tokens.
    bypass = ro.post(
        "/modules/01_Arquitectura_y_Conectividad/assistant/chat",
        data={"pack": "datasphere_core", "question": "x", "conversation_id": conv.id,
              "routing_bypass": "true", "route_module": "01_Arquitectura_y_Conectividad"},
    )
    assert bypass.status_code == 200
    assert "IA no disponible para Danwor Academy" in bypass.text


def test_readonly_section_analysis_is_blocked(monkeypatch):
    ro = _readonly_client(monkeypatch)
    analyze = ro.post(
        "/modules/05_Reporting_y_Consumo/sections/analyze",
        data={"pack": "datasphere_core", "section_slug": "__module__",
              "action": "Guía operativa del módulo", "conversation_id": ""},
    )
    assert analyze.status_code == 403
    assert "IA no disponible para Danwor Academy" in analyze.text


def test_home_redirects_by_tier(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "senior@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")
    ro = _readonly_client(monkeypatch)
    ro_home = ro.get("/home", follow_redirects=False)
    assert ro_home.status_code == 307
    assert "/academy/ACAD01_Paradigmas" in ro_home.headers["location"]

    full_session = auth_service.create_session(
        {"email": "senior@danwor.com", "name": "Senior", "first_name": "Senior", "family_name": "Y"}
    )
    full = TestClient(app)
    full.cookies.set(auth_service.COOKIE_NAME, full_session)
    full_home = full.get("/home", follow_redirects=False)
    assert full_home.status_code == 307
    # El perfil full aterriza en la intro inmersiva (00), no en el módulo 01.
    assert "/modules/00_Intro" in full_home.headers["location"]


def test_full_profile_keeps_ia(monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "senior@danwor.com")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")
    session = auth_service.create_session(
        {"email": "senior@danwor.com", "name": "Senior", "first_name": "Senior", "family_name": "Y"}
    )
    full = TestClient(app)
    full.cookies.set(auth_service.COOKIE_NAME, session)
    page = full.get("/modules/05_Reporting_y_Consumo")
    assert page.status_code == 200
    assert ">Asistente</button>" in page.text
    assert ">Sección</button>" in page.text
    assert "/sections/analyze" in page.text
    assert "Modo lectura" not in page.text


def test_regular_module_is_not_immersive():
    response = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert response.status_code == 200
    assert 'class="module-content"' in response.text
    assert 'class="academy-stage"' not in response.text


def test_intro_has_no_panel_even_in_diagnostic_mode():
    # 00 Intro es solo lectura (inmersivo, sin IA): nunca panel derecho, ni con diagnóstico.
    response = diag_client.get("/modules/00_Intro")
    assert response.status_code == 200
    assert 'class="academy-stage"' in response.text
    assert "copilot-panel" not in response.text
    assert "no-copilot" in response.text


def test_sac_appendix_a06_renders():
    response = client.get("/modules/A06_SAP_Analytics_Cloud")
    assert response.status_code == 200
    assert 'class="module-content"' in response.text
    # Secciones clave del gobierno de contenido SAC.
    assert "Vista System" in response.text
    assert "Identidad Técnica" in response.text
    assert "Object ID" in response.text
    # Apéndice con IA habilitada → panel derecho presente.
    assert "copilot-panel" in response.text
    # Sin tokens sin renderizar.
    assert "{{" not in response.text


def test_sac_appendix_a06_hidden_from_nav_and_unlinked():
    # A06 está en revisión: nav_hidden → NO aparece en el nav de apéndices…
    nav = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert nav.status_code == 200
    assert "/modules/A06_SAP_Analytics_Cloud" not in nav.text
    # …y el módulo 05 conserva su sección SAC pero ya no enlaza a A06 (sin punteros
    # colgando a una página oculta).
    mod05 = client.get("/modules/05_Reporting_y_Consumo")
    assert mod05.status_code == 200
    assert "Organización de Contenido en" in mod05.text
    assert "/modules/A06_SAP_Analytics_Cloud" not in mod05.text


# --- What's New router (Fase 2D: webapp/routers/whats_new.py) ----------------
# Tres comportamientos observables reubicados desde main.py sin cambios: render
# autenticado, redirect de acceso anónimo y emisión de la cookie de "visto" en el
# dismiss. Clientes AISLADOS (como en logout) para no mutar la cookie
# dw-whatsnew-seen del cliente compartido.

def test_whats_new_page_authenticated_renders():
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, _SESSION)
    r = c.get("/whats-new")
    assert r.status_code == 200
    # Marcador estable del template (título), no del contenido dinámico de entries.
    assert "Novedades" in r.text
    assert "{{" not in r.text


def test_whats_new_page_anonymous_redirects_to_login():
    c = TestClient(app)  # sin cookie de sesión
    r = c.get("/whats-new", follow_redirects=False)
    # Comportamiento actual de nav.login_redirect: 307 a /?pack=…&next=…#acceso.
    assert r.status_code == 307
    location = r.headers["location"]
    assert location.startswith("/?")
    assert location.endswith("#acceso")
    assert "next=%2Fwhats-new" in location
    assert "pack=datasphere_core" in location


def test_whats_new_dismiss_emits_seen_cookie():
    from webapp.services import whats_new_service

    c = TestClient(app)
    # latest_ref() depende de git/pack en runtime; se fija para un aserto
    # determinista sobre la cookie (no sobre el contenido de las novedades).
    original = whats_new_service.latest_ref
    whats_new_service.latest_ref = lambda *a, **k: "ref-test-123"
    try:
        r = c.post("/whats-new/dismiss")
    finally:
        whats_new_service.latest_ref = original
    assert r.status_code == 200
    assert r.text == ""
    assert r.cookies.get(whats_new_service.COOKIE_NAME) == "ref-test-123"
    set_cookie = r.headers["set-cookie"]
    assert "Max-Age=31536000" in set_cookie
    assert "Path=/" in set_cookie


# --- Academy router (Fase 2E: webapp/routers/academy.py) ---------------------
# El render autenticado, el feedback react (up) y la regresión de /pack-assets ya
# están cubiertos por tests previos (embeds/feedback/desafío). Acá se cierran los
# gaps: redirect anónimo (B) y submit(down)/cancel (D/E), con el seam del router.

def test_academy_page_anonymous_redirects_to_login():
    c = TestClient(app)  # sin cookie de sesión
    r = c.get("/academy/ACAD01_Paradigmas", follow_redirects=False)
    # Sin sesión, academy_page devuelve nav.login_redirect (307 a …#acceso).
    assert r.status_code == 307
    assert r.headers["location"].endswith("#acceso")


def test_academy_feedback_submit_persists_and_cancel_resets(monkeypatch, tmp_path):
    from webapp.routers import academy as academy_router
    monkeypatch.setattr(academy_router, "FEEDBACK_LOG_DIR", tmp_path)
    # submit 👎 con comentario → persiste YAML academy_content + agradece.
    submit = client.post(
        "/academy/ACAD05_Desafio/feedback/submit",
        data={"rating": "down", "comment": "faltó un ejemplo", "pack": "datasphere_core"},
    )
    assert submit.status_code == 200
    saved = list(tmp_path.glob("academy_feedback_*.yaml"))
    assert len(saved) == 1
    import yaml
    payload = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert payload["type"] == "academy_content" and payload["rating"] == "down"
    assert payload["comment"] == "faltó un ejemplo"
    # cancel → devuelve la botonera base (reactions) y NO persiste nada nuevo.
    cancel = client.post("/academy/ACAD05_Desafio/feedback/cancel", data={"pack": "datasphere_core"})
    assert cancel.status_code == 200
    assert "module-feedback__reactions" in cancel.text
    assert len(list(tmp_path.glob("academy_feedback_*.yaml"))) == 1
