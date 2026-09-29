"""
test_human_capital_list_panel_t2.py — T2 (owner): el listado de Candidatos vive
en el primitivo compartido de paneles (`initDashboardSections` /
`<details class="dashboard-section">`), colapsable/reordenable con persistencia
compartida. El conteo va en el HEADER del panel (arriba-derecha), nunca como
línea suelta sobre la tabla. Body del panel = filtros + tabla."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, human_capital_service, profile_repository


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "hc-t2@danwor.com")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


def _client():
    s = auth_service.create_session({"email": "hc-t2@danwor.com", "name": "R", "first_name": "R", "family_name": "X"})
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, s)
    return c


def test_list_uses_shared_dashboard_section_primitive():
    human_capital_service.create_profile({"first_name": "Ana", "last_name": "Perez"})
    html = _client().get("/cchh/candidatos").text
    # Reusa el primitivo compartido (mismo que Reporting/Staffing/Comercial).
    assert 'data-dashboard-key="cchh-candidatos-list"' in html
    assert '<details class="dashboard-section" data-section-id="candidatos"' in html
    assert 'class="dashboard-section__head"' in html
    assert 'class="dashboard-section__title">Candidatos<' in html


def test_count_lives_in_panel_header_not_standalone():
    human_capital_service.create_profile({"first_name": "Ana", "last_name": "Perez"})
    human_capital_service.create_profile({"first_name": "Bob", "last_name": "Lopez"})
    html = _client().get("/cchh/candidatos").text
    # count en el header del panel (arriba-derecha), no como línea suelta.
    assert '<span class="dashboard-section__count">2 candidatos</span>' in html
    assert 'hc-results-count' not in html  # la línea suelta anterior fue removida


def test_count_singular_in_header():
    human_capital_service.create_profile({"first_name": "Ana", "last_name": "Perez"})
    html = _client().get("/cchh/candidatos").text
    assert '<span class="dashboard-section__count">1 candidato</span>' in html


def test_filters_are_page_level_outside_the_section_table_is_inside():
    # Owner UX correction (post-#107 QA): REEMPLAZA la política anterior de
    # este test, que exigía filtros DENTRO del body del panel. Regla de
    # composición Hub-wide (MISMO patrón que Staffing:
    # `.staffing-filter-bar` vive ANTES de `.dashboard-sections`): los
    # filtros de Candidatos son PÁGINA/GLOBALES (aplican a la lista
    # entera), nunca "de este panel" -- sólo la tabla/resultados en sí
    # sigue siendo contenido colapsable/reordenable del panel.
    human_capital_service.create_profile({"first_name": "Ana", "last_name": "Perez"})
    html = _client().get("/cchh/candidatos").text
    i_filters = html.find('class="hc-profile-filters')
    i_body = html.find('class="dashboard-section__body"')
    i_table = html.find('id="hc-candidatos-table"')
    assert i_filters != -1 and i_body != -1 and i_table != -1
    assert i_filters < i_body < i_table, "filtros ANTES del panel; tabla DENTRO del body del panel"


def test_count_updates_after_filtering():
    # 3 candidatos; un filtro por nombre que matchea 1 -> el count del header
    # refleja el RESULTADO filtrado (se re-rendea en el swap de HTMX).
    human_capital_service.create_profile({"first_name": "Ana", "last_name": "Perez"})
    human_capital_service.create_profile({"first_name": "Ana", "last_name": "Lopez"})
    human_capital_service.create_profile({"first_name": "Bruno", "last_name": "Diaz"})
    c = _client()
    assert '<span class="dashboard-section__count">3 candidatos</span>' in c.get("/cchh/candidatos").text
    html = c.get("/cchh/candidatos", params={"q": "Bruno"}).text
    assert '<span class="dashboard-section__count">1 candidato</span>' in html


def test_stable_unique_dashboard_key():
    html = _client().get("/cchh/candidatos").text
    assert html.count('data-dashboard-key="cchh-candidatos-list"') == 1
    assert 'data-persist-collapse' in html  # opt-in de persistencia de colapso


def test_source_error_hides_count_in_header(monkeypatch):
    # Si el listado no se pudo leer, no se muestra un conteo engañoso.
    monkeypatch.setattr(human_capital_service, "list_profiles",
                        lambda: (_ for _ in ()).throw(profile_repository.ProfileStorageError("down")))
    html = _client().get("/cchh/candidatos").text
    assert 'dashboard-section__count' not in html
    assert 'data-error-kind' in html  # el estado "No disponible" sí se muestra
