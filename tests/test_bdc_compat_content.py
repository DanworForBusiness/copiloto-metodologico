"""Integración metodológica de la Compatibilidad BDC: el aprendizaje vive en el contenido del Copiloto (no solo en el Assessment).

Contrato: una sola fuente de verdad para las restricciones (el YAML), las 4 fuentes SAP asociadas a los conceptos mediante el `{{support: …}}` existente
(contrato real: `Etiqueta="url"`), registradas una vez en la Biblioteca de Referencias (A04), y las referencias del Assessment al contenido resueltas por
`key_topics` (`module_reference`), jamás por número. Las tres evidencias (SAP documentado / Validado Danwor / Pendiente) se distinguen visiblemente."""
from __future__ import annotations

import re
from pathlib import Path

import yaml

from tools.catalog import compat_bdc as cb
from webapp.services import content_service as cs
from webapp.services import module_reference as MR

REPO = Path(__file__).resolve().parents[1]
PACK = REPO / "content_packs" / "datasphere_core"
MX = cb.load_compatibility("datasphere_core")
M03 = "03_Integracion_y_Estrategia_de_Datos"


def _text(rel: str) -> str:
    return (PACK / rel).read_text(encoding="utf-8")


def _section_html(stem: str, title_start: str) -> str:
    doc = cs.load_module(stem, "datasphere_core")
    return next(s for s in doc.sections if s.title.startswith(title_start)).html


def _support_lines(src: str) -> list[dict[str, str]]:
    out = []
    for m in cs.SUPPORT_RE.finditer(src):
        out.append(dict(cs.SUPPORT_ITEM_RE.findall(m.group(1))))
    return out


# ── fuentes SAP: una vez en el YAML, en A04 y en los Support Links, sin divergir ─────────────────────────────────────
def test_every_sap_source_url_is_in_the_reference_library_and_in_a_support_link_of_module_03():
    a04, m03 = _text("content/apendices/A04_Biblioteca_de_Referencias.md"), _text(f"content/{M03}.md")
    support_urls = {u for d in _support_lines(m03) for u in d.values()}
    for sid, f in MX["fuentes"].items():
        assert f["url"] in a04, sid                                                              # registro central de URLs SAP
        assert f["url"] in support_urls, sid                                                     # asociada a un concepto mediante Support Links


def test_support_link_labels_in_the_content_match_the_registered_labels_exactly():
    m03 = _text(f"content/{M03}.md")
    by_url = {f["url"]: f["etiqueta"] for f in MX["fuentes"].values()}
    seen = 0
    for d in _support_lines(m03):
        for label, url in d.items():
            if url in by_url:
                assert label.strip() == by_url[url], (label, url)
                seen += 1
    assert seen == 4                                                                             # cada fuente aparece asociada a un concepto, no como bibliografía suelta


def test_the_reference_library_lists_each_source_once_as_a_link_and_once_for_the_assistant():
    a04 = _text("content/apendices/A04_Biblioteca_de_Referencias.md")
    for f in MX["fuentes"].values():
        assert a04.count(f'| [Link]({f["url"]})') == 1 and a04.count(f'asset: "{f["url"]}"') == 1


def test_sap_sources_are_associated_to_the_concept_sections_not_dumped_at_the_end():
    sections = {s.title: s.html for s in cs.load_module(M03, "datasphere_core").sections}
    hib = next(h for t, h in sections.items() if t.startswith("Modelo de Integración Híbrido"))
    head = hib[: hib.index("</header>")]
    for sid in ("SAP-RF-TIPOS", "SAP-LTF-TIPOS", "SAP-COL-TIPOS"):
        assert MX["fuentes"][sid]["url"] in head and MX["fuentes"][sid]["etiqueta"] in head      # en el ENCABEZADO de la sección del concepto
    contrato = next(h for t, h in sections.items() if t.startswith("Contrato de Datos vs"))
    assert MX["fuentes"]["SAP-DP-BDC"]["url"] in contrato[: contrato.index("</header>")]
    assert head.count('class="support-link"') == 3


# ── una sola fuente de verdad: las reglas técnicas no se copian al contenido ─────────────────────────────────────────
def test_constraint_ids_and_rule_definitions_live_only_in_the_yaml():
    for p in list((PACK / "content").rglob("*.md")) + list((PACK / "trees").glob("*.yaml")):
        t = p.read_text(encoding="utf-8")
        assert "SAPC-" not in t and "compatibilidad_bdc" not in t, p.name


# ── el Assessment referencia el contenido por clave semántica ───────────────────────────────────────────────────────
def test_every_constraint_reference_resolves_through_key_topics_to_a_real_section_and_anchor():
    for r in MX["restricciones"]:
        ref = r["metodologia"]
        resolved = MR.resolve(ref["module_key"], ref["section_key"], "datasphere_core", ref.get("label"))
        assert resolved["number"] is not None and resolved["slug"], r["id"]                       # se resuelve contra la estructura VIGENTE
        if ref.get("ancla"):
            doc = cs.load_module(M03 if ref["module_key"] == "03" else "", "datasphere_core")
            html = next(s.html for s in doc.sections if s.slug == resolved["slug"])
            assert f'id="{ref["ancla"]}"' in html, r["id"]                                         # el ancla del H3 existe de verdad


