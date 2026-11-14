"""Modelado de vistas: decisiones, condiciones y continuidad sin cambiar el motor."""
import copy
import hashlib
import html
from html.parser import HTMLParser
import json
from pathlib import Path
import re

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webapp.services.content_service import load_module
from webapp.services.wizard_service import WizardStore

ID = 'm04_tipos_vistas_sql'
MODULE = '04_Modelado_y_Desarrollo'
ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / 'content_packs/datasphere_core/content/04_Modelado_y_Desarrollo.md'
DECL = 'Combinar fuentes, filtrar, calcular columnas o resumir datos en una vista'
ALGORITHM = 'Un algoritmo que repite pasos o cambia su ejecución según resultados intermedios'
FLOW = 'Un proceso de carga, deltas o Python'
PERFORMANCE = 'Mejorar el rendimiento o persistir una vista existente'
UNKNOWN = 'Todavía no puedo describir la transformación'
CODE = 'Como código: necesitamos SQL explícito o una convención de mantenimiento acordada'
VISUAL = 'Como modelo visual de fuentes, relaciones y operaciones mantenible por el equipo'
UNDECIDED = 'Todavía no está definido o faltan criterios para elegir'
SCENARIOS = [
    ([DECL, CODE], 'leaf_standard'),
    ([DECL, VISUAL], 'leaf_grafica'),
    ([DECL, UNDECIDED], 'leaf_revision_modalidad'),
    ([ALGORITHM], 'leaf_table_function'),
    ([FLOW], 'leaf_flujos_de_datos'),
    ([PERFORMANCE], 'leaf_revision_rendimiento'),
    ([UNKNOWN], 'leaf_revision_necesidad'),
]


def tree():
    return WizardStore.tree_by_id(ID)


def routes(t, node=None, trail=()):
    node = node or t['start']
    if node in t['leaves']:
        yield trail, node
        return
    assert node not in [step[0] for step in trail], 'ciclo'
    question = t['nodes'][node]
    assert set(question['options']) == set(question['next'])
    for answer in question['options']:
        yield from routes(t, question['next'][answer], (*trail, (node, answer)))


@pytest.mark.parametrize('answers,expected', SCENARIOS)
def test_seven_routes_execute_with_real_engine_and_support_back_restart(answers, expected):
    store = WizardStore()
    run = store.start(tree())
    for answer in answers:
        store.answer(run.id, answer)
        assert not run.error
    assert run.current == expected and run.leaf
    assert len(run.history) <= 2
    if answers[0] == DECL:
        assert len(run.history) == 2
    previous = run.history[-1][0]
    store.back(run.id)
    assert run.current == previous
    store.restart(run.id)
    assert run.current == tree()['start'] and not run.history


def test_graph_has_exactly_seven_reachable_routes_and_two_contextual_questions():
    t = tree()
    complete = list(routes(t))
    assert len(complete) == 7
    assert {end for _, end in complete} == set(t['leaves'])
    assert len(t['nodes']) == 2
    assert all(node.get('help') for node in t['nodes'].values())
    assert max(len(trail) for trail, _ in complete) == 2


@pytest.fixture
def http(monkeypatch):
    from webapp.routers import wizard
    isolated = WizardStore()
    monkeypatch.setattr(wizard, 'store', isolated)
    monkeypatch.setattr(wizard, 'log_verbose', lambda *a, **kw: None)
    monkeypatch.setattr(wizard.auth_service, 'session_user', lambda request: {})
    app = FastAPI()
    app.include_router(wizard.router)
    with TestClient(app) as client:
        yield client, isolated


def start(client, **selector):
    response = client.post(f'/modules/{MODULE}/trees/start', data=selector)
    assert response.status_code == 200
    body = html.unescape(response.text)
    assert 'Modelado de vistas' in body and '¿Qué cubre esta pregunta?' in body
    return re.search(r'/wizards/([a-f0-9]+)/answer', response.text).group(1)


@pytest.mark.parametrize('answers,expected', SCENARIOS)
def test_http_renders_actionable_results_or_follows_existing_flow_redirect(http, answers, expected):
    client, store = http
    run_id = start(client, tree_id=ID)
    for answer in answers:
        response = client.post(f'/wizards/{run_id}/answer', data={'answer': answer})
        assert response.status_code == 200
    original = store.get(run_id)
    assert original.current == expected
    body = html.unescape(response.text)
    if expected == 'leaf_flujos_de_datos':
        target = WizardStore.tree_by_id('m04_flujos_de_datos')
        assert target['title'] in body
        assert target['nodes'][target['start']]['question'] in body
        redirected_id = re.search(r'/wizards/([a-f0-9]+)/answer', response.text).group(1)
        assert store.get(redirected_id).tree['id'] == target['id']
    else:
        assert original.leaf['decision'] in body
        assert all(step in body for step in original.leaf['post_actions']['next_steps'])
        if expected.startswith('leaf_revision_'):
            assert 'Revisión' in body  # visible, no depender solo de mode/reason
        if expected == 'leaf_table_function':
            assert 'no certifica la compatibilidad' in body
            assert 'capacidad concreta imprescindible' in body
            assert 'contrato de salida' in body
        if original.leaf.get('detail'):
            assert 'href="https://' in response.text


