"""Informe de Cliente → «Regenerar N Assessments»: advertencia consolidada (solo con elegibles), modelos ÚNICOS (varios flujos del mismo AM = una
regeneración), la MISMA regeneración individual (``assessment_service.generate``), progreso por ``operation_registry``, resumen actualizados / omitidos /
fallidos, preservación del informe anterior ante una falla, anti doble clic / concurrencia por cliente, permisos, aislamiento entre clientes y bloqueo
durante una activación de ruleset en transición."""
from __future__ import annotations

import pytest

import webapp.main as main
from tests.webapp import test_catalog_assessment_routes as cat
from tests.webapp.test_catalog_assessment_routes import ACME, HX, ORIGIN, _flow, client_for
from tools.catalog import catalog_service as cs
from tools.catalog.methodological_judgment import RULESET_V4, RULESET_V5
from webapp.services import assessment_service as svc
from webapp.services import catalog_reads
from webapp.services import client_assessment_regeneration as regen
from webapp.services import judgment_activation as ja
from webapp.services import operation_registry

OWNER = "t@d.com"


@pytest.fixture()
def world(cat_world, monkeypatch):
    """El mundo del Catálogo + un SEGUNDO flujo del mismo AM de Acme; el hilo de la operación corre en línea (determinista)."""
    entries = [*cat_world.entries, _flow("Acme", ACME, "acme-4am-b", dominio="Dom B")]
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))

    class Inline:
        def __init__(self, target=None, **kw):
            self._t = target

        def start(self):
            self._t()
    monkeypatch.setattr(regen.threading, "Thread", Inline)
    regen._running.clear()
    cat_world.entries = entries
    yield cat_world
    regen._running.clear()


cat_world = cat.world


def _make_stale(w):
    f = w.inbound / "Acme/views/1VR_RAW.json"
    f.write_text(f.read_text(encoding="utf-8").replace('"A"', '"A9"', 1), encoding="utf-8")


def _report(c, flow="acme-4am") -> str:
    return c.get(f"/catalog/client/assessment?flow={flow}", headers=HX).text


def _regen(c, flow="acme-4am"):
    return c.post(f"/catalog/client/assessment/regenerate?flow={flow}", headers={**ORIGIN, **HX})


def _events(run_id):
    return operation_registry.operation_events(run_id, OWNER, 0)["events"]


def test_no_warning_when_nothing_is_stale(world):
    c = client_for()
    cat._post(c)                                                                # CURRENT
    html = _report(c)
    assert "data-cli-regen" not in html and "Assessments desactualizados" not in html
    r = _regen(c)
    assert r.status_code == 200 and r.json()["outcome"] == regen.NOTHING_TO_DO


def test_stale_models_are_counted_once_even_with_several_flows_of_the_same_am(world):
    c = client_for()
    cat._post(c)
    _make_stale(world)
    html = _report(c)
    assert "data-cli-regen" in html and "Regenerar 1 Assessment" in html and "2 flujos del catálogo comparten este modelo" in html


def test_regeneration_updates_the_stale_model_once_and_refreshes_the_indicators(world, monkeypatch):
    c = client_for()
    cat._post(c)
    _make_stale(world)
    calls = []
    real = svc.generate
    monkeypatch.setattr(svc, "generate", lambda e, **k: (calls.append(e.entry_id), real(e, **k))[1])
    monkeypatch.setattr(regen.svc, "generate", svc.generate)
    r = _regen(c)
    assert r.status_code == 202 and r.json()["total"] == 1
    ev = _events(r.json()["run_id"])
    done = [e for e in ev if e["t"] == "done"][0]
    assert done["state"] == "SUCCESS" and done["summary"] == {"updated": 1, "skipped": 0, "failed": 0, "total": 1}
    assert [e for e in ev if e["t"] == "progress"][0]["am"] == "4AM_ACME" and len(calls) == 1          # un solo AM, una sola regeneración
    assert svc.status_for_entry(world.entries[0]).state == svc.CURRENT
    html = _report(c)
    assert "data-cli-regen" not in html                                         # sin elegibles: la advertencia desaparece


