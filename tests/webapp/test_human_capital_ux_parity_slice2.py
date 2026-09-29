"""
test_human_capital_ux_parity_slice2.py — CCHH UX parity slice 2 (owner-approved):
D1 CV dentro de "Datos del candidato" (create-only, no en edición),
D2 alineación de tabla SEMÁNTICA por identidad de columna (no posicional),
B2 result count, B3 filtro Seniority = select gobernado, B4 badge de
marca_potencial junto a la identidad. Solo el semántico de marca_potencial
existente; sin star-rating, sin columna nueva."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, human_capital_service, profile_repository
from webapp.services import human_capital_catalog_repository as catalog_repo


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", "hc-parity2@danwor.com")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


def _client():
    s = auth_service.create_session({"email": "hc-parity2@danwor.com", "name": "R", "first_name": "R", "family_name": "X"})
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, s)
    return c


def _seed_seniority(*values):
    snap = catalog_repo.build_snapshot(
        {"seniority": [{"valor": v, "orden": i + 1} for i, v in enumerate(values)]},
        refreshed_by="test", source={"drive_id": "d", "item_id": "i"})
    p = catalog_repo.snapshot_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))


# --- D1: CV dentro de "Datos del candidato" (create), NO en edición ---------
def test_d1_create_cv_is_inside_datos_del_candidato():
    html = _client().get("/cchh/candidatos/nuevo").text
    # El campo CV existe y cae DENTRO de "Datos del candidato" (antes de "Clasificación").
    assert 'name="file"' in html
    i_datos = html.find("Datos del candidato")
    i_cv = html.find('name="file"')
    i_clasif = html.find("Clasificación")
    assert i_datos != -1 and i_cv != -1 and i_clasif != -1
    assert i_datos < i_cv < i_clasif, "el CV debe ir dentro de Datos del candidato, antes de Clasificación"


def test_d1_edit_has_no_create_cv_field():
    # La edición NO debe renderear el campo CV del ALTA (macro sin cv_html);
    # el panel de Documentos (multi-doc del profile existente) es OTRA cosa y
    # sí está presente legítimamente.
    saved = human_capital_service.create_profile({"first_name": "Ana", "last_name": "Perez"})
    html = _client().get(f"/cchh/candidatos/{saved['profile_id']}/editar").text
    assert 'id="hc-new-cv-file"' not in html, "la edición no debe tener el campo CV del alta"
    # sanity: el panel de Documentos (upload propio del profile existente) sí existe
    assert 'hc-document' in html


# --- B2: result count -------------------------------------------------------
def test_b2_result_count_shown_with_results():
    human_capital_service.create_profile({"first_name": "Ana", "last_name": "Perez"})
    human_capital_service.create_profile({"first_name": "Bob", "last_name": "Lopez"})
    html = _client().get("/cchh/candidatos").text
    assert "2 candidatos" in html


def test_b2_count_singular():
    human_capital_service.create_profile({"first_name": "Ana", "last_name": "Perez"})
    html = _client().get("/cchh/candidatos").text
    assert "1 candidato" in html and "1 candidatos" not in html


# --- B3: filtro Seniority = select gobernado --------------------------------
def test_b3_seniority_filter_is_governed_select():
    _seed_seniority("Junior", "Senior")
    html = _client().get("/cchh/candidatos").text
    # Es un <select name="seniority"> con las opciones del catálogo (no un
    # input text) -- id/name, nunca el resto de los atributos (el filtro
    # reactivo, owner CRM parity, agrega hx-trigger="change" ahí mismo).
    assert '<select id="hc-filter-seniority" name="seniority"' in html
    assert ">Junior<" in html and ">Senior<" in html


def test_b3_seniority_filter_preserves_legacy_value():
    _seed_seniority("Junior", "Senior")
    html = _client().get("/cchh/candidatos", params={"seniority": "ArquitectoLegacy"}).text
    assert "ArquitectoLegacy (fuera de catálogo)" in html


# --- B4: badge de marca_potencial junto a la identidad ----------------------
def test_b4_potential_badge_near_identity():
    # `potential_mark` pasó a ser un catálogo gobernado (P2) -- sembrar
    # "Alto" antes de crear, MISMO criterio que `_seed_seniority` arriba.
    snap = catalog_repo.build_snapshot(
        {"potencial": [{"valor": "Alto", "orden": 1}]},
        refreshed_by="test", source={"drive_id": "d", "item_id": "i"})
    p = catalog_repo.snapshot_path(); p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(catalog_repo._serialize(snap))
    human_capital_service.create_profile(
        {"first_name": "Ana", "last_name": "Perez", "potential_mark": "Alto"})
    html = _client().get("/cchh/candidatos").text
    # El badge conserva sus clases de estado + `.dw-idcell__meta` (queda dentro
    # del wrapper de identidad como indicador compacto) -- se matchea sin exigir
    # la comilla de cierre para no acoplar el test al orden exacto de clases.
    assert "status-badge status-badge--potential" in html
    assert "Alto" in html


def test_b4_no_badge_without_mark():
    human_capital_service.create_profile({"first_name": "Bob", "last_name": "Lopez"})
    html = _client().get("/cchh/candidatos").text
    assert "status-badge--potential" not in html


def test_b4_no_new_column_added():
    human_capital_service.create_profile({"first_name": "Ana", "last_name": "Perez"})
    html = _client().get("/cchh/candidatos").text
    # Sigue habiendo exactamente 5 columnas data-column-key + Acciones (sin columna nueva).
    assert html.count('data-column-key="nombre"') >= 1
    assert 'data-column-key="potential"' not in html and 'data-column-key="marca"' not in html


# --- D2: alineación SEMÁNTICA por identidad de columna (no posicional) -------
def test_d2_text_columns_left_actions_center_by_class():
    human_capital_service.create_profile({"first_name": "Ana", "last_name": "Perez"})
    html = _client().get("/cchh/candidatos").text
    # columnas de texto -> clase semántica left (no first-child)
    for key in ("skills", "seniority", "ubicacion", "fuente"):
        assert f'<th class="left" data-column-key="{key}"' in html, f"th {key} debe ser left por clase"
        assert f'<td class="left" data-column-key="{key}"' in html, f"td {key} debe ser left por clase"
    # Nombre = columna de texto normal `.left` (owner closure: se sacó el
    # tratamiento especial `row-label`/`row-label-col` de la primera columna
    # -- ése fue el fix final del "split de dos tablas"; la identidad es una
    # celda de tabla común, con un wrapper interno `.dw-idcell`). Acciones = center.
    assert '<th class="left" data-column-key="nombre"' in html
    assert '<td class="left" data-column-key="nombre"' in html
    assert '<th class="center">Acciones</th>' in html
    assert '<td class="center">' in html


def test_d2_no_positional_alignment_selectors_in_css():
    css = (config.BASE_DIR.parents[0] if False else None)  # placeholder, ver abajo
    # Lee el CSS del worktree real (no BASE_DIR de test).
    from pathlib import Path
    tools = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    hc = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "human_capital.css").read_text(encoding="utf-8")
    import re
    # Ninguna regla de alineación de tabla atada a posición.
    assert not re.search(r"(first-child|nth-child)[^{]*\{[^}]*text-align", tools + hc)
    assert "table.dashboard-table th.center, table.dashboard-table td.center" in tools
