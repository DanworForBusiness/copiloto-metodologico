"""Funnel de Arquitectura e Innovación en Operaciones → Solicitudes: el motor de árboles del Copiloto como capacidad del Hub.

- Un solo motor: el YAML nativo vive en `hub_trees/operaciones/` y lo recorren `wizard_service`, `partials/wizard.html` y `/wizards/*`.
- Misma autorización que «Nueva observación de Arquitectura» (servidor, por ítem).
- Solo asiste: un run de contexto «operaciones» no escribe case_library, no ofrece el feedback de árboles ni se mide como Copiloto.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp import copilot_runtime
from webapp.main import app
from webapp.routers import solicitudes as routes
from webapp.routers import wizard as wizard_routes
from webapp.services import solicitudes_repository as repository
from webapp.services import wizard_service as ws
from webapp.templating import templates

ROOT = Path(__file__).resolve().parents[2]
client = TestClient(app)
TREE = ws.hub_tree("operaciones", "funnel_arquitectura_innovacion")


def _walk(answers):
    store = ws.WizardStore(case_repository=None)
    run = store.start(TREE, context="operaciones")
    for answer in answers:
        run = store.answer(run.id, answer)
        assert not run.error, run.error
    return run.leaf["decision"]


@pytest.mark.parametrize("answers,decision", [
    (["Sí", "No"], "Asignar colaborador"),
    *[(["Sí", "Sí", area], "Asignar a Arquitectura & Innovación")
      for area in ("Metodología", "Arquitectura", "Compatibilidad/Roadmap", "Configuración/Integración", "Innovación y Estrategia")],
    (["Sí", "Sí", "Otra área"], "Validar con Operaciones"),
    (["No", "No"], "Validar con Operaciones"),
    (["No", "Sí", "Base Instalada"], "Asignar colaborador"),
    (["No", "Sí", "New Name"], "Validar con Operaciones"),
])
def test_every_path_of_the_reference_tree(answers, decision):
    assert _walk(answers) == decision


def test_tree_uses_the_native_contract_and_keeps_the_reference_explanations():
    assert TREE["start"] == "q_conocimiento" and TREE["nodes"]["q_conocimiento"]["question"].startswith("¿La demanda es estratégica")
    for node in TREE["nodes"].values():
        assert node["type"] == "single_choice" and set(node["options"]) == set(node["next"])
    leaves = TREE["leaves"]
    assert "marco técnico definido" in leaves["leaf_asignar_colaborador"]["detail"]
    assert "primera revisión y validación" in leaves["leaf_asignar_arquitectura_innovacion"]["detail"]
    assert any("n colaboradores" in n for n in leaves["leaf_asignar_arquitectura_innovacion"]["notes"])
    guardrail = leaves["leaf_validar_operaciones"]
    assert "validada por Operaciones" in guardrail["detail"] and guardrail["post_actions"]["mode"] == "needs_human_review"   # instancia humana
    assert ws.hub_tree("../datasphere_core", "x") is None and ws.hub_tree("operaciones", "nope") is None


def test_operations_runs_never_persist_cases_while_copilot_runs_still_do():
    calls = []

    class Repo:
        def create_for_run(self, run):
            calls.append(run.context)

    store = ws.WizardStore(case_repository=Repo())
    run = store.start(TREE, context="operaciones")
    for answer in ("No", "No"):
        run = store.answer(run.id, answer)
    assert run.leaf and calls == []
    copilot = store.start({"start": "q", "nodes": {"q": {"type": "single_choice", "options": ["A"], "next": {"A": "l"}}},
                           "leaves": {"l": {"decision": "x"}}})
    store.answer(copilot.id, "A")
    assert calls == ["copilot"]


def test_wizard_shows_tree_feedback_only_for_copilot_runs():
    store = ws.WizardStore(case_repository=None)
    run = store.start(TREE, context="operaciones")
    run = store.answer(run.id, "No"); run = store.answer(run.id, "No")
    html = templates.get_template("partials/wizard.html").render(run=run, node=run.node, leaf=run.leaf, pack="datasphere_core")
    assert "Validar con Operaciones" in html and 'id="tree-feedback"' not in html and "Nueva evaluaci" in html


def _item_env(monkeypatch, architecture):
    monkeypatch.setattr(routes, "_user", lambda request: {"sub": "arq@danwor.com"})
    monkeypatch.setattr(repository, "read_item", lambda *a: {"id": "12", "fields": {}})
    monkeypatch.setattr(repository, "edit_permissions", lambda *a: (True, architecture))


def test_funnel_requires_the_same_architecture_authorization(monkeypatch):
    _item_env(monkeypatch, False)
    assert client.post("/operaciones/solicitudes/12/funnel", data={"graph_token": "t"}).status_code == 403


def test_funnel_opens_the_shared_wizard_and_its_steps_are_guarded_by_operations(monkeypatch):
    _item_env(monkeypatch, True)
    r = client.post("/operaciones/solicitudes/12/funnel", data={"graph_token": "t"})
    assert r.status_code == 200 and 'id="wizard-shell"' in r.text and "conocimiento como activo Danwor" in r.text
    assert 'class="tree-tab-panel"' in r.text and "se registra en el historial" in r.text
    run_id = next(rid for rid, run in copilot_runtime.store._runs.items() if run.context == "operaciones")
    monkeypatch.setattr(wizard_routes.auth_service, "session_user", lambda request: None)
    assert client.post(f"/wizards/{run_id}/answer", data={"answer": "No"}).status_code == 403       # sin sesión de Operaciones: no avanza
    assert client.post(f"/wizards/{run_id}/feedback", data={"rating": "up"}).status_code in (403, 404)


def test_button_lives_in_the_page_header_with_the_observation_condition_and_hub_treatment():
    hero = (ROOT / "webapp/templates/partials/solicitudes_detail_hero.html").read_text(encoding="utf-8")
    assert "{% if item_id and funnel_action is defined and funnel_action %}" in hero and "hc-profiles-hero-actions" in hero
    assert 'class="btn-primary chart-tooltip-anchor is-tooltip-below is-tooltip-end' in hero and "chart-tooltip--label" in hero
    assert ">Funnel<" in hero and "Pasar por el Funnel" in hero and "dw-funnel-open" in hero
    router = (ROOT / "webapp/routers/solicitudes.py").read_text(encoding="utf-8")
    assert '"funnel_action": bool(item) and template == "partials/solicitudes_edit.html" and can_edit_architecture' in router
    page = (ROOT / "webapp/templates/operations/solicitudes.html").read_text(encoding="utf-8")
    assert 'class="copilot-panel solicitudes-funnel-panel"' in page and "data-funnel-panel hidden" in page    # oculto por defecto
    assert "panel-toggle" not in page[page.index("solicitudes-funnel-panel"):]                                 # sin cabecera externa


def test_panel_composition_is_a_renderer_option_and_copilot_is_unchanged():
    store = ws.WizardStore(case_repository=None)
    render = lambda run: templates.get_template("partials/wizard.html").render(run=run, node=run.node, leaf=run.leaf, pack="datasphere_core")
    funnel = render(store.start(TREE, context="operaciones", presentation={"eyebrow": "Funnel de Arquitectura", "close_event": "dw-funnel-close"}))
    assert "Funnel de Arquitectura</span>" in funnel and "&Aacute;rbol YAML" not in funnel
    assert "wizard-heading__tools" in funnel and "$dispatch('dw-funnel-close')" in funnel and 'class="progress"' in funnel
    copilot = render(store.start(TREE))
    assert "&Aacute;rbol YAML" in copilot and "wizard-heading__tools" not in copilot and "panel-close" not in copilot


# ── Registro automático en el historial, snapshot, eliminación de simulaciones ───────────────────────────────────────────────────────
import yaml as _yaml

from webapp.services import solicitudes_funnel as funnel
from webapp.services import solicitudes_journal as journal

_USER = {"name": "Ana Arquitecta", "email": "ana@danwor.com", "sub": "ana@danwor.com"}


@pytest.fixture
def funnel_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(funnel, "FEEDBACK_LOG_DIR", tmp_path)
    return tmp_path / "solicitudes_funnel" / "12"


def _complete(store, answers=("No", "No")):
    run = store.start(TREE, context="operaciones", subject={"item_id": "12"})
    for a in answers:
        run = store.answer(run.id, a)
    return run


def test_only_completed_evaluations_are_recorded_once_with_their_snapshot(funnel_dir):
    store = ws.WizardStore(case_repository=None)
    partial = store.start(TREE, context="operaciones", subject={"item_id": "12"})
    store.answer(partial.id, "No")
    assert funnel.record(partial, item_id="12", actor=_USER) is False                                   # incompleto: nunca
    run = _complete(store)
    assert funnel.record(run, item_id="12", actor=_USER) is True
    assert funnel.record(run, item_id="12", actor=_USER) is False                                        # refresco / reintento: sin duplicar
    [saved] = list(funnel_dir.glob("*.yaml"))
    data = _yaml.safe_load(saved.read_text(encoding="utf-8"))
    assert data["result"] == "Validar con Operaciones" and data["actor"] == "Ana Arquitecta" and data["tree_version"] == "0.1"
    assert [s["answer"] for s in data["steps"]] == ["No", "No"]
    assert data["steps"][0]["question"].startswith("¿La demanda es estratégica desde el conocimiento")    # snapshot, no se relee el YAML
    store.restart(run.id)
    again = store.answer(store.answer(run.id, "Sí").id, "No")
    assert funnel.record(again, item_id="12", actor=_USER) is True and len(list(funnel_dir.glob("*.yaml"))) == 2   # evaluación nueva


def test_history_rows_and_discard_preserve_other_evaluations(funnel_dir):
    store = ws.WizardStore(case_repository=None)
    first, second = _complete(store), _complete(store, ("Sí", "No"))
    funnel.record(first, item_id="12", actor=_USER); funnel.record(second, item_id="12", actor=_USER)
    rows = funnel.history_rows("12")
    assert {r["text"] for r in rows} == {"Resultado: Validar con Operaciones", "Resultado: Asignar colaborador"}
    assert all(r["movement"] == "Funnel Operativo" and r["actor"] == "Ana Arquitecta" for r in rows)
    target = rows[0]["funnel"]["id"]
    assert funnel.discard("12", target, _USER) == "deleted" and funnel.discard("12", target, _USER) == "already"   # idempotente
    assert funnel.discard("13", target, _USER) is None
    assert [r["funnel"]["id"] for r in funnel.history_rows("12")] != [target] and len(funnel.history_rows("12")) == 1
    kept = _yaml.safe_load((funnel_dir / f"{target}.yaml").read_text(encoding="utf-8"))
    assert kept["deleted_at"] and kept["deleted_by"] == "ana@danwor.com"                                  # eliminación lógica durable
    sharepoint = {"id": "creation:12", "movement": "Apertura", "at": "2026-11-27T20:47:00+00:00"}
    merged = journal.merge_rows([sharepoint], funnel.history_rows("12"))
    assert merged[-1]["id"] == "creation:12" and len(merged) == 2                                         # SharePoint intacto


def test_engine_records_through_the_completion_hook_and_signals_the_page(monkeypatch, funnel_dir):
    _item_env(monkeypatch, True)
    monkeypatch.setattr(wizard_routes.auth_service, "session_user", lambda request: dict(_USER))
    monkeypatch.setattr(wizard_routes, "_authorized_run", lambda request, run_id: None)
    monkeypatch.setattr(routes.auth_service, "session_user", lambda request: dict(_USER))
    client.post("/operaciones/solicitudes/12/funnel", data={"graph_token": "t"})
    run_id = [rid for rid, run in copilot_runtime.store._runs.items() if run.context == "operaciones"][-1]
    assert client.post(f"/wizards/{run_id}/answer", data={"answer": "No"}).headers.get("HX-Trigger") is None
    done = client.post(f"/wizards/{run_id}/answer", data={"answer": "No"})
    assert done.headers.get("HX-Trigger") == "dw-tree-recorded" and "Validar con Operaciones" in done.text
    assert len(list(funnel_dir.glob("*.yaml"))) == 1
    assert copilot_runtime.store.get(run_id).presentation["eyebrow"] == "Funnel Operativo"


def test_discard_requires_architecture_authorization(monkeypatch, funnel_dir):
    _item_env(monkeypatch, False)
    r = client.post("/operaciones/solicitudes/12/funnel/0123456789abcdef0123/eliminar", data={"graph_token": "t"},
                    headers={"Origin": "http://testserver", "Referer": "http://testserver/operaciones/solicitudes/12/editar"})
    assert r.status_code == 403


def test_metrics_and_history_share_the_same_tree_run_timeline():
    metrics = (ROOT / "webapp/templates/partials/admin_activity_tree_runs.html").read_text(encoding="utf-8")
    history = (ROOT / "webapp/templates/partials/solicitudes_history.html").read_text(encoding="utf-8")
    assert "tree_run_timeline(" in metrics and '<ol class="story-timeline">' not in metrics
    assert "tree_run_timeline(" in history and "table_action_icon('delete')" in history and "'− ver menos' : '+ ver más'" in history
    html = templates.get_template("partials/tree_run_timeline.html").module.tree_run_timeline(
        "Funnel Operativo", [{"question": "¿Qué tipo de cliente?", "answer": "New Name"}], {"leaf": "Validar con Operaciones"}, "Inició la evaluación")
    assert "Inició la evaluación" in str(html) and "¿Qué tipo de cliente?" in str(html) and "Respondió <strong>New Name</strong>" in str(html)


def test_help_texts_only_on_the_four_defined_questions():
    helps = {k for k, n in TREE["nodes"].items() if n.get("help")}
    assert helps == {"q_conocimiento", "q_capacidades", "q_posicionamiento", "q_tipo_cliente"}


def test_discard_is_lightweight_204_without_rebuilding_history_from_sharepoint(monkeypatch, funnel_dir):
    _item_env(monkeypatch, True)
    monkeypatch.setattr(repository, "read_item_versions", lambda *a: pytest.fail("no debe releer versiones de SharePoint"))
    store = ws.WizardStore(case_repository=None)
    funnel.record(_complete(store), item_id="12", actor=_USER)
    eid = funnel.history_rows("12")[0]["funnel"]["id"]
    headers = {"Origin": "http://testserver", "Referer": "http://testserver/operaciones/solicitudes/12/editar"}
    r = client.post(f"/operaciones/solicitudes/12/funnel/{eid}/eliminar", data={"graph_token": "t"}, headers=headers)
    assert r.status_code == 204 and funnel.history_rows("12") == []
    again = client.post(f"/operaciones/solicitudes/12/funnel/{eid}/eliminar", data={"graph_token": "t"}, headers=headers)
    assert again.status_code == 204                                                                       # concurrente / reintento: idempotente
    history = (ROOT / "webapp/templates/partials/solicitudes_history.html").read_text(encoding="utf-8")
    assert 'data-dw-optimistic-remove="tr"' in history and 'hx-swap="none"' in history