def test_an_individual_failure_keeps_the_previous_report_and_is_reported_as_failed(world, monkeypatch):
    c = client_for()
    cat._post(c)
    _make_stale(world)
    monkeypatch.setattr(svc, "_engine_assess", lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    r = _regen(c)
    done = [e for e in _events(r.json()["run_id"]) if e["t"] == "done"][0]
    assert done["state"] == "FAILED" and done["summary"]["failed"] == 1 and done["summary"]["updated"] == 0
    st = svc.status_for_entry(world.entries[0])
    assert st.state == svc.FAILED and st.profile is not None                    # el perfil anterior se conserva (contrato de la regeneración individual)


def test_partial_success_and_skips_are_summarized(world):
    snap = {"client": "X", "assets": [{"entry_id": "a", "asset_id": "AM1", "state": svc.STALE}, {"entry_id": "b", "asset_id": "AM2", "state": svc.INCOMPATIBLE_SCHEMA},
                                      {"entry_id": "c", "asset_id": "AM3", "state": svc.STALE}, {"entry_id": "d", "asset_id": "AM4", "state": svc.CURRENT}]}
    entries = {k: type("E", (), {"entry_id": k})() for k in "abcd"}
    targets = regen.eligible_targets(snap, entries)
    assert [t.terminal_am for t in targets] == ["AM1", "AM2", "AM3"]           # nunca los vigentes
    outcome = {"a": svc.CURRENT, "b": svc.FAILED}
    before = {"a": svc.STALE, "b": svc.STALE, "c": svc.CURRENT}                  # «c» se actualizó por otra vía antes de su turno
    st = lambda state: svc.AssessmentStatus(state, "x", "X", "AM")             # noqa: E731
    out = regen.start("X", targets, OWNER, pack="p", result_url=None, generate=lambda e: st(outcome[e.entry_id]), status_of=lambda e: st(before[e.entry_id]), run_async=False)
    done = [e for e in _events(out["run_id"]) if e["t"] == "done"][0]
    assert done["state"] == "PARTIAL" and done["summary"] == {"updated": 1, "skipped": 1, "failed": 1, "total": 3}


def test_double_click_and_concurrent_operations_never_start_a_second_run(world):
    c = client_for()
    cat._post(c)
    _make_stale(world)
    regen._running["acme"] = ("run-existente", OWNER)                           # operación en curso para el cliente
    r = _regen(c)
    assert r.status_code == 409 and r.json()["outcome"] == regen.ALREADY_RUNNING and r.json()["run_id"] == "run-existente"
    other = client_for("otro@d.com")
    r2 = _regen(other)
    assert r2.status_code == 409 and r2.json()["run_id"] is None                # otro usuario no ve la operación ajena
    assert "Regeneración en curso" in _report(c)


def test_permissions_origin_and_client_isolation(world):
    c = client_for()
    cat._post(c)
    _make_stale(world)
    assert cat.anon().post("/catalog/client/assessment/regenerate?flow=acme-4am", headers={**ORIGIN, **HX}).status_code == 401
    assert c.post("/catalog/client/assessment/regenerate?flow=acme-4am", headers={"Origin": "http://evil.example", **HX}).status_code == 403
    r = _regen(c, flow="solo-na")                                                # el cliente sale del flujo: Solo no tiene elegibles y Acme no se toca
    assert r.status_code == 200 and r.json()["outcome"] == regen.NOTHING_TO_DO
    assert svc.status_for_entry(world.entries[0]).state == svc.STALE


def test_blocked_while_a_ruleset_activation_is_in_transition(world, tmp_path, monkeypatch):
    c = client_for()
    cat._post(c)
    _make_stale(world)
    marker = tmp_path / "ruleset-activation.json"
    monkeypatch.setattr(ja, "marker_path", lambda: marker)
    ja.write_marker(ja.ACTIVATING, ruleset_version=RULESET_V5, previous_ruleset_version=RULESET_V4, plan_digest="d" * 64, operator="op", path=marker)
    try:
        r = _regen(c)
        assert r.status_code == 409 and r.json()["outcome"] == regen.BLOCKED_TRANSITION
        html = _report(c)
        assert "data-cli-regen-blocked" in html and "data-cli-regen-start" not in html
        assert svc.status_for_entry(world.entries[0]).state == svc.STALE
    finally:
        ja.remove_marker(marker)


def test_rest_state_is_compact_and_layout_rules_are_scoped_without_own_look(world):
    """Reposo: callout con la tipografía de contenido del informe (`.methodology`), acción en el MISMO `.support-links` del informe individual y los
    estados iniciando / ejecutando / finalizado presentes pero `hidden` (sin reservar espacio). El CSS del bloque es SOLO distribución, acotado a
    `[data-cli-regen]`: la barra no hereda el margen del asistente (`.progress` 22px/30px) y no hay colores, fuentes ni bordes propios."""
    import re
    from pathlib import Path
    c = client_for()
    cat._post(c)
    _make_stale(world)
    html = _report(c)
    block = html[html.index("data-cli-regen "): html.index("Indicadores del cliente")]
    assert '<div class="methodology" data-cli-regen ' in html
    assert re.search(r'<div class="support-links">\s*<button type="button" class="support-link asr-generate" data-cli-regen-start', block)
    for hook in ("data-cli-regen-note ", "data-cli-regen-progress ", "data-cli-regen-summary "):
        assert re.search(hook + r'[^>]*hidden', block), hook
    assert 'role="progressbar"' in block and 'aria-label="Avance de la regeneración"' in block
    css = (Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    rules = re.findall(r"(?m)^(\[data-cli-regen\][^{]*)\{([^}]*)\}", css)
    assert rules and any(".progress" in sel and "margin: 8px 0 0" in body for sel, body in rules)
    assert not [b for _, b in rules if re.search(r"(?<![-\w])(color|background|border|font)[\w-]*\s*:", b)]   # solo layout
