"""Recorridos funcionales 5C: contrato del wizard y límites arquitectónicos."""
import copy
import hashlib
import json

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from webapp.services.wizard_service import WizardStore


VIRTUAL = 'Calcular al consultar'
HANA = 'Guardar en una tabla de HANA'
FILE = 'Guardar en una tabla de File Space'
DECL = 'Combinar, filtrar, calcular o resumir datos'
PYTHON = 'Ejecutar código Python'
LOCAL = 'Tablas locales del espacio'
REMOTE = 'Tablas remotas generales o vistas sobre ellas'
CONNECTOR = 'Azure SQL Database, SQL Server, Oracle o SAP HANA externo'
REVIEW = 'leaf_revision_sin_delta'


def tree():
    return WizardStore.tree_by_id('m04_flujos_de_datos')


def walk(answers):
    store = WizardStore()
    run = store.start(tree())
    for answer in ['No', *answers]:
        store.answer(run.id, answer)
        assert not run.error
    return store, run


@pytest.mark.parametrize('answers,expected', [
    ([VIRTUAL, DECL], 'leaf_vista_sql'),
    ([HANA, DECL, LOCAL], 'leaf_transformation_flow_simple'),
    ([FILE, DECL, LOCAL], 'leaf_tf_declarativo_file'),
    ([HANA, PYTHON, LOCAL], 'leaf_data_flow_python'),
    ([FILE, PYTHON, LOCAL], 'leaf_tf_python_file'),
    ([HANA, DECL, 'Vistas u objetos Open SQL sobre datos locales'], 'leaf_transformation_flow_simple'),
    ([HANA, DECL, 'Objetos compartidos desde BW bridge'], 'leaf_transformation_flow_simple'),
    ([HANA, DECL, REMOTE, 'No, alcanza con transformar y cargar directamente'], 'leaf_data_flow_directo'),
    ([HANA, DECL, REMOTE, 'Sí, por reutilización o recuperación de la adquisición', CONNECTOR], 'leaf_rf_tf_declarativo_hana'),
    ([FILE, DECL, 'Datos remotos que necesitan adquirirse primero', CONNECTOR], 'leaf_rf_tf_declarativo_file'),
    ([FILE, PYTHON, 'Datos remotos que necesitan adquirirse primero', CONNECTOR], 'leaf_rf_tf_python_file'),
    ([HANA, PYTHON, 'Datos aún en un sistema externo'], REVIEW),
    ([FILE, DECL, 'Vistas, Open SQL, BW bridge u otros objetos compartidos'], REVIEW),
    ([HANA, DECL, 'Origen mixto, desconocido o con una restricción conocida'], REVIEW),
    ([FILE, PYTHON, 'Datos aún en un sistema externo', 'SAP/ABAP, otro conector o restricciones del objeto'], REVIEW),
    ([VIRTUAL, PYTHON, 'Necesito mantenerlo virtual'], REVIEW),
    ([VIRTUAL, PYTHON, 'Sí, guardar en HANA', LOCAL], 'leaf_data_flow_python'),
    ([VIRTUAL, PYTHON, 'Sí, guardar en File Space', LOCAL], 'leaf_tf_python_file'),
    ([HANA, 'Otra lógica / no está caracterizada'], REVIEW),
    (['Otra necesidad / no está definido'], REVIEW),
])
def test_scenarios(answers, expected):
    _, run = walk(answers)
    assert run.current == expected and run.leaf
    if len(answers) <= 3 and expected != REVIEW:
        assert len(run.history) - 1 <= 3  # q1_delta no cuenta como decisión 5C.


def test_materialization_confirmation_back_and_restart():
    store, run = walk([VIRTUAL, PYTHON])
    assert run.current == 'q5c_confirmar_materializacion'
    assert 'guardar' in run.node['question']
    store.answer(run.id, 'Sí, guardar en File Space')
    store.back(run.id)
    assert run.current == 'q5c_confirmar_materializacion'
    store.answer(run.id, 'Necesito mantenerlo virtual')
    assert run.current == REVIEW
    store.restart(run.id)
    assert run.current == 'q1_delta' and not run.history


def routes(t, current, trail=()):
    assert current not in [n for n, _ in trail], 'ciclo'
    if current in t['leaves']:
        yield trail, current
        return
    node = t['nodes'][current]
    if current.startswith('q5c_') or current == 'q4_persistencia':
        assert node['help']
    assert set(node['options']) == set(node['next'])
    for answer, target in node['next'].items():
        yield from routes(t, target, (*trail, (current, answer)))


