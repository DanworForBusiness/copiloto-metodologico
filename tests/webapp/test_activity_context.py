"""
test_activity_context.py — Fundación de contexto de actividad (Batch A1).

Prueba, con directorios de log temporales aislados (nunca los runtime reales):
- el envelope aditivo scope/area en el sink (activity_service);
- que el saneo/redacción y el gate BASIC/VERBOSE NO cambiaron;
- la normalización canónica (activity_context.context_of / classify) para
  registros históricos (sin campos) y nuevos (con campos explícitos);
- que el lector (activity_analytics._read_events) adjunta scope/area a ambos.
"""
from __future__ import annotations

import json

from webapp.services import activity_service
from webapp.services import activity_analytics
from webapp.services import activity_context as ax


def _write_jsonl(tmp_path, day: str, records: list[dict]) -> None:
    (tmp_path / f"activity_{day}.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
    )


def _read_only_file(tmp_path) -> list[dict]:
    files = list(tmp_path.glob("activity_*.jsonl"))
    assert len(files) == 1, files
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines() if line.strip()]


# ── classify / context_of ────────────────────────────────────────────────────

def test_classify_known_families():
    assert ax.classify("login") == (ax.SCOPE_PLATFORM, ax.AREA_AUTH)
    assert ax.classify("module_selected") == (ax.SCOPE_COPILOT, ax.AREA_METHODOLOGY)
    assert ax.classify("assistant_query") == (ax.SCOPE_COPILOT, ax.AREA_ASSISTANT)
    assert ax.classify("academy_selected") == (ax.SCOPE_COPILOT, ax.AREA_ACADEMY)
    assert ax.classify("catalog_view") == (ax.SCOPE_COPILOT, ax.AREA_CATALOG)
    assert ax.classify("tree_outcome") == (ax.SCOPE_COPILOT, ax.AREA_METHODOLOGY)
    assert ax.classify("staffing_overview_error") == (ax.SCOPE_OPERATIONS, ax.AREA_STAFFING)


def test_classify_unknown_is_explicit_fallback_not_copilot():
    # Un evento sin evidencia NO se clasifica en silencio como copiloto.
    assert ax.classify("algo_que_no_existe") == (ax.SCOPE_UNKNOWN, ax.AREA_UNKNOWN)


def test_context_of_prefers_explicit_over_classification():
    # Registro nuevo con campos explícitos: se respetan tal cual.
    assert ax.context_of({"event": "login", "scope": "operations", "area": "staffing"}) == ("operations", "staffing")
    # Registro histórico sin campos: cae a la clasificación por event.
    assert ax.context_of({"event": "login"}) == (ax.SCOPE_PLATFORM, ax.AREA_AUTH)
    # Parcial (solo scope explícito) completa el área por clasificación.
    assert ax.context_of({"event": "login", "scope": "platform"}) == ("platform", ax.AREA_AUTH)


def test_every_emitted_event_name_is_classified():
    # Guardia: todo evento que un productor real emite debe tener clasificación
    # (no caer a unknown). Si se agrega un productor nuevo sin mapear, falla acá.
    emitted = [
        "login", "module_selected", "module_continue", "module_reached_end",
        "nav_jump", "link_external", "disclosure_toggle", "theme_change",
        "academy_selected", "assistant_query", "catalog_view", "catalog_regenerate",
        "tree_started", "tree_step", "tree_outcome",
        "staffing_overview_viewed", "staffing_overview_config_incomplete", "staffing_overview_error",
    ]
    for name in emitted:
        scope, area = ax.classify(name)
        assert scope != ax.SCOPE_UNKNOWN and area != ax.AREA_UNKNOWN, name


# ── module / event canónico / event_class (corrective A2 — Transversal
# Module + Event Semantics) ────────────────────────────────────────────────

def test_normalize_event_renames_copilot_module_selected():
    # test 10: histórico module_selected normaliza a module_viewed.
    event, module, operation = ax.normalize_event("module_selected")
    assert event == "module_viewed"
    assert module is None    # el module real viene del propio registro (payload), no se inyecta
    assert operation is None


def test_normalize_event_renames_staffing_viewed_with_injected_module():
    # test 11: histórico staffing_overview_viewed normaliza a module_viewed/assignments.
    event, module, operation = ax.normalize_event("staffing_overview_viewed")
    assert event == "module_viewed"
    assert module == "assignments"
    assert operation is None


def test_normalize_event_renames_staffing_errors_and_diagnostics_with_operation():
    # test 12: errores/diagnósticos históricos de Staffing normalizan a los
    # eventos canónicos + operation=initial/history.
    assert ax.normalize_event("staffing_overview_error") == ("data_load_failed", "assignments", "initial")
    assert ax.normalize_event("staffing_historial_error") == ("data_load_failed", "assignments", "history")
    assert ax.normalize_event("staffing_data_diagnostics") == ("data_load_diagnostics", "assignments", "initial")
    assert ax.normalize_event("staffing_historial_diagnostics") == ("data_load_diagnostics", "assignments", "history")


