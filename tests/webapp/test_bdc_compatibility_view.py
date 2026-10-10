"""Compatibilidad BDC en el Assessment: composición en SOLO LECTURA desde el Catálogo + presentación en «Portabilidad & preparación BDC».

Contrato: el eje es aditivo (no puntaje, no toca `portabilidad_bdc`/linaje/Juicio), el destino se DECLARA en el flujo (`manual.publicacion_bdc`),
sin declaración se muestra «condicionada al destino» con las preguntas, y las fuentes SAP salen como Support Links. Fixtures sintéticas."""
from __future__ import annotations

import copy
import html
import hashlib
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from webapp.main import templates
from webapp.services import asset_evaluation as ae
from webapp.services import bdc_compatibility_service as svc

MACROS = "partials/assessment_report_macros.html"
PORT = {"score": 80, "clasificacion": "buena", "semaforo": "green", "confianza": "alta", "campos_revisados": 4, "campos_no_evaluables": 0,
        "n_riesgos": 0, "n_adaptaciones": 0, "n_buenas_practicas": 0, "findings": []}


def _vista(name: str, campos: list[tuple]) -> str:
    rows = "".join(f"  - campo: {c}\n    tipo: {t}\n    longitud: '{l}'\n" for c, t, l in campos)
    return f"---\nauto:\n  nombre: {name}\n  campos:\n{rows}---\n# {name}\n"


def _flow(tmp_path: Path, manual: str = "", crlf: bool = False):
    d = tmp_path / "Cli" / "Dom"
    (d / "vistas").mkdir(parents=True)
    (d / "vistas" / "1TR_X.md").write_text(_vista("1TR_X", [("Importe1", "DecimalFloat", ""), ("CostCenter", "String", "10"), ("Created", "Timestamp", "")]), encoding="utf-8")
    (d / "vistas" / "4AM_X.md").write_text(_vista("4AM_X", [("Importe1", "DecimalFloat", ""), ("Bi", "hana.BINARY", "8")]), encoding="utf-8")
    flow = d / "flujo_Dom.md"
    text = f"---\nauto:\n  tipo: flujo\n{manual}---\n# Flujo\n"
    flow.write_text(text.replace("\n", "\r\n") if crlf else text, encoding="utf-8", newline="")
    return SimpleNamespace(path=str(flow), artefactos=["1TR_X", "4AM_X"], entry_id="cli-dom", portabilidad_bdc=copy.deepcopy(PORT), portabilidad_ack=[], hallazgos=[], body="")


def _render(view):
    return templates.env.from_string('{% from "' + MACROS + '" import compatibility_block %}{{ compatibility_block(c, "x") }}').render(c=view)


DECLARE_RF = "manual:\n  publicacion_bdc:\n  - mecanismo: replication_flow\n    destino: local_table_file\n"
DECLARE_DP_ITEM = "  - mecanismo: data_product_delta_share\n    destino: custom_data_product\n"
DECLARE_DP = "manual:\n  publicacion_bdc:\n" + DECLARE_DP_ITEM


# ── derivación en lectura ────────────────────────────────────────────────────────────────────────────────────────────
def test_without_a_declared_destination_the_view_is_conditioned_with_the_questions(tmp_path):
    v = svc.view_for_entry(_flow(tmp_path))
    assert v["declared"] is False and v["state"]["label"] == "Compatibilidad condicionada al destino" and v["state"]["tone"] == "neutra"
    assert len(v["scenarios"]) >= 4 and v["questions"] and "destino no declarado" in v["summary"]
    assert [t["tipo"] for t in v["inventory"]] == ["DecimalFloat", "String", "Timestamp", "hana.BINARY"][:0] or {t["tipo"] for t in v["inventory"]} == {"DecimalFloat", "String", "Timestamp", "hana.BINARY"}
    assert next(t for t in v["inventory"] if t["tipo"] == "DecimalFloat")["columnas"] == 2


