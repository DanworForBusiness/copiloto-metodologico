"""
test_hub_metrics_organic_discovery.py — HUB METRICS: GENERIC MODULE
DISCOVERY + STRATEGY CORRECTNESS (owner gate, ver HANDOFF).

Prueba la arquitectura, no solo el caso Estrategia: `hub_metrics_access.
metrics_visibility_for` y `activity_analytics._vertical_areas` ya NO
mantienen su propia lista de áreas por vertical — derivan de
`module_registry` (workspace -> área de cada módulo registrado) + el MISMO
predicado de autorización que ya gobierna ese workspace. La prueba clave
(A/B) registra un módulo SINTÉTICO bajo un workspace YA gateado
("commercial") sin tocar hub_metrics_access.py/activity_analytics.py y
confirma que se vuelve visible solo (y solo) para un usuario autorizado —
esa es la garantía que el próximo módulo real (CM03, CM04...) hereda gratis.

También prueba la corrección de atribución de comentarios de feedback
(E/F/G: `activity_analytics._reaction_area_for_record` resuelve por
identidad de módulo primero, cae al mapa histórico `type` solo para
registros legacy sin `module` resoluble) y la clasificación de
`strategy.saved` (I/J).

MISMO patrón de fixtures que test_hub_metrics_visibility.py (`_client`
análogo acá) y test_activity_routes.py (`_owner_client`/`_hx`, reusados tal
cual)."""
from __future__ import annotations

import json

import yaml
from fastapi.testclient import TestClient

from webapp import hub_metrics_access
from webapp.main import app
from webapp.services import activity_analytics, activity_context, activity_service, auth_service, module_registry
from tests.webapp.test_activity_routes import _hx, _owner_client


# ============================================================================
# A/B — DESCUBRIMIENTO ORGÁNICO: un módulo NUEVO en un workspace YA gateado
# se vuelve visible/invisible según autorización, sin tocar ningún archivo
# de metrics. Nunca se edita hub_metrics_access.py/activity_analytics.py acá
# — solo se registra un ModuleDescriptor sintético vía monkeypatch.
# ============================================================================

def _with_synthetic_commercial_module(monkeypatch):
    """Registra CM99 (workspace="commercial", area="widgets") ADEMÁS de los
    módulos reales — simula "el próximo módulo Comercial" sin depender de
    que exista de verdad en el código."""
    real_entries = module_registry.all_entries()
    synthetic = module_registry.ModuleDescriptor(
        module_id="CM99", workspace="commercial", area="widgets", kind="module",
        content_key=None, visible_label="Widgets (synthetic test module)",
        display_order=99, legacy_aliases=("widgets_test",),
    )
    monkeypatch.setattr(module_registry, "all_entries", lambda: real_entries + (synthetic,))


def _seed_synthetic_event(tmp_path):
    monkeypatch_row = {
        "ts_utc": "2026-09-12T10:00:00+00:00", "event": "module_viewed",
        "user": "widgets-user@d.com", "name": "WidgetsUser",
        "scope": "commercial", "area": "widgets", "module": "CM99", "payload": {},
    }
    (tmp_path / "activity_20260912.jsonl").write_text(json.dumps(monkeypatch_row) + "\n", encoding="utf-8")


def _client(monkeypatch, email, *, commercial=False, operations=False, metrics=True):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", email if commercial else "")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", email if operations else "")
    metrics_list = email if metrics else "someone-else@danwor.com"
    monkeypatch.setattr(auth_service, "HUB_METRICS_ALLOWED_USERS", metrics_list, raising=False)
    monkeypatch.setattr(hub_metrics_access, "HUB_METRICS_ALLOWED_USERS", metrics_list, raising=False)
    monkeypatch.setattr(hub_metrics_access, "HUB_METRICS_ENABLED_VERTICALS", "operations,commercial", raising=False)
    session = auth_service.create_session({"email": email, "name": "Test", "first_name": "T", "family_name": "U"})
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, session)
    return c


def test_a_synthetic_module_in_gated_workspace_is_visible_to_authorized_user_without_metrics_edits(monkeypatch, tmp_path):
    _with_synthetic_commercial_module(monkeypatch)
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    empty_feedback = tmp_path / "_no_feedback"
    empty_feedback.mkdir()
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback)
    _seed_synthetic_event(tmp_path)

    c = _client(monkeypatch, "widgets-user@d.com", commercial=True)
    t = _hx(c, "/admin/activity").text
    assert "widgets-user@d.com" in t
    assert "1 evento(s)" in t


def test_b_synthetic_module_stays_invisible_to_unauthorized_user(monkeypatch, tmp_path):
    _with_synthetic_commercial_module(monkeypatch)
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    empty_feedback = tmp_path / "_no_feedback"
    empty_feedback.mkdir()
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback)
    _seed_synthetic_event(tmp_path)

    # operations-only -- NUNCA autorizado a "commercial", así que el módulo
    # sintético (área "widgets") debe quedar afuera por completo. El ÚNICO
    # evento del dataset es ese (área "widgets"), así que el universo
    # autorizado de este usuario queda vacío -> dashboard.total_events == 0
    # dispara el estado "Sin actividad" (nunca el texto "N evento(s)", que
    # solo se renderiza cuando total_events > 0, ver admin_activity_content.
    # html).
    c = _client(monkeypatch, "ops-only@d.com", commercial=False, operations=True)
    t = _hx(c, "/admin/activity").text
    assert "widgets-user@d.com" not in t
    assert "Sin actividad" in t


