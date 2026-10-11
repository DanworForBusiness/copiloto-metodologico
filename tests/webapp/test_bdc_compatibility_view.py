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
def test_asset_evaluation_adds_the_axis_only_when_assessed_and_keeps_the_score_tiles(tmp_path):
    e = _flow(tmp_path)
    assert ae.asset_evaluation(object(), assessed=False) == {"governance": None, "portability": None}   # sin Assessment: nada, como siempre
    ev = ae.asset_evaluation(e, findings=[], assessed=True)
    assert set(ev) == {"governance", "portability", "compatibility"} and ev["compatibility"]["state"]["key"] == "condicionada_a_destino"
    base = ae.portability_view(e.portabilidad_bdc, [], "dominio")
    assert ev["portability"]["tiles"] == base["tiles"] and ev["portability"]["classification"] == base["classification"]      # score, confianza y KPI: los del scoring
    assert ev["portability"]["tiles"][0]["value"] == "80/100" and ev["portability"]["tiles"][0]["label"] == "Consistencia de tipos"
    assert len(ev["portability"]["rows"]) == len(base["rows"]) + 1 and ev["portability"]["n_compat"] >= 1                      # una sola fila de descubrimiento (sin destino declarado)


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


# ── Catálogo / Assessment: la compatibilidad converge en la tabla EXISTENTE (filas del mismo patrón) ────────────────────────
ENGINE_LABELS = ("SAPC-", "ESC-", "sap_documentado", "danwor_empirico", "pendiente_verificacion", "compatibilidad_bdc")
V2 = {"version": "portability-scoring/v2", "score": 100, "clasificacion": "plena", "semaforo": "green", "confianza": "alta", "campos_revisados": 4, "campos_no_evaluables": 0,
      "n_riesgos": 0, "n_adaptaciones": 0, "n_buenas_practicas": 0, "findings": [], "campos_distintos": {"riesgos": 0, "indicios": 0, "normalizaciones": 0},
      "clase_evidencia": "ejercida_sin_hallazgos", "evidencia": {"derivadas_numericas": 2, "relaciones_fk_resueltas": 3, "ejercida": True},
      "confianza_detalle": {"cobertura": 1.0, "sensible_a_heuristicas": False, "evidencia_ejercida": True, "derivadas_numericas": 2, "relaciones_fk_resueltas": 3}}


def _flow_types(tmp_path, tipos, manual=""):
    """Flujo sintético con las columnas indicadas (tipo, longitud) en una sola vista."""
    e = _flow(tmp_path, manual)
    vistas = Path(e.path).parent / "vistas"
    for f in vistas.glob("*.md"):
        f.unlink()
    (vistas / "1TR_X.md").write_text(_vista("1TR_X", [(f"C{i}", t, l) for i, (t, l) in enumerate(tipos)]), encoding="utf-8")
    e.artefactos = ["1TR_X"]
    return e


def _rows(tmp_path, tipos=None, manual=""):
    e = _flow(tmp_path, manual) if tipos is None else _flow_types(tmp_path, tipos, manual)
    return svc.table_rows(svc.evaluate_entry(e))


def _table(rows):
    return templates.env.from_string('{% from "' + MACROS + '" import evaluation_table %}{{ evaluation_table(rows, ["Tipo", "Hallazgo", "Campos · Recomendación"]) }}').render(rows=rows)


def _bullets(row) -> list[str]:
    return [re.sub(r"<[^>]+>", "", b) for b in re.findall(r"<li>(.*?)</li>", str(row["evidence"]), re.S)]


def test_without_a_declared_destination_compat_is_ONE_discovery_row_not_one_per_restriction_and_scenario(tmp_path):
    rows = _rows(tmp_path, [("DecimalFloat", ""), ("DecimalFloat", ""), ("Timestamp", ""), ("Time", ""), ("String", "10")])
    assert len(rows) == 1 and rows[0]["label"] == "Condicionado por destino" and rows[0]["tone"] == "neutra"          # un hallazgo: hay tipos sujetos a restricciones según el destino
    r = rows[0]
    assert str(r["title"]) == "Tipos sujetos a restricciones según el mecanismo y el destino de publicación"
    ev = re.sub(r"<[^>]+>", " ", str(r["evidence"]))
    assert "4 columnas en 1 artefacto (4 campos distintos): DecimalFloat ×2, Time ×1, Timestamp ×1" in ev            # ALCANCE: una sola vez, tipos por conteo real
    assert "declarar el destino lo concreta" in ev and "Detalle y fuentes en el Assessment" in ev
    assert set(r["tipos"]) == {"DecimalFloat", "Timestamp", "Time"}
    assert not any(lbl in ev + str(r["title"]) for lbl in ENGINE_LABELS)                                              # sin ids internos del motor