def test_every_no_delta_route_executes_and_preserves_context():
    t = tree()
    seen = set()
    for trail, terminal in routes(t, t['nodes']['q1_delta']['next']['No']):
        _, run = walk([answer for _, answer in trail])
        assert run.current == terminal and run.leaf
        seen.update(n for n, _ in trail)
        seen.add(terminal)
        answers = [a for _, a in trail]
        if terminal.startswith('leaf_rf_tf_'):
            assert not (PYTHON in answers and HANA in answers)
            if PYTHON in answers:
                assert terminal == 'leaf_rf_tf_python_file'
            elif FILE in answers:
                assert terminal == 'leaf_rf_tf_declarativo_file'
            else:
                assert terminal == 'leaf_rf_tf_declarativo_hana'
        if terminal in {'leaf_data_flow_python', 'leaf_data_flow_directo'}:
            assert FILE not in answers and 'Sí, guardar en File Space' not in answers
    all_seen = {'q1_delta'}
    for trail, terminal in routes(t, t['start']):
        all_seen.update(n for n, _ in trail)
        all_seen.add(terminal)
    assert all_seen == set(t['nodes']) | set(t['leaves'])


def test_delta_matches_inherited_5b_snapshot():
    t = tree()
    ids = set()
    for trail, terminal in routes(t, 'q2_origen'):
        ids.update(n for n, _ in trail)
        ids.add(terminal)
    snapshot = {key: (t['nodes'] | t['leaves'])[key] for key in sorted(ids)}
    digest = hashlib.sha256(json.dumps(snapshot, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    # Huella de la rama delta 5B respaldada antes de editar, no del resultado 5C.
    assert digest == '117d2e39acdc5f41b69207c4aa4b4f44853ffacc1d543b96a9f1baf338d57b00'


def test_retired_leaf_does_not_break_live_snapshot_or_saved_case(tmp_path):
    from webapp.services.case_library import CaseLibraryRepository
    old = copy.deepcopy(tree())
    old['start'] = 'q_legacy'
    old['nodes']['q_legacy'] = {'options': ['Join'], 'next': {'Join': 'leaf_data_flow_joins'}}
    old['leaves']['leaf_data_flow_joins'] = {'decision': 'Decisión histórica', 'notes': ['Nota histórica']}
    repository = CaseLibraryRepository(tmp_path)
    store = WizardStore(case_repository=repository)
    run = store.start(old)
    # Recargar el catálogo no reemplaza el árbol conservado por una sesión activa.
    assert 'leaf_data_flow_joins' not in tree()['leaves']
    store.answer(run.id, 'Join')
    assert run.leaf['decision'] == 'Decisión histórica'
    case = yaml.safe_load((tmp_path / (run.case_id + '.yaml')).read_text(encoding='utf-8'))
    assert case['leaf_id'] == 'leaf_data_flow_joins'
    assert case['decision'] == 'Decisión histórica'
    assert case['route'][0]['answer'] == 'Join'


def test_real_http_router_renders_fast_path_and_conditions(monkeypatch):
    from webapp.routers import wizard
    isolated = WizardStore()
    monkeypatch.setattr(wizard, 'store', isolated)
    monkeypatch.setattr(wizard, 'log_verbose', lambda *a, **kw: None)
    monkeypatch.setattr(wizard.auth_service, 'session_user', lambda request: {})
    app = FastAPI()
    app.include_router(wizard.router)
    run = isolated.start(tree())
    client = TestClient(app)
    for answer in ['No', VIRTUAL, DECL]:
        response = client.post(f'/wizards/{run.id}/answer', data={'answer': answer})
        assert response.status_code == 200
    assert 'Vista en SAP Datasphere' in response.text and 'fuentes son consultables' in response.text
    assert 'modalidad gráfica o SQL' in response.text
    assert 'Pr&oacute;ximos pasos' in response.text and 'Modelado de vistas' in response.text
    response = client.post(f'/wizards/{run.id}/back')
    assert response.status_code == 200 and '¿Qué procesamiento necesitás?' in response.text
    # Las cinco recomendaciones condicionadas deben explicar el alcance en el HTML real.
    for answers in [
        [HANA, PYTHON, LOCAL],
        [HANA, DECL, REMOTE, 'No, alcanza con transformar y cargar directamente'],
        [HANA, DECL, REMOTE, 'Sí, por reutilización o recuperación de la adquisición', CONNECTOR],
        [FILE, DECL, 'Datos remotos que necesitan adquirirse primero', CONNECTOR],
        [FILE, PYTHON, 'Datos remotos que necesitan adquirirse primero', CONNECTOR],
    ]:
        run = isolated.start(tree())
        for answer in ['No', *answers]:
            response = client.post(f'/wizards/{run.id}/answer', data={'answer': answer})
            assert response.status_code == 200
        assert 'no certifica la compatibilidad de un objeto concreto' in response.text