def test_normalize_event_renames_staffing_config_incomplete_with_operation():
    # Consistency check post-corrective: ambos `_config_incomplete` seguían
    # siendo productores CURRENT (no solo históricos) — normalizan al canónico
    # reusable `configuration_incomplete` + operation=initial/history. NO se
    # fuerza a `data_load_failed`: config incompleta no es un fallo de
    # Graph/transporte.
    assert ax.normalize_event("staffing_overview_config_incomplete") == (
        "configuration_incomplete", "assignments", "initial",
    )
    assert ax.normalize_event("staffing_historial_config_incomplete") == (
        "configuration_incomplete", "assignments", "history",
    )


def test_normalize_event_passthrough_for_unmapped_events():
    assert ax.normalize_event("catalog_view") == ("catalog_view", None, None)
    assert ax.normalize_event("mystery_event") == ("mystery_event", None, None)


def test_event_class_usage():
    # test 15/16/17: module_viewed, login y el resto del uso Copilot conocido.
    for name in (
        "login", "module_viewed", "module_continue", "module_reached_end",
        "nav_jump", "link_external", "disclosure_toggle", "theme_change",
        "assistant_query", "section_analyze", "academy_selected",
        "catalog_view", "catalog_regenerate", "tree_started", "tree_step", "tree_outcome",
    ):
        assert ax.event_class(name) == ax.EVENT_CLASS_USAGE, name


def test_event_class_diagnostic():
    # test 18/19/20: canónicos nuevos + históricos de Staffing normalizados.
    assert ax.event_class("data_load_failed") == ax.EVENT_CLASS_DIAGNOSTIC
    assert ax.event_class("data_load_diagnostics") == ax.EVENT_CLASS_DIAGNOSTIC
    assert ax.event_class("configuration_incomplete") == ax.EVENT_CLASS_DIAGNOSTIC
    for raw in (
        "staffing_overview_error", "staffing_historial_error",
        "staffing_data_diagnostics", "staffing_historial_diagnostics",
        "staffing_overview_config_incomplete", "staffing_historial_config_incomplete",
    ):
        canonical, _, _ = ax.normalize_event(raw)
        assert ax.event_class(canonical) == ax.EVENT_CLASS_DIAGNOSTIC, raw


def test_event_class_unknown_for_unrecognized_event():
    # test 21: un evento no reconocido nunca cae en "usage" en silencio.
    assert ax.event_class("quien_sabe_que_es_esto") == ax.EVENT_CLASS_UNKNOWN


def test_module_label_maps_assignments_and_falls_back_to_raw_key():
    assert ax.module_label(ax.MODULE_ASSIGNMENTS) == "Asignaciones"
    # Módulos de Copiloto (stems de contenido) no tienen entrada — se muestran
    # tal cual, nunca se renombran como parte de este corrective.
    assert ax.module_label("01_arquitectura_y_conectividad") == "01_arquitectura_y_conectividad"


# ── labels visibles de evento (USAGE DASHBOARD SEMANTIC CLEANUP) ─────────────

def test_event_label_maps_known_usage_events():
    assert ax.event_label("module_viewed") == "Vista de módulo"
    assert ax.event_label("assistant_query") == "Consulta al asistente"
    assert ax.event_label("catalog_view") == "Vista de catálogo"
    assert ax.event_label("tree_started") == "Inicio de recorrido"


def test_event_label_falls_back_to_raw_name_for_unmapped_event():
    # Un evento de uso sin label mapeado (o cualquier evento futuro todavía
    # sin clasificar) nunca rompe la UI — se muestra tal cual.
    assert ax.event_label("un_evento_nuevo_sin_label") == "un_evento_nuevo_sin_label"


# ── envelope del sink ────────────────────────────────────────────────────────