def test_the_consolidated_row_preserves_each_mechanism_specific_consequence_with_its_basis(tmp_path):
    """Pampa-like: DecimalFloat + Time + Timestamp. Las consecuencias salen de las restricciones SAPC-* (no hay textos por tipo/cliente en el código)."""
    row = _rows(tmp_path, [("DecimalFloat", ""), ("Time", ""), ("Timestamp", "")])[0]
    b = _bullets(row)
    rf, tf, dp, shared = (next(x for x in b if x.startswith(p)) for p in ("Replication Flow → Local Table (File):", "Transformation Flow → Local Table (File):",
                                                                           "Data Product Delta Share (BDC):", "Replication Flow → Local Table (File), Transformation Flow"))
    assert "los valores DecimalFloat se convierten a DECIMAL(38,6) al replicar (SAP documentado)" in rf and "las columnas Time no se replican (SAP documentado)" in rf
    assert "los valores DecimalFloat requieren adaptación explícita a DECIMAL(38,6) (Validado Danwor)" in tf and "los tipos Time no figuran en la matriz de tipos soportados (Pendiente de verificación)" in tf
    assert "los tipos DecimalFloat y Time no figuran en la matriz de tipos soportados (Pendiente de verificación)" in dp
    assert shared.endswith("los Timestamp deben almacenarse en UTC (SAP documentado)") and "Data Product Delta Share (BDC): los Timestamp" in shared.replace(" y Data", " y Data")
    assert len(b) == 4                                                                                                 # nada más: sin viñetas equivalentes


def test_consequences_are_never_presented_as_universal_and_keep_documented_empirical_and_conditioned_apart(tmp_path):
    row = _rows(tmp_path, [("DecimalFloat", ""), ("Time", ""), ("Timestamp", "")])[0]
    for line in _bullets(row):
        assert re.match(r"(Replication Flow|Transformation Flow|Data Product Delta Share)", line), line                  # toda consecuencia nombra su mecanismo/destino
        assert re.search(r"\((SAP documentado|Validado Danwor|Pendiente de verificación)\)", line), line               # y su base de evidencia
    text = " ".join(_bullets(row))
    assert not re.search(r"(?i)en todos los (destinos|mecanismos)|siempre|cualquier destino", text)
    assert text.count("(SAP documentado)") >= 3 and text.count("(Validado Danwor)") == 1 and text.count("(Pendiente de verificación)") == 2


def test_equivalent_phrases_counts_and_examples_are_not_repeated_per_mechanism(tmp_path):
    row = _rows(tmp_path, [("DecimalFloat", ""), ("Time", ""), ("Timestamp", "")])[0]
    ev = str(row["evidence"])
    assert ev.count("columnas en") == 1 and ev.count("p. ej.") == 1 and ev.count("campos distintos") == 1                # el alcance se dice UNA vez
    assert len(set(_bullets(row))) == len(_bullets(row))
    assert ev.count("los Timestamp deben almacenarse en UTC") == 1                                                      # lo equivalente se une en una viñeta con todos los escenarios


def test_consequences_come_from_the_constraints_not_from_hardcoded_types(tmp_path):
    row = _rows(tmp_path, [("LargeString", ""), ("hana.ST_POINT", "")])[0]
    b = " ".join(_bullets(row))
    assert "las columnas hana.ST_POINT no se replican (SAP documentado)" in b                                           # el mismo mecanismo cubre cualquier tipo de la restricción SAPC-RFLTF-002
    assert "no figuran en la matriz de tipos soportados (Pendiente de verificación)" in b
    from tools.catalog import compat_bdc
    patterns = [re.compile("^" + ".+".join(re.escape(part) for part in r["sintesis"].split("{tipos}")) + "$") for r in compat_bdc.load_compatibility("datasphere_core")["restricciones"]]
    for line in _bullets(row):
        for clause in line.split(": ", 1)[1].split("; "):
            text = re.sub(r" \((SAP documentado|Validado Danwor|Pendiente de verificación)\)$", "", clause)
            assert any(p.match(text) for p in patterns), text                                                          # cada frase es exactamente una `sintesis` del YAML (con los tipos afectados)


