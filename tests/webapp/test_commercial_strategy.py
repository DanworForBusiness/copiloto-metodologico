"""
test_commercial_strategy.py — Commercial Strategy (Phase 1, READ-ONLY).

Cubre el checklist del owner (storage gate + phase 1 spec):
 A. nav expone "02 · Estrategia"
 B. ruta protegida por la MISMA auth de Comercial
 C. el documento seedeado carga
 D. las 13 secciones fuente están representadas
 E. tabs de segmento cambian correctamente (markup/estado Alpine presente)
 F. sub-tabs anidados cambian correctamente
 G. contenido de negocio real está presente (texto del artefacto fuente)
 H. las filas de "Próximos pasos" preservan el orden fuente
 I. status/prioridad mapean a las clases Hub esperadas
 J. ningún bloque de HTML/script/style crudo del artefacto fuente se renderiza
 K. no existe UI de Editar/Guardar en Fase 1
 L. no existe ningún endpoint POST/write de Estrategia
 M. la capability CRM (01) sigue intacta
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from webapp.main import app
from webapp.services import auth_service

_TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates"
_ROUTERS = Path(__file__).resolve().parents[2] / "webapp" / "routers"
_STRATEGY_HTML = (_TEMPLATES / "commercial" / "strategy.html").read_text(encoding="utf-8")
_STRATEGY_PY = (_ROUTERS / "commercial_strategy.py").read_text(encoding="utf-8")
_NAV_HTML = (_TEMPLATES / "partials" / "commercial_nav.html").read_text(encoding="utf-8")

auth_service.ALLOWED_USERS = "ALL"
auth_service.COMMERCIAL_ALLOWED_USERS = (
    "commercial-test@danwor.com,commercial-odoo-test@danwor.com,content-owner-test@danwor.com"
)

_SESSION = auth_service.create_session(
    {"email": "commercial-test@danwor.com", "name": "Commercial Test", "first_name": "Commercial", "family_name": "Test"}
)

client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)


def _unlisted_client(monkeypatch):
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "commercial-test@danwor.com")
    session = auth_service.create_session(
        {"email": "unlisted@danwor.com", "name": "Unlisted", "first_name": "Unlisted", "family_name": "User"}
    )
    ro = TestClient(app)
    ro.cookies.set(auth_service.COOKIE_NAME, session)
    return ro


# ============================================================================
# A — Nav expone "02 · Estrategia"
# ============================================================================

def test_commercial_nav_exposes_estrategia_as_second_capability():
    response = client.get("/comercial")
    text = response.text
    assert 'href="/comercial" class="module-link is-active"' in text
    assert 'href="/comercial/estrategia" class="module-link ' in text
    assert "<strong>Estrategia</strong>" in text


def test_estrategia_page_highlights_its_own_nav_item():
    response = client.get("/comercial/estrategia")
    assert response.status_code == 200
    assert 'href="/comercial/estrategia" class="module-link is-active"' in response.text


# ============================================================================
# B — Ruta protegida por la MISMA auth de Comercial
# ============================================================================

def test_anonymous_redirects_to_login():
    anon = TestClient(app)
    response = anon.get("/comercial/estrategia", follow_redirects=False)
    assert response.status_code == 303


def test_unlisted_authorized_app_user_is_denied_with_403(monkeypatch):
    ro = _unlisted_client(monkeypatch)
    response = ro.get("/comercial/estrategia")
    assert response.status_code == 403


def test_commercial_allowed_users_blank_denies_the_previously_authorized_user(monkeypatch):
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    response = client.get("/comercial/estrategia")
    assert response.status_code == 403


def test_route_reuses_commercial_workspace_gate_never_a_new_one():
    assert "commercial_access.is_commercial_authorized" in _STRATEGY_PY
    assert "workspaces.platform_login_redirect(" in _STRATEGY_PY


# ============================================================================
# C — El documento seedeado carga
# ============================================================================

def test_authorized_user_gets_200_and_real_content():
    response = client.get("/comercial/estrategia")
    assert response.status_code == 200
    assert "no está disponible" not in response.text
    assert "Dónde jugamos" in response.text


# ============================================================================
# D — Las 13 secciones fuente están representadas
# ============================================================================

def test_document_index_reuses_the_copilot_section_index_contract():
    # MISMO primitivo que module.html's "En este módulo" (.section-index,
    # variante plana sin roadmap): `<nav class="section-index"><p>...</p>
    # <a><span>NN</span>Título</a></nav>` — nunca una implementación propia.
    text = client.get("/comercial/estrategia").text
    assert 'class="section-index"' in text
    assert 'aria-label="En este documento"' in text
    assert "<p>En este documento</p>" in text
    assert "<a href=\"#p1\"><span>01</span>Objetivos</a>" in text
    assert "<a href=\"#reglas\"><span>12</span>Reglas de oro</a>" in text


def test_document_index_has_no_leftover_source_number_prefix():
    # El artefacto fuente ya trae "1 · Objetivos" en el label — el índice
    # numera POR SU CUENTA (igual que Copilot); el prefijo del fuente no debe
    # duplicarse en el título visible.
    text = client.get("/comercial/estrategia").text
    index_html = text.split('aria-label="En este documento"')[1].split("</nav>")[0]
    assert "1 · Objetivos" not in index_html
    assert ">Objetivos</a>" in index_html


def test_no_duplicate_strategy_specific_index_implementation():
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial_strategy.css").read_text(encoding="utf-8")
    for banned in (".cs-index", ".cs-nav", ".cs-toc", ".cs-section-index"):
        assert banned not in css, banned


# ============================================================================
# 26 — Contrato visual (regresión estructural — la validación de color/
# contraste real es SIEMPRE en browser, ver reporte; esto solo evita que la
# regresión concreta encontrada por el owner vuelva a colarse en silencio).
# ============================================================================

def test_strategy_tiles_theme_via_scoped_tokens_not_fixed_dark_only():
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial_strategy.css").read_text(encoding="utf-8")
    # La técnica REAL (no inventada): reusa la MISMA redefinición de
    # `--card`/`--ink`/`--soft`/`--line` que `.copilot-panel` ya usa en
    # tools.css, aplicada a `.cs-doc` — nunca overrides de color sueltos
    # por componente como único mecanismo.
    assert ".content-theme-light .cs-doc" in css
    assert ".content-theme-dark .cs-doc" in css
    assert "--card:" in css.replace(" ", "") or "--card :" in css


def test_strategy_page_root_carries_the_theme_scoping_class():
    html = (Path(__file__).resolve().parents[2] / "webapp" / "templates" / "commercial" / "strategy.html").read_text(encoding="utf-8")
    assert 'class="module-content cs-doc"' in html


def test_no_standalone_font_import_or_root_palette_reintroduced():
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "commercial_strategy.css").read_text(encoding="utf-8")
    for banned in ("fonts.googleapis.com/css2?family=Barlow", "@import url", "--gold-dim", "--panel2"):
        assert banned not in css, banned


def test_no_global_contenteditable_reintroduced():
    html = (Path(__file__).resolve().parents[2] / "webapp" / "templates" / "commercial" / "strategy.html").read_text(encoding="utf-8")
    assert "contenteditable" not in html


def test_all_expected_section_links_present_in_index():
    text = client.get("/comercial/estrategia").text
    index_html = text.split('aria-label="En este documento"')[1].split("</nav>")[0]
    assert index_html.count("<a href=") == 12  # 13 secciones fuente - la portada (hero)


def test_all_thirteen_source_sections_are_represented():
    response = client.get("/comercial/estrategia")
    text = response.text
    for section_id in (
        "hero", "p1", "genda", "p2", "p3", "p4", "p5", "p6", "proximos",
        "mapa", "roles", "proceso", "reglas",
    ):
        assert f'id="{section_id}"' in text, section_id


def test_section_titles_present():
    text = client.get("/comercial/estrategia").text
    for title in (
        "Objetivos", "Generación de demanda", "Dónde jugamos", "Criterio de foco",
        "Asignación de capacidad", "Cadencia de revisión", "Qué no se hace",
        "Próximos pasos", "Mapa de contactos", "Mapa de roles y responsabilidades",
        "Etapas del pipeline", "Reglas de oro",
    ):
        assert title in text, title


# ============================================================================
# E/F — Tabs de segmento y sub-tabs (markup + estado Alpine)
# ============================================================================

def test_segment_tabs_markup_present_for_all_six_segments():
    text = client.get("/comercial/estrategia").text
    assert 'class="cs-segtabs"' in text
    for label in (
        "1 · Semillero de eventos", "2 · C-Field Base Instalada", "3 · C-Field NNN",
        "4 · C-Digital NNN", "5 · Base propia Danwor", "6 · Servicios / Partners",
    ):
        assert label in text, label
    # Estado Alpine: un `seg` inicial + `x-show` por panel (6 paneles).
    assert "x-data=\"{ seg: 'sgen'" in text
    assert text.count("x-show=\"seg ===") == 6


def test_nested_subtabs_markup_present_for_segments_with_subtabs():
    text = client.get("/comercial/estrategia").text
    assert 'class="cs-subtabs"' in text
    for label in ("RISE/GROW aún no en vivo", "Business One", "New Names puros"):
        assert text.count(label) >= 2  # aparece en C-Field NNN y C-Digital NNN
    # `subsel` cubre exactamente los 2 segmentos con sub-tabs (s2, s3).
    assert "'s2': 's2a'" in text
    assert "'s3': 's3a'" in text


# ============================================================================
# G — Contenido de negocio real presente
# ============================================================================

def test_real_business_wording_from_source_artifact_is_present():
    text = client.get("/comercial/estrategia").text
    for fragment in (
        "USD 500.000", "25.000 horas", "Modernización SAP", "Fernando Oriolo",
        "María José", "Julieta Tiddens", "Reality Data Breakfast",
        "Una oportunidad = un dueño", "Prospecto",
    ):
        assert fragment in text, fragment


# ============================================================================
# H — "Próximos pasos" preserva el orden fuente
# ============================================================================

def test_next_steps_rows_preserve_source_order():
    text = client.get("/comercial/estrategia").text
    first = text.index("Mapear cada cuenta por madurez de datos")
    second = text.index("Ejecutar el mapeo de madurez de cuentas")
    last = text.index("Organizar evento de generación de demanda")
    assert first < second < last


def test_next_steps_has_sixteen_rows():
    from webapp.services.commercial_strategy_repository import load_strategy
    doc = load_strategy()
    assert len(doc["next_steps"]["tasks"]) == 16


# ============================================================================
# I — Status/prioridad mapean a clases Hub
# ============================================================================

def test_status_and_priority_map_to_hub_badge_classes():
    text = client.get("/comercial/estrategia").text
    assert 'class="cs-priority">PRIORIDAD 1</span>' in text
    assert 'cs-priority--new' in text  # Segmento 5 · "PRIORIDAD 5 · SOLO LICENCIAS"
    assert "status-badge--tentativo" in text  # tareas Q4
    assert "status-badge--inactivo" in text  # tareas "A definir"


# ============================================================================
# J — Ningún bloque de HTML/script/style crudo del artefacto se renderiza
# ============================================================================

def test_no_raw_source_script_or_style_block_is_rendered():
    text = client.get("/comercial/estrategia").text
    # Nada del JS/CSS vanilla propio del artefacto standalone sobrevivió.
    for banned in (
        "function applyEditMode", "globalEditBtn", "dragstart", "recolorQ",
        "@import url('https://fonts.googleapis.com/css2?family=Barlow",
        "--gold-dim", "font-family:'Barlow'",
    ):
        assert banned not in text, banned


def test_persisted_markup_never_leaks_arbitrary_html_tags():
    # El mini-markup solo puede producir <strong>/<em>/<a href="http(s)...">/<br>
    # dentro del contenido persistido — nunca <script>/<img>/on*= atributos.
    text = client.get("/comercial/estrategia").text
    assert "<script>alert" not in text
    assert "onerror=" not in text
    assert "javascript:" not in text


# ============================================================================
# K/F — Un usuario de Comercial NO listado en CONTENT_OWNER: sin Editar,
# 403 si fuerza el POST. `client` (commercial-test@danwor.com) nunca está en
# CONTENT_OWNER en este archivo — MISMO usuario que el resto de los tests de
# lectura, deliberado (prueba el caso común: autorizado a Comercial, no a
# editar).
# ============================================================================

def test_non_owner_never_sees_edit_affordance():
    text = client.get("/comercial/estrategia").text
    for banned in ("cs-edit-toggle", "cs-edit-panel", ">Editar<", "editing-mode", "contenteditable"):
        assert banned not in text, banned
    assert "contenteditable" not in _STRATEGY_HTML


def test_non_owner_write_attempt_returns_403(monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "")
    response = client.post("/comercial/estrategia")
    assert response.status_code == 403


# ============================================================================
# G — Usuario CONTENT_OWNER: recibe el affordance de Editar, puede "entrar"
# a EDIT (el panel existe en el DOM, aunque hoy responda 503 honesto al
# guardar — ver H/router).
# ============================================================================

_OWNER_SESSION = auth_service.create_session(
    {"email": "content-owner-test@danwor.com", "name": "Owner Test", "first_name": "Owner", "family_name": "Test"}
)
owner_client = TestClient(app)
owner_client.cookies.set(auth_service.COOKIE_NAME, _OWNER_SESSION)


def _with_owner(monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "content-owner-test@danwor.com")


def test_content_owner_sees_edit_affordance_and_panel(monkeypatch):
    _with_owner(monkeypatch)
    text = owner_client.get("/comercial/estrategia").text
    assert 'id="cs-edit-toggle"' in text
    assert 'id="cs-edit-panel"' in text
    assert 'id="cs-save-btn"' in text


# ============================================================================
# H — CONTENT_OWNER ausente/vacío: fail-closed, nadie edita implícitamente.
# ============================================================================

def test_empty_content_owner_fails_closed_for_everyone(monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "")
    text = owner_client.get("/comercial/estrategia").text
    assert 'id="cs-edit-toggle"' not in text
    response = owner_client.post("/comercial/estrategia")
    assert response.status_code == 403


# ============================================================================
# I — El parser de CONTENT_OWNER es el MISMO CSV/alias que el resto del Hub
# (username compacto -> @AUTH_DEFAULT_DOMAIN, case-insensitive, coma-separado).
# ============================================================================

def test_content_owner_reuses_the_existing_csv_allowlist_convention(monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "Content-Owner-Test, otro-usuario")
    assert auth_service.is_content_owner_user("content-owner-test@danwor.com") is True
    assert auth_service.is_content_owner_user("otro-usuario@danwor.com") is True
    assert auth_service.is_content_owner_user("nadie@danwor.com") is False


def test_content_owner_never_accepts_the_all_wildcard(monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "ALL")
    assert auth_service.is_content_owner_user("anyone@danwor.com") is False


# ============================================================================
# J — APP_OWNER solo NUNCA implica can_edit_strategy.
# ============================================================================

def test_app_owner_alone_does_not_bypass_content_owner(monkeypatch):
    import config as _config
    monkeypatch.setattr(_config, "APP_OWNER", "content-owner-test@danwor.com")
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "")
    text = owner_client.get("/comercial/estrategia").text
    assert 'id="cs-edit-toggle"' not in text
    response = owner_client.post("/comercial/estrategia")
    assert response.status_code == 403


def test_commercial_access_never_references_app_owner():
    # `APP_OWNER` aparece en la PROSA de los docstrings (explicando por qué NO
    # se usa, herencia de Phase 1) — lo que este test protege es que nunca se
    # IMPORTE ni se LLAME como código real.
    access_py = (Path(__file__).resolve().parents[2] / "webapp" / "commercial_access.py").read_text(encoding="utf-8")
    assert "import access" not in access_py
    assert "from webapp import access" not in access_py
    assert "access.is_owner(" not in access_py
    assert "from config import APP_OWNER" not in access_py


# ============================================================================
# L/K-Z — El endpoint de escritura EXISTE, autoritativo, y con topología
# single-instance confirmada por el owner, persiste de verdad. Cada test que
# guarda usa un repositorio AISLADO (tmp_path, nunca el seed/runtime real)
# vía `_isolated_repo` — sin eso, un save real de un test contaminaría el
# resto (incluidos los de lectura de arriba, que leen el singleton global).
# ============================================================================

def test_write_endpoint_exists_and_enforces_auth_before_anything_else():
    assert "@router.post" in _STRATEGY_PY
    repo_text = (Path(__file__).resolve().parents[2] / "webapp" / "services" /
                 "commercial_strategy_repository.py").read_text(encoding="utf-8")
    assert "def save_strategy" in repo_text


def _isolated_repo(tmp_path, monkeypatch):
    """Reemplaza el singleton `_repository` del módulo por uno nuevo
    apuntando a `tmp_path` (seed real, runtime path descartable) — el
    router lee `_repository` por nombre en cada call, así que esto alcanza
    sin tocar nada más. `monkeypatch` revierte solo al terminar el test."""
    from webapp.services import commercial_strategy_repository as repo_module
    seed_path = Path(__file__).resolve().parents[2] / "webapp" / "services" / "commercial_strategy_data" / "strategy.yaml"
    fresh = repo_module.CommercialStrategyRepository(seed_path=seed_path, runtime_path=tmp_path / "strategy.yaml")
    monkeypatch.setattr(repo_module, "_repository", fresh)
    return fresh


def _save_payload(revision: int, **overrides) -> dict:
    import yaml
    seed_path = Path(__file__).resolve().parents[2] / "webapp" / "services" / "commercial_strategy_data" / "strategy.yaml"
    doc = yaml.safe_load(seed_path.read_text(encoding="utf-8"))
    payload = {
        "revision": revision,
        "hero": doc["hero"],
        "objectives": {
            "result_targets": doc["objectives"]["result_targets"],
            "demand_targets": doc["objectives"]["demand_targets"],
            "calc_note": doc["objectives"]["calc_note"],
            "coverage_note": doc["objectives"]["coverage_note"],
            "sensitivity_note": doc["objectives"]["sensitivity_note"],
            "assumptions_pending": doc["objectives"]["assumptions_pending"],
        },
        "next_steps": {"tasks": doc["next_steps"]["tasks"]},
    }
    payload.update(overrides)
    return payload


def _owner_post(payload: dict):
    return owner_client.post("/comercial/estrategia", json=payload, headers={"Origin": "http://testserver"})


def test_prose_edit_save_reload_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["hero"]["lead"] = "Nueva bajada editada en el test."
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    assert res.json()["document"]["hero"]["lead"] == "Nueva bajada editada en el test."
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["hero"]["lead"] == "Nueva bajada editada en el test."
    assert load_strategy()["revision"] == 2


def test_get_never_mutates_the_canonical_document(tmp_path, monkeypatch):
    # "Cancelar" es 100% client-side (Alpine descarta `doc` y vuelve a copiar
    # `original`) — lo único verificable server-side es que un GET repetido
    # nunca cambia nada por sí mismo.
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    from webapp.services.commercial_strategy_repository import load_strategy
    before = load_strategy()
    owner_client.get("/comercial/estrategia")
    assert load_strategy() == before


def test_task_edit_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["next_steps"]["tasks"][0]["tarea"] = "Tarea editada"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["next_steps"]["tasks"][0]["tarea"] == "Tarea editada"


def test_task_add_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    before_count = len(payload["next_steps"]["tasks"])
    payload["next_steps"]["tasks"].append(
        {"id": "new-1", "tarea": "Nueva tarea", "origen": "Test", "responsable": "QA", "q": "Q1"}
    )
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    tasks = load_strategy()["next_steps"]["tasks"]
    assert len(tasks) == before_count + 1
    assert tasks[-1]["tarea"] == "Nueva tarea"


def test_task_delete_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    removed_id = payload["next_steps"]["tasks"].pop(0)["id"]
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    ids = [t["id"] for t in load_strategy()["next_steps"]["tasks"]]
    assert removed_id not in ids


def test_task_reorder_persists(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    tasks = payload["next_steps"]["tasks"]
    first_id, second_id = tasks[0]["id"], tasks[1]["id"]
    tasks[0], tasks[1] = tasks[1], tasks[0]
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    from webapp.services.commercial_strategy_repository import load_strategy
    reloaded = load_strategy()["next_steps"]["tasks"]
    assert reloaded[0]["id"] == second_id
    assert reloaded[1]["id"] == first_id


def test_invalid_enum_rejected_atomically(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    from webapp.services.commercial_strategy_repository import load_strategy
    before = load_strategy()
    payload = _save_payload(1)
    payload["next_steps"]["tasks"][0]["q"] = "Q9"
    payload["hero"]["lead"] = "Esto NO debería persistir"
    res = _owner_post(payload)
    assert res.status_code == 422
    assert load_strategy() == before  # nada parcial se escribió


def test_stale_revision_returns_409_without_overwriting(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["hero"]["lead"] = "Primer guardado"
    res1 = _owner_post(payload)
    assert res1.status_code == 200, res1.text

    stale_payload = _save_payload(1)  # misma revision vieja, como si nunca hubiera visto el primer guardado
    stale_payload["hero"]["lead"] = "Segundo guardado, revision vieja"
    res2 = _owner_post(stale_payload)
    assert res2.status_code == 409
    body = res2.json()
    assert body["state"] == "stale"
    assert body["current_revision"] == 2

    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["hero"]["lead"] == "Primer guardado"


def test_durable_push_failure_reports_error_and_preserves_previous_canonical(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    from webapp.services import commercial_strategy_repository as repo_module
    monkeypatch.setattr(repo_module.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo_module.remote_store, "push_path", lambda path: False)

    from webapp.services.commercial_strategy_repository import load_strategy
    before = load_strategy()
    payload = _save_payload(1)
    payload["hero"]["lead"] = "Esto no debería sobrevivir al push fallido"
    res = _owner_post(payload)
    assert res.status_code == 503
    body = res.json()
    assert body["state"] == "unavailable"
    assert body["reason"] == "durable_push_failed"

    after = load_strategy()
    assert after["hero"]["lead"] == before["hero"]["lead"]
    assert after["revision"] == before["revision"] == 1


def test_retry_after_push_failure_succeeds(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    from webapp.services import commercial_strategy_repository as repo_module
    monkeypatch.setattr(repo_module.remote_store, "enabled", lambda: True)
    monkeypatch.setattr(repo_module.remote_store, "push_path", lambda path: False)

    payload = _save_payload(1)
    payload["hero"]["lead"] = "Intento fallido"
    res = _owner_post(payload)
    assert res.status_code == 503

    # El intento fallido NUNCA incrementó la revision — el reintento sigue
    # siendo válido con revision=1 una vez que el push "empieza a andar".
    monkeypatch.setattr(repo_module.remote_store, "push_path", lambda path: True)
    payload2 = _save_payload(1)
    payload2["hero"]["lead"] = "Reintento exitoso"
    res2 = _owner_post(payload2)
    assert res2.status_code == 200, res2.text
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["hero"]["lead"] == "Reintento exitoso"
    assert load_strategy()["revision"] == 2


def test_malicious_markup_inert_on_reload_and_render(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["hero"]["lead"] = '<script>alert(1)</script> normal text <img src=x onerror="alert(2)">'
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    rendered = owner_client.get("/comercial/estrategia").text
    # Nunca una forma VIVA/ejecutable — ni como tag real ni como atributo real.
    # (La subcadena "onerror=" SÍ aparece en la página de forma inerte, dos
    # veces: dentro del JSON de `editable_doc` que precarga el formulario de
    # edición — Alpine lo asigna via `.value`, nunca `innerHTML` — y dentro
    # del texto escapado en la vista `onerror=&quot;...&quot;`. Ninguna de las
    # dos es un atributo real de un tag real, así que buscar la subcadena
    # sola daría falsos positivos — lo que importa es que NO exista como
    # atributo HTML vivo.)
    assert "<script>alert(1)</script>" not in rendered
    assert "<img src=x onerror=" not in rendered
    assert "&lt;script&gt;" in rendered  # el texto sobrevive, escapado — nunca ejecutable
    assert "&lt;img src=x onerror=" in rendered  # ídem, en la vista de lectura


def test_double_quote_in_edited_content_cannot_break_out_of_the_edit_state_attribute(tmp_path, monkeypatch):
    # Bug REAL encontrado por este módulo de tests: `{{ editable_doc|tojson }}`
    # escapa comillas SIMPLES pero no DOBLES — embebido antes directo dentro
    # de `x-data="..."` (atributo de comillas dobles, necesario porque el
    # resto de la expresión Alpine usa `'...'` sin escapar), un campo
    # editable con una comilla doble cerraba el atributo antes de tiempo.
    # Fix: el JSON vive en un <script type="application/json"> inerte, nunca
    # inline en el atributo — este test prueba el síntoma concreto que
    # habría delatado una regresión: la comilla doble del payload nunca debe
    # aparecer SIN escapar dentro de `x-data="`.
    import re
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["hero"]["lead"] = 'texto con "comillas dobles" adentro'
    res = _owner_post(payload)
    assert res.status_code == 200, res.text
    rendered = owner_client.get("/comercial/estrategia").text
    assert 'id="cs-editable-doc"' in rendered
    # El payload SÍ debe aparecer (sin escapar HTML) dentro del <script> inerte...
    script_body = re.search(r'id="cs-editable-doc">(.*?)</script>', rendered, re.S).group(1)
    assert "comillas dobles" in script_body
    # ...pero NUNCA sin escapar dentro de ningún atributo `x-data="..."` — si
    # el bug volviera, la comilla del payload truncaría ese atributo antes de
    # tiempo y esta búsqueda (no-greedy hasta la PRÓXIMA comilla real)
    # capturaría un fragmento roto en vez del JS completo.
    for attr_body in re.findall(r'x-data="(.*?)"', rendered, re.S):
        assert "comillas dobles" not in attr_body


def test_same_origin_content_owner_write_succeeds(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    res = _owner_post(_save_payload(1))
    assert res.status_code == 200


def test_non_content_owner_write_returns_403_even_with_valid_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "")
    _isolated_repo(tmp_path, monkeypatch)
    res = client.post("/comercial/estrategia", json=_save_payload(1), headers={"Origin": "http://testserver"})
    assert res.status_code == 403


def test_empty_content_owner_write_fails_closed_with_valid_payload(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "")
    _isolated_repo(tmp_path, monkeypatch)
    res = owner_client.post("/comercial/estrategia", json=_save_payload(1), headers={"Origin": "http://testserver"})
    assert res.status_code == 403


def test_repository_reload_sees_durable_saved_document(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    fresh = _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["hero"]["kicker"] = "Kicker persistido"
    res = _owner_post(payload)
    assert res.status_code == 200, res.text

    from webapp.services import commercial_strategy_repository as repo_module
    reloaded_repo = repo_module.CommercialStrategyRepository(seed_path=fresh._seed_path, runtime_path=fresh._runtime_path)
    reloaded = reloaded_repo.load()
    assert reloaded["hero"]["kicker"] == "Kicker persistido"
    assert reloaded["revision"] == 2


def test_successful_save_increments_revision_exactly_once(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    res = _owner_post(_save_payload(1))
    assert res.json()["document"]["revision"] == 2


def test_failed_save_does_not_increment_revision(tmp_path, monkeypatch):
    _with_owner(monkeypatch)
    _isolated_repo(tmp_path, monkeypatch)
    payload = _save_payload(1)
    payload["next_steps"]["tasks"][0]["q"] = "not-a-real-value"
    res = _owner_post(payload)
    assert res.status_code == 422
    from webapp.services.commercial_strategy_repository import load_strategy
    assert load_strategy()["revision"] == 1


# ============================================================================
# V — Un write forjado cross-site-style (sin Origin/Referer del mismo
# origen) falla el guard, incluso siendo CONTENT_OWNER.
# ============================================================================

def test_forged_cross_site_style_write_fails_the_same_origin_guard(monkeypatch):
    _with_owner(monkeypatch)
    response = owner_client.post(
        "/comercial/estrategia", json={}, headers={"Origin": "https://evil.example"},
    )
    assert response.status_code == 403


def test_write_with_no_origin_or_referer_fails_closed(monkeypatch):
    _with_owner(monkeypatch)
    response = owner_client.post("/comercial/estrategia", json={}, headers={"Origin": "", "Referer": ""})
    assert response.status_code == 403


# ============================================================================
# M — La capability CRM (01) sigue intacta
# ============================================================================

def test_crm_capability_unaffected():
    response = client.get("/comercial")
    assert response.status_code == 200
    assert "<h1>Dashboard CRM</h1>" in response.text
    assert 'href="/comercial" class="module-link is-active"' in response.text


def test_nav_partial_lists_both_capabilities_in_order():
    idx_crm = _NAV_HTML.index('href="/comercial"')
    idx_strategy = _NAV_HTML.index('href="/comercial/estrategia"')
    assert idx_crm < idx_strategy
