"""
Métricas de Uso v2 — fase 3: tablero `/admin/activity` (filtros dinámicos,
KPIs desde agregados, drill-down vertical → módulo → sección → eventos,
inventario sin actividad, visibilidad de Capital Humano y paneles históricos
sin regresión).

Disco a `tmp_path` vía monkeypatch de `ACTIVITY_LOG_DIR` en analytics y
agregados (mismo patrón que test_activity_v2.py); HOY fijo vía
`activity_dashboard._today`. Nunca se toca os.environ.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from webapp import access
from webapp.main import app
from webapp.services import activity_aggregates, activity_analytics, activity_dashboard, auth_service

_TODAY = date(2026, 10, 21)
_TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates"


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


def _pv(ts, user, view_id, *, scope="copilot", area="methodology", module="CP01", nav_type="navigate"):
    return _v2(ts, "page_view", user, scope, area, "navigation",
               {"view_id": view_id, "nav_type": nav_type, "path": "/x"}, module=module, rid=f"pv-{view_id}-{nav_type}")


_DAYS = {
    # v1: login + visita a CP01 + pregunta (con texto) + árbol + reacción.
    "20261019": [
        _v1("2026-10-19T09:00:00+00:00", "login", "uno@d.com", "platform", "auth"),
        _v1("2026-10-19T09:05:00+00:00", "module_selected", "uno@d.com", "copilot", "methodology",
            {"module": "01_Arquitectura_y_Conectividad"}),
        _v1("2026-10-19T09:10:00+00:00", "assistant_query", "uno@d.com", "copilot", "assistant",
            {"question": "pregunta v1", "answer": "respuesta v1", "conversation_id": "c1", "reply_id": "r1"}),
        _v1("2026-10-19T09:20:00+00:00", "tree_started", "uno@d.com", "copilot", "methodology",
            {"stem": "01", "tree_id": "t1", "tree_title": "Árbol de prueba"}),
        _v1("2026-10-19T09:30:00+00:00", "staffing.feedback", "tres@d.com", "operations", "staffing",
            {"feedback_sentiment": "positive"}, module="asignaciones"),
    ],
    # v2: visitas, sección, resultados (éxito/falla), CCHH y una pregunta v2 sin texto.
    "20261020": [
        _pv("2026-10-20T23:50:00+00:00", "uno@d.com", "a1"),
        _v2("2026-10-20T10:00:00+00:00", "section_view", "dos@d.com", "copilot", "catalog", "navigation",
            {"view_id": "s1", "path": "/modules/01"}, module="CP01", section="catalog_domain"),
        _v2("2026-10-20T10:05:00+00:00", "catalog_apis_sync", "dos@d.com", "copilot", "catalog", "outcome",
            {"outcome": "success", "state": "ok"}),
        _v2("2026-10-20T10:06:00+00:00", "catalog_apis_sync", "dos@d.com", "copilot", "catalog", "outcome",
            {"outcome": "failed"}),
        _pv("2026-10-20T11:00:00+00:00", "tres@d.com", "o1", scope="operations", area="staffing", module="OP01"),
        _v2("2026-10-20T12:00:00+00:00", "cchh.profile_created", "cuatro@d.com", "human_capital", "cchh", "action",
            {"outcome": "success"}),
        _pv("2026-10-20T12:01:00+00:00", "cuatro@d.com", "h1", scope="human_capital", area="cchh", module=None),
        _v2("2026-10-20T12:30:00+00:00", "assistant_query", "uno@d.com", "copilot", "assistant", "action",
            {"conversation_id": "c2", "reply_id": "r2-sin-store", "chars_in": 10}),
    ],
    # HOY: misma persona 20 min después de las 23:50 → misma sesión; el reload no suma visita.
    "20261021": [
        _pv("2026-10-21T00:10:00+00:00", "uno@d.com", "a2"),
        _pv("2026-10-21T00:11:00+00:00", "uno@d.com", "a2", nav_type="reload"),
    ],
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


def _client(monkeypatch, *, cchh=True, email="owner@danwor.com"):
    """Owner con todas las verticales; `cchh=False` = viewer de Metrics NO owner
    (el dueño es superusuario de CCHH) y sin acceso a Capital Humano."""
    from webapp import hub_metrics_access
    if cchh:
        monkeypatch.setattr(access, "APP_OWNER", email)
        metrics = ""
    else:
        email = "metrics-viewer@danwor.com"
        monkeypatch.setattr(access, "APP_OWNER", "otro-owner@danwor.com")
        metrics = email
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


def _kpi(html, label):
    m = re.search(r'<span class="activity-kpi__value">([^<]*)</span>\s*<span class="activity-kpi__label">' + re.escape(label), html)
    assert m, label
    return m.group(1)


def _drill_rows(html):
    """{key: (visitas, usuarios, actividad, zero)} de la tabla del desglose."""
    out = {}
    for m in re.finditer(r'<tr data-drill-key="([^"]+)"( data-drill-zero)?>(.*?)</tr>', html, re.S):
        cells = re.findall(r"<td>(\d+)</td>", m.group(3))
        out[m.group(1)] = (*map(int, cells[:3]), bool(m.group(2)))
    return out


def _select_block(html, name):
    return html.split(f'name="{name}"', 1)[1].split("</select>", 1)[0]


# ── KPIs y filtros ───────────────────────────────────────────────────────────

def test_kpis_over_full_history_union_users_and_midnight_session(logdir, monkeypatch):
    t = _hx(_client(monkeypatch), "/admin/activity")
    # visitas: v1 module_selected (1) + v2 a1, o1, h1, a2 (el reload de a2 no suma) = 5
    assert _kpi(t, "Visitas") == "5"
    # usuarios: uno (3 días), dos, tres, cuatro → 4, nunca 6 por sumar días.
    assert _kpi(t, "Usuarios únicos") == "4"
    assert _kpi(t, "Operaciones (éxito / falla)") == "2 / 1"   # + la acción de CCHH con resultado
    assert "67% de éxito" in t                                            # 2 éxitos (sync de APIs + acción de CCHH) / 3


def test_session_crossing_midnight_counts_once_in_two_day_range(logdir, monkeypatch):
    c = _client(monkeypatch)
    t = _hx(c, "/admin/activity?date_from=2026-10-20&date_to=2026-10-21&user=uno%40d.com")
    # 23:50 (a1), 00:10 (a2), 00:11 (reload) y la pregunta de las 12:30 → 2 sesiones: [12:30] y [23:50→00:11].
    assert _kpi(t, "Sesiones") == "2"
    assert _kpi(t, "Usuarios únicos") == "1"
    only_today = _hx(c, "/admin/activity?period=today&user=uno%40d.com")
    assert _kpi(only_today, "Sesiones") == "1" and _kpi(only_today, "Visitas") == "1"


def test_filters_narrow_kpis(logdir, monkeypatch):
    c = _client(monkeypatch)
    ops = _hx(c, "/admin/activity?vertical=operations")
    assert _kpi(ops, "Visitas") == "1" and _kpi(ops, "Usuarios únicos") == "1"
    actions = _hx(c, "/admin/activity?kind=outcome")
    assert _kpi(actions, "Visitas") == "0" and _kpi(actions, "Operaciones (éxito / falla)") == "1 / 1"
    sec = _hx(c, "/admin/activity?vertical=copilot&module=CP01&section=catalog_domain")
    assert _kpi(sec, "Visitas") == "0" and _kpi(sec, "Usuarios únicos") == "1"
    week = _hx(c, "/admin/activity?period=today")
    assert _kpi(week, "Usuarios únicos") == "1"
    assert 'name="date_from" value="2026-10-21"' in week and 'name="date_to" value="2026-10-21"' in week


def test_period_presets_and_custom_resolution():
    r = activity_dashboard.resolve_period("7d", "", "", today=_TODAY)
    assert (r["period"], r["date_from"], r["date_to"]) == ("7d", "2026-10-15", "2026-10-21")
    # Las fechas del preset vuelven en el form: el round-trip sigue siendo el preset.
    assert activity_dashboard.resolve_period("7d", "2026-10-15", "2026-10-21", today=_TODAY)["period"] == "7d"
    # Un clic de día (fechas distintas) gana: rango personalizado.
    assert activity_dashboard.resolve_period("7d", "2026-10-20", "2026-10-20", today=_TODAY)["period"] == "custom"
    assert activity_dashboard.resolve_period("month", "", "", today=_TODAY)["date_from"] == "2026-10-01"
    assert activity_dashboard.resolve_period("", "", "", today=_TODAY)["period"] == ""


# ── drill-down ───────────────────────────────────────────────────────────────

def test_drilldown_levels_and_event_list(logdir, monkeypatch):
    c = _client(monkeypatch)
    overview = _hx(c, "/admin/activity")
    rows = _drill_rows(overview)
    assert rows["copilot"][3] is False and rows["operations"][0] == 1
    assert rows["commercial"] == (0, 0, 0, True)          # vertical visible sin actividad: inventario
    assert 'hx-get="/admin/activity?vertical=copilot"' in overview

    vertical = _hx(c, "/admin/activity?vertical=copilot")
    rows = _drill_rows(vertical)
    assert rows["CP01"][0] == 3                            # v1 module_selected + a1 + a2
    assert rows["CP02"] == (0, 0, 0, True)                 # módulo del registro sin actividad
    assert rows["CPA06"][3] is True                         # apéndices también son inventario
    assert "OP01" not in rows and "CM01" not in rows
    assert '<span class="dashboard-section__title">Desglose: vertical, módulo y sección</span>' in vertical

    module = _hx(c, "/admin/activity?vertical=copilot&module=CP01")
    rows = _drill_rows(module)
    assert rows["catalog_domain"][2] == 1 and rows["catalog_domain"][3] is False
    assert rows["_none"][0] == 3                            # vistas del módulo sin sección
    assert rows["catalog_lob"] == (0, 0, 0, True)          # sección del inventario sin actividad
    assert "cchh_candidate_contact" not in rows            # pertenece a otra vertical
    assert "Todas las verticales" in module and "Copiloto" in module

    section = _hx(c, "/admin/activity?vertical=copilot&module=CP01&section=catalog_domain")
    assert 'data-drill-level="section"' in section and "data-drill-events" in section
    events = section.split("data-drill-events", 1)[1].split("</table>", 1)[0]
    assert "Dos" in events or "dos@d.com" in events
    assert '<strong aria-current="page">Catálogo · Dominio</strong>' in section and "1 registro(s)" in section

    none_section = _hx(c, "/admin/activity?vertical=copilot&module=CP01&section=_none")
    events = none_section.split("data-drill-events", 1)[1].split("</table>", 1)[0]
    assert events.split("<tbody>", 1)[1].count("<tr>") == 4                       # a1, a2, reload de a2 y la visita v1
    assert "nav_type=" in events and "payload" not in events


def test_event_list_shows_outcome_badges_and_only_whitelisted_attrs(logdir, monkeypatch):
    t = _hx(_client(monkeypatch), "/admin/activity?vertical=copilot&module=_none&section=_none&kind=outcome")
    events = t.split("data-drill-events", 1)[1].split("</table>", 1)[0]
    assert "status-badge--activo\">Éxito" in events and "status-badge--inactivo\">Falla" in events
    assert "outcome=success" in events


# ── inventario sin actividad ─────────────────────────────────────────────────

def test_zero_activity_inventory_matches_real_templates():
    found = set()
    for path in _TEMPLATES.rglob("*.html"):
        found |= set(re.findall(r'data-dw-section="([a-z][a-z0-9_]+)"', path.read_text(encoding="utf-8")))
    inventory = set(activity_dashboard.SECTION_INVENTORY)
    assert found <= inventory, found - inventory           # toda sección literal está inventariada
    templated = {"catalog_cds", "catalog_sql"}             # de catalog_{{ catalog_kind }} (cds/sql)
    assert inventory - found == templated


def test_zero_activity_modules_appear_with_zero_even_without_any_activity(logdir, monkeypatch):
    t = _hx(_client(monkeypatch), "/admin/activity?period=today&vertical=commercial")
    rows = _drill_rows(t)
    assert {"CM01", "CM02", "CM03"} <= set(rows) and all(rows[k][3] for k in ("CM01", "CM02", "CM03"))
    assert "Sin actividad" in t


# ── visibilidad ──────────────────────────────────────────────────────────────

def test_viewer_without_cchh_never_sees_human_capital(logdir, monkeypatch):
    c = _client(monkeypatch, cchh=False)
    t = _hx(c, "/admin/activity")
    assert _kpi(t, "Usuarios únicos") == "3"                # cuatro@ (solo CCHH) fuera
    assert _kpi(t, "Visitas") == "4"
    assert "human_capital" not in _drill_rows(t)
    assert "cuatro@d.com" not in t and "Capital Humano" not in t
    assert "cchh_candidate_contact" not in _select_block(t, "section")
    assert 'value="human_capital"' not in _select_block(t, "vertical")
    # Forzar la vertical por URL no la habilita (se ignora) y el detalle tampoco filtra nada de CCHH.
    forged = _hx(c, "/admin/activity?vertical=human_capital&module=_none&section=_none")
    assert "cuatro@d.com" not in forged and "CCHH" not in forged.split("data-drill-level", 1)[1]
    # Con acceso a CCHH sí aparece.
    owner = _hx(_client(monkeypatch, cchh=True), "/admin/activity")
    assert _drill_rows(owner)["human_capital"][0] == 1 and _kpi(owner, "Usuarios únicos") == "4"


def test_unauthorized_viewer_gets_403(logdir, monkeypatch):
    c = _client(monkeypatch)
    monkeypatch.setattr(access, "APP_OWNER", "otro@danwor.com")
    assert c.get("/admin/activity").status_code == 403


# ── estado en la URL ─────────────────────────────────────────────────────────

def test_url_state_round_trips(logdir, monkeypatch):
    c = _client(monkeypatch)
    url = "/admin/activity?period=7d&vertical=copilot&module=CP01&kind=navigation&user=uno%40d.com"
    first = _hx(c, url)
    assert 'option value="7d" selected' in first
    assert 'option value="copilot" selected' in _select_block(first, "vertical")
    assert 'option value="CP01" selected' in _select_block(first, "module")
    assert 'option value="navigation" selected' in _select_block(first, "kind")
    # Las filas del drill-down conservan TODO el estado y suman un nivel.
    href = re.search(r'hx-get="(/admin/activity\?[^"]*section=catalog_domain[^"]*)"', first).group(1)
    q = parse_qs(urlparse(href.replace("&amp;", "&")).query)
    assert q == {"period": ["7d"], "vertical": ["copilot"], "module": ["CP01"], "section": ["catalog_domain"],
                 "kind": ["navigation"], "user": ["uno@d.com"]}
    # La página completa (recarga/compartir) reconstruye lo mismo que el fragmento HTMX.
    full = c.get(url).text
    assert _drill_rows(full) == _drill_rows(first) and _kpi(full, "Visitas") == _kpi(first, "Visitas")


# ── paneles existentes ───────────────────────────────────────────────────────

def test_existing_panels_still_render_including_v1_and_v2_stories(logdir, monkeypatch):
    t = _hx(_client(monkeypatch), "/admin/activity")
    for sid in ("resumen", "desglose", "tendencia", "modulos-usuarios", "eventos-catalogo", "reacciones",
                "preguntas", "arboles"):
        assert f'data-section-id="{sid}"' in t, sid
    assert "pregunta v1" in t                               # historia v1 intacta
    assert "texto no disponible" in t                       # v2 sin texto en memoria: se muestra con nota
    assert "2 pregunta(s)" in t
    assert "Árbol de prueba" in t
    assert "1 reacción(es)" in t
    assert "Detalle disponible por" not in t                                   # nota de retención retirada de la UI
    # Paneles históricos responden al período/vertical/usuario.
    ops = _hx(_client(monkeypatch), "/admin/activity?vertical=operations")
    assert "pregunta v1" not in ops and "1 reacción(es)" in ops
    fragment = _hx(_client(monkeypatch), "/admin/activity/stories?date_from=2026-10-19&date_to=2026-10-21")
    assert "pregunta v1" in fragment and "texto no disponible" in fragment


def test_operations_entry_point_renders_same_dashboard(logdir, monkeypatch):
    from webapp import hub_metrics_access
    monkeypatch.setattr(hub_metrics_access, "HUB_METRICS_ENABLED_VERTICALS", "operations", raising=False)
    t = _client(monkeypatch).get("/operaciones/metricas?vertical=operations").text
    assert 'data-section-id="desglose"' in t and _drill_rows(t)["OP01"][0] == 1
