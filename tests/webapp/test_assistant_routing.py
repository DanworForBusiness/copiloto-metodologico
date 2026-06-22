from webapp.services.assistant_routing import AssistantRoutingService
from webapp.services.content_service import load_module


def test_routes_template_question_to_curated_asset_before_llm():
    document = load_module("01_Arquitectura_y_Conectividad")

    route = AssistantRoutingService().route(
        document, "tenemos algun template CDS?", "datasphere_mvp"
    )

    assert route is not None
    assert "Template CDS" in route.message
    assert route.explain_module == "03_Integracion_y_Estrategia_de_Datos"
    assert any(action.label == "Abrir recurso" for action in route.actions)
    assert any("sharepoint.com" in action.url for action in route.actions)


def test_routes_cross_module_topic_to_exact_section_anchor():
    document = load_module("01_Arquitectura_y_Conectividad")

    route = AssistantRoutingService().route(
        document, "federacion vs persistencia cual conviene", "datasphere_mvp"
    )

    assert route is not None
    assert route.explain_module == "03_Integracion_y_Estrategia_de_Datos"
    assert route.actions[0].label == "Ir a la sección"
    assert route.actions[0].url.endswith("#federacion-vs-persistencia")


def test_routes_active_module_asset_without_importing_streamlit_content():
    document = load_module("04_Modelado_y_Desarrollo")

    route = AssistantRoutingService().route(
        document, "tenemos algun template CDS?", "datasphere_mvp"
    )

    assert route is not None
    assert route.explain_module == document.summary.stem
    assert route.actions[0].url == (
        "/pack-resources/datasphere_mvp/templates/cds_extractor_template.abap"
    )
