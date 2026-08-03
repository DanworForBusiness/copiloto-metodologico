from webapp.services.content_service import list_modules, load_module

def test_loads_real_module_from_core_registry():
    module = next(item for item in list_modules() if item.number == "01")
    document = load_module(module.stem)
    assert document.summary.title == "Arquitectura y Conectividad"
    assert len(document.sections) >= 3
    assert "section_meta" not in document.sections[0].html
    assert '<iframe class="content-embed"' in document.sections[0].html
    assert "/pack-assets/datasphere_core/" in document.sections[0].html
    assert "Abrir recurso" not in document.sections[0].html
    assert '<span class="section-eyebrow">[' in document.sections[0].html
    assert "{{callout" not in "".join(section.html for section in document.sections)


def test_content_packs_are_resolved_independently():
    mvp = list_modules("datasphere_core")
    bdc = list_modules("bdc_next")

    assert len(mvp) == 6
    assert len(bdc) == 2
    assert mvp[1].stem == "01_Arquitectura_y_Conectividad"
    assert bdc[1].stem == "01_Estrategia_y_Readiness_BDC"


def test_internal_editorial_tokens_never_reach_html():
    for module in list_modules("datasphere_core"):
        document = load_module(module.stem, "datasphere_core")
        rendered = "".join(section.html for section in document.sections)
        assert "{{" not in rendered, module.stem


def test_first_blockquote_is_rendered_as_editorial_subtitle():
    module = next(item for item in list_modules() if item.number == "01")
    document = load_module(module.stem)
    section = next(item for item in document.sections if "section-subtitle" in item.html)

    assert 'content-heading has-subtitle' in section.html
    assert '<blockquote class="section-subtitle">' in section.html


def test_leading_support_is_in_heading_before_subtitle():
    document = load_module("01_Arquitectura_y_Conectividad")
    section = next(item for item in document.sections if item.title == "Espacios & Transportes")

    assert 'content-heading has-subtitle' in section.html
    assert '<blockquote class="section-subtitle">' in section.html
    heading_end = section.html.index("</header>")
    support_position = section.html.index('class="support-links heading-supports"')
    subtitle_position = section.html.index('class="section-subtitle"')
    assert section.html.index("<h2>") < support_position < heading_end < subtitle_position


def test_callout_parameters_render_tone_icon_and_title():
    success = load_module("01_Arquitectura_y_Conectividad")
    success_html = "".join(section.html for section in success.sections)
    warn = load_module("04_Modelado_y_Desarrollo")
    warn_html = "".join(section.html for section in warn.sections)

    assert 'class="callout callout--success"' in success_html
    assert 'class="callout__icon">🌿</span>' in success_html
    assert 'class="callout__title">Momento recomendado</strong>' in success_html
    assert 'class="callout callout--warn"' in warn_html


def test_callout_row_groups_parameterized_callouts():
    document = load_module("02_Seguridad_y_Gobernanza")
    rendered = "".join(section.html for section in document.sections)

    assert 'class="callout-row"' in rendered
    assert 'class="callout callout--note"' in rendered
    assert 'class="callout callout--punch"' in rendered
    assert 'class="callout callout--success"' in rendered


def test_collapsible_directive_wraps_code_as_details_on_demand():
    document = load_module("03_Integracion_y_Estrategia_de_Datos")
    section = next(item for item in document.sections if item.title == "Referencia Técnica")

    # El bloque colapsable envuelve el codigo; el fenced code sigue renderizado
    # (pre) y no queda ningun token/centinela sin consumir.
    assert '<details class="content-collapsible"' in section.html
    assert section.html.count("<details") == section.html.count("</details>")
    assert "<pre" in section.html
    assert "DWCOLL" not in section.html and "{{collapsible" not in section.html
    # La tabla de decision queda ANTES del codigo colapsado (pedagogia primero).
    assert section.html.index("Escenario") < section.html.index("<details")


def test_module_roadmap_groups_every_section_into_stages():
    document = load_module("03_Integracion_y_Estrategia_de_Datos")

    assert len(document.roadmap) == 4
    assert [stage.stage for stage in document.roadmap] == [
        "Contrato", "Extracción", "Materialización", "Referencia",
    ]
    # Cada seccion del modulo aparece exactamente una vez en el roadmap.
    linked = [link.slug for stage in document.roadmap for link in stage.links]
    assert sorted(linked) == sorted(section.slug for section in document.sections)
    assert len(linked) == len(set(linked))
    # "Referencia" es un apendice opcional (tramo punteado); el resto no.
    optional = {stage.stage: stage.optional for stage in document.roadmap}
    assert optional == {
        "Contrato": False, "Extracción": False,
        "Materialización": False, "Referencia": True,
    }


def test_modules_without_roadmap_meta_have_empty_roadmap():
    document = load_module("01_Arquitectura_y_Conectividad")
    assert document.roadmap == ()