def test_affected_columns_are_deduplicated_by_technical_identity_preserving_artifact_and_column(tmp_path):
    e = _flow(tmp_path)                                                    # `Importe1` DecimalFloat existe en DOS artefactos distintos (1TR_X y 4AM_X)
    row = svc.table_rows(svc.evaluate_entry(e))[0]
    ev = str(row["evidence"]).replace("<wbr>", "")      # <wbr> solo permite partir identificadores largos
    assert "DecimalFloat ×2" in ev and "2 artefactos" in ev and "1TR_X.Importe1" in ev                                   # misma columna en 2 artefactos = 2 identidades; se conserva artefacto.columna
    res = svc.evaluate_entry(e)
    hits = [h for s in res["escenarios"] for h in s["resultados"] if "DecimalFloat" in h["tipos"]]
    assert len(hits) >= 3                                                                                               # la misma columna aparece en varias restricciones/escenarios…
    assert sum(1 for t in row["tipos"] if t == "DecimalFloat") == 1 and ev.count("DecimalFloat ×") == 1                 # …y NO se suma: un solo conteo por tipo
    assert "(2 campos distintos)" not in ev or "Importe1" in ev


def test_each_type_counts_once_even_when_it_appears_in_many_scenarios(tmp_path):
    ts = svc.table_rows(svc.evaluate_entry(_flow_types(tmp_path, [("Timestamp", ""), ("Timestamp", "")])))[0]
    assert "2 columnas en 1 artefacto (2 campos distintos): Timestamp ×2" in str(ts["evidence"])                       # 3+ escenarios repiten la restricción UTC: el conteo no se triplica


def test_with_a_declared_destination_states_are_preserved_one_row_per_state(tmp_path):
    rows = _rows(tmp_path, [("DecimalFloat", ""), ("hana.BINARY", "8"), ("Timestamp", "")], DECLARE_RF + DECLARE_DP_ITEM)
    by = {r["label"]: r for r in rows}
    assert set(by) == {"Incompatibilidad", "Conversión documentada", "Condicionado por destino"} and len(rows) == 3        # un descubrimiento por ESTADO: no se mezclan
    inc, conv, cond = by["Incompatibilidad"], by["Conversión documentada"], by["Condicionado por destino"]
    assert str(inc["title"]) == "Tipos sin soporte en el destino declarado" and inc["tipos"] == ["hana.BINARY"] and inc["tone"] == "parcial"
    assert "Data Product Delta Share (BDC)" in str(inc["evidence"]) and "DecimalFloat" not in str(inc["evidence"])      # lo explícito no arrastra tipos solo condicionados
    assert conv["tipos"] == ["DecimalFloat"] and "Replication Flow → Local Table (File)" in str(conv["evidence"]) and "Data Product" not in str(conv["evidence"])
    assert "DecimalFloat" in cond["tipos"] and "Timestamp" in cond["tipos"] and "depende del destino" not in str(cond["title"]).lower()
    assert not any("Incompatibilidad" == r["label"] and "Timestamp" in r["tipos"] for r in rows)


def test_a_single_declared_state_with_two_scenarios_is_one_row_with_the_scenario_breakdown(tmp_path):
    rows = _rows(tmp_path, [("Timestamp", "")], DECLARE_RF + DECLARE_DP_ITEM)
    assert len(rows) == 1 and rows[0]["label"] == "Condicionado por destino"
    ev = str(rows[0]["evidence"])
    assert ev.count("los Timestamp deben almacenarse en UTC") == 1 and "Replication Flow → Local Table (File) y Data Product Delta Share (BDC)" in ev   # mismo texto, un solo desglose


