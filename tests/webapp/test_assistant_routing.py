from webapp.services.assistant_routing import AssistantRoutingService
from webapp.services.content_service import load_module


def test_routes_template_question_to_curated_asset_before_llm():
    document = load_module("01_Arquitectura_y_Conectividad")

    route = AssistantRoutingService().route(
        document, "tenemos algun template CDS?", "datasphere_core"
    )

    assert route is not None
    assert "Template CDS" in route.message
    assert route.explain_module == "03_Integracion_y_Estrategia_de_Datos"
    assert any(action.label == "Abrir recurso" for action in route.actions)
    assert any("sharepoint.com" in action.url for action in route.actions)


def test_routes_cross_module_topic_to_exact_section_anchor():
    document = load_module("01_Arquitectura_y_Conectividad")

    route = AssistantRoutingService().route(
        document, "federacion vs persistencia cual conviene", "datasphere_core"
    )

    assert route is not None
    assert route.explain_module == "03_Integracion_y_Estrategia_de_Datos"
    assert route.actions[0].label == "Ir a la sección"
    assert route.actions[0].url.endswith("#federacion-vs-persistencia")


def test_routes_active_module_asset_without_importing_streamlit_content():
    document = load_module("04_Modelado_y_Desarrollo")

    route = AssistantRoutingService().route(
        document, "tenemos algun template CDS?", "datasphere_core"
    )

    assert route is not None
    assert route.explain_module == document.summary.stem
    assert route.actions[0].url == (
        "/pack-resources/datasphere_core/templates/cds_extractor_template.abap"
    )


def test_conceptual_power_bi_protocol_question_keeps_asset_as_related_support():
    document = load_module("01_Arquitectura_y_Conectividad")

    route = AssistantRoutingService().route(
        document,
        "Power BI pueden consumir modelos analiticos? existe alguna diferencia de compatibilidad entre ODBC y OData en ese sentido?",
        "datasphere_core",
    )

    assert route is not None
    assert "comparativa" in route.message
    assert "recurso relacionado" in route.message
    assert "How-to Danwor: Conectar Power BI" in route.message
    assert route.explain_module == "05_Reporting_y_Consumo"
    assert route.explain_heading == "Conexión de Herramientas Externas"
    assert route.actions[0].label == "Ir a la sección"
    assert route.actions[1].label == "Abrir recurso relacionado"


def test_direct_power_bi_odbc_how_to_still_routes_to_asset():
    document = load_module("01_Arquitectura_y_Conectividad")

    route = AssistantRoutingService().route(
        document, "como conectar Power BI por ODBC", "datasphere_core"
    )

    assert route is not None
    assert "recurso curado" in route.message
    assert any(action.label == "Abrir recurso" for action in route.actions)


def _seed_apis(tmp_path):
    apis = tmp_path / "APIs"
    apis.mkdir()
    for n in ("ZCDS_DSP_FACT_LIPS", "ZCDS_DSP_DIM_ADRC"):
        (apis / f"{n}.md").write_text(f"# {n}", encoding="utf-8")
    return tmp_path


def test_find_matching_cds_por_nombre_tecnico(tmp_path, monkeypatch):
    from webapp.services.assistant_routing import _find_matching_cds

    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(_seed_apis(tmp_path)))
    assert _find_matching_cds("que hace ZCDS_DSP_FACT_LIPS?") == "ZCDS_DSP_FACT_LIPS"
    # tolera el prefijo de transporte del artefacto (1TR_)
    assert _find_matching_cds("mostrame 1TR_ZCDS_DSP_DIM_ADRC") == "ZCDS_DSP_DIM_ADRC"
    # nombre ZCDS que NO está publicado (name-vs-reality) → no se inventa
    assert _find_matching_cds("ZCDS_DSP_FACT_MCHB") is None
    # sin referencia a una CDS → None
    assert _find_matching_cds("como modelo una vista analitica") is None


def test_clasificador_inventario_de_dominios():
    from webapp.services.assistant_routing import _is_domain_inventory_query as inv

    assert inv("que modelos de datos tenemos ya desarrollados?")
    assert inv("que dominios tenemos ya desarrollados?")
    assert inv("que modelos analiticos tenemos?")
    assert inv("cuales flujos hay implementados?")
    # how-to → no inventario
    assert not inv("como creo un modelo de datos?")
    # sin término de dominio → no
    assert not inv("que conexiones tenemos?")


def test_clasificador_inventario_de_cds():
    from webapp.services.assistant_routing import _is_cds_inventory_query as inv

    assert inv("que CDS tenemos ya armadas?")
    assert inv("cuales extractores tenemos?")
    assert inv("que vistas cds hay implementadas?")
    # crear/escribir una CDS es how-to (template), no inventario
    assert not inv("como creo una CDS view?")
    assert not inv("escribir una cds")
    # sin término CDS → no
    assert not inv("que conexiones tenemos?")


def test_route_lidera_con_inventario_de_cds(tmp_path, monkeypatch):
    apis = tmp_path / "APIs"
    apis.mkdir()
    for n in ("ZCDS_DSP_FACT_LIPS", "ZCDS_DSP_DIM_ADRC", "ZCDS_DSP_TSPAT"):
        (apis / f"{n}.md").write_text(f"# {n}", encoding="utf-8")
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path))
    document = load_module("03_Integracion_y_Estrategia_de_Datos")

    route = AssistantRoutingService().route(document, "que CDS tenemos ya armadas?", "datasphere_core")

    assert route is not None
    assert "3 CDS views" in route.message
    # una acción embed por CDS, apuntando a /catalog/cds
    assert len(route.actions) == 3
    assert all(a.embed and a.url.startswith("/catalog/cds?name=") for a in route.actions)


def test_route_lidera_con_cds_view_por_nombre(tmp_path, monkeypatch):
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(_seed_apis(tmp_path)))
    document = load_module("01_Arquitectura_y_Conectividad")

    route = AssistantRoutingService().route(
        document, "que hace ZCDS_DSP_FACT_LIPS?", "datasphere_core"
    )

    assert route is not None
    assert "ZCDS_DSP_FACT_LIPS" in route.message
    assert route.actions[0].label == "Ver la CDS view"
    assert route.actions[0].url == "/catalog/cds?name=ZCDS_DSP_FACT_LIPS&pack=datasphere_core"


def test_clasificador_inventario_de_conectividad():
    from webapp.services.assistant_routing import _is_connectivity_inventory_query as inv

    # inventario genérico → sí (frases variadas de cobertura/posesión, robusto a
    # inserciones tipo "tipos de" y verbos como "hemos cubierto"/"manejamos")
    assert inv("que conexiones tenemos resueltas en proyectos?")
    assert inv("que conexiones tenemos?")
    assert inv("que origenes de datos tenemos implementados?")
    assert inv("cuales fuentes de datos hay?")
    assert inv("que tipos de conexiones hemos cubierto en nuestros proyectos?")
    assert inv("cuales origenes de datos manejamos?")
    # conceptual → NO es inventario (no debe secuestrar "qué es…")
    assert not inv("que es una conexion?")
    # how-to o conexión puntual → NO es inventario (los maneja otro lead)
    assert not inv("como configurar PostgreSQL?")
    assert not inv("como conectar Power BI por ODBC")
    # sin término de conectividad → no
    assert not inv("que modelos analiticos tenemos?")