def test_new_event_persists_explicit_scope_area(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE")
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    activity_service.log_activity(
        "staffing_overview_viewed", user={"sub": "a@danwor.com", "name": "A"},
        scope="operations", area="staffing",
    )
    (rec,) = _read_only_file(tmp_path)
    assert list(rec.keys()) == ["ts_utc", "event", "user", "name", "scope", "area", "payload"]
    assert rec["scope"] == "operations" and rec["area"] == "staffing"
    assert rec["event"] == "staffing_overview_viewed"
    assert rec["user"] == "a@danwor.com" and rec["name"] == "A"
    assert rec["payload"] == {}


def test_new_event_persists_optional_module_as_top_level_field(monkeypatch, tmp_path):
    # test 1: el envelope NUEVO soporta `module` de primera clase (corrective
    # A2), aditivo entre `area` y `payload` — mismo criterio que scope/area.
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE")
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    activity_service.log_activity(
        "module_viewed", user={"sub": "a@danwor.com", "name": "A"},
        scope="operations", area="staffing", module="assignments",
    )
    (rec,) = _read_only_file(tmp_path)
    assert list(rec.keys()) == ["ts_utc", "event", "user", "name", "scope", "area", "module", "payload"]
    assert rec["module"] == "assignments"


def test_existing_callers_without_module_remain_compatible(monkeypatch, tmp_path):
    # test 2: callers que NO pasan module (todos los preexistentes) siguen
    # escribiendo el envelope sin ese campo — nunca se fuerza module=None.
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE")
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    activity_service.log_activity(
        "login", user={"sub": "a@danwor.com", "name": "A"}, scope="platform", area="auth",
    )
    (rec,) = _read_only_file(tmp_path)
    assert "module" not in rec
    assert list(rec.keys()) == ["ts_utc", "event", "user", "name", "scope", "area", "payload"]


def test_envelope_omits_context_when_not_passed(monkeypatch, tmp_path):
    # Backward-compat de forma: un caller que no pasa scope/area escribe el
    # envelope histórico de 5 campos (el lector normaliza su ausencia).
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE")
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    activity_service.log_activity("legacy_shape", user={"sub": "a@danwor.com"})
    (rec,) = _read_only_file(tmp_path)
    assert "scope" not in rec and "area" not in rec
    assert set(rec.keys()) == {"ts_utc", "event", "user", "name", "payload"}


def test_sanitization_and_redaction_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE")
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    long_normal = "x" * 5000
    long_answer = "y" * 5000
    activity_service.log_activity(
        "e", user={"sub": "a@danwor.com"}, scope="copilot", area="assistant",
        token="super-secret", note=long_normal, answer=long_answer,
    )
    (rec,) = _read_only_file(tmp_path)
    p = rec["payload"]
    assert p["token"] == "[redacted]"                       # clave sensible redactada
    assert p["note"].endswith("…[truncated]") and len(p["note"]) <= 4000 + len("…[truncated]")
    assert p["answer"] == long_answer                        # long-text: NO truncado (<20000)


def test_basic_verbose_gate_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)

    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "OFF")
    activity_service.log_activity("x", user={"sub": "a@danwor.com"}, scope="copilot", area="catalog")
    assert list(tmp_path.glob("activity_*.jsonl")) == []     # OFF: nada

    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "BASIC")
    activity_service.log_verbose("verbose_evt", user={"sub": "a@danwor.com"}, scope="copilot", area="assistant")
    assert list(tmp_path.glob("activity_*.jsonl")) == []     # BASIC: log_verbose no-op
    activity_service.log_activity("basic_evt", user={"sub": "a@danwor.com"}, scope="platform", area="auth")
    names = [r["event"] for r in _read_only_file(tmp_path)]
    assert names == ["basic_evt"]                            # BASIC: log_activity sí


# ── lector: normalización de históricos y nuevos ─────────────────────────────

