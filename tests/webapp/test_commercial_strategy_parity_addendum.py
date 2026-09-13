"""
test_commercial_strategy_parity_addendum.py — Strategy Parity Addendum
(Pipeline Forecast changeset, ver HANDOFF): seis correcciones de paridad
sobre `02 · Estrategia` restringidas al scope explícitamente autorizado —
S1 (subtítulo Catálogo de oferta), S2 (color semántico Inbound/Outbound),
S3 (encabezado/subtítulo Catálogo de eventos), S4 (subtítulo Calendario),
S5 (jerarquía de la nota de Cadencia), S6 (Mapa de contactos colapsable).

Reusa los fixtures ya establecidos de `test_commercial_strategy.py`
(`_isolated_repo`/`_save_payload`/`owner_client`/`_with_owner`/`client`) en
vez de reconstruirlos — mismo patrón de auth/repositorio aislado que el
resto de la suite de escritura.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from tests.webapp.test_commercial_strategy import (
    _isolated_repo,
    _save_payload,
    _with_owner,
    client,
    owner_client,
)
from webapp.services import commercial_strategy_editing as editing

_STATIC = Path(__file__).resolve().parents[2] / "webapp" / "static"
_TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates"
_STRATEGY_HTML_SRC = (_TEMPLATES / "commercial" / "strategy.html").read_text(encoding="utf-8")
_STRATEGY_CSS = (_STATIC / "commercial_strategy.css").read_text(encoding="utf-8")
_SEED_PATH = Path(__file__).resolve().parents[2] / "webapp" / "services" / "commercial_strategy_data" / "strategy.yaml"


def _owner_post(payload: dict):
    return owner_client.post("/comercial/estrategia", json=payload, headers={"Origin": "http://testserver"})


# ============================================================================
# A — Documento runtime VIEJO (sin los tres campos nuevos) sigue cargando
# ============================================================================

def test_a_old_runtime_document_without_new_fields_still_loads(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    repo = _isolated_repo(tmp_path, monkeypatch)
    doc = yaml.safe_load(_SEED_PATH.read_text(encoding="utf-8"))
    # Simula un documento runtime persistido ANTES de este feature: borra los
    # tres campos nuevos por completo (ni siquiera existen como clave, el
    # caso más estricto — no sólo "vacíos").
    del doc["demand_generation"]["clusters_intro"]
    del doc["demand_generation"]["events_catalog_intro"]
    del doc["demand_generation"]["calendar_intro"]
    repo._runtime_path.parent.mkdir(parents=True, exist_ok=True)
    repo._runtime_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")

    res = owner_client.get("/comercial/estrategia")
    assert res.status_code == 200
    # Sin basura de Jinja Undefined en la salida (`Undefined`/`None` crudo).
    assert "Undefined" not in res.text
    # El resto del bloque sigue presente: no rompió media sección por faltar
    # un campo opcional en un dict anidado.
    assert "Catálogo de oferta" in res.text
    assert "Canales de demanda" in res.text


def test_a_old_runtime_document_edit_mode_does_not_crash(tmp_path, monkeypatch):
    # El campo nuevo, ausente en `doc`, debe poder editarse igual: `getPath`
    # devuelve `undefined` -> el macro cae a `''`, `setPath` crea la clave
    # sobre la marcha (mismo mecanismo ya usado por `intro`/`owner_note`,
    # ver el macro `inline_text`) — cero hidratación explícita necesaria.
    _with_owner(monkeypatch)
    repo = _isolated_repo(tmp_path, monkeypatch)
    doc = yaml.safe_load(_SEED_PATH.read_text(encoding="utf-8"))
    del doc["demand_generation"]["clusters_intro"]
    repo._runtime_path.parent.mkdir(parents=True, exist_ok=True)
    repo._runtime_path.write_text(yaml.safe_dump(doc, allow_unicode=True, sort_keys=False), encoding="utf-8")

    res = owner_client.get("/comercial/estrategia")
    assert res.status_code == 200
    assert "getPath('demand_generation.clusters_intro'" in res.text


def test_a_saving_over_an_old_document_backfills_the_new_field_safely():
    # El validador server-side (`_validate_demand_generation`) usa
    # `raw.get(key, "")` para los tres campos nuevos — un payload de cliente
    # que todavía no los manda (pestaña vieja) no rompe el Save, sólo persiste
    # `""` para ellos, igual que `intro`/`owner_note` siempre hicieron.
    doc = yaml.safe_load(_SEED_PATH.read_text(encoding="utf-8"))
    dg_raw = dict(doc["demand_generation"])
    del dg_raw["clusters_intro"]
    del dg_raw["events_catalog_intro"]
    del dg_raw["calendar_intro"]
    from webapp.services.commercial_strategy_editing import merge_editable_fields
    result = merge_editable_fields(
        {**{k: doc[k] for k in editing.EDITABLE_TOP_KEYS}, "demand_generation": dg_raw}, doc,
    )
    assert result["demand_generation"]["clusters_intro"] == ""
    assert result["demand_generation"]["events_catalog_intro"] == ""
    assert result["demand_generation"]["calendar_intro"] == ""


# ============================================================================
# B/C/D — Los tres subtítulos restaurados vienen de contenido ESTRUCTURADO
# ============================================================================

def test_b_clusters_intro_renders_from_structured_content_not_hardcoded():
    text = client.get("/comercial/estrategia").text
    assert "Lo que vendemos, con su ticket en horas, su decisor" in text
    # Nunca un string literal en el TEMPLATE — sólo la referencia al campo.
    assert "Lo que vendemos, con su ticket en horas" not in _STRATEGY_HTML_SRC
    assert "strategy.demand_generation.clusters_intro" in _STRATEGY_HTML_SRC


def test_c_events_catalog_heading_and_intro_render_from_structured_content():
    text = client.get("/comercial/estrategia").text
    assert "Catálogo de eventos que generan demanda" in text
    assert "Formatos repetibles, con su cadencia anual" in text
    # El ENCABEZADO es chrome code-owned (mismo criterio que "Canales de
    # demanda"/"Calendario", ya hardcodeados en este mismo archivo desde
    # siempre) — el SUBTÍTULO, en cambio, es contenido de negocio y viene
    # del documento.
    assert '<div class="cs-block-label content-block">Catálogo de eventos que generan demanda</div>' in _STRATEGY_HTML_SRC
    assert "strategy.demand_generation.events_catalog_intro" in _STRATEGY_HTML_SRC
    assert "Formatos repetibles, con su cadencia anual" not in _STRATEGY_HTML_SRC


def test_d_calendar_intro_renders_from_structured_content():
    text = client.get("/comercial/estrategia").text
    assert "La bajada mensual de la ventana de ejecución en curso" in text
    assert "strategy.demand_generation.calendar_intro" in _STRATEGY_HTML_SRC
    assert "La bajada mensual de la ventana de ejecución en curso" not in _STRATEGY_HTML_SRC


# ============================================================================
# E — Los tres subtítulos son editables y persisten por el Save canónico
# ============================================================================

def test_e_all_three_new_subtitles_are_editable_and_persist(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["demand_generation"]["clusters_intro"] = "Nuevo subtítulo de oferta (test)."
    payload["demand_generation"]["events_catalog_intro"] = "Nuevo subtítulo de eventos (test)."
    payload["demand_generation"]["calendar_intro"] = "Nuevo subtítulo de calendario (test)."
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    saved = res.json()["document"]["demand_generation"]
    assert saved["clusters_intro"] == "Nuevo subtítulo de oferta (test)."
    assert saved["events_catalog_intro"] == "Nuevo subtítulo de eventos (test)."
    assert saved["calendar_intro"] == "Nuevo subtítulo de calendario (test)."
    from webapp.services.commercial_strategy_repository import load_strategy
    reloaded = load_strategy()["demand_generation"]
    assert reloaded["clusters_intro"] == "Nuevo subtítulo de oferta (test)."
    # Sobrevive un GET posterior (mismo doc, sin re-fabricar).
    text = owner_client.get("/comercial/estrategia").text
    assert "Nuevo subtítulo de oferta (test)." in text


# ============================================================================
# F — Inbound/Outbound: modificador semántico distinto, no colores sueltos
# ============================================================================

def test_f_inbound_and_outbound_expose_distinct_semantic_modifier_classes():
    text = client.get("/comercial/estrategia").text
    assert 'class="cs-chan cs-chan--inbound"' in text
    assert 'class="cs-chan cs-chan--outbound"' in text
    # Contrato: reglas dedicadas por modificador, nunca compartiendo el
    # mismo valor de color (eso sería "sin distinción").
    assert ".cs-chan--inbound {" in _STRATEGY_CSS
    assert ".cs-chan--outbound {" in _STRATEGY_CSS
    inbound_rule = _STRATEGY_CSS.split(".cs-chan--inbound {", 1)[1].split("}", 1)[0]
    outbound_rule = _STRATEGY_CSS.split(".cs-chan--outbound {", 1)[1].split("}", 1)[0]
    assert inbound_rule.strip() != outbound_rule.strip()
    # No se filtra a otras tarjetas de la vertical (clusters, meetings, kpis).
    assert "cs-cluster--inbound" not in _STRATEGY_CSS
    assert "cs-kpi--inbound" not in _STRATEGY_CSS


def test_f_edit_mode_channel_cards_keep_the_same_semantic_modifier():
    # El modificador debe sobrevivir al toggle VIEW/EDIT — no es sólo un
    # adorno de la vista de sólo lectura.
    edit_block = _STRATEGY_HTML_SRC.split('x-show="editing" x-cloak>\n          {% for key in ["inbound", "outbound"] %}', 1)[1][:400]
    assert 'cs-chan cs-chan--{{ key }}' in edit_block


def test_f_semantic_tokens_are_theme_valid_light_and_dark():
    # `--gold` es global (mismo valor en los dos temas en toda la vertical,
    # ver docstring del archivo) y el verde reusa LITERAL el mismo que ya usa
    # el Hub para este significado (`.activity-kpi:nth-child(2)`, tools.css)
    # sin una variante oscura propia — porque esa tampoco la tiene ahí.
    tools_css = (_STATIC / "tools.css").read_text(encoding="utf-8")
    assert "#2e9e5b" in tools_css, "el verde reusado debe ser el MISMO que ya define el Hub, no uno inventado"
    assert "var(--gold)" in _STRATEGY_CSS.split(".cs-chan--outbound {", 1)[1].split("}", 1)[0]


# ============================================================================
# G — Nota de Cadencia: jerarquía secundaria, texto sin cambios
# ============================================================================

_CADENCE_NOTE_TEXT = ("Las decisiones que requieren al socio se toman en la reunión semanal de los "
                      "lunes, no en la quincenal")


def test_g_cadence_note_text_is_byte_for_byte_unchanged():
    doc = yaml.safe_load(_SEED_PATH.read_text(encoding="utf-8"))
    assert doc["cadence"]["note"].rstrip(".") == _CADENCE_NOTE_TEXT


def test_g_cadence_note_carries_secondary_note_semantics():
    text = client.get("/comercial/estrategia").text
    assert 'class="cs-note"' in text
    assert _CADENCE_NOTE_TEXT in text
    assert ".cs-note {" in _STRATEGY_CSS
    note_rule = _STRATEGY_CSS.split(".cs-note {", 1)[1].split("}", 1)[0]
    assert "italic" in note_rule
    assert "var(--soft)" in note_rule, "reusa el MISMO token muted ya usado por .cs-cluster__msg/.cs-role__mision"


def test_g_edit_mode_note_field_keeps_the_same_secondary_class():
    assert "inline_text('cadence.note', tag='p', cls='cs-note'" in _STRATEGY_HTML_SRC


# ============================================================================
# H/I — Mapa de contactos: disclosure independiente por rama mayor
# ============================================================================

def test_h_both_contact_map_roots_have_their_own_independent_details_element():
    text = client.get("/comercial/estrategia").text
    # Dos `<details ... open>` de disclosure real (nativos), uno por rama
    # mayor — nunca un único toggle compartido gobernando ambas.
    assert text.count('<details class="dashboard-section" open>') == 2


def test_h_disclosure_reuses_the_existing_hub_primitive_not_a_custom_accordion():
    block = _STRATEGY_HTML_SRC.split('id="mapa"', 1)[1]
    assert block.count("dashboard-section__head") == 2
    assert block.count("dashboard-section__caret") == 2
    # Cero implementación de accordion propia (ningún `x-data`/`x-show` nuevo
    # gobernando el colapso de las ramas — el `<details>` nativo es la ÚNICA
    # fuente de estado de disclosure).
    mapa_section = block.split("</section>", 1)[0]
    assert "collapsed" not in mapa_section.lower()
    assert "x-data=\"{ open" not in mapa_section


def test_i_view_state_default_is_open_for_backwards_compatible_visibility():
    text = client.get("/comercial/estrategia").text
    assert text.count('<details class="dashboard-section" open>') == 2, (
        "ambas ramas mayores arrancan ABIERTAS — visibilidad idéntica a antes de este cambio"
    )


def test_i_collapsed_branch_child_nodes_remain_in_the_dom_always():
    # `<details>` sin JS oculta vía `content-visibility`/`display:none` nativo
    # del navegador, pero el CONTENIDO sigue en el DOM/HTML servido siempre
    # (nunca lazy-loaded) — los nombres de las ramas hijas están en la
    # respuesta del servidor sin importar el estado abierto/cerrado.
    text = client.get("/comercial/estrategia").text
    assert "Federico Echenique" in text or "Julián Grinstein" in text or "Fernando Oriolo" in text


# ============================================================================
# J/K — Edición/Save del árbol es independiente del estado de disclosure
# ============================================================================

def test_j_save_serializes_full_tree_data_including_nodes_under_collapsed_branches(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    # Edita un nodo NIETO (nivel más profundo del árbol SAP) — el que
    # quedaría "adentro" de una rama colapsada en la UI real.
    payload["annex_map"]["sap_tree"]["branches"][0]["children"][0]["desc"] = "Descripción editada en profundidad (test)."
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    saved_branch = res.json()["document"]["annex_map"]["sap_tree"]["branches"][0]["children"][0]
    assert saved_branch["desc"] == "Descripción editada en profundidad (test)."
    from webapp.services.commercial_strategy_repository import load_strategy
    reloaded = load_strategy()["annex_map"]["sap_tree"]["branches"][0]["children"][0]
    assert reloaded["desc"] == "Descripción editada en profundidad (test)."


def test_k_disclosure_open_state_is_never_part_of_the_editable_contract():
    # `open`/`collapsed`/`expanded` no son claves reconocidas por el
    # validador de `annex_map` en ningún nivel del árbol — disclosure es
    # 100% vista, cero persistencia de negocio.
    editing_src = (Path(__file__).resolve().parents[2] / "webapp" / "services"
                   / "commercial_strategy_editing.py").read_text(encoding="utf-8")
    tree_validator = editing_src.split("def _validate_tree_node(", 1)[1].split("\ndef ", 1)[0]
    for banned in ('"open"', "'open'", '"collapsed"', "'collapsed'", '"expanded"', "'expanded'"):
        assert banned not in tree_validator, banned


def test_k_the_details_open_attribute_is_static_markup_never_doc_bound():
    # El atributo `open` de los dos `<details>` de Mapa de contactos es un
    # LITERAL fijo en el template (siempre abierto al servir) — nunca
    # `:open="doc....` ni ningún binding Alpine, que lo volvería estado.
    block = _STRATEGY_HTML_SRC.split('id="mapa"', 1)[1].split("</section>", 1)[0]
    assert ':open="' not in block
    assert "x-model" not in block.split("dashboard-section__body", 1)[0] if "dashboard-section__body" in block else True