def test_metrics_visibility_for_derives_areas_from_module_registry_not_a_hardcoded_list(monkeypatch):
    # Prueba directa del predicado puro (sin HTTP): un módulo sintético en
    # "commercial" aparece en allowed_areas para un usuario commercial-
    # authorized, SIN que metrics_visibility_for haya sido tocado.
    _with_synthetic_commercial_module(monkeypatch)
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "user@d.com")
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    _, allowed_areas = hub_metrics_access.metrics_visibility_for({"sub": "user@d.com"})
    assert "widgets" in allowed_areas
    assert "strategy" in allowed_areas  # CM02, registrado de verdad -- mismo mecanismo
    assert "crm" in allowed_areas


# ============================================================================
# C/D — Strategy module-view llega a Usage Metrics; CRM sigue distinguible.
# ============================================================================

def _seed_activity_dirs(monkeypatch, tmp_path):
    # tests/conftest.py's autouse `_isolate_runtime_logs` sets LOG_ACTIVITY
    # to "OFF" for every test by default (never pollute the real log) — a
    # test that needs a REAL `log_activity()` write from a live route must
    # explicitly re-enable it and win over that fixture, same as
    # test_routes.py already does.
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "VERBOSE", raising=False)
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    monkeypatch.setattr(activity_analytics, "ACTIVITY_LOG_DIR", tmp_path)
    empty_feedback = tmp_path / "_no_feedback"
    empty_feedback.mkdir()
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", empty_feedback)


def test_c_strategy_page_visit_reaches_usage_metrics_as_strategy(monkeypatch, tmp_path):
    _seed_activity_dirs(monkeypatch, tmp_path)
    c = _owner_client(monkeypatch)  # commercial+operations+metrics todos autorizados

    page = c.get("/comercial/estrategia")
    assert page.status_code == 200

    t = _hx(c, "/admin/activity").text
    assert 'name="area" value="strategy"' in t
    assert "1 evento(s)" in t


def test_d_crm_remains_separately_attributable_alongside_strategy(monkeypatch, tmp_path):
    _seed_activity_dirs(monkeypatch, tmp_path)
    c = _owner_client(monkeypatch)

    assert c.get("/comercial/estrategia").status_code == 200
    assert c.get("/comercial").status_code == 200  # CRM

    t = _hx(c, "/admin/activity").text
    assert "2 evento(s)" in t
    assert 'name="area" value="strategy"' in t
    assert 'name="area" value="crm"' in t
    # No colapsan al mismo módulo/área -- ambos aparecen como opciones DISTINTAS.
    module_select = t.split('name="module"', 1)[1].split("</select>", 1)[0]
    assert "Estrategia" in module_select
    assert "CRM" in module_select


# ============================================================================
# E/F/G — Atribución de feedback: reacciones (conteo) y comentarios.
# ============================================================================

def test_e_strategy_pillar_reaction_attributes_to_strategy_not_methodology(monkeypatch, tmp_path):
    _seed_activity_dirs(monkeypatch, tmp_path)
    c = _owner_client(monkeypatch)

    res = c.post(
        "/comercial/estrategia/sections/objectives/feedback",
        data={"rating": "up", "pack": "datasphere_core"},
        headers={"Origin": "http://testserver"},
    )
    assert res.status_code == 200

    t = _hx(c, "/admin/activity").text
    assert 'title="Estrategia: 1 👍 · 0 👎 de 1"' in t
    assert 'title="Metodología: 1 👍' not in t


def test_f_strategy_annex_feedback_comment_attributes_to_strategy(monkeypatch, tmp_path):
    # F: el correctness bug real -- `type="section_content"` es COMPARTIDO
    # con Copiloto, así que sin la resolución por `module` este comentario
    # caía en "methodology". Prueba end-to-end vía el endpoint real.
    _seed_activity_dirs(monkeypatch, tmp_path)
    feedback_dir = tmp_path / "_feedback"
    feedback_dir.mkdir()
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", feedback_dir)
    from webapp.routers import commercial_strategy as strategy_router
    monkeypatch.setattr(strategy_router, "FEEDBACK_LOG_DIR", feedback_dir)

    c = _owner_client(monkeypatch)
    res = c.post(
        "/comercial/estrategia/sections/annex_map/feedback/submit",
        data={"rating": "down", "comment": "Falta el organigrama actualizado."},
        headers={"Origin": "http://testserver"},
    )
    assert res.status_code == 200

    saved = list(feedback_dir.glob("*.yaml"))
    assert len(saved) == 1
    record = yaml.safe_load(saved[0].read_text(encoding="utf-8"))
    assert record["module"] == "CM02"
    assert activity_analytics._reaction_area_for_record(record) == "strategy"
    assert activity_analytics._reaction_area_for_record(record) != "methodology"