def test_reader_normalizes_historical_and_new(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    _write_jsonl(tmp_path, "20260101", [
        # Históricos SIN scope/area (forma de 5 campos) — deben clasificarse.
        {"ts_utc": "2026-01-01T00:00:00+00:00", "event": "login", "user": "u@d.com", "name": "U", "payload": {}},
        {"ts_utc": "2026-01-01T00:01:00+00:00", "event": "module_selected", "user": "u@d.com", "name": "U", "payload": {"module": "01"}},
        {"ts_utc": "2026-01-01T00:02:00+00:00", "event": "academy_selected", "user": "u@d.com", "name": "U", "payload": {}},
        {"ts_utc": "2026-01-01T00:03:00+00:00", "event": "catalog_view", "user": "u@d.com", "name": "U", "payload": {"kind": "cds"}},
        {"ts_utc": "2026-01-01T00:04:00+00:00", "event": "staffing_overview_error", "user": "u@d.com", "name": "U", "payload": {}},
        {"ts_utc": "2026-01-01T00:05:00+00:00", "event": "quien_sabe", "user": "u@d.com", "name": "U", "payload": {}},
        # Nuevo CON scope/area explícitos — deben preservarse.
        {"ts_utc": "2026-01-01T00:06:00+00:00", "event": "staffing_overview_viewed", "user": "u@d.com", "name": "U", "scope": "operations", "area": "staffing", "payload": {}},
    ])
    events = activity_analytics._read_events("", "")
    # Keyed por RAW event (nunca por el canónico): module_selected y
    # staffing_overview_viewed normalizan AMBOS a "module_viewed" — colisionarían
    # si se indexara por evento canónico.
    by_raw = {e["raw_event"]: e for e in events}
    assert len(events) == 7                                   # 5-field históricos legibles
    assert (by_raw["login"]["scope"], by_raw["login"]["area"]) == ("platform", "auth")
    assert (by_raw["academy_selected"]["scope"], by_raw["academy_selected"]["area"]) == ("copilot", "academy")
    assert (by_raw["catalog_view"]["scope"], by_raw["catalog_view"]["area"]) == ("copilot", "catalog")
    assert (by_raw["staffing_overview_error"]["scope"], by_raw["staffing_overview_error"]["area"]) == ("operations", "staffing")
    assert (by_raw["quien_sabe"]["scope"], by_raw["quien_sabe"]["area"]) == ("unknown", "unknown")  # fallback explícito
    assert (by_raw["staffing_overview_viewed"]["scope"], by_raw["staffing_overview_viewed"]["area"]) == ("operations", "staffing")  # explícito preservado

    # test 9/10/13: raw_event se preserva íntegro (inspeccionable) aunque
    # `event` quede normalizado al canónico.
    assert by_raw["module_selected"]["event"] == "module_viewed"
    assert by_raw["module_selected"]["module"] == "01"          # legacy: viene de payload.module
    assert by_raw["module_selected"]["raw_event"] == "module_selected"

    # test 11: staffing_overview_viewed normaliza a module_viewed/assignments,
    # y esa key legacy resuelve al module_id canónico OP01 (MODULE IDENTIFIER
    # CONVENTION V2 — Fase 2) — raw_module preserva el valor legacy intacto.
    assert by_raw["staffing_overview_viewed"]["event"] == "module_viewed"
    assert by_raw["staffing_overview_viewed"]["raw_module"] == "assignments"
    assert by_raw["staffing_overview_viewed"]["module"] == "OP01"

    # test 12: el error histórico normaliza a data_load_failed +
    # operation=initial inyectada en payload (no estaba en el registro crudo).
    assert by_raw["staffing_overview_error"]["event"] == "data_load_failed"
    assert by_raw["staffing_overview_error"]["raw_module"] == "assignments"
    assert by_raw["staffing_overview_error"]["module"] == "OP01"
    assert by_raw["staffing_overview_error"]["payload"]["operation"] == "initial"

    # test 14: unknown sigue siendo unknown, nunca cae en usage/Copilot en silencio.
    assert by_raw["quien_sabe"]["event_class"] == ax.EVENT_CLASS_UNKNOWN
    assert by_raw["login"]["event_class"] == ax.EVENT_CLASS_USAGE
    assert by_raw["staffing_overview_error"]["event_class"] == ax.EVENT_CLASS_DIAGNOSTIC


# ── Batch A2: dashboard Danwor Hub-wide (scope/area) ──────────────────────────

def _seed_hub(monkeypatch, tmp_path):
    """Dataset mixto Hub-wide para los tests A2. Incluye plataforma, copiloto y
    operaciones; un evento HISTÓRICO de 5 campos (sin scope/area) y uno de scope
    desconocido, para probar normalización y fallback explícito."""
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    _write_jsonl(tmp_path, "20260301", [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "login", "user": "copi@d.com", "name": "Copi", "scope": "platform", "area": "auth", "payload": {}},
        {"ts_utc": "2026-03-01T10:01:00+00:00", "event": "module_selected", "user": "copi@d.com", "name": "Copi", "scope": "copilot", "area": "methodology", "payload": {"module": "01"}},
        {"ts_utc": "2026-03-01T10:02:00+00:00", "event": "assistant_query", "user": "copi@d.com", "name": "Copi", "scope": "copilot", "area": "assistant", "payload": {"question": "q", "conversation_id": "c1", "reply_id": "r1", "answer": "a", "api_cost": True}},
        {"ts_utc": "2026-03-01T10:03:00+00:00", "event": "tree_started", "user": "copi@d.com", "name": "Copi", "scope": "copilot", "area": "methodology", "payload": {"stem": "01", "tree_id": "t1", "tree_title": "Árbol 1"}},
        {"ts_utc": "2026-03-01T10:04:00+00:00", "event": "staffing_overview_viewed", "user": "ops@d.com", "name": "Ops", "scope": "operations", "area": "staffing", "payload": {}},
        # Histórico de 5 campos (sin scope/area): debe normalizar por clasificación.
        {"ts_utc": "2026-03-01T10:05:00+00:00", "event": "academy_selected", "user": "copi@d.com", "name": "Copi", "payload": {"page": "ACAD01"}},
        # Evento de scope desconocido (sin campos, nombre sin evidencia).
        {"ts_utc": "2026-03-01T10:06:00+00:00", "event": "mystery_event", "user": "x@d.com", "name": "X", "payload": {}},
    ])


