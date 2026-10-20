"""Navegación del bloque de impedimentos de «No determinable»: misma gramática de acciones del informe (ícono + tooltip + nombre accesible, X de cierre en el
encabezado del disclosure) y enlaces a áreas SOLO desde el mapeo canónico contrato→área del motor (``CONTRACT_AREAS``), nunca inferidos."""
from __future__ import annotations

import html as _html
import re

from tests._nd_cases import ALIASES
from tests.webapp.test_nd_blockers_presentation import judgment, render, world  # noqa: F401
from tools.catalog.methodological_judgment import contract as C
from tools.catalog.methodological_judgment import explain as X
from webapp.services import assessment_presenter as P


def esc(t):
    return _html.escape(t, quote=True)


def test_every_blocker_row_has_an_icon_action_to_its_disclosure_and_a_close_x_in_the_header(world):  # noqa: F811
    for alias in ALIASES:
        j, p = judgment(alias)
        sec = render(world, j, p)
        ids = set(re.findall(r'\sid="([^"]+)"', sec))
        assert "Ver detalle" not in sec and '<th>Para resolverlo</th><th class="asr-col-action">Acciones</th>' in sec, alias
        for b in P.nd_explanation(j, p)["blockers"]:
            row = re.search(rf'<tr id="{b["dom_id"]}-row">(.*?)</tr>', sec, re.S).group(1)
            assert f"<td>{b['resolution_text']}</td>" in row                                                        # la acción de resolución se lee sin abrir nada
            act = re.search(rf'<a href="#{b["dom_id"]}" class="table-action-btn chart-tooltip-anchor" aria-label="([^"]+)">(.*?)</a>', row, re.S)
            assert act and b["dom_id"] in ids                                                                        # acción icónica semántica hacia un destino que existe
            assert act.group(1) == esc(f"Ver el detalle del impedimento de «{b['dimension']}»")
            assert '<svg viewBox="0 0 24 24" aria-hidden="true">' in act.group(2) and "<p>Ver causa, evidencia y resolución</p>" in act.group(2)
            det = re.search(rf'<details class="dashboard-section" id="{b["dom_id"]}">(.*?)</details>', sec, re.S).group(1)
            head = det[: det.index("</summary>")]
            assert head.count("data-asr-close") == 1 and esc(f"Cerrar el detalle del impedimento de «{b['dimension']}»") in head   # una sola X por encabezado


def test_related_area_links_come_only_from_the_canonical_contract_area_mapping_and_exist_in_the_profile():
    for alias in ALIASES:
        j, p = judgment(alias)
        area_ids = {a["id"] for a in p.get("areas") or []}
        views = P.nd_explanation(j, p)["blockers"]
        for raw in X.explain_determination(j, p)["blockers"]:
            dim = raw.get("dimension")
            view = next(v for v in views if v["dimension"] == P._DIM_LABEL.get(dim or "", "Juicio global"))
            expected = {f"asr-a-{a}" for a in C.CONTRACT_AREAS.get(dim or "", ()) if a in area_ids}             # mapeo del motor ∩ áreas realmente presentes
            assert {a["dom_id"] for a in view["areas"]} == expected, (alias, dim)