def test_the_assessment_detail_does_not_repeat_what_was_already_detailed_and_keeps_traceability(tmp_path):
    v = svc.view_for_entry(_flow_types(tmp_path, [("DecimalFloat", ""), ("Timestamp", "")]))
    rows = [(s["name"], r) for s in v["scenarios"] for r in s["rows"]]
    repeats = [(n, r) for n, r in rows if r["repeat_of"]]
    assert repeats and all(r["repeat_of"] != n for n, r in repeats)                                                   # se remite a un escenario DISTINTO
    first = {r["id"]: n for n, r in rows if not r["repeat_of"]}
    assert all(first[r["id"]] == r["repeat_of"] for _n, r in repeats)                                                 # siempre al escenario donde SÍ se detalló
    full = [r for _n, r in rows if not r["repeat_of"]]
    assert len({(r["id"], r["state"]) for r in full}) == len(full)                                                    # cada restricción+estado se detalla UNA sola vez
    html_ = _render(v)
    assert html_.count("Mismo resultado que en «") == len(repeats) and "(detalle arriba)" in html_
    for _n, r in repeats:
        assert f"<code>{r['id']}</code>" in html_                                                                    # el id SAPC se conserva en la fila compacta
    assert html_.count('class="support-link"') >= 2 and html_.count("SAP Help Tipos Local Table File") == 1           # Support Links intactos (una sola barra de fuentes)
    assert 'href="/modules/03_Integracion_y_Estrategia_de_Datos?pack=datasphere_core#del-tipo-del-runtime-al-tipo-del-contrato"' in html_


def test_the_detail_keeps_different_states_in_full_when_a_destination_is_declared(tmp_path):
    v = svc.view_for_entry(_flow_types(tmp_path, [("DecimalFloat", ""), ("hana.BINARY", "8")], DECLARE_RF + DECLARE_DP_ITEM))
    by_id = {}
    for s in v["scenarios"]:
        for r in s["rows"]:
            by_id.setdefault(r["id"], []).append((s["name"], r["state"], bool(r["repeat_of"])))
    for rid, apariciones in by_id.items():
        estados = {a[1] for a in apariciones}
        assert len({(a[1]) for a in apariciones if not a[2]}) == len(estados)                                          # cada estado distinto se detalla completo al menos una vez


def test_compat_rows_do_not_add_to_the_risk_counters_or_the_score(tmp_path):
    rows = _rows(tmp_path, [("DecimalFloat", ""), ("Timestamp", "")])
    base = ae.portability_view(V2, [], "dominio")
    both = ae.portability_view(V2, [], "dominio", compat_rows=rows)
    assert [t for t in both["tiles"] if t["label"] != "Confianza"] == [t for t in base["tiles"] if t["label"] != "Confianza"]
    assert next(t for t in both["tiles"] if t["label"] == "Riesgos")["value"] == 0 and both["tiles"][0]["value"] == "100/100"
    assert both["summary"] == base["summary"] + " · 2 tipos sujetos a restricciones por destino"                      # tipos DISTINTOS: ni filas ni escenarios
    assert len(both["rows"]) == 1 and both["n_compat"] == 2
    one = ae.portability_view(V2, [], "dominio", compat_rows=_rows(tmp_path / "u", [("Timestamp", "")]))
    assert one["summary"].endswith("· 1 tipo sujeto a restricciones por destino")


def test_asset_evaluation_projects_the_same_compat_result_into_table_rows_and_detail_block(tmp_path):
    e = _flow_types(tmp_path, [("DecimalFloat", ""), ("Timestamp", "")])
    e.portabilidad_bdc = copy.deepcopy(V2)
    ev = ae.asset_evaluation(e, findings=[], assessed=True)
    assert ev["portability"]["n_compat"] == 2 and ev["compatibility"]["scenarios"] and len(ev["portability"]["rows"]) == 1
    assert [r["label"] for r in ev["portability"]["rows"]] == ["Condicionado por destino"]
    assert ae.asset_evaluation(object(), assessed=False) == {"governance": None, "portability": None}


def test_compat_rows_carry_a_working_methodological_reference_link(tmp_path):
    row = _rows(tmp_path, [("DecimalFloat", "")])[0]
    assert row["reference_url"] == "/modules/03_Integracion_y_Estrategia_de_Datos?pack=datasphere_core#del-tipo-del-runtime-al-tipo-del-contrato"
    assert row["reference_text"].startswith("Módulo 03") and "Modelo de Integración Híbrido" in row["reference_text"]
    html_ = _table([row])
    assert 'class="reference-text-link"' in html_ and f'href="{row["reference_url"]}"' in html_ and 'target="_blank"' in html_
    assert "Referencia: <em>" not in html_ and html_.count("reference-text-link") == 1                                      # un único enlace discreto, reutilizando el componente


