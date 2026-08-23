"""
test_reporting_empty_states.py — Reporting consume el estado vacío
COMPARTIDO del Hub (owner increment: "consistent empty states").

Reporting era el único vertical que se salteaba `.dashboard-empty` — el
primitivo que ya usan Copiloto/Métricas de Uso
(`partials/admin_activity_content.html`), CRM/Comercial
(`commercialEmptyStateHtml`) y Asignaciones (`staffing_dashboard.html`) — y
emitía un `<p class="empty-note">` suelto, clase LOCAL de reporting.css: un
segundo lenguaje visual para el mismo estado.

Alcance de este incremento: la experiencia RENTABILIDAD (el caso reportado —
"Ranking por Rentabilidad horas" / Mejor performance — más los equivalentes
del mismo camino de render). Los estados vacíos de los otros tabs quedan
como follow-up explícito, no como olvido: hay un test que fija su cuenta.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"
_HARNESS = Path(__file__).resolve().parent / "reporting_empty_states.node.js"
_REPORTING_JS = (_STATIC / "reporting.js").read_text(encoding="utf-8")
_TOOLS_CSS = (_STATIC / "tools.css").read_text(encoding="utf-8")
_REPORTING_CSS = (_STATIC / "reporting.css").read_text(encoding="utf-8")


def _js_code(raw: str) -> str:
    """JS sin comentarios: los bloques que documentan este contrato citan a
    propósito los literales que estos tests cuentan."""
    return "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))


def _rentabilidad_block(name: str) -> str:
    return _js_code(_REPORTING_JS).split(f"function {name}(", 1)[1].split("\n}", 1)[0]


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_reporting_empty_states_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Reporting empty states failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL REPORTING EMPTY STATE INVARIANTS PASSED" in result.stdout


# --- 1: el fallback de texto plano ya no existe en Rentabilidad -----------

def test_the_plain_text_fallback_is_gone_from_the_rentabilidad_ranking():
    resumen = _rentabilidad_block("renderRentabilidadResumen")
    assert "empty-note" not in resumen
    # Y los tres mensajes del bloque siguen existiendo, ahora por el helper.
    for msg in ("Sin proyectos con rentabilidad ≥ 0.",
                "Sin proyectos con rentabilidad negativa.",
                "Sin proyectos para los filtros actuales."):
        assert f'reportingEmptyStateHtml("{msg}")' in resumen, msg


def test_the_twin_certificadas_vs_ejecutadas_path_is_normalized_too():
    # La perspectiva hermana del MISMO tab, mismo camino de render.
    cert = _rentabilidad_block("renderRentabilidadCertEjecutadas")
    assert "empty-note" not in cert
    assert cert.count("reportingEmptyStateHtml(") == 3


# --- 2: usa el primitivo COMPARTIDO, sin inventar nada -------------------

def test_reporting_now_consumes_the_shared_hub_empty_state_primitive():
    code = _js_code(_REPORTING_JS)
    assert code.count("function reportingEmptyStateHtml(") == 1
    helper = code.split("function reportingEmptyStateHtml(", 1)[1].split("\n}", 1)[0]
    # Exactamente el markup de los otros tres consumidores del Hub.
    assert 'class="dashboard-empty"' in helper
    assert 'class="dashboard-empty__label"' in helper
    assert "esc(message)" in helper and "esc(eyebrow)" in helper
    # El primitivo vive en el CSS compartido, no en el de la vertical.
    rules = re.sub(r"/\*.*?\*/", "", _TOOLS_CSS, flags=re.S)
    assert ".dashboard-empty {" in rules
    assert ".dashboard-empty__label {" in rules
    assert "dashboard-empty" not in _REPORTING_CSS


def test_no_new_ad_hoc_empty_state_primitive_was_introduced():
    # Cero CSS nuevo: el helper es un ADAPTADOR, no un diseño.
    for banned in ("reporting-empty", "empty-state", "ranking-empty", "rentabilidad-empty"):
        assert banned not in _REPORTING_CSS, banned
        assert banned not in _js_code(_REPORTING_JS), banned
    # Y no se redeclara el primitivo compartido desde la vertical.
    assert ".dashboard-empty" not in _REPORTING_CSS


def test_the_shared_hub_consumers_are_untouched():
    # El increment no puede haber tocado a los otros consumidores.
    activity = (_ROOT / "webapp" / "templates" / "partials" / "admin_activity_content.html").read_text(encoding="utf-8")
    staffing = (_ROOT / "webapp" / "templates" / "operations" / "staffing_dashboard.html").read_text(encoding="utf-8")
    commercial = (_STATIC / "commercial.js").read_text(encoding="utf-8")
    assert activity.count('class="dashboard-empty"') == 4        # Copiloto / Métricas de Uso
    assert staffing.count('class="dashboard-empty"') == 2        # Asignaciones
    assert commercial.count('class="dashboard-empty"') == 1      # CRM, vía su helper
    assert "function commercialEmptyStateHtml(" in commercial


# --- 3/4: la semántica de datos no cambia --------------------------------

def test_the_populated_ranking_path_is_unchanged():
    resumen = _rentabilidad_block("renderRentabilidadResumen")
    # Con datos, el ranking sigue saliendo por su lista de siempre.
    assert 'rentabilidadRankingList(positivos, "positivos", maxAbs)' in resumen
    assert 'rentabilidadRankingList(negativos, "negativos", maxAbs)' in resumen
    # Y el estado vacío sigue siendo una RAMA por lado: un lado con datos y el
    # otro sin datos no se pisan.
    assert "positivos.length ? rentabilidadRankingList" in resumen
    assert "negativos.length ? rentabilidadRankingList" in resumen
    # Composición de 2 columnas intacta (primitivo Hub compartido).
    assert 'class="activity-grid-2"' in resumen


def test_no_calculation_sorting_or_filter_semantics_changed():
    code = _js_code(_REPORTING_JS)
    # El increment es SOLO presentación: los helpers de datos siguen igual.
    assert "function rentabilidadSelectedRows()" in code
    assert "function rentabilidadRankingList(" in code
    assert "const RENTABILIDAD_RANKING_TOP_N = 5;" in code
    # Ni el service ni el repository saben de estados vacíos.
    for rel in ("webapp/services/reporting_service.py", "webapp/services/reporting_repository.py"):
        src = (_ROOT / rel).read_text(encoding="utf-8")
        for banned in ("dashboard-empty", "reportingEmptyStateHtml", "empty-note"):
            assert banned not in src, (rel, banned)


# --- 5: los otros tabs quedan como follow-up EXPLÍCITO -------------------

def test_no_plain_text_empty_state_remains_anywhere_in_reporting():
    # Normalización COMPLETA: cero `<p class="empty-note">` en todo Reporting.
    # Los 13 estados vacíos (3 del ranking + 3 de su perspectiva hermana + 7 de
    # los otros tabs) pasan por el primitivo compartido.
    code = _js_code(_REPORTING_JS)
    assert '<p class="empty-note">' not in code
    assert 'class="empty-note"' not in code
    # Y ninguna función de render la reintroduce.
    for fn in ("renderRentabilidadResumen", "renderRentabilidadCertEjecutadas"):
        assert "empty-note" not in _rentabilidad_block(fn), fn


def test_the_dead_local_primitive_was_removed():
    # Con cero consumidores, la regla local se retira: dejarla es una
    # invitación a reintroducir el segundo lenguaje visual.
    assert "empty-note" not in _REPORTING_CSS
    # El token que usaba sigue vivo para otras reglas — no se huerfanizó nada.
    assert "--ink-faint" in _REPORTING_CSS


def test_every_valid_empty_reporting_path_uses_the_shared_primitive():
    # Las 13 ramas de cero-filas resuelven por el helper, y son las ÚNICAS que
    # lo usan: los estados de ERROR conservan su tratamiento propio.
    code = _js_code(_REPORTING_JS)
    assert code.count("reportingEmptyStateHtml(") == 14   # 13 usos + la definición
    for fn in ("renderCartera", "renderHorasVendidas", "renderComercialParticipacion",
               "renderComercialSalud", "renderCalendario", "renderCapacidadProductivas",
               "renderCapacidadNoAsignadas"):
        body = code.split(f"function {fn}(", 1)[1].split("\n}", 1)[0]
        assert "reportingEmptyStateHtml(" in body, fn
        assert "empty-note" not in body, fn


def test_error_states_never_borrow_the_empty_state_treatment():
    # CONTRATO SEMÁNTICO: un fallo de fuente/carga/cálculo NUNCA se disfraza de
    # "Sin resultados". `reportingSourceErrorHtml` conserva su propio markup y
    # su propia copy, y no emite el primitivo de vacío.
    code = _js_code(_REPORTING_JS)
    err = code.split("function reportingSourceErrorHtml(", 1)[1].split("\n}", 1)[0]
    assert "dashboard-empty" not in err
    assert "Sin resultados" not in err
    assert "No pudimos cargar" in err
    # Y el helper de vacío no conoce el vocabulario de error.
    empty = code.split("function reportingEmptyStateHtml(", 1)[1].split("\n}", 1)[0]
    assert "No pudimos" not in empty and "No disponible" not in empty
    # Los caminos de error (incluidos los parciales por año) siguen siendo
    # ramas separadas, no la de cero filas.
    assert code.count("reportingSourceErrorHtml(") >= 12
