from types import SimpleNamespace

from fastapi.testclient import TestClient
from webapp.main import app, templates
import webapp.main as web_main
from webapp.services import auth_service
from webapp.services.section_analysis import SectionAnalysisService
from webapp.services.content_service import load_module

# El modo diagnóstico solo lo habilita el dueño (APP_OWNER). En tests, el usuario
# de prueba ES el owner para poder ejercitar el grupo de gestión / selector.
web_main.APP_OWNER = "test@danwor.com"

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

def test_module_page_renders_server_side():
    response = client.get("/modules/01_Arquitectura_y_Conectividad")
    assert response.status_code == 200
    assert "Arquitectura y Conectividad" in response.text
    assert "Topología de Espacios" in response.text
    assert "hx-post=" in response.text
    assert "1 árbol asociado" in response.text
    assert "/sections/analyze" in response.text
    assert "Salida completa (operativa)" in response.text
    assert "¿Qué hace cada acción?" in response.text


def test_section_analysis_endpoint_returns_htmx_fragment(monkeypatch):
    monkeypatch.setattr(
        web_main,
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
    monkeypatch.setattr(
        web_main,
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
    monkeypatch.setattr(
        web_main,
        "section_analysis_service",
        SectionAnalysisService(execution_mode="Simulación"),
    )
    document = load_module("01_Arquitectura_y_Conectividad")
    conversation = web_main.assistant_store.start(
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
    assert len(web_main.assistant_store.history(
        conversation.id, conversation.module, conversation.pack,
    )) == 2


def test_assistant_feedback_rejects_an_older_reply():
    conversation = web_main.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )
    first = web_main.assistant_store.append(
        conversation.id, conversation.module, conversation.pack,
        "Primera pregunta", "Primera respuesta", "<p>Primera respuesta</p>",
        topic="Pregunta libre", action="Asistente",
    )
    web_main.assistant_store.append(
        conversation.id, conversation.module, conversation.pack,
        "Pregunta siguiente", "Respuesta siguiente", "<p>Respuesta siguiente</p>",
        topic="Pregunta libre", action="Asistente",
    )

    response = client.post(
        f"/assistant/responses/{first.id}/feedback", data={"rating": "up"}
    )

    assert response.status_code == 409
    assert "respuesta más reciente" in response.text


def test_assistant_chat_routes_to_curated_asset_without_calling_llm(monkeypatch):
    conversation = web_main.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )

    def unexpected_llm(*args, **kwargs):
        raise AssertionError("El LLM no debe ejecutarse cuando hay routing curado")

    monkeypatch.setattr(web_main.assistant_chat_service, "ask", unexpected_llm)
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


def test_explain_route_appends_only_the_assistant_answer(monkeypatch):
    conversation = web_main.assistant_store.start(
        "01_Arquitectura_y_Conectividad", "datasphere_core"
    )
    web_main.assistant_store.append(
        conversation.id, conversation.module, conversation.pack,
        "tenemos algun template CDS?", "Encontré un recurso curado.",
        "<p>Encontré un recurso curado.</p>", topic="Routing", action="Routing",
    )
    monkeypatch.setattr(
        web_main.assistant_chat_service,
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
    assert web_main.assistant_store.history(
        conversation.id, conversation.module, conversation.pack,
    ).count(("user", "tenemos algun template CDS?")) == 1


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
    assert "3 árboles asociados" in response.text
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


def test_academy_asset_injects_embed_flag_and_theme():
    response = client.get("/pack-assets/datasphere_core/academy/paradigmas.html?branding=danwor&embed=1")
    assert response.status_code == 200
    assert "window.DANWOR_EMBED_PROPS" in response.text
    assert "data-dw-embed" in response.text          # el script marca el modo embebido
    assert "data-dw-theme" in response.text          # theme del copiloto inyectado


def test_academy_shared_stylesheet_is_served():
    response = client.get("/pack-assets/datasphere_core/academy/dw-academy.css")
    assert response.status_code == 200
    assert 'branding-danwor' in response.text


def test_unknown_academy_page_returns_404():
    response = client.get("/academy/ACAD99_NoExiste")
    assert response.status_code == 404


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
