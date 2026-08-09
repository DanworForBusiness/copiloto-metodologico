"""
test_staffing_app_js.py — Contrato de filtrado FACETADO de
staffingDashboard() (revisión con dato real, ver HANDOFF).

Este archivo lee el CÓDIGO FUENTE de webapp/static/app.js en vez de
ejecutarlo — no hay runtime de JS en la suite (no hay Jest/Vitest en este
repo). La lógica en sí (dedup, exclusión de no-resueltos, reset de
selecciones inválidas) YA fue verificada con dato sintético real contra el
estado interno de Alpine en un servidor aislado (ver HANDOFF) — lo que
estos tests fijan es que esa implementación específica no se borre ni se
reemplace por accidente en un refactor futuro. Mismo criterio que ya usan
otros tests de este directorio al pinnear expresiones Alpine específicas
en las plantillas (`paletteClass(row)`, etc.), aplicado acá al archivo
fuente porque la lógica vive 100% en JS, no en el template.
"""
from __future__ import annotations

from pathlib import Path

_APP_JS = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "app.js").read_text(encoding="utf-8")


def test_faceted_assignments_excludes_only_the_named_filter():
    assert "facetedAssignments(excludeKey)" in _APP_JS
    for key in ("consultant", "project", "client", "availability"):
        assert f'excludeKey !== "{key}"' in _APP_JS


def test_status_is_never_excluded_from_the_facet_it_is_always_applied():
    # Estado no tiene su propio "excludeKey !== 'status'" — sus opciones son
    # la lista fija de estados conocidos (statusOptions), nunca recalculada
    # dinámicamente vía facetedAssignments.
    assert 'excludeKey !== "status"' not in _APP_JS


def test_options_are_computed_from_the_faceted_universe_not_the_raw_assignments():
    assert 'this.facetedAssignments("consultant")' in _APP_JS
    assert 'this.facetedAssignments("project")' in _APP_JS
    assert 'this.facetedAssignments("client")' in _APP_JS
    assert 'this.facetedAssignments("availability")' in _APP_JS
    assert "get filteredAssignments() {\n      return this.facetedAssignments(null);" in _APP_JS


def test_consultant_and_project_options_exclude_unresolved_rows():
    assert "!a.consultant_unresolved" in _APP_JS
    assert "!a.project_unresolved" in _APP_JS


def test_client_options_require_both_id_and_name_never_a_synthetic_option():
    assert "a.client_id && a.client_name && !seen.has(a.client_id)" in _APP_JS


def test_selections_reconcile_and_reset_when_invalidated_by_another_filter():
    assert "_reconcileSelections()" in _APP_JS
    assert 'this.filters.consultant = "";' in _APP_JS
    assert 'this.filters.project = "";' in _APP_JS
    assert 'this.filters.client = "";' in _APP_JS
    assert 'this.filters.availability = "";' in _APP_JS
    # Se re-valida en respuesta a un cambio en CUALQUIERA de los 5 filtros
    # (incluyendo Estado, que sí puede invalidar a los otros aunque sus
    # propias opciones no se recalculen).
    for watched in ("filters.consultant", "filters.project", "filters.client", "filters.availability", "filters.status"):
        assert f'this.$watch("{watched}", () => this._reconcileSelections())' in _APP_JS


def test_today_marker_has_no_text_label_by_intentional_product_decision():
    # Decisión de producto explícita (revisión con dato real, ver HANDOFF):
    # SOLO la línea vertical del día actual — sin chip de texto "HOY". No
    # reintroducir ni reemplazar por otra etiqueta.
    assert "staffing-timeline__today-label" not in _APP_JS