def test_clean_types_produce_no_compat_rows_and_the_table_is_unchanged(tmp_path):
    assert _rows(tmp_path, [("String", "10"), ("Decimal", "15,2"), ("Integer", ""), ("Binary", "16")]) == []
    legacy = ae.portability_view(PORT, [], "dominio", compat_rows=[])
    assert legacy["rows"] == ae.portability_view(PORT, [], "dominio")["rows"] == []


def test_kpis_count_distinct_fields_and_keep_occurrences_in_the_detail():
    port = {**V2, "score": 80, "n_riesgos": 6, "n_adaptaciones": 5, "campos_distintos": {"riesgos": 2, "indicios": 3, "normalizaciones": 1},
            "findings": [{"regla": "PB-005B", "titulo": "FK más ancha", "artefacto": a, "campo": c, "tipo_hallazgo": "riesgo", "semaforo": "orange", "recomendacion": "Revisar"}
                         for a in ("A1", "A2", "A3") for c in ("FK1", "FK2")]}
    v = ae.portability_view(port, [], "dominio")
    tiles = {t["label"]: t["value"] for t in v["tiles"]}
    assert tiles["Riesgos"] == 2 and tiles["Normalizaciones / indicios"] == 4                                                # campos distintos, no ocurrencias
    row = v["rows"][0]
    assert str(row["title"]).startswith("2 campos · ") and "(6 ocurrencias en 3 artefactos)" in str(row["evidence"])      # el detalle conserva las ocurrencias y la trazabilidad
    assert v["summary"].startswith("Consistencia 80/100 · confianza alta · 2 riesgos")


def test_legacy_persisted_results_are_flagged_and_still_render():
    v = ae.portability_view(PORT, [], "dominio")                                                                              # sin `version`: resultado v1 persistido
    assert v["legacy"] is True and "Evaluado con reglas anteriores (v1)" in v["coverage"] and v["tiles"][0]["value"] == "80/100"
    assert "reglas anteriores (v1)" in next(t for t in v["tiles"] if t["label"] == "Confianza")["help"][0]
    assert ae.portability_view(V2, [], "dominio")["legacy"] is False


def test_confidence_help_states_what_evidence_backs_it_and_is_not_about_the_destination():
    help_ = next(t for t in ae.portability_view(V2, [], "dominio")["tiles"] if t["label"] == "Confianza")["help"]
    text = " ".join(help_)
    assert "no mide la compatibilidad con un destino" in text and "Cobertura de metadata: 100 %" in text
    assert "2 campos numéricos derivados" in text and "3 relaciones FK resueltas" in text
    no_ev = {**V2, "confianza": "media", "clase_evidencia": "no_ejercida", "confianza_detalle": {**V2["confianza_detalle"], "evidencia_ejercida": False, "derivadas_numericas": 0, "relaciones_fk_resueltas": 0}}
    assert "las reglas estructurales no tuvieron entrada" in " ".join(next(t for t in ae.portability_view(no_ev, [], "dominio")["tiles"] if t["label"] == "Confianza")["help"])
    sens = {**V2, "confianza_detalle": {**V2["confianza_detalle"], "sensible_a_heuristicas": True}}
    assert "La banda cambia al retirar los indicios que dependen de nombres" in " ".join(next(t for t in ae.portability_view(sens, [], "dominio")["tiles"] if t["label"] == "Confianza")["help"])