def test_a_declared_destination_narrows_the_view_and_reads_it_from_the_flow_manual_block(tmp_path):
    v = svc.view_for_entry(_flow(tmp_path, DECLARE_RF))
    assert v["declared"] is True and [s["id"] for s in v["scenarios"]] == ["ESC-RF-LTF"] and "Replication Flow → Local Table (File)" in v["context"]
    ids = {r["id"] for r in v["scenarios"][0]["rows"]}
    assert "SAPC-RFLTF-001" in ids and not any(r["id"] == "SAPC-DP-001" for r in v["scenarios"][0]["rows"])
    conv = next(r for r in v["scenarios"][0]["rows"] if r["id"] == "SAPC-RFLTF-001")
    assert (conv["state_label"], conv["validation_label"], conv["converts_to"]) == ("Conversión documentada", "SAP documentado", "DECIMAL(38,6)")


def test_crlf_front_matter_is_read_the_same(tmp_path):
    assert svc.view_for_entry(_flow(tmp_path, DECLARE_RF, crlf=True))["declared"] is True


def test_data_product_scenario_flags_an_explicit_incompatibility_with_its_sap_source(tmp_path):
    manual = "manual:\n  publicacion_bdc:\n    mecanismo: data_product_delta_share\n    destino: custom_data_product\n"
    v = svc.view_for_entry(_flow(tmp_path, manual))
    row = next(r for r in v["scenarios"][0]["rows"] if r["id"] == "SAPC-DP-001")
    assert row["state_label"] == "Incompatibilidad explícita" and row["validation_label"] == "SAP documentado" and row["sources"][0]["url"].startswith("https://help.sap.com/")
    assert v["state"]["label"] == "Incompatibilidad explícita"


def test_an_unrecognised_declaration_is_reported_and_treated_as_undeclared(tmp_path):
    v = svc.view_for_entry(_flow(tmp_path, "manual:\n  publicacion_bdc:\n  - mecanismo: carrier_pigeon\n    destino: local_table_file\n"))
    assert v["declared"] is False and v["invalid"] and v["state"]["label"].endswith("al destino")
    assert "Declaración no reconocida" in _render(v)


def test_evaluation_is_read_only_files_entry_and_historic_score_are_untouched(tmp_path):
    e = _flow(tmp_path, DECLARE_RF)
    files = sorted(Path(e.path).parent.rglob("*.md"))
    before = ({f: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}, copy.deepcopy(e.portabilidad_bdc), list(e.artefactos))
    svc.view_for_entry(e); svc.view_for_entry(e)
    assert ({f: hashlib.sha256(f.read_bytes()).hexdigest() for f in files}, e.portabilidad_bdc, e.artefactos) == before
    assert sorted(Path(e.path).parent.rglob("*.md")) == files                                      # no escribe nada (ni caché en disco)


def test_a_changed_declaration_is_picked_up_because_the_cache_is_keyed_by_the_catalog_file_mtime(tmp_path):
    e = _flow(tmp_path)
    assert svc.view_for_entry(e)["declared"] is False
    p = Path(e.path)
    marker = "---" + chr(10) + "# Flujo"
    p.write_text(p.read_text(encoding="utf-8").replace(marker, DECLARE_RF + marker), encoding="utf-8")
    import os
    os.utime(p, ns=(p.stat().st_atime_ns, p.stat().st_mtime_ns + 5_000_000_000))
    assert svc.view_for_entry(e)["declared"] is True


@pytest.mark.parametrize("breaker", ["no_vistas", "no_artefactos", "none_entry"])
def test_fail_soft_returns_none_and_never_raises(tmp_path, breaker):
    e = _flow(tmp_path)
    if breaker == "no_vistas":
        for f in (Path(e.path).parent / "vistas").glob("*.md"):
            f.unlink()
    elif breaker == "no_artefactos":
        e.artefactos = []
    else:
        e = None
    assert svc.view_for_entry(e) is None


# ── composición en asset_evaluation: el resto del contrato no cambia ─────────────────────────────────────────────────
def test_asset_evaluation_adds_the_axis_only_when_assessed_and_keeps_portability_identical(tmp_path):
    e = _flow(tmp_path)
    assert ae.asset_evaluation(object(), assessed=False) == {"governance": None, "portability": None}   # sin Assessment: nada, como siempre
    ev = ae.asset_evaluation(e, findings=[], assessed=True)
    assert set(ev) == {"governance", "portability", "compatibility"} and ev["compatibility"]["state"]["key"] == "condicionada_a_destino"
    assert ev["portability"] == ae.portability_view(e.portabilidad_bdc, [], "dominio")             # la portabilidad histórica es la MISMA
    assert ev["portability"]["tiles"][0] == {"value": "80/100", "label": "Score de portabilidad"}