def test_internal_links_into_the_hybrid_section_resolve_to_a_real_section_slug():
    slug = next(s.slug for s in cs.load_module(M03, "datasphere_core").sections if s.title.startswith("Modelo de Integración Híbrido"))
    for stem in ("01_Arquitectura_y_Conectividad", M03):
        html = "".join(s.html for s in cs.load_module(stem, "datasphere_core").sections)
        assert f'href="/modules/{M03}?pack=datasphere_core#{slug}"' in html, stem


# ── narrativa: decisión → restricciones → implicaciones → alternativas, con la evidencia distinguida ────────────────
def test_the_old_overgeneralised_callout_is_gone_and_string_10_is_scoped_to_its_mechanism():
    m03 = _text(f"content/{M03}.md")
    assert "Portabilidad HDLF/BDC" not in m03
    for line in m03.splitlines():
        if "String(10)" in line:
            assert "Transformation Flow" in line, line                                            # lo empírico va acotado al mecanismo en que se observó
        assert not re.search(r"(?i)DecimalFloat[^.]*\b(siempre|nunca|todo[s]? (los )?(destino|mecanismo))", line), line


def _h3_text() -> str:
    html = _section_html(M03, "Modelo de Integración Híbrido")
    h3 = html[html.index('<h3 id="del-tipo-del-runtime-al-tipo-del-contrato">'):]
    return re.sub(r"<[^>]+>", "", h3)


def test_the_h3_is_brief_prose_that_answers_why_a_valid_type_may_need_adaptation_for_a_destination():
    html = _section_html(M03, "Modelo de Integración Híbrido")
    h3_html = html[html.index('<h3 id="del-tipo-del-runtime-al-tipo-del-contrato">'):]
    assert "callout" not in h3_html.split("Te ayudamos a identificar")[0]                               # sin cajas ni componentes nuevos: prosa editorial
    text = _h3_text().split("Te ayudamos a identificar")[0]
    assert len(text) < 1900 and text.count("\n\n") <= 6                                                  # breve: no vuelve a explicar toda la metodología
    assert "Un tipo válido en SAP Datasphere no necesariamente forma parte del conjunto admitido por Local Table (File), HDLF" in text
    assert text.index("depende del mecanismo") < text.index("DecimalFloat") < text.index("precisión, escala y rango")   # tipo/destino → ejemplo → implicancias


def test_the_h3_distinguishes_sap_documentation_from_danwor_experience_and_unverified_in_plain_prose():
    text = _h3_text().split("Te ayudamos a identificar")[0]
    assert "SAP documenta que un Replication Flow hacia una Local Table (File) convierte" in text and "decimal(38,6)" in text
    assert "no figura en la matriz de tipos soportados" in text and "no equivale a que esté prohibido" in text    # ausencia ≠ prohibición
    assert "Nuestra experiencia con HDLF" in text and "observación empírica de Danwor" in text
    assert "no está verificada para otros mecanismos y no debe generalizarse" in text                    # pendiente de verificación, sin generalizar
    assert re.search(r"String\(10\)", text) and "Transformation Flow" in text[text.index("String(10)") - 120: text.index("String(10)")]


def test_the_h3_flags_precision_scale_and_range_as_derived_not_documented_by_sap():
    text = _h3_text().split("Te ayudamos a identificar")[0]
    seg = text[text.index("validar el impacto"):]
    assert "escala en 6" in seg and "rango de un decimal flotante" in seg and "implicancias derivadas del tipo, no documentadas por SAP" in seg
    assert "siempre preserva" not in text and "sin pérdida" not in text                                  # nunca se promete que preserva precisión/rango


def test_the_h3_does_not_explain_the_layers_it_only_points_to_module_04():
    text = _h3_text().split("Te ayudamos a identificar")[0]
    for layer in ("Ingesta", "Armonización", "Propagación", "Consumo"):
        assert layer not in text, layer                                                                  # las responsabilidades de las capas viven en el módulo 04
    html = _section_html(M03, "Modelo de Integración Híbrido")
    assert 'href="/modules/04_Modelado_y_Desarrollo?pack=datasphere_core#arquitectura-por-capas-guia-operativa"' in html
    slug = next(s.slug for s in cs.load_module("04_Modelado_y_Desarrollo", "datasphere_core").sections if s.title.startswith("Arquitectura por Capas: Guia Operativa"))
    assert slug == "arquitectura-por-capas-guia-operativa"                                               # el destino del enlace existe


