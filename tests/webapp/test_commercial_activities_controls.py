"""
test_commercial_activities_controls.py — C9 (ver HANDOFF): la capability
Actividades adopta los tres comportamientos TRANSVERSALES que ya tenía el
detalle de Oportunidades Activas — búsqueda + "✕ Limpiar", badges tonales de
urgencia y orden por columna.

Las invariantes de comportamiento viven en
commercial_activities_controls.node.js, que ejecuta el motor REAL. Acá quedan
las de ARCHIVO: que se reusaron los primitivos compartidos en vez de inventar
mecanismos propios, y que no se tocó backend ni motor compartido.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"
_HARNESS = Path(__file__).resolve().parent / "commercial_activities_controls.node.js"
_COMMERCIAL_JS = (_STATIC / "commercial.js").read_text(encoding="utf-8")
_COMMERCIAL_CSS = (_STATIC / "commercial.css").read_text(encoding="utf-8")
_TOOLS_CSS = (_STATIC / "tools.css").read_text(encoding="utf-8")


def _js_code(raw: str) -> str:
    return "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_activities_controls_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Activities controls invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL COMMERCIAL ACTIVITIES CONTROLS INVARIANTS PASSED" in result.stdout


def test_c9_reuses_shared_primitives_instead_of_inventing_its_own():
    # Los tres comportamientos salen de primitivos que YA existían: el filtro y
    # el Limpiar de `whatsnew-*`, el badge de `.dashboard-status-badge` y la
    # cabecera ordenable de `.dashboard-table-th--sortable`. C9 no agrega CSS.
    for shared in (".whatsnew-filter__clear", ".whatsnew-filter--fill",
                   "th.dashboard-table-th--sortable", ".dashboard-status-badge"):
        assert shared in _TOOLS_CSS, shared
    code = _js_code(_COMMERCIAL_JS)
    assert 'class="whatsnew-filter__clear" data-commercial-act-clear' in code
    assert "dashboard-table-th--sortable\" data-commercial-act-sort=" in code
    assert 'class="dashboard-status-badge dashboard-status-badge--${color}"' in code
    # Ninguna clase propia de Actividades para esto.
    for banned in ("act-badge", "activity-badge", "act-sortable", "activities-search"):
        assert banned not in _COMMERCIAL_CSS, banned


def test_activities_urgency_scale_is_the_same_one_as_active_opportunities():
    # UN solo clasificador y UNA sola leyenda para las tres tablas del
    # workspace (Commercial Tile 5 — Prospectos, ver HANDOFF, agregó la
    # tercera) — si se duplicaran, las escalas podrían divergir sin que
    # nadie lo note.
    assert _COMMERCIAL_JS.count("function commercialUrgencyColor(") == 1
    assert _COMMERCIAL_JS.count("function commercialUrgencyLegendHtml(") == 1
    code = _js_code(_COMMERCIAL_JS)
    assert code.count("commercialUrgencyColor(") == 4   # definición + Activas + Actividades + Prospectos
    assert code.count("commercialUrgencyLegendHtml()") == 4


def test_activities_search_and_sort_are_client_side_only():
    code = _js_code(_COMMERCIAL_JS)
    # Un solo puente por interacción, y ninguno pide nada a la red.
    assert code.count("function commercialActivitySearchableText(") == 1
    assert code.count("function commercialActivitiesSorted(") == 1
    assert code.count("function commercialToggleActSort(") == 1
    for fn in ("commercialActivitySearchableText", "commercialActivitiesSorted", "commercialToggleActSort"):
        body = code.split(f"function {fn}(", 1)[1].split("\n}", 1)[0]
        assert "fetch(" not in body and "/comercial/data/" not in body, fn
    # El cuerpo se re-renderiza quirúrgicamente: la barra de controles nunca se
    # regenera al buscar, o el `<input>` perdería el foco entre teclas.
    assert code.count("function commercialRenderActivitiesBody(") == 1
    surgical = code.split("function commercialRenderActivitiesBody(", 1)[1].split("\n}", 1)[0]
    assert "commercialRender(" not in surgical and "commercialRenderCapability(" not in surgical
    # El delegado de búsqueda usa ese camino, no el completo.
    branch = code.split('closest("[data-commercial-act-search]")', 1)[1].split("return;", 1)[0]
    assert "commercialRenderActivitiesBody()" in branch
    assert "commercialRenderCapability" not in branch


def test_activities_clear_resets_the_three_filters_but_not_the_sort():
    code = _js_code(_COMMERCIAL_JS)
    branch = code.split('closest("[data-commercial-act-clear]")', 1)[1].split("return;", 1)[0]
    for reset in ('commercialState.actPeriod = "all"', 'commercialState.actUser = ""',
                  'commercialState.actSearchText = ""'):
        assert reset in branch, reset
    # El orden es una preferencia de LECTURA, no un filtro — mismo criterio que
    # `oppSortKey` en el detalle de Oportunidades.
    assert "actSortKey" not in branch
    assert "fetch(" not in branch


def test_activities_column_widths_favour_the_opportunity_column():
    # Owner smoke: el excedente de Fecha pasa a Oportunidad. Con
    # `table-layout:fixed` lo que manda es la RELACIÓN entre los anchos
    # declarados, y la suma se conserva para no mover el piso de scroll mobile.
    table = _COMMERCIAL_JS.split("function commercialActivityTableHtml(", 1)[1] \
        .split("function commercialActivitiesControlsHtml(", 1)[0]
    widths = [int(w) for w in re.findall(r'<col style="width:(\d+)px">', table)]
    assert len(widths) == 6, widths
    assert sum(widths) == 680, sum(widths)
    fecha, oportunidad = widths[0], widths[2]
    assert oportunidad > fecha, (fecha, oportunidad)
    assert oportunidad == max(widths), widths
    assert "matrix-scroll" in table


def test_c9_left_backend_and_shared_engine_untouched():
    app_js = (_STATIC / "app.js").read_text(encoding="utf-8")
    for banned in ("commercialActivitiesBodyHtml", "data-commercial-act-search",
                   "data-commercial-act-sort", "data-commercial-act-clear"):
        assert banned not in app_js, banned
    service = (_ROOT / "webapp" / "services" / "commercial_service.py").read_text(encoding="utf-8")
    router = (_ROOT / "webapp" / "routers" / "commercial.py").read_text(encoding="utf-8")
    for f, name in ((service, "service"), (router, "router")):
        for banned in ("actSearchText", "actSortKey", "act-sort"):
            assert banned not in f, (name, banned)


def test_active_opportunities_contracts_are_untouched_by_c9():
    code = _js_code(_COMMERCIAL_JS)
    # El detalle de Oportunidades conserva SU búsqueda table-only y su sort.
    subset = code.split("function commercialCurrentAnalysisFilteredOpportunities(", 1)[1].split("\n}", 1)[0]
    assert "oppSearchText" not in subset, "el subset analítico de Activas sigue sin la búsqueda"
    assert code.count("function commercialToggleOppSort(") == 1
    assert 'data-commercial-sort="' in code
    # Y los dos mecanismos de orden no se mezclaron.
    act_keys = code.split("const COMMERCIAL_ACT_SORT_KEYS = {", 1)[1].split("\n};", 1)[0]
    assert "hours" not in act_keys and "probability" not in act_keys