# ── presentación ────────────────────────────────────────────────────────────────────────────────────────────────────
def test_rendered_block_uses_hub_primitives_shows_scenario_evidence_validation_and_support_links(tmp_path):
    html = _render(svc.view_for_entry(_flow(tmp_path, DECLARE_RF)))
    assert html.startswith('\n<details class="dashboard-section" id="x">') or '<details class="dashboard-section" id="x">' in html
    assert "<details" in html and " open" not in html.split("</summary>")[0]                       # colapsado por defecto
    assert "Compatibilidad con destinos BDC" in html and "Replication Flow → Local Table (File)" in html
    assert 'class="matrix-scroll"' in html and "catalog-card__verdict--buena" in html               # conversión documentada: tono aceptable, no error
    assert "SAP documentado" in html and "Validado Danwor" in html and "SAPC-RFLTF-001" in html
    assert 'class="support-links"' in html and html.count('class="support-link"') >= 2 and 'target="_blank"' in html
    assert "score" not in html.lower() or "sin puntaje" in html.lower()                              # el único «puntaje» mencionado es para negarlo
    assert re.search(r'href="/modules/03_Integracion_y_Estrategia_de_Datos\?pack=datasphere_core#del-tipo-del-runtime-al-tipo-del-contrato"', html)


def test_undeclared_block_shows_hypotheses_the_questions_callout_and_how_to_declare(tmp_path):
    html = _render(svc.view_for_entry(_flow(tmp_path)))
    assert "Compatibilidad condicionada al destino" in html and html.count("(hipótesis)") >= 4
    assert 'class="callout callout--punch"' in html and "Para completar el análisis" in html and "publicacion_bdc" in html
    assert "¿El dominio se publicará fuera de HANA" in html


def test_a_datasphere_supported_type_is_never_presented_as_an_architectural_error(tmp_path):
    v = svc.view_for_entry(_flow(tmp_path))
    labels = {r["state_label"] for s in v["scenarios"] for r in s["rows"]}
    assert "Compatibilidad condicionada" in labels and "Conversión documentada" in labels
    html = _render(v)
    assert "Válido en Datasphere" in html and "no es un error del modelo" in html
    assert not any(re.search(r"(?i)error|falla|riesgo", r["state_label"]) for s in v["scenarios"] for r in s["rows"])
    assert all(r["tone"] in ("neutra", "buena", "plena") for s in v["scenarios"] for r in s["rows"] if r["state"] in ("condicionada", "conversion_documentada"))


def test_dynamic_text_is_escaped(tmp_path):
    e = _flow(tmp_path, "manual:\n  publicacion_bdc:\n  - mecanismo: '<script>alert(1)</script>'\n    destino: local_table_file\n")
    html = _render(svc.view_for_entry(e))
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_empty_view_renders_nothing():
    assert _render(None).strip() == ""


def test_the_assessment_template_mounts_the_block_inside_the_portability_section_only():
    tpl = (Path(__file__).resolve().parents[2] / "webapp/templates/partials/catalog_assessment_report.html").read_text(encoding="utf-8")
    start = tpl.index('<section class="content-section" id="asr-portabilidad">')
    sec = tpl[start: tpl.index("[06] Casos a revisar", start)]
    assert 'compatibility_block(v.evaluation.compatibility, "asr-compatibilidad")' in sec and sec.count("</section>") == 1


# ── Catálogo · Mirada Danwor: observación editorial derivada del MISMO resultado ─────────────────────────────────────
GET_AH = SimpleNamespace(url="/catalog/flow/assessment?flow=x&pack=datasphere_core", action={"method": "get", "label": "Ver Assessment"})
POST_AH = SimpleNamespace(url="/catalog/flow/assessment?flow=x&pack=datasphere_core", action={"method": "post", "label": "Generar informe"})
UNDECLARED_TEXT = ("La compatibilidad de publicación depende del destino elegido. "
                   "El modelo contiene tipos de datos que pueden requerir adaptación para BDC.")
