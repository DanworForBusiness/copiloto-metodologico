"""
Métricas de Uso v2 — paneles por vertical.

- `page_view` de pantallas completas sin módulo (CCHH, Búsquedas, Reporting,
  Plataforma) deriva su SECCIÓN de la ruta en telemetry.py (tabla explícita
  `activity_catalog.PAGE_SECTIONS`); una ruta con ids cae en la sección padre.
- Con una vertical elegida, TODOS los contenedores del tablero (KPIs, desglose,
  «Módulos/Secciones y usuarios», Eventos, Reacciones, Tendencia) muestran
  solo esa vertical: nunca módulos, eventos, rótulos ni usuarios de otra; los
  paneles del Copiloto (Historia de preguntas, Árboles) solo con Copiloto o sin
  vertical; Reacciones solo en verticales con feedback catalogado.
- El desglose de una vertical de pantallas (CCHH) baja directo a sus secciones.

Disco a `tmp_path` (monkeypatch de `ACTIVITY_LOG_DIR`), eventos sintéticos v1/v2.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp import access, hub_metrics_access
from webapp.main import app
from webapp.services import (
    activity_aggregates, activity_analytics, activity_catalog, activity_dashboard, activity_service, auth_service,
)
from webapp.services.activity_context import area_label, module_label

_TODAY = date(2026, 10, 21)


def _v1(ts, event, user, scope, area, payload=None, **extra):
    return {"ts_utc": ts, "event": event, "user": user, "name": user.split("@")[0].title(),
            "scope": scope, "area": area, "payload": payload or {}, **extra}


def _v2(ts, event, user, scope, area, kind, attrs=None, module=None, section=None, rid=""):
    rec = {"v": 2, "id": rid or f"{event}-{ts}-{user}", "ts_utc": ts, "kind": kind, "event": event, "user": user,
           "vertical": scope, "scope": scope, "area": area, "surface": "server", "attrs": attrs or {}}
    if module:
        rec["module"] = module
    if section:
        rec["section"] = section
    return rec


def _pv(ts, user, view_id, scope, area, *, module=None, section=None, path="/x"):
    return _v2(ts, "page_view", user, scope, area, "navigation",
               {"view_id": view_id, "nav_type": "navigate", "path": path}, module=module, section=section,
               rid=f"pv-{view_id}")


_DAYS = {
    # Solo Copiloto ese día: sirve para probar una vertical SIN actividad en el período.
    "20261019": [
        _v1("2026-10-19T09:05:00+00:00", "module_selected", "uno@d.com", "copilot", "methodology",
            {"module": "01_Arquitectura_y_Conectividad"}),
        _v1("2026-10-19T09:10:00+00:00", "assistant_query", "uno@d.com", "copilot", "assistant",
            {"question": "pregunta copiloto", "answer": "respuesta", "conversation_id": "c1", "reply_id": "r1"}),
        _v1("2026-10-19T09:20:00+00:00", "tree_started", "uno@d.com", "copilot", "methodology",
            {"stem": "01", "tree_id": "t1", "tree_title": "Árbol copiloto"}),
        _v2("2026-10-19T09:30:00+00:00", "module.feedback", "uno@d.com", "copilot", "methodology", "action",
            {"feedback_sentiment": "positive"}, module="CP01"),
    ],
    "20261020": [
        _pv("2026-10-20T10:00:00+00:00", "dos@d.com", "c1", "commercial", "strategy", module="CM02"),
        _v2("2026-10-20T10:05:00+00:00", "commercial.feedback", "dos@d.com", "commercial", "crm", "action",
            {"feedback_sentiment": "negative"}, module="CM01"),
        _pv("2026-10-20T11:00:00+00:00", "tres@d.com", "o1", "operations", "staffing", module="OP01"),
        _v2("2026-10-20T11:05:00+00:00", "staffing.feedback", "tres@d.com", "operations", "staffing", "action",
            {"feedback_sentiment": "positive"}, module="OP01"),
        _pv("2026-10-20T12:00:00+00:00", "cuatro@d.com", "h1", "human_capital", "cchh",
            section="cchh_candidatos", path="/cchh/candidatos"),
        _pv("2026-10-20T12:01:00+00:00", "cuatro@d.com", "h2", "human_capital", "cchh",
            section="cchh_busquedas", path="/cchh/busquedas"),
        _pv("2026-10-20T12:02:00+00:00", "cuatro@d.com", "h3", "human_capital", "cchh",
            section="cchh_busquedas", path="/cchh/busquedas"),
        _v2("2026-10-20T12:05:00+00:00", "cchh.profile_created", "cuatro@d.com", "human_capital", "cchh", "action",
            {"outcome": "success"}),
    ],
}

# Rótulos propios de cada vertical en estos datos: ninguno puede aparecer en un contenedor de OTRA vertical.
_LABELS = {
    "copilot": [module_label("CP01"), activity_catalog.label_of("assistant_query"), "pregunta copiloto",
                "Árbol copiloto", activity_catalog.label_of("module.feedback"), area_label("methodology"), "Uno"],
    "commercial": [module_label("CM02"), activity_catalog.label_of("commercial.feedback"), "dos@d.com"],
    "operations": [module_label("OP01"), activity_catalog.label_of("staffing.feedback"), "tres@d.com"],
    "human_capital": ["CCHH · Candidatos", "CCHH · Búsquedas", activity_catalog.label_of("cchh.profile_created"),
                      "cuatro@d.com"],
}


@pytest.fixture
def logdir(monkeypatch, tmp_path):
    for mod in (activity_analytics, activity_aggregates):
        monkeypatch.setattr(mod, "ACTIVITY_LOG_DIR", tmp_path)
    feedback = tmp_path / "_no_feedback"
    feedback.mkdir()
    monkeypatch.setattr(activity_analytics, "FEEDBACK_LOG_DIR", feedback)
    monkeypatch.setattr(activity_analytics, "_assistant_reply", lambda rid: None)
    monkeypatch.setattr(activity_dashboard, "_today", lambda: _TODAY)
    for day, rows in _DAYS.items():
        (tmp_path / f"activity_{day}.jsonl").write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return tmp_path


def _client(monkeypatch, *, cchh=True):
    email = "owner@danwor.com" if cchh else "metrics-viewer@danwor.com"
    monkeypatch.setattr(access, "APP_OWNER", email if cchh else "otro-owner@danwor.com")
    metrics = "" if cchh else email
    monkeypatch.setattr(auth_service, "HUB_METRICS_ALLOWED_USERS", metrics, raising=False)
    monkeypatch.setattr(hub_metrics_access, "HUB_METRICS_ALLOWED_USERS", metrics, raising=False)
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", email)
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", email)
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", email if cchh else "")
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session(
        {"email": email, "name": "Owner", "first_name": "Own", "family_name": "Er"}))
    return c


def _hx(c, url):
    r = c.get(url, headers={"HX-Request": "true"})
    assert r.status_code == 200, r.text[:500]
    return r.text


def _containers(html):
    """Solo los contenedores del tablero (sin la barra de filtros, que lista verticales/ámbitos a propósito)."""
    return html.split('class="catalog-flow-sep"', 1)[1]


def _panel(html, sid):
    m = re.search(r'<details class="dashboard-section" data-section-id="' + sid + r'"(.*?)</details>', html, re.S)
    return m.group(1) if m else None


def _drill_rows(html):
    out = {}
    for m in re.finditer(r'<tr data-drill-key="([^"]+)"( data-drill-zero)?>(.*?)</tr>', html, re.S):
        cells = re.findall(r"<td>(\d+)</td>", m.group(3))
        out[m.group(1)] = (*map(int, cells[:3]), bool(m.group(2)))
    return out


def _assert_no_leak(html, vertical):
    body = _containers(html)
    for other, labels in _LABELS.items():
        if other == vertical:
            continue
        for label in labels:
            # Rótulo como TEXTO de un elemento (no subcadena: «Integración y Estrategia» es del Copiloto).
            assert not re.search(r"(?:>|› )\s*" + re.escape(label) + r"\s*<", body), (vertical, other, label)


# ── sección derivada de la ruta ──────────────────────────────────────────────

@pytest.mark.parametrize("path,scope,expected", [
    ("/cchh", "human_capital", "cchh_inicio"),
    ("/cchh/candidatos", "human_capital", "cchh_candidatos"),
    ("/cchh/candidatos/9f8e7d/editar", "human_capital", "cchh_candidatos"),     # id → sección padre
    ("/cchh/candidatos/importar", "human_capital", "cchh_candidatos"),
    ("/cchh/busquedas/b-123/candidatos/a-9/evaluacion-cchh", "human_capital", "cchh_busquedas"),
    ("/cchh/entrevistas/e1/editar", "human_capital", "cchh_entrevistas"),
    ("/cchh/onboarding", "human_capital", "cchh_onboarding"),
    ("/cchh/publicaciones/nueva", "human_capital", "cchh_publicaciones"),
    ("/cchh/indicadores", "human_capital", "cchh_indicadores"),
    ("/operaciones/busquedas/xyz", "human_capital", "ops_busquedas"),
    ("/operaciones/reporting/integral", "operations", "ops_reporting_integral"),
    ("/whats-new", "platform", "hub_novedades"),
    ("/cchh/candidatos", "copilot", None),                                    # nunca cruza verticales
    ("/catalog/domain", "copilot", None),
])
def test_page_section_table(path, scope, expected):
    assert activity_catalog.page_section(path, scope) == expected


def test_page_section_prefixes_are_real_routes_and_valid_slugs():
    # Superficie HTTP real (snapshot del contrato de rutas): solo pantallas GET.
    surface = json.loads((Path(__file__).resolve().parents[1] / "route_surface.json").read_text(encoding="utf-8"))
    paths = {entry.split(" ", 1)[1] for entry in surface if entry.split(" ", 1)[0] == "GET"}
    for prefix, _vertical, slug, _label in activity_catalog.PAGE_SECTIONS:
        assert activity_catalog.is_valid_section_slug(slug), slug
        assert any(p == prefix or p.startswith(prefix + "/") for p in paths), prefix   # no se inventan pantallas


@pytest.fixture
def beacon_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "BASIC")
    monkeypatch.setattr(activity_service, "ACTIVITY_LOG_DIR", tmp_path)
    return tmp_path


def test_page_view_beacon_writes_section_from_path_without_ids(beacon_dir):
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session(
        {"email": "beacon@danwor.com", "name": "B", "first_name": "B", "family_name": "B"}))
    c.post("/telemetry/event", json={"event": "page_view", "view_id": "pvA1", "nav_type": "navigate",
                                     "path": "/cchh/candidatos/abc123/editar?q=juan"})
    c.post("/telemetry/event", json={"event": "page_view", "view_id": "pvA2", "nav_type": "navigate",
                                     "path": "/modules/01", "module_id": "CP01"})
    recs = [json.loads(line) for p in sorted(beacon_dir.glob("activity_*.jsonl"))
            for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    cchh, cp = recs
    assert (cchh["scope"], cchh["section"]) == ("human_capital", "cchh_candidatos") and "module" not in cchh
    assert "q=" not in json.dumps(cchh)
    assert cp["module"] == "CP01" and "section" not in cp


# ── contenedores por vertical ────────────────────────────────────────────────

def test_human_capital_shows_sections_and_nothing_from_other_verticals(logdir, monkeypatch):
    t = _hx(_client(monkeypatch), "/admin/activity?vertical=human_capital")
    nav = _panel(t, "modulos-usuarios")
    assert 'data-nav-mode="sections"' in nav and "Secciones y usuarios" in nav and "Módulos y usuarios" not in nav
    assert "CCHH · Búsquedas" in nav and "CCHH · Candidatos" in nav and "cuatro@d.com" in nav
    for sid in ("preguntas", "arboles", "reacciones"):
        assert _panel(t, sid) is None, sid
    assert "Historia de preguntas" not in t and "Árboles de decisión" not in t
    _assert_no_leak(t, "human_capital")
    rows = _drill_rows(t)                                  # desglose: secciones de CCHH, no un «sin módulo»
    assert rows["cchh_busquedas"][0] == 2 and rows["cchh_candidatos"][0] == 1
    assert rows["cchh_onboarding"][3] is True
    assert rows["_none"][0] == 0                           # solo la acción sin pantalla, ninguna visita «sin sección»
    assert 'hx-get="/admin/activity?vertical=human_capital&amp;section=cchh_busquedas"' in t


@pytest.mark.parametrize("vertical,module", [("commercial", "CM02"), ("operations", "OP01")])
def test_module_verticals_show_only_their_modules_and_feedback(logdir, monkeypatch, vertical, module):
    t = _hx(_client(monkeypatch), f"/admin/activity?vertical={vertical}")
    nav = _panel(t, "modulos-usuarios")
    assert 'data-nav-mode="modules"' in nav and "Módulos y usuarios" in nav and module_label(module) in nav
    assert _panel(t, "reacciones") is not None and "1 reacción(es)" in t
    assert _panel(t, "preguntas") is None and _panel(t, "arboles") is None
    _assert_no_leak(t, vertical)


def test_copilot_keeps_its_panels_and_no_other_vertical(logdir, monkeypatch):
    t = _hx(_client(monkeypatch), "/admin/activity?vertical=copilot")
    assert module_label("CP01") in _panel(t, "modulos-usuarios")
    assert _panel(t, "preguntas") is not None and _panel(t, "arboles") is not None
    _assert_no_leak(t, "copilot")


def test_vertical_without_activity_in_period_never_falls_back_to_copilot(logdir, monkeypatch):
    t = _hx(_client(monkeypatch), "/admin/activity?vertical=commercial&date_from=2026-10-19&date_to=2026-10-19")
    _assert_no_leak(t, "commercial")
    assert "Sin actividad" in t


def test_no_vertical_keeps_historic_panels(logdir, monkeypatch):
    t = _hx(_client(monkeypatch), "/admin/activity")
    nav = _panel(t, "modulos-usuarios")
    assert 'data-nav-mode="modules"' in nav and "Módulos y usuarios" in nav and module_label("CP01") in nav
    for sid in ("tendencia", "eventos-catalogo", "reacciones", "preguntas", "arboles"):
        assert _panel(t, sid) is not None, sid


def test_viewer_without_cchh_never_sees_cchh_sections(logdir, monkeypatch):
    c = _client(monkeypatch, cchh=False)
    for url in ("/admin/activity", "/admin/activity?vertical=human_capital",
                "/admin/activity?vertical=human_capital&section=cchh_busquedas"):
        t = _hx(c, url)
        assert "CCHH ·" not in t and "cuatro@d.com" not in t and "Secciones y usuarios" not in t, url


# ── subhead del desglose y posición ──────────────────────────────────────────

def test_drill_subhead_and_scroll_preserving_links(logdir, monkeypatch):
    t = _hx(_client(monkeypatch), "/admin/activity?vertical=commercial")
    subhead = t.split("data-drill-subhead", 1)[1].split("</p>", 1)[0]
    assert "Módulos" in subhead and "click para desglosar" in subhead
    assert 'Filtrado por <strong aria-current="page">Comercial</strong>' in subhead
    assert 'class="dashboard-subhead__reset" data-drill-nav hx-get="/admin/activity"' in subhead
    links = re.findall(r'<button[^>]*data-drill-nav[^>]*>', t)
    assert links and all('hx-swap="outerHTML show:none"' in b for b in links)
    overview = _hx(_client(monkeypatch), "/admin/activity")
    assert "data-drill-filter" not in overview                 # sin filtro no hay «Filtrado por»


def test_paging_fragments_keep_the_selected_vertical():
    """Paginar Tendencia, Historia o Árboles no puede volver a «todas las verticales»: las rutas aceptan `vertical` y los paginadores la propagan."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[2]
    main_src = (root / "webapp" / "main.py").read_text(encoding="utf-8")
    assert main_src.count("vertical=vertical.strip(),        # paginar no rompe la vertical estricta") == 3
    for name in ("admin_activity_trend", "admin_activity_stories", "admin_activity_tree_runs"):
        tpl = (root / "webapp" / "templates" / "partials" / f"{name}.html").read_text(encoding="utf-8")
        assert '"&vertical=" ~ (summary.selected_vertical|urlencode)' in tpl
