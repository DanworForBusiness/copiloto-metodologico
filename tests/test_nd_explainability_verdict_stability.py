"""
No regresión de VEREDICTOS: la explicabilidad de «No determinable» es una proyección de lectura y no puede cambiar ninguna decisión del motor.

  * tabla dorada de veredictos (estado, nivel, regla, causa) de TODAS las fixtures reales del repo (8 perfiles dorados del Juicio + 8 ND seudonimizados): si el motor v4
    o la evidencia cambian un veredicto, este test lo señala (la explicabilidad no debe ser la causa);
  * explicar / presentar un Juicio no lo muta ni muta el perfil, y no cambia su `output_signature`;
  * el nivel visible en la vista (`level_label` / `determined`) es el del Juicio, con o sin perfil (con perfil solo se AGREGAN impedimentos);
  * `explain.py` no es un segundo motor: no importa ni invoca la evaluación de niveles.
"""
from __future__ import annotations

import ast
import copy
import re
from pathlib import Path

import pytest

from tests._judgment_cases import FIXTURES as LEGACY_FIXTURES, fresh as legacy_fresh
from tests._nd_cases import ALIASES, fresh as nd_fresh
from tools.catalog.methodological_judgment import contract as C
from tools.catalog.methodological_judgment import evaluate_judgment
from tools.catalog.methodological_judgment import explain as X
from webapp.services import assessment_presenter as P

D, ND = "DETERMINED", "NOT_DETERMINABLE"
CRIT = "EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL"
INSUF = "INSUFFICIENT_AFFIRMATIVE_EVIDENCE"
GOLDEN = {
    "L:biogenesis_base_stocks": (D, "PARCIAL", "LVL-02", None),
    "L:biogenesis_descuentos_comerciales": (D, "ADECUADA", "LVL-03", None),
    "L:biogenesis_lista_precios": (D, "PARCIAL", "LVL-02", None),
    "L:biogenesis_prefacturacion": (D, "ADECUADA", "LVL-03", None),
    "L:biogenesis_reserva_material": (D, "PARCIAL", "LVL-02", None),
    "L:biogenesis_sellout_gp": (ND, None, "LVL-05", CRIT),
    "L:illolay_pedidos_entregas": (D, "PARCIAL", "LVL-02", None),
    "L:kensing_gl_line_item_raw": (D, "BUENA", "LVL-04", None),
    "N:nd_lvl05_external_class_critical_hojobar": (ND, None, "LVL-05", CRIT),
    "N:nd_lvl05_external_class_critical_one_source": (ND, None, "LVL-05", CRIT),
    "N:nd_lvl05_resolvable_by_ratification": (ND, None, "LVL-05", CRIT),
    "N:nd_lvl06_harmonization_label_unknown": (ND, None, "LVL-06", INSUF),
    "N:nd_lvl06_harmonization_label_unknown_unconsumed": (ND, None, "LVL-06", INSUF),
    "N:nd_lvl06_mapping_lineage_and_class_not_observable": (ND, None, "LVL-06", INSUF),
    "N:nd_lvl06_source_class_and_harmonization_unresolved": (ND, None, "LVL-06", INSUF),
    "N:nd_lvl06_unresolved_three_dimensions": (ND, None, "LVL-06", INSUF),
}
CASES = [("L:" + p.name[: -len(".json.gz")], legacy_fresh) for p in sorted(LEGACY_FIXTURES.glob("*.json.gz"))] + [("N:" + a, nd_fresh) for a in ALIASES]


def _judge(profile):
    return evaluate_judgment(profile, ruleset=C.RULESET_V4, ratified_contracts=[])


def test_the_golden_table_covers_exactly_every_real_fixture():
    assert {c for c, _ in CASES} == set(GOLDEN)


@pytest.mark.parametrize("label,loader", CASES, ids=[c for c, _ in CASES])
def test_every_verdict_is_the_golden_one_and_explaining_or_presenting_it_changes_nothing(label, loader):
    profile = loader(label.split(":", 1)[1])
    judgment = _judge(profile)
    assert (judgment["status"], judgment.get("level"), judgment.get("level_rule"), judgment.get("not_determinable_reason")) == GOLDEN[label]
    j0, p0 = copy.deepcopy(judgment), copy.deepcopy(profile)
    X.explain_determination(judgment, profile)
    view_with, view_without = P.judgment_view(judgment, profile), P.judgment_view(judgment)
    P.nd_explanation(judgment, profile)
    assert judgment == j0 and profile == p0 and judgment["output_signature"] == j0["output_signature"]        # ni el Juicio ni el perfil se mutan
    assert (view_with["determined"], view_with["level_label"]) == (view_without["determined"], view_without["level_label"])
    assert view_with["determined"] is (judgment["status"] == D) and (view_with["nd"] is None) is (judgment["status"] == D)
    assert _judge(profile) == judgment                                                                        # el motor sigue dando exactamente el mismo Juicio


def test_explain_py_is_a_projection_not_a_second_engine():
    """Nunca decide un nivel: no importa ni llama a `evaluate_judgment` / `_decide`. La ÚNICA llamada a la función de nivel del motor (`resolve_level_v4`) es la
    CORROBORACIÓN «el veredicto es función de los estados persistidos» (`... is None` ⇒ sigue siendo ND), registrada como un chequeo más; no asigna ningún nivel."""
    src = Path(X.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    called = {n.func.id for n in calls if isinstance(n.func, ast.Name)} | {n.func.attr for n in calls if isinstance(n.func, ast.Attribute)}
    assert not ({"evaluate_judgment", "_decide", "resolve_level"} & (imported | called))
    uses = [ln for ln in src.splitlines() if "resolve_level_v4(" in ln]
    assert len(uses) == 1 and 'checks.append' in uses[0] and uses[0].rstrip().endswith("is None})")
    assert not re.search(r'"level"\s*:|\["level"\]\s*=', src)                                                    # lee el nivel del Juicio, pero no construye ni asigna ninguno