ENGINE_LABELS = ("Condicionada", "Compatibilidad condicionada", "Conversión documentada", "Requiere adaptación", "Incompatibilidad explícita", "Compatible", "No evaluable",
                 "SAPC-", "ESC-", "sap_documentado", "danwor_empirico", "pendiente_verificacion", "Falta declarar", "Compatibilidad con destinos BDC")


def _signal(view, ah=GET_AH):
    return templates.env.from_string('{% from "' + MACROS + '" import compatibility_signal %}{{ compatibility_signal(c, ah) }}').render(c=view, ah=ah)


def _flow_types(tmp_path, tipos, manual=""):
    """Flujo sintético con las columnas indicadas (tipo, longitud) en una sola vista."""
    e = _flow(tmp_path, manual)
    vistas = Path(e.path).parent / "vistas"
    for f in vistas.glob("*.md"):
        f.unlink()
    (vistas / "1TR_X.md").write_text(_vista("1TR_X", [(f"C{i}", t, l) for i, (t, l) in enumerate(tipos)]), encoding="utf-8")
    e.artefactos = ["1TR_X"]
    return e


def test_catalog_observation_without_destination_is_one_natural_sentence_with_no_engine_vocabulary(tmp_path):
    v = svc.view_for_entry(_flow_types(tmp_path, [("DecimalFloat", ""), ("Timestamp", ""), ("String", "10")]))
    assert v["signal"] == UNDECLARED_TEXT
    out = _signal(v)
    assert UNDECLARED_TEXT in out and out.count("<p") == 1 and out.count("<a ") == 1                      # una frase y UN enlace
    assert not any(label in out for label in ENGINE_LABELS)                                              # sin badge, ni título repetido, ni instrucción operativa
    assert "catalog-card__verdict" not in out and "catalog-verdict-line" not in out and "callout" not in out and "<table" not in out and "activity-kpi" not in out


def test_catalog_observation_adapts_to_the_real_results_not_to_a_hardcoded_text(tmp_path):
    e = _flow_types(tmp_path, [("hana.BINARY", "8"), ("DecimalFloat", "")])
    assert "algunos destinos no admiten" in svc.view_for_entry(e)["signal"]                              # hay una incompatibilidad explícita posible
    softer = svc.view_for_entry(_flow_types(tmp_path / "b", [("DecimalFloat", "")]))["signal"]
    assert softer == UNDECLARED_TEXT and "Kensing" not in softer and "DecimalFloat" not in softer         # genérico: no nombra tipos ni cliente


def test_catalog_observation_is_contextual_and_silent_when_there_is_nothing_to_warn(tmp_path):
    clean = svc.view_for_entry(_flow_types(tmp_path, [("String", "10"), ("Decimal", "15,2"), ("Integer", ""), ("Binary", "16")]))
    assert clean is not None and clean["signal"] is None                                                  # el bloque del Assessment existe, la observación no
    assert _signal(clean).strip() == ""                                                                   # sin ruido en el Catálogo
    assert _signal(None).strip() == ""


def test_catalog_observation_with_a_declared_destination_concludes_on_the_effective_adaptations(tmp_path):
    v = svc.view_for_entry(_flow(tmp_path, DECLARE_RF))
    assert v["signal"] == ("Para Replication Flow → Local Table (File), los valores DecimalFloat se convierten a DECIMAL(38,6) al replicar "
                           "y los Timestamp deben almacenarse en UTC.")
    out = _signal(v)
    assert "depende del destino elegido" not in out and "Falta declarar" not in out                       # ya no habla del destino pendiente
    assert not any(label in out for label in ENGINE_LABELS)


def test_catalog_observation_declared_destination_with_no_applicable_adaptation_is_silent(tmp_path):
    e = _flow_types(tmp_path, [("String", "10"), ("DecimalFloat", "")], "manual:" + chr(10) + "  publicacion_bdc:" + chr(10) + "  - mecanismo: replication_flow" + chr(10) + "    destino: hana_local_table" + chr(10))
    v = svc.view_for_entry(e)
    assert v["declared"] and v["signal"] is None                                                          # destino HANA: tipos nativos, nada que advertir