def test_default_summary_is_hub_wide_including_operations(monkeypatch, tmp_path):
    # item 1/2: default incluye platform + copilot + operations (exclusión A1 retirada).
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "")
    scopes = {f["key"] for f in s["scope_facets"]}
    assert {"platform", "copilot", "operations"} <= scopes
    assert all(f["checked"] for f in s["scope_facets"])       # default: todo tildado
    assert all(f["checked"] for f in s["area_facets"])
    events = {r["event"] for r in s["event_counts"]}
    # module_selected (Copilot) y staffing_overview_viewed (Operations)
    # normalizan AMBOS a module_viewed (corrective A2) — uno solo, transversal.
    assert "module_viewed" in events
    assert "login" in events


def test_filter_scope_operations_returns_operations_only(monkeypatch, tmp_path):
    # item 3
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "", scopes=["operations"])
    assert {r["event"] for r in s["event_counts"]} == {"module_viewed"}
    assert s["copilot_present"] is False
    # Área sigue listando TODAS las opciones (mismo criterio que Ámbito,
    # estable) — las incompatibles quedan disabled en vez de desaparecer.
    by_key = {f["key"]: f for f in s["area_facets"]}
    assert by_key["staffing"]["checked"] and not by_key["staffing"]["disabled"]
    assert by_key["auth"]["disabled"] and not by_key["auth"]["checked"]
    assert by_key["methodology"]["disabled"] and not by_key["methodology"]["checked"]


def test_filter_scope_copilot_excludes_operations_preserves_copilot(monkeypatch, tmp_path):
    # item 4 + item 13 (Operations no contamina lentes Copilot)
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "", scopes=["copilot"])
    events = {r["event"] for r in s["event_counts"]}
    assert "module_viewed" in events
    assert {"assistant_query", "tree_started"} <= events
    assert s["copilot_present"] is True
    assert s["assistant_query_count"] == 1                    # lente Copilot correcta
    assert s["tree_started"]                                  # árboles presentes
    # module_viewed de este universo (solo Copiloto) debe ser el de Copiloto
    # ("01"), nunca el de Operations ("assignments") — filtrar por Ámbito
    # SÍ debe excluir la instancia de Operations aunque comparta nombre de evento.
    modules_in_universe = {m["key"] for m in s["available_modules"]}
    assert "01" in modules_in_universe
    assert "assignments" not in modules_in_universe


def test_filter_scope_platform_preserves_auth(monkeypatch, tmp_path):
    # item 5
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "", scopes=["platform"])
    assert {r["event"] for r in s["event_counts"]} == {"login"}
    by_key = {f["key"]: f for f in s["area_facets"]}
    assert by_key["auth"]["checked"] and not by_key["auth"]["disabled"]
    assert by_key["methodology"]["disabled"] and not by_key["methodology"]["checked"]
    assert by_key["staffing"]["disabled"] and not by_key["staffing"]["checked"]


def test_filter_area_staffing_returns_staffing_and_scope_facets_stay_stable(monkeypatch, tmp_path):
    # item 6 + item 14 (facets derivados del universo). El faceting es
    # UNIDIRECCIONAL (Ámbito → Área): elegir un Área nunca angosta ni hace
    # desaparecer opciones de Ámbito — si lo hiciera, destildar Ámbito podía
    # angostar Área, que a su vez volvía a angostar Ámbito y le hacía
    # desaparecer su propia opción, sin forma de volver a tildarla (bug
    # reportado por el owner). `scope_facets` siempre lista el universo
    # completo de ámbitos presentes.
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "", areas=["staffing"])
    assert {r["event"] for r in s["event_counts"]} == {"module_viewed"}
    # Ningún Ámbito desapareció ni quedó destildado solo por elegir Área=staffing
    # (Ámbito nunca fue tocado, sigue en su default de "todo tildado").
    assert {"copilot", "operations", "platform"} <= {f["key"] for f in s["scope_facets"]}
    assert all(f["checked"] for f in s["scope_facets"])


def test_scope_and_area_combine(monkeypatch, tmp_path):
    # item 7
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "", scopes=["copilot"], areas=["methodology"])
    assert {r["event"] for r in s["event_counts"]} == {"module_viewed", "tree_started"}


def test_historical_five_field_and_unknown(monkeypatch, tmp_path):
    # item 8 (histórico 5 campos normaliza) + item 9 (unknown explícito, no copilot)
    _seed_hub(monkeypatch, tmp_path)
    read = {e["event"]: (e["scope"], e["area"]) for e in activity_analytics._read_events("", "")}
    assert read["academy_selected"] == ("copilot", "academy")   # histórico clasificado
    assert read["mystery_event"] == ("unknown", "unknown")      # fallback explícito
    # USAGE DASHBOARD SEMANTIC CLEANUP: unknown NUNCA entra al read-model del
    # dashboard (event_counts/adopción), sin importar el filtro de Ámbito —
    # "unknown" es filtrable como Ámbito (scope_facets/area_facets siguen
    # ofreciéndolo), pero mystery_event (sin clasificar) queda afuera de
    # event_counts en los dos escenarios: elegido explícitamente o no.
    s = activity_analytics.build_summary("", "", scopes=["unknown"])
    assert {r["event"] for r in s["event_counts"]} == set()
    assert any(f["key"] == "unknown" for f in s["scope_facets"])
    s_cop = activity_analytics.build_summary("", "", scopes=["copilot"])
    assert "mystery_event" not in {r["event"] for r in s_cop["event_counts"]}