@pytest.mark.parametrize('selector', [
    {'tree_id': ID},
    {'tree_title': 'Modelado de vistas'},
    {'tree_title': 'Tipos de Vistas SQL'},
    {'tree_title': '  TIPOS DE VISTAS SQL  '},
])
def test_stable_id_and_legacy_title_start_the_same_tree(http, selector):
    client, store = http
    assert store.get(start(client, **selector)).tree['id'] == ID


def test_unknown_title_still_returns_404(http):
    client, _ = http
    response = client.post(f'/modules/{MODULE}/trees/start', data={'tree_title': 'Árbol inexistente'})
    assert response.status_code == 404


def test_partial_redirect_replaces_picker_and_wizard_then_manual_selection_works(http):
    client, store = http
    run_id = start(client, tree_id=ID)
    response = client.post(
        f'/wizards/{run_id}/answer', data={'answer': FLOW, 'panel': 'tree'},
        headers={'HX-Request': 'true', 'HX-Target': 'wizard-shell'},
    )
    assert response.headers['HX-Retarget'] == '#tree-tab-panel'
    assert response.headers['HX-Reswap'] == 'outerHTML'
    assert response.text.count('id="tree-tab-panel"') == 1
    assert response.text.count('id="wizard-shell"') == 1
    assert re.search(r'<option value="m04_flujos_de_datos"\s+selected', response.text)
    assert '<h2>Flujos de Datos</h2>' in response.text
    assert '¿Necesitás carga delta?' in html.unescape(response.text)
    redirected = re.search(r'/wizards/([a-f0-9]+)/answer', response.text).group(1)
    assert store.get(redirected).tree['id'] == 'm04_flujos_de_datos'
    response = client.post(f'/wizards/{redirected}/answer', data={'answer': 'No', 'panel': 'tree'})
    assert 'HX-Retarget' not in response.headers
    response = client.post(f'/wizards/{redirected}/back')
    assert '¿Necesitás carga delta?' in html.unescape(response.text)
    response = client.post(f'/wizards/{redirected}/restart')
    assert '<h2>Flujos de Datos</h2>' in response.text
    assert store.get(start(client, tree_id=ID)).tree['id'] == ID


def test_redirect_without_tree_panel_keeps_embedded_wizard_contract(http):
    client, _ = http
    run_id = start(client, tree_id=ID)
    response = client.post(f'/wizards/{run_id}/answer', data={'answer': FLOW})
    assert 'HX-Retarget' not in response.headers
    assert 'id="tree-selector"' not in response.text
    assert '<h2>Flujos de Datos</h2>' in response.text


def test_support_labels_preserve_unicode_and_existing_ascii_labels():
    from webapp.services.content_service import _support_links_html
    rendered = _support_links_html('Vista gráfica="https://help.sap.com/graphical" SQL="https://help.sap.com/sql"')
    assert '>Vista gráfica<span>' in rendered
    assert '>SQL<span>' in rendered
    section = next(s for s in load_module(MODULE, 'datasphere_core').sections if s.title == 'Modelado de vistas')
    assert '>Vista gráfica<span>' in section.html


def test_modalities_are_legitimate_and_tie_break_is_governed_not_universal():
    t = tree()
    for key in ['leaf_standard', 'leaf_grafica']:
        text = t['leaves'][key]['detail'] + ' '.join(t['leaves'][key]['notes'])
        assert 'productivos y reutilizables' in text
        assert 'patrón metodológico gobernado' in text
        assert 'no certifica un objeto concreto' in text
    help_text = t['nodes']['q_modalidad']['help']
    assert 'técnicamente viables' in help_text and 'desarrollo, revisión y evolución' in help_text
    undecided = t['leaves']['leaf_revision_modalidad']
    assert 'no corresponde inventar una preferencia universal' in ' '.join(undecided['notes'])


def test_sqlscript_requires_concrete_capacity_and_output_contract_not_complexity():
    leaf = tree()['leaves']['leaf_table_function']
    text = ' '.join(leaf['notes'])
    assert leaf['post_actions']['mode'] == 'needs_human_review'
    assert 'Candidato' in leaf['decision']
    assert 'capacidad concreta imprescindible' in text
    assert 'Varias etapas, joins, CASE, CTE o una transformación compleja no demuestran' in text
    for condition in ['compatibilidad', 'restricciones', 'contrato de salida', 'columnas y tipos', 'incompatibilidad']:
        assert condition in text
    assert 'sin darlo por garantizado por el wrapper' in text