def test_catalog_observation_names_each_declared_scenario_with_its_own_conclusion(tmp_path):
    v = svc.view_for_entry(_flow(tmp_path, DECLARE_RF + DECLARE_DP_ITEM))
    assert v["signal"].count("Para ") == 2 and "Para Data Product Delta Share (BDC), " in v["signal"]
    assert "los tipos hana.BINARY no se admiten en un Data Product" in v["signal"]
    assert not any(label in _signal(v) for label in ENGINE_LABELS)


def test_catalog_observation_comes_from_the_same_result_as_the_assessment_block(tmp_path):
    v = svc.view_for_entry(_flow(tmp_path, DECLARE_DP))
    assert any(r["id"] == "SAPC-DP-001" for s in v["scenarios"] for r in s["rows"]) and "hana.BINARY" in v["signal"]
    assert "SAPC-DP-001" in _render(v)                                                                    # el detalle técnico vive solo en el Assessment


def test_every_constraint_has_a_natural_language_synthesis_without_engine_vocabulary():
    from tools.catalog import compat_bdc
    for r in compat_bdc.load_compatibility("datasphere_core")["restricciones"]:
        assert r["sintesis"] and r["sintesis"][0].islower() and not any(label in r["sintesis"] for label in ENGINE_LABELS), r["id"]
        if len(r["tipos"]) > 1:
            assert "{tipos}" in r["sintesis"], r["id"]                                                    # varios tipos posibles: se nombran los realmente afectados


def test_catalog_link_goes_to_the_compatibility_diagnosis_of_the_assessment_only_when_it_exists(tmp_path):
    v = svc.view_for_entry(_flow_types(tmp_path, [("DecimalFloat", "")]))
    out = _signal(v)
    assert 'class="reference-text-link"' in out and f'hx-get="{html.escape(GET_AH.url)}"' in out and 'hx-target="#catalog-detail-slot"' in out
    assert "Ver detalle en el Assessment →" in out and out.count("hx-get") == 1                            # un único enlace discreto
    assert 'hx-swap="innerHTML show:#asr-compatibilidad:top"' in out                                      # aterriza en el bloque del informe
    assert "getElementById('asr-compatibilidad')" in out.replace("&#39;", "'") and "setAttribute('open'" in out.replace("&#39;", "'")   # y lo deja abierto
    assert 'id="asr-compatibilidad"' in templates.env.from_string('{% from "' + MACROS + '" import compatibility_block %}{{ compatibility_block(c, "asr-compatibilidad") }}').render(c=v)
    for ah in (POST_AH, None):
        no_link = _signal(v, ah)
        assert "hx-get" not in no_link and UNDECLARED_TEXT in no_link                                    # sin Assessment no hay enlace; la observación igual informa


def test_the_catalog_flow_template_mounts_only_the_prose_observation_inside_the_existing_portability_disclosure():
    root = Path(__file__).resolve().parents[2] / "webapp/templates/partials"
    flow = (root / "catalog_flow_content.html").read_text(encoding="utf-8")
    start = flow.index('id="eval-portabilidad"')
    disclosure = flow[start: flow.index("</details>", start)]
    assert "compatibility_signal(ev.compatibility, ah)" in disclosure
    assert disclosure.index("compatibility_signal") < disclosure.index("evaluation_table(por.rows")      # entre la cobertura y la tabla histórica
    assert "compatibility_block" not in flow and flow.count("compatibility_signal(") == 1               # sin segundo bloque/tabla de restricciones
    macros = (root / "assessment_report_macros.html").read_text(encoding="utf-8")
    sig = macros[macros.index("{% macro compatibility_signal"): macros.index("{% endmacro %}", macros.index("{% macro compatibility_signal"))]
    assert "catalog-card__verdict" not in sig and "catalog-verdict-line" not in sig and "callout" not in sig   # sin badge, sin línea de estado, sin callout
    report = (root / "catalog_assessment_report.html").read_text(encoding="utf-8")
    assert report.count('compatibility_block(v.evaluation.compatibility, "asr-compatibilidad")') == 1