def test_date_user_module_filters_still_behave(monkeypatch, tmp_path):
    # item 10/11 (transversal, corrective A2): module ahora SÍ existe para
    # Operations ("assignments", canónico OP01 desde MODULE IDENTIFIER
    # CONVENTION V2 — Fase 2) — "module no absorbe Operations" pasa a
    # verificarse por VALOR de módulo (01 vs OP01), no por ausencia.
    _seed_hub(monkeypatch, tmp_path)
    # user filter — sin cambios de comportamiento, solo el nombre de evento normalizado.
    s_user = activity_analytics.build_summary("", "", user="ops@d.com")
    assert {r["event"] for r in s_user["event_counts"]} == {"module_viewed"}
    # module filter = "01" (Copilot) → excluye el module_viewed de Operations
    # (module="OP01"), aunque comparta el mismo NOMBRE de evento.
    s_mod = activity_analytics.build_summary("", "", module="01")
    # module="01" trae module_viewed (payload.module) Y tree_started (payload.stem
    # también resuelve a "01") — ambos pertenecen al mismo módulo de Copiloto.
    assert {r["event"] for r in s_mod["event_counts"]} == {"module_viewed", "tree_started"}
    assert s_mod["copilot_present"] is True
    assert s_mod["total_events"] == 2                          # module_viewed + tree_started, ambos de Copiloto ("01")
    # El facet de Módulo NO colapsa a la selección actual (mismo criterio que
    # Área con Ámbito): "OP01" sigue OFRECIDO como opción, aunque el
    # filtro activo (module="01") ya excluya sus eventos de `events`.
    module_keys = {m["key"] for m in s_mod["available_modules"]}
    assert {"01", "OP01"} <= module_keys


def test_module_filter_exposes_assignments_for_staffing(monkeypatch, tmp_path):
    # item 31/36/37/38: Ámbito=Operaciones + Área=Staffing con actividad real
    # → Módulo debe ofrecer "Asignaciones" (label) / "OP01" (key canónica,
    # MODULE IDENTIFIER CONVENTION V2 — Fase 2).
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "", scopes=["operations"], areas=["staffing"])
    modules = {m["key"]: m["label"] for m in s["available_modules"]}
    assert modules == {"OP01": "Asignaciones"}
    # El filtro usa la key CANÓNICA internamente (test 12, Fase 2) — "OP01",
    # no la legacy "assignments" (que ya no existe en `available_modules`).
    s_filtered = activity_analytics.build_summary("", "", scopes=["operations"], areas=["staffing"], module="OP01")
    assert s_filtered["total_events"] == 1
    assert s_filtered["selected_module"] == "OP01"


def test_invalid_module_selection_resets_deterministically(monkeypatch, tmp_path):
    # Mismo criterio determinístico que Área/Ámbito: una selección de Módulo
    # que ya no aplica tras cambiar Ámbito se descarta, no deja el dashboard
    # en un "0 eventos" silencioso ni rompe.
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "", scopes=["copilot"], module="assignments")
    assert s["selected_module"] == ""                            # "assignments" no aplica a Copiloto → se descarta
    assert s["total_events"] > 0                                 # no queda en 0 eventos por la selección inválida


def test_areas_active_kpi_counts_distinct_areas(monkeypatch, tmp_path):
    # test 27: Áreas activas es uso-driven (corrective A2) — "unknown" (área
    # del único evento no-usage, mystery_event) NO cuenta como área activa.
    _seed_hub(monkeypatch, tmp_path)
    s = activity_analytics.build_summary("", "")
    # auth, methodology, assistant, staffing, academy = 5 áreas de USO distintas
    # (unknown queda afuera: mystery_event es event_class=unknown, no usage).
    assert s["areas_active"] == 5


# ── USAGE DASHBOARD SEMANTIC CLEANUP ──────────────────────────────────────────

def _seed_usage_only(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    _write_jsonl(tmp_path, "20260301", [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "module_viewed", "user": "ops@d.com", "name": "Ops", "scope": "operations", "area": "staffing", "module": "assignments", "payload": {}},
    ])