def test_g_legacy_feedback_record_without_resolvable_module_uses_type_fallback():
    # G: un registro histórico de Copiloto (`section_content`, `module` =
    # un stem NO registrado en module_registry, o ausente) debe seguir
    # resolviendo "methodology" -- el fallback nunca se rompe.
    legacy_with_unresolvable_module = {"type": "section_content", "module": "un_stem_inexistente"}
    assert activity_analytics._reaction_area_for_record(legacy_with_unresolvable_module) == "methodology"

    legacy_without_module = {"type": "section_content"}
    assert activity_analytics._reaction_area_for_record(legacy_without_module) == "methodology"

    # Un `type` genuinamente sin dimensión de módulo (nunca la tuvo).
    reporting_legacy = {"type": "reporting_report", "module": ""}
    assert activity_analytics._reaction_area_for_record(reporting_legacy) == "reporting"


def test_k_copilot_module_feedback_still_resolves_to_methodology_via_module_first_path(monkeypatch):
    # K: la resolución por módulo PRIMERO no debe cambiar el resultado para
    # Copiloto -- CP01 SÍ está en module_registry y su área YA es
    # "methodology", así que el nuevo camino y el fallback histórico
    # coinciden (regresión: Copiloto no se rompe con el fix de Estrategia).
    copilot_record = {"type": "section_content", "module": "CP01"}
    assert activity_analytics._reaction_area_for_record(copilot_record) == "methodology"


# ============================================================================
# H — el subject contextual del feedback ("este pilar"/"esta sección"/
# "este anexo") es presentación pura, cero efecto en identidad persistida.
# ============================================================================

def test_h_contextual_subject_does_not_alter_persisted_feedback_identity(monkeypatch, tmp_path):
    _seed_activity_dirs(monkeypatch, tmp_path)
    feedback_dir = tmp_path / "_feedback"
    feedback_dir.mkdir()
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", feedback_dir)
    from webapp.routers import commercial_strategy as strategy_router
    monkeypatch.setattr(strategy_router, "FEEDBACK_LOG_DIR", feedback_dir)

    c = _owner_client(monkeypatch)
    # "objectives" (pilar -> subject "este pilar") y "exclusions" (activación
    # -> subject genérico) tienen wording de UI distinta pero deben persistir
    # con la MISMA forma de payload.
    c.post("/comercial/estrategia/sections/objectives/feedback", data={"rating": "up"}, headers={"Origin": "http://testserver"})
    c.post("/comercial/estrategia/sections/exclusions/feedback", data={"rating": "up"}, headers={"Origin": "http://testserver"})

    records = [yaml.safe_load(p.read_text(encoding="utf-8")) for p in feedback_dir.glob("*.yaml")]
    assert len(records) == 2
    for record in records:
        assert set(record.keys()) == {"type", "module", "module_title", "section_slug", "section_title", "pack", "rating", "user", "ts"}
        assert record["module"] == "CM02"
        assert "subject" not in record
        assert "este pilar" not in json.dumps(record)
        assert "esta sección" not in json.dumps(record)


# ============================================================================
# I/J — strategy.saved: clasificado como uso, un evento desconocido sigue
# excluido (no se ensanchó accidentalmente la taxonomía).
# ============================================================================

def test_i_strategy_saved_is_classified_as_usage():
    assert activity_context.event_class("strategy.saved") == activity_context.EVENT_CLASS_USAGE


def test_i_strategy_saved_appears_in_usage_metrics(monkeypatch, tmp_path):
    _seed_activity_dirs(monkeypatch, tmp_path)
    (tmp_path / "activity_20260912.jsonl").write_text(
        json.dumps({
            "ts_utc": "2026-09-12T11:00:00+00:00", "event": "strategy.saved",
            "user": "owner@danwor.com", "name": "Owner",
            "scope": "commercial", "area": "strategy", "module": "CM02",
            "payload": {"revision": 5},
        }) + "\n",
        encoding="utf-8",
    )
    c = _owner_client(monkeypatch)
    t = _hx(c, "/admin/activity").text
    assert "1 evento(s)" in t
    assert "Estrategia — guardado exitoso" in t


def test_j_unrelated_unknown_event_remains_excluded(monkeypatch, tmp_path):
    _seed_activity_dirs(monkeypatch, tmp_path)
    (tmp_path / "activity_20260912.jsonl").write_text(
        json.dumps({
            "ts_utc": "2026-09-12T11:05:00+00:00", "event": "some_totally_unrelated_event",
            "user": "owner@danwor.com", "name": "Owner",
            "scope": "commercial", "area": "strategy", "module": "CM02", "payload": {},
        }) + "\n",
        encoding="utf-8",
    )
    c = _owner_client(monkeypatch)
    t = _hx(c, "/admin/activity").text
    # Único evento del dataset, y no clasifica como uso -> total_events == 0
    # -> "Sin actividad" (nunca el texto "N evento(s)", ver test_b arriba).
    assert "Sin actividad" in t
    assert activity_context.event_class("some_totally_unrelated_event") == activity_context.EVENT_CLASS_UNKNOWN
