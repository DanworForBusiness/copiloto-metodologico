"""
Métricas de Uso v2 — fase 2: barrido de instrumentación.

- `section_view` (cliente, solo disparos humanos) aceptado por /telemetry/event
  únicamente con un slug válido; el servidor fija kind/surface/section.
- Emisores duplicados/legados retirados (`catalog_view`, `module_viewed`,
  `governed_contracts_*` server-side): aserciones de fuente, que es donde el
  comportamiento es difícil de observar sin levantar cada pantalla.
- `page_view` atribuye el módulo de las páginas que antes escribían
  `module_viewed` (CP/OP/CM) y "Top módulos" cuenta esas visitas.
- Capital Humano: acciones `cchh.*` sin PII y visibles solo para viewers de
  Metrics que además tienen acceso a CCHH.

Disco a `tmp_path` vía monkeypatch de `ACTIVITY_LOG_DIR` (mismo patrón que
test_activity_v2.py); nunca se toca os.environ.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp import hub_metrics_access
from webapp.main import app
from webapp.services import activity_analytics, activity_catalog, activity_service, auth_service

_ROOT = Path(__file__).resolve().parents[2]
_WEBAPP = _ROOT / "webapp"
_SESSION = auth_service.create_session(
    {"email": "v2-instr@danwor.com", "name": "V2 Instr", "first_name": "V2", "family_name": "Instr"}
)
# Claves que JAMÁS pueden ser atributos de una acción de CCHH (PII / contenido libre).
_PII_KEYS = {
    "name", "first_name", "last_name", "full_name", "display_name", "email", "phone", "linkedin_url", "location",
    "notes", "observaciones", "observacion", "filename", "file", "cv", "cv_text", "text", "q", "query",
    "nombre_puesto", "descripcion", "jd_texto", "palabras_clave", "posicion", "profile_id", "busqueda_id",
    "application_id", "entrevista_id", "remuneracion_pretendida", "skills", "profile_skills", "tags", "message",
}


@pytest.fixture
def logdir(monkeypatch, tmp_path):
    monkeypatch.setattr(activity_service, "LOG_ACTIVITY", "BASIC")   # section_view es navegación básica
    for mod in (activity_service, activity_analytics):
        monkeypatch.setattr(mod, "ACTIVITY_LOG_DIR", tmp_path)
    return tmp_path


@pytest.fixture
def client():
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, _SESSION)
    return c


def _records(logdir):
    out = []
    for p in sorted(logdir.glob("activity_*.jsonl")):
        out += [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
    return out


def _src(rel: str) -> str:
    return (_WEBAPP / rel).read_text(encoding="utf-8")


# ── section_view ─────────────────────────────────────────────────────────────

def test_section_view_written_as_navigation_partial_with_valid_slug(logdir, client):
    r = client.post("/telemetry/event", json={
        "event": "section_view", "section": "catalog_flow", "view_id": "pv123",
        "path": "/modules/01_Arquitectura_y_Conectividad?tab=catalogo", "module_id": "CP01",
    })
    assert r.status_code == 204
    (rec,) = _records(logdir)
    assert rec["event"] == "section_view" and rec["kind"] == "navigation" and rec["surface"] == "partial"
    assert rec["section"] == "catalog_flow"
    assert (rec["scope"], rec["area"], rec["module"]) == ("copilot", "methodology", "CP01")
    assert rec["attrs"] == {"view_id": "pv123", "path": "/modules/01_Arquitectura_y_Conectividad"}


def test_section_view_without_module_resolves_origin_by_path(logdir, client):
    client.post("/telemetry/event", json={"event": "section_view", "section": "cchh_candidate_contact",
                                          "view_id": "pv9", "path": "/cchh/busquedas/abc"})
    (rec,) = _records(logdir)
    assert (rec["scope"], rec["area"]) == ("human_capital", "cchh") and "module" not in rec


@pytest.mark.parametrize("bad", ["", "Catalog_flow", "catalog flow", "catalog-flow", "1catalog", "x",
                                 "a" * 49, "catalog_flow<script>", None, 42, ["catalog_flow"]])
def test_section_view_with_invalid_slug_is_dropped(logdir, client, bad):
    r = client.post("/telemetry/event", json={"event": "section_view", "section": bad, "view_id": "pv1",
                                              "path": "/catalog/flow"})
    assert r.status_code == 204
    assert _records(logdir) == []


def test_section_view_with_unknown_module_id_is_dropped(logdir, client):
    client.post("/telemetry/event", json={"event": "section_view", "section": "catalog_flow",
                                          "view_id": "pv1", "path": "/x", "module_id": "ZZ99"})
    assert _records(logdir) == []


def test_section_view_client_only_counts_trusted_human_triggers():
    js = _src("static/app.js")
    block = js.split("function dwSectionViews()", 1)[1].split("})();", 1)[0]
    assert '"htmx:afterSwap"' in block
    assert "triggeringEvent" in block and "isTrusted === true" in block
    assert "dwHuman === true" in block            # única excepción: htmx.trigger declarado humano por su caller
    assert 'event: "section_view"' in block and "location.pathname" in block
    # dw-sap-open / dw-registry-open propagan el isTrusted del cambio REAL del dropdown.
    assert "dwHuman: !!(humanEvent && humanEvent.isTrusted)" in js
    assert "$refs.registryOpen, $event)" in _src("templates/partials/catalog_tab.html")


def test_every_static_section_marker_is_a_valid_slug():
    found = set()
    for path in (_WEBAPP / "templates").rglob("*.html"):
        for slug in re.findall(r'data-dw-section="([^"]+)"', path.read_text(encoding="utf-8")):
            if "{{" in slug:
                continue
            assert activity_catalog.is_valid_section_slug(slug), (path.name, slug)
            found.add(slug)
    assert {"catalog_flow", "catalog_sap_types", "catalog_connectivity_registry", "catalog_domain",
            "catalog_lob", "catalog_client", "governed_contracts_detail", "cchh_candidate_contact"} <= found


# ── emisores retirados ───────────────────────────────────────────────────────

def test_server_side_navigation_emitters_are_gone():
    for rel in ("main.py", "routers/catalog_assessment.py", "routers/governed_contracts_ui.py"):
        src = _src(rel)
        assert 'log_verbose("catalog_view"' not in src, rel
        assert 'log_verbose("governed_contracts_' not in src, rel
    for path in (_WEBAPP / "routers").glob("*.py"):
        src = path.read_text(encoding="utf-8")
        assert "EVENT_MODULE_VIEWED" not in src, path.name
        assert 'log_activity("module_viewed"' not in src, path.name
    # Siguen catalogados como compatibilidad de LECTURA v1.
    for name in ("catalog_view", "module_viewed", "governed_contracts_list", "governed_contracts_detail"):
        assert activity_catalog.spec_of(name) is not None


@pytest.mark.parametrize("rel, expected", [
    ("routers/modules.py", '"dw_module_id": _persisted_module'),
    ("routers/staffing.py", '"dw_module_id": OP01_MODULE_ID'),
    ("routers/commercial.py", '"dw_module_id": CM01_MODULE_ID'),
    ("routers/commercial_strategy.py", '"dw_module_id": CM02_MODULE_ID'),
    ("routers/commercial_presentations.py", '"dw_module_id": CM03_MODULE_ID'),
])
def test_pages_that_had_module_viewed_expose_dw_module_id(rel, expected):
    assert expected in _src(rel)


@pytest.mark.parametrize("module_id, scope, area", [
    ("CP01", "copilot", "methodology"), ("OP01", "operations", "staffing"),
    ("CM01", "commercial", "crm"), ("CM02", "commercial", "strategy"), ("CM03", "commercial", "presentations"),
])
def test_page_view_attributes_module_for_cp_op_cm(logdir, client, module_id, scope, area):
    client.post("/telemetry/event", json={"event": "page_view", "view_id": f"pv{module_id}", "nav_type": "navigate",
                                          "path": "/x", "module_id": module_id})
    (rec,) = _records(logdir)
    assert (rec["scope"], rec["area"], rec["module"]) == (scope, area, module_id)


def test_top_modules_counts_v2_page_views_dedup_by_view_id_and_skips_reloads(logdir):
    def pv(view_id, nav="navigate", module="OP01"):
        return {"v": 2, "id": view_id + nav, "ts_utc": "2026-11-01T10:00:00+00:00", "kind": "navigation",
                "event": "page_view", "user": "a@danwor.com", "vertical": "operations", "scope": "operations",
                "area": "staffing", "module": module, "surface": "page",
                "attrs": {"view_id": view_id, "nav_type": nav, "path": "/operaciones/staffing/asignaciones"}}
    with (logdir / "activity_20261101.jsonl").open("w", encoding="utf-8") as fh:
        for rec in (pv("a1"), pv("a1"), pv("a2"), pv("a3", "reload")):
            fh.write(json.dumps(rec) + "\n")
    summary = activity_analytics.build_summary("2026-11-01", "2026-11-01")
    (row,) = [r for r in summary["top_modules"] if r["key"] == "OP01"]
    assert row["count"] == 2


# ── Capital Humano ───────────────────────────────────────────────────────────

def _calls(src: str, func: str):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == func:
            yield node


def test_cchh_catalog_events_have_no_pii_attrs():
    cchh = [s for s in activity_catalog.SPECS.values() if s.name.startswith("cchh.")]
    assert len(cchh) >= 10
    for spec in cchh:
        assert (spec.scope, spec.area, spec.kind) == ("human_capital", "cchh", "action"), spec.name
        assert "outcome" in spec.attrs and not (spec.attrs & _PII_KEYS), spec.name


def test_every_cchh_log_call_uses_only_catalog_allowed_attrs():
    seen = set()
    for call in _calls(_src("routers/human_capital.py"), "_log_cchh"):
        event = call.args[1].value
        keys = {kw.arg for kw in call.keywords}
        spec = activity_catalog.spec_of(event)
        assert spec is not None and spec.scope == "human_capital", event
        assert keys <= spec.attrs, (event, keys - spec.attrs)
        assert not keys & _PII_KEYS, event
        seen.add(event)
    assert {"cchh.profile_created", "cchh.profile_updated", "cchh.cv_uploaded", "cchh.profiles_imported",
            "cchh.profiles_consolidated", "cchh.search_created", "cchh.search_closed"} <= seen
    (ops_call,) = list(_calls(_src("routers/operations_busquedas.py"), "log_activity"))
    assert ops_call.args[0].value == "cchh.search_requested"
    assert {kw.arg for kw in ops_call.keywords} == {"user", "scope", "area", "outcome"}


def test_cchh_sink_drops_pii_even_if_a_producer_passes_it(logdir):
    activity_service.log_activity("cchh.profile_created", user={"sub": "hc@danwor.com"}, scope="human_capital",
                                  area="cchh", outcome="success", with_cv=True, first_name="Ana",
                                  email="ana@x.com", filename="cv_ana.pdf", notes="texto libre")
    (rec,) = _records(logdir)
    assert rec["attrs"] == {"outcome": "success", "with_cv": True}


def _viewer(monkeypatch, *, cchh: bool):
    email = "metrics-viewer@danwor.com"
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", email if cchh else "")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    from webapp import access
    monkeypatch.setattr(access, "APP_OWNER", "")
    return {"sub": email, "email": email}


def test_cchh_visibility_only_for_viewers_with_cchh_access(monkeypatch):
    scopes, areas = hub_metrics_access.metrics_visibility_for(_viewer(monkeypatch, cchh=False))
    assert "human_capital" not in scopes and "cchh" not in areas
    scopes, areas = hub_metrics_access.metrics_visibility_for(_viewer(monkeypatch, cchh=True))
    assert "human_capital" in scopes and "cchh" in areas


# ── outcomes de operaciones largas ───────────────────────────────────────────

def test_outcome_mapping_and_long_operations_carry_it():
    assert [activity_catalog.outcome_of(s) for s in ("SUCCESS", "PARTIAL", "FAILED", "BLOCKED", "aborted",
                                                     "incomplete", "ok", "rare")] == \
        ["success", "partial", "failed", "rejected", "rejected", "partial", "success", "failed"]
    for event in ("connectivity_inventory_run", "connectivity_catalog_publish", "datasphere_assessment_run",
                  "datasphere_publication_run", "catalog_regenerate", "catalog_apis_sync"):
        assert {"outcome", "duration_s"} <= activity_catalog.allowed_attrs(event), event
    srcs = {rel: _src(rel) for rel in ("main.py", "routers/connectivity_inventory.py",
                                        "routers/datasphere_assessment.py", "routers/datasphere_publication.py")}
    for rel, src in srcs.items():
        for call in _calls(src, "log_activity"):
            event = call.args[0].value if call.args and isinstance(call.args[0], ast.Constant) else ""
            if event in ("connectivity_inventory_run", "connectivity_catalog_publish", "datasphere_assessment_run",
                         "datasphere_publication_run", "catalog_regenerate", "catalog_apis_sync"):
                assert "outcome" in {kw.arg for kw in call.keywords}, (rel, event)