def test_diagnostic_events_do_not_affect_adoption_metrics(monkeypatch, tmp_path):
    # tests 1-3/7: data_load_failed/data_load_diagnostics/configuration_incomplete
    # siguen persistidos/clasificados diagnostic, pero agregarlos NO cambia ni
    # un solo número de adopción (Eventos/Usuarios únicos/Eventos por
    # día/Tendencia diaria/Áreas activas) frente al mismo día con solo uso.
    _seed_usage_only(monkeypatch, tmp_path)
    before = activity_analytics.build_summary("", "")
    _write_jsonl(tmp_path, "20260301", [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "module_viewed", "user": "ops@d.com", "name": "Ops", "scope": "operations", "area": "staffing", "module": "assignments", "payload": {}},
        {"ts_utc": "2026-03-01T10:01:00+00:00", "event": "data_load_failed", "user": "ops@d.com", "name": "Ops", "scope": "operations", "area": "staffing", "module": "assignments", "payload": {"operation": "initial"}},
        {"ts_utc": "2026-03-01T10:02:00+00:00", "event": "data_load_diagnostics", "user": "ops@d.com", "name": "Ops", "scope": "operations", "area": "staffing", "module": "assignments", "payload": {"operation": "initial"}},
        {"ts_utc": "2026-03-01T10:03:00+00:00", "event": "configuration_incomplete", "user": "diag-only@d.com", "name": "DiagOnly", "scope": "operations", "area": "staffing", "module": "assignments", "payload": {"operation": "initial"}},
    ])
    after = activity_analytics.build_summary("", "")
    # Persistidos y clasificados como diagnostic (nunca desaparecen del log ni
    # de la clasificación read-model, solo quedan afuera del dashboard).
    read = {e["event"]: e["event_class"] for e in activity_analytics._read_events("", "")}
    assert read["data_load_failed"] == ax.EVENT_CLASS_DIAGNOSTIC
    assert read["data_load_diagnostics"] == ax.EVENT_CLASS_DIAGNOSTIC
    assert read["configuration_incomplete"] == ax.EVENT_CLASS_DIAGNOSTIC
    # Ningún número de adopción cambia con 3 eventos diagnostic más en el log.
    assert after["total_events"] == before["total_events"]
    assert after["unique_users"] == before["unique_users"]
    assert after["total_days"] == before["total_days"]
    assert after["daily_series"] == before["daily_series"]
    assert after["areas_active"] == before["areas_active"]
    # No aparecen en la lista de "Acciones de usuario" ni en su drilldown.
    assert {r["event"] for r in after["event_counts"]} == {"module_viewed"}
    assert "data_load_failed" not in after["event_breakdowns"]
    assert "data_load_diagnostics" not in after["event_breakdowns"]
    assert "configuration_incomplete" not in after["event_breakdowns"]
    # El usuario que solo generó diagnóstico no aparece en Top usuarios.
    assert "DiagOnly" not in {r["label"] for r in after["top_users"]}


def test_diagnostic_events_do_not_inflate_module_breakdown(monkeypatch, tmp_path):
    # Un data_load_failed dentro de "Asignaciones" no debe aparecer en el
    # desglose module-scoped de ese módulo (module_event_breakdowns) — mismo
    # criterio que Top módulos, ahora también en el drilldown por módulo.
    _seed_usage_only(monkeypatch, tmp_path)
    _write_jsonl(tmp_path, "20260301", [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "module_viewed", "user": "ops@d.com", "name": "Ops", "scope": "operations", "area": "staffing", "module": "assignments", "payload": {}},
        {"ts_utc": "2026-03-01T10:01:00+00:00", "event": "data_load_failed", "user": "ops@d.com", "name": "Ops", "scope": "operations", "area": "staffing", "module": "assignments", "payload": {"operation": "initial"}},
    ])
    s = activity_analytics.build_summary("", "")
    rows = s["module_event_breakdowns"].get("Asignaciones", [])
    assert {r["event"] for r in rows} == {"module_viewed"}


# ── MODULE IDENTIFIER CONVENTION V2 — Fase 2 (Canonical Module Adoption) ─────
# `module_registry` (Fase 1, CLOSED/GREEN) es ahora el dueño único de la
# identidad canónica de módulo — estos tests prueban que Activity lo consume
# correctamente: normalización en lectura (raw_module/module), labels vía el
# registro, y que histórico+nuevo agregan en el MISMO bucket de Top Módulos.

def _seed_module_identity(monkeypatch, tmp_path, records):
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    _write_jsonl(tmp_path, "20260301", records)


# test 1
def test_historical_copilot_stem_normalizes_to_cpxx(monkeypatch, tmp_path):
    _seed_module_identity(monkeypatch, tmp_path, [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "module_viewed", "user": "u@d.com", "name": "U",
         "scope": "copilot", "area": "methodology", "module": "01_Arquitectura_y_Conectividad", "payload": {}},
    ])
    ev = activity_analytics._read_events("", "")[0]
    assert ev["raw_module"] == "01_Arquitectura_y_Conectividad"
    assert ev["module"] == "CP01"