def test_layers_rule_by_purpose_lives_only_in_module_04_and_its_layer_table_is_untouched():
    m04 = _text("content/04_Modelado_y_Desarrollo.md")
    assert "**se hace acá**; la normalización corporativa de tipos, precisión y escala pertenece a Armonización." in m04
    assert "sin tocar Ingesta ni Armonización" not in m04 and "acá se fija el tipo contractual" not in m04
    assert "| Tipos hacia HDLF |" not in m04                                                              # sin fila duplicada de capas en Flujos de Datos
    row = next(line for line in m04.splitlines() if line.startswith("| **(2) Armonizacion**"))
    assert "Limpieza y estandarización" in row and "Normalizar formatos" in row
    assert "Ninguno o muy bajo. Mapeo 1:1 y datos tal cual la fuente." in m04
    for rel in ("content/01_Arquitectura_y_Conectividad.md", f"content/{M03}.md"):
        t = _text(rel)
        assert "normalización semántica corporativa" not in t and "pertenece a Propagación" not in t, rel   # no se duplica la regla de capas fuera del 04


def test_constraint_recommendations_never_ask_to_freeze_the_original_type_in_harmonization():
    for r in MX["restricciones"]:
        assert not re.search(r"preservar el tipo original en Ingesta y Armonizaci", r["recomendacion"]), r["id"]
        if "Propagación" in r["recomendacion"] and "DECIMAL(38,6)" in r["recomendacion"]:
            assert "adaptación técnica" in r["recomendacion"], r["id"]


def test_key_topic_for_the_assessment_is_declared_in_the_module_front_matter():
    meta, _ = cs.split_front_matter(_text(f"content/{M03}.md"))
    t = next(t for t in meta["key_topics"] if t["id"] == "portabilidad_tipos")
    assert t["heading"] == "Modelo de Integración Híbrido"
    assert {t["id"] for t in meta["key_topics"]} >= {"data_contract", "integracion", "extraccion", "cds_views", "tablas_remotas"}   # los existentes no cambian


def test_the_module_roadmap_still_lists_only_existing_sections():
    meta, body = cs.split_front_matter(_text(f"content/{M03}.md"))
    titles = {s.title for s in cs.load_module(M03, "datasphere_core").sections}
    for stage in meta["roadmap"]:
        for s in stage["sections"]:
            assert any(cs._norm(s) == cs._norm(t) for t in titles), s


def test_bdc_next_replaces_the_content_so_it_does_not_inherit_module_03_and_is_left_untouched():
    meta = yaml.safe_load((REPO / "content_packs/bdc_next/pack.yaml").read_text(encoding="utf-8"))
    assert meta["extends"] == "datasphere_core" and meta["content_mode"] == "replace"              # reemplaza: el módulo 03 no se ve en bdc_next
    readiness = (REPO / "content_packs/bdc_next/content/01_Estrategia_y_Readiness_BDC.md").read_text(encoding="utf-8")
    assert "Contrato de tipos" not in readiness and "Del tipo del runtime" not in readiness         # sin referencias colgantes a contenido que ese pack no muestra


# ── el asistente recupera el conocimiento nuevo sin contradecir otras secciones ─────────────────────────────────────
H3_ANCHOR = "del-tipo-del-runtime-al-tipo-del-contrat"


def _route(stem: str, question: str):
    from webapp.services.assistant_routing import AssistantRoutingService
    route = AssistantRoutingService().route(cs.load_module(stem, "datasphere_core"), question, "datasphere_core")
    return route, " ".join(a.url for a in (route.actions or ())) if route else ""


def test_typical_assistant_questions_land_on_the_new_h3_with_a_working_deep_link():
    for q in ("DecimalFloat se puede publicar a HDLF?", "tipos soportados en local table file", "que tipos no soporta un custom data product",
              "por que un tipo valido en datasphere puede requerir adaptacion para publicarlo", "donde convierto el tipo antes de publicar a delta sharing"):
        route, urls = _route(M03, q)
        assert route is not None and H3_ANCHOR in urls, q
    h3_id = "del-tipo-del-runtime-al-tipo-del-contrato"
    assert h3_id in _section_html(M03, "Modelo de Integración Híbrido")                             # el fragmento del router existe en el DOM


def test_conceptual_type_questions_asked_from_other_modules_point_to_module_03():
    for stem in ("01_Arquitectura_y_Conectividad", "04_Modelado_y_Desarrollo"):
        for q in ("por que un tipo valido en datasphere puede requerir adaptacion para publicarlo", "donde convierto el tipo antes de publicar a delta sharing"):
            route, urls = _route(stem, q)
            assert route is not None and H3_ANCHOR in urls and "03_Integracion" in urls, (stem, q)


def test_neighbouring_questions_keep_their_own_sections():
    cases = (("03_Integracion_y_Estrategia_de_Datos", "diferencia entre federacion y persistencia", "federacion-vs-persistencia"),
             ("03_Integracion_y_Estrategia_de_Datos", "que es un data product?", "contrato-de-datos-vs-data-product"),
             ("01_Arquitectura_y_Conectividad", "que es HDLF?", "coexistencia-de-paradigmas"),
             ("04_Modelado_y_Desarrollo", "que capas hay en el modelado?", "evolucion-de-arquitectura-por-capas"))
    for stem, q, expect in cases:
        route, urls = _route(stem, q)
        assert route is not None and expect in urls and H3_ANCHOR not in urls, q