def test_three_review_origins_have_distinct_visible_actions_and_persistence_never_selects_df():
    t = tree()
    reviews = [t['leaves'][key] for key in ['leaf_revision_necesidad', 'leaf_revision_rendimiento', 'leaf_revision_modalidad']]
    assert len({leaf['decision'] for leaf in reviews}) == 3
    assert len({tuple(leaf['post_actions']['next_steps']) for leaf in reviews}) == 3
    assert all(leaf['post_actions']['mode'] == 'needs_human_review' for leaf in reviews)
    performance = reviews[1]
    assert not performance.get('redirect_tree')
    assert 'No recomendar automáticamente Data Flow' in ' '.join(performance['notes'])
    assert t['nodes']['q_necesidad']['next'][PERFORMANCE] == 'leaf_revision_rendimiento'
    assert t['leaves']['leaf_flujos_de_datos']['redirect_tree'] == 'm04_flujos_de_datos'


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids, self.links, self.launchers = [], [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'a' and 'href' in attrs:
            self.links.append(attrs['href'])
        if tag == 'button' and attrs.get('class') == 'tree-launch':
            self.launchers.append(json.loads(attrs['hx-vals']))


def test_m04_content_new_navigation_old_anchor_and_sources_are_consistent():
    document = load_module(MODULE, 'datasphere_core')
    section = next(s for s in document.sections if s.title == 'Modelado de vistas')
    assert section.slug == 'modelado-de-vistas'
    parser = Elements()
    parser.feed(section.html)
    assert parser.ids.count('tipos-de-vistas-sql') == 1
    assert parser.launchers == [{'tree_id': ID, 'tree_title': 'Modelado de vistas', 'pack': 'datasphere_core'}]
    assert any('learning.sap.com' in url for url in parser.links)
    assert any('9bd12cf116ae40e09cdba8b60cf75e11' in url for url in parser.links)
    assert any('81920e4d583f45fd8761c662d3c8abab' in url for url in parser.links)
    source = CONTENT.read_text(encoding='utf-8')
    assert '~80%' not in source and '<15%' not in source and '≤5%' not in source
    assert 'Gráfica solo si aplica' not in source
    assert 'Modelos productivos reutilizables — plan de ejecución HANA es menos optimizable' not in source
    assert '**3 — Propagación**' in source and '**4 — Consumo**' in source
    assert 'no para toda vista relacional' in source
    assert 'necesidad de SQLScript y su contrato de salida están validados' in source
    assert 'label: Modelado de vistas' in source and 'heading: "Modelado de vistas"' in source
    assert any(link.title == 'Modelado de vistas' for stage in document.roadmap for link in stage.links)


def test_entire_flows_contract_is_unchanged_except_the_two_authorized_virtual_leaf_texts():
    flows = copy.deepcopy(WizardStore.tree_by_id('m04_flujos_de_datos'))
    leaf = flows['leaves']['leaf_vista_sql']
    assert 'Vista en SAP Datasphere' in leaf['decision']
    assert 'gráfica o SQL' in leaf['decision']
    assert 'Modelado de vistas' in leaf['post_actions']['next_steps'][0]
    leaf.pop('decision')
    leaf['post_actions'].pop('next_steps')
    digest = hashlib.sha256(json.dumps(flows, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # Huella de main ANTES del cambio; incluye todos los nodos, rutas, condiciones y hojas 5B/5C.
    assert digest == 'edb5c4cb1ae70873e35bc9c16aa274f99b39b69a598c8c87d6f876e0c1475ef1'


@pytest.mark.parametrize('retired', ['leaf_data_flow', 'leaf_table_function_simple'])
def test_retired_leaves_remain_valid_in_live_sessions_and_saved_cases(tmp_path, retired):
    from webapp.services.case_library import CaseLibraryRepository
    legacy = copy.deepcopy(tree())
    legacy.update(title='Tipos de Vistas SQL', version='0.1', start='q_legacy')
    legacy['nodes'] = {'q_legacy': {'options': ['Histórica'], 'next': {'Histórica': retired}}}
    decision = 'Decisión histórica conservada: ' + retired
    legacy['leaves'] = {retired: {'decision': decision, 'notes': ['Justificación histórica']}}
    assert retired not in tree()['leaves']
    store = WizardStore(case_repository=CaseLibraryRepository(tmp_path))
    run = store.start(legacy)
    store.answer(run.id, 'Histórica')
    assert run.leaf['decision'] == decision
    case = yaml.safe_load((tmp_path / (run.case_id + '.yaml')).read_text(encoding='utf-8'))
    assert case['leaf_id'] == retired and case['decision'] == decision
    assert case['route'][0]['answer'] == 'Histórica'