# test 2
def test_historical_appendix_stem_normalizes_to_cpaxx(monkeypatch, tmp_path):
    _seed_module_identity(monkeypatch, tmp_path, [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "module_viewed", "user": "u@d.com", "name": "U",
         "scope": "copilot", "area": "methodology", "module": "A01_Arquitectura_y_Modelado", "payload": {}},
    ])
    ev = activity_analytics._read_events("", "")[0]
    assert ev["raw_module"] == "A01_Arquitectura_y_Modelado"
    assert ev["module"] == "CPA01"


# test 3
def test_historical_assignments_normalizes_to_op01(monkeypatch, tmp_path):
    _seed_module_identity(monkeypatch, tmp_path, [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "module_viewed", "user": "u@d.com", "name": "U",
         "scope": "operations", "area": "staffing", "module": "assignments", "payload": {}},
    ])
    ev = activity_analytics._read_events("", "")[0]
    assert ev["raw_module"] == "assignments"
    assert ev["module"] == "OP01"


# test 4
def test_already_canonical_cpxx_remains_canonical(monkeypatch, tmp_path):
    _seed_module_identity(monkeypatch, tmp_path, [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "module_viewed", "user": "u@d.com", "name": "U",
         "scope": "copilot", "area": "methodology", "module": "CP01", "payload": {}},
    ])
    ev = activity_analytics._read_events("", "")[0]
    assert ev["raw_module"] == "CP01"
    assert ev["module"] == "CP01"


# test 5
def test_already_canonical_op01_remains_canonical(monkeypatch, tmp_path):
    _seed_module_identity(monkeypatch, tmp_path, [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "module_viewed", "user": "u@d.com", "name": "U",
         "scope": "operations", "area": "staffing", "module": "OP01", "payload": {}},
    ])
    ev = activity_analytics._read_events("", "")[0]
    assert ev["raw_module"] == "OP01"
    assert ev["module"] == "OP01"


# test 6
def test_unknown_module_remains_unchanged_and_preserves_raw_module(monkeypatch, tmp_path):
    _seed_module_identity(monkeypatch, tmp_path, [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "module_viewed", "user": "u@d.com", "name": "U",
         "scope": "copilot", "area": "methodology", "module": "something_unknown", "payload": {}},
    ])
    ev = activity_analytics._read_events("", "")[0]
    # Nunca se fabrica un module_id para algo que el registro no reconoce.
    assert ev["raw_module"] == "something_unknown"
    assert ev["module"] == "something_unknown"


# tests 7/8
def test_module_label_returns_current_product_label_for_canonical_and_legacy():
    assert ax.module_label("CP01") == "Arquitectura y Conectividad"
    assert ax.module_label("01_Arquitectura_y_Conectividad") == "Arquitectura y Conectividad"


# tests 9/10
def test_module_label_returns_asignaciones_for_canonical_and_legacy_op01():
    assert ax.module_label("OP01") == "Asignaciones"
    assert ax.module_label("assignments") == "Asignaciones"


# test 11
def test_historical_and_canonical_records_aggregate_into_one_top_module_bucket(monkeypatch, tmp_path):
    _seed_module_identity(monkeypatch, tmp_path, [
        # Histórico: module=assignments (legacy).
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "module_viewed", "user": "a@d.com", "name": "A",
         "scope": "operations", "area": "staffing", "module": "assignments", "payload": {}},
        # Nuevo: module=OP01 (canónico, post Fase 2).
        {"ts_utc": "2026-03-01T10:01:00+00:00", "event": "module_viewed", "user": "b@d.com", "name": "B",
         "scope": "operations", "area": "staffing", "module": "OP01", "payload": {}},
    ])
    s = activity_analytics.build_summary("", "")
    # UN solo bucket "Asignaciones" con las dos visitas — no dos filas separadas.
    asignaciones_rows = [r for r in s["top_modules"] if r["label"] == "Asignaciones"]
    assert len(asignaciones_rows) == 1
    assert asignaciones_rows[0]["count"] == 2
    assert asignaciones_rows[0]["key"] == "OP01"


# test 20
def test_historical_jsonl_is_never_rewritten_by_normalization(monkeypatch, tmp_path):
    records = [
        {"ts_utc": "2026-03-01T10:00:00+00:00", "event": "module_viewed", "user": "u@d.com", "name": "U",
         "scope": "copilot", "area": "methodology", "module": "01_Arquitectura_y_Conectividad", "payload": {}},
    ]
    _seed_module_identity(monkeypatch, tmp_path, records)
    path = next(tmp_path.glob("activity_*.jsonl"))
    before = path.read_text(encoding="utf-8")
    activity_analytics._read_events("", "")
    activity_analytics.build_summary("", "")
    ax.module_label("01_Arquitectura_y_Conectividad")
    after = path.read_text(encoding="utf-8")
    assert before == after
    # El registro leído sigue con el stem LEGACY crudo — nunca se reescribió.
    assert json.loads(before.strip())["module"] == "01_Arquitectura_y_Conectividad"