def test_visible_labels_use_the_neutral_structural_vocabulary_and_never_the_internal_evidence_classes():
    visible = " ".join([*ae.PORT_CLASIF_LABEL.values(), ae.PORT_CLASIF_NOT_EXERCISED])                                          # etiquetas de BANDA
    assert "Adaptación" not in " ".join(ae.PORT_HALLAZGO_LABEL.values())                                                        # «Adaptación» queda para compat («Adaptación requerida»)
    for banned in ("Plena", "Buena", "Condicionada", "Inspiracional", "No recomendable", "E1", "E2", "E3", "no_ejercida", "ejercida"):
        assert banned not in visible, banned
    assert ae.PORT_CLASIF_LABEL["plena"] == "Sin hallazgos estructurales" and ae.PORT_HALLAZGO_LABEL["adaptacion"] == "Normalización detectada"
    not_ex = {**V2, "clase_evidencia": "no_ejercida", "confianza": "media"}
    assert ae.portability_view(not_ex, [], "dominio")["classification"] == "Sin elementos estructurales que evaluar"
    assert ae.portability_view(V2, [], "dominio")["classification"] == "Sin hallazgos estructurales"
    flat = str(ae.portability_view(not_ex, [], "dominio")) + str(ae.portability_view(V2, [], "dominio"))
    assert not re.search(r"\bE[123]\b", flat)                                                                                 # las siglas no son una clasificación visible


def test_the_catalog_and_the_assessment_render_the_same_unified_rows_and_the_prose_observation_is_gone():
    root = Path(__file__).resolve().parents[2] / "webapp/templates/partials"
    flow = (root / "catalog_flow_content.html").read_text(encoding="utf-8")
    report = (root / "catalog_assessment_report.html").read_text(encoding="utf-8")
    macros = (root / "assessment_report_macros.html").read_text(encoding="utf-8")
    assert "evaluation_table(por.rows" in flow and "evaluation_table(por.rows" in report                                      # MISMA tabla y MISMAS filas en ambos
    assert "compatibility_signal" not in flow + report + macros and "catalog_signal" not in (root.parents[1] / "services/bdc_compatibility_service.py").read_text(encoding="utf-8")
    assert "compatibility_block" not in flow and report.count('compatibility_block(v.evaluation.compatibility, "asr-compatibilidad")') == 1   # el detalle por escenario solo en el Assessment
    start = flow.index('id="eval-portabilidad"')
    assert flow.count("evaluation_table(por.rows", start) == 1


def test_every_constraint_has_a_natural_language_synthesis_without_engine_vocabulary():
    from tools.catalog import compat_bdc
    for r in compat_bdc.load_compatibility("datasphere_core")["restricciones"]:
        assert r["sintesis"] and r["sintesis"][0].islower() and not any(label in r["sintesis"] for label in ENGINE_LABELS), r["id"]
        if len(r["tipos"]) > 1:
            assert "{tipos}" in r["sintesis"], r["id"]                                                    # varios tipos posibles: se nombran los realmente afectados


def test_data_product_page_shares_the_catalog_vocabulary_and_semantics():
    from webapp import main
    not_ex = {**V2, "clase_evidencia": "no_ejercida", "confianza": "media", "n_adaptaciones": 4, "campos_distintos": {"riesgos": 0, "indicios": 0, "normalizaciones": 4}}
    md = main._catalog_portability_section_md(not_ex, "data_product", [])
    assert "Sin elementos estructurales que evaluar" in md and "Consistencia **100/100**" in md and "Confianza **Media**" in md
    assert "Score" not in md and "hint" not in md.lower().replace("indicio", "") and "**4** normalización/indicio" in md
    assert "los indicios no penalizan" in md and "reglas anteriores" not in md
    legacy = main._catalog_portability_section_md(PORT, "data_product", [])
    assert "reglas anteriores (v1)" in legacy and "Consistencia **80/100**" in legacy
    assert "Sin hallazgos estructurales" in main._catalog_portability_section_md(V2, "dominio", [])                                 # misma etiqueta de banda que Catálogo/Assessment


def test_consequence_list_is_styled_locally_and_inherits_the_cell_typography(tmp_path):
    row = _rows(tmp_path, [("DecimalFloat", ""), ("Time", ""), ("Timestamp", "")])[0]
    assert '<ul class="eval-cell-list">' in str(row["evidence"]) and str(row["evidence"]).count("<ul") == 1
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    rules = re.findall(r"([^{}/]*eval-cell-list[^{}]*)\{([^}]*)\}", css)
    assert rules and all(sel.strip().startswith(".methodology ul.eval-cell-list") for sel, _ in rules)       # nada global: todo selector pasa por la clase local
    assert any("font: inherit" in body and "color: inherit" in body for _s, body in rules)                   # misma familia/tamaño/peso/line-height/color que la celda
