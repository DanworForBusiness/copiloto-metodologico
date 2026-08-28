"""
test_commercial_creadas_drill.py — Creadas Legacy Parity (ver HANDOFF —
paridad con el legacy `#created-drill-section`/`/crm/opp-by-month`). Ver
commercial_creadas_drill.node.js para las invariantes del motor real
(bucket de semana día-del-mes, año por default, ranking por vendedor, tabla
mensual con expansión, detalle por oportunidad, y los 4 estados del lazy
load).
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent / "commercial_creadas_drill.node.js"
_NO_REFETCH = Path(__file__).resolve().parent / "commercial_creadas_no_refetch.node.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_commercial_creadas_drill_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Creadas drill invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL CREADAS DRILL INVARIANTS PASSED" in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_creadas_never_refetches_on_local_interaction():
    """PERFORMANCE GUARD del owner UX corrective: después de la primera carga,
    NINGUNA interacción local —año, vendedor, búsqueda tecla por tecla,
    expandir/colapsar, seleccionar mes o semana, cerrar el detalle, ordenar,
    limpiar— puede producir una request más a `/comercial/data/creadas`.

    El harness ejecuta los listeners REALES contra un `fetch` instrumentado y
    cuenta; además verifica EN POSITIVO que los handlers efectivamente
    corrieron, porque un guard de "cero requests" pasa trivialmente si nada se
    ejecuta (que es exactamente lo que pasaba antes de sembrar el host del
    bootstrap: cero listeners registrados, verde falso)."""
    result = subprocess.run(["node", str(_NO_REFETCH)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Creadas no-refetch guard failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL CREADAS NO-REFETCH INVARIANTS PASSED" in result.stdout


_STATIC = Path(__file__).resolve().parents[2] / "webapp" / "static"


def test_the_detail_table_distributes_by_content_and_never_truncates():
    """El defecto aparecio DOS veces en el smoke del owner, asi que queda fijado.

    1. Con el `table-layout: fixed` del primitivo compartido y un `<colgroup>` en
       porcentajes, Oportunidad se quedaba con su 28% aunque no lo usara mientras
       Cliente truncaba ("MURCHISON S.A. ESTIBAJES Y CARG…") con espacio sobrante
       al lado. Los anchos rigidos no redistribuyen: la tabla pasa a `auto`.

    2. `td.left` trae `white-space: nowrap` + `ellipsis`, correcto para una matriz
       de medidas pero no para una tabla de TEXTO — truncar un nombre de cliente
       pierde el dato que se vino a leer.

    Y `th.row-label-col` trae `width: 340px` de tools.css, que descompensaba el
    reparto (Oportunidad 340 vs Cliente 80).
    """
    js = (_STATIC / "commercial.js").read_text(encoding="utf-8")
    css = (_STATIC / "commercial.css").read_text(encoding="utf-8")
    # Cero anchos rigidos en la tabla de detalle.
    detail_block = js.split("commercial-creadas-detail-table", 1)[1][:900]
    assert "<colgroup" not in detail_block, "el detalle no debe volver a anchos rigidos"
    # Reparto por contenido, con minimo para que en angosto scrollee adentro.
    assert "table.dashboard-table.commercial-creadas-detail-table { table-layout: auto;" in css
    assert re.search(r"commercial-creadas-detail-table \{[^}]*min-width:", css)
    # Las celdas de texto no truncan.
    assert "white-space: normal; overflow: visible; text-overflow: clip;" in css
    # Y el ancho fijo del rotulo queda neutralizado para esta tabla.
    assert "commercial-creadas-detail-table th.row-label-col { width: auto; }" in css


def test_the_hierarchy_table_keeps_predictable_columns_fixed_and_the_bar_elastic():
    """La otra mitad del reparto: en la tabla Mes/Semana el contenido de las tres
    primeras columnas NO crece con el viewport (una etiqueta de semana y dos
    numeros), asi que se fijan y la barra absorbe el sobrante. Con porcentajes, a
    1560px la primera columna se llevaba 682px para un texto de 110px.

    El selector lleva `table.dashboard-table` adelante a proposito: sin eso queda
    en (0,1,2) y pierde contra `th.row-label-col` (0,2,2) de tools.css.
    """
    css = (_STATIC / "commercial.css").read_text(encoding="utf-8")
    for n, w in ((1, "264px"), (2, "76px"), (3, "92px")):
        assert f"table.dashboard-table.commercial-creadas-table th:nth-child({n})" in css
        assert w in css, w
    assert "table.dashboard-table.commercial-creadas-table th:nth-child(4)" in css
    # La barra NO usa `.forecast-bar-cell`: esa composicion la fija en 72px
    # (`flex: 0 0 72px`) porque incluye el numero en la misma celda. Aca los
    # numeros tienen columna propia — medido, 84px de barra en una columna de 496.
    js = (_STATIC / "commercial.js").read_text(encoding="utf-8")
    hier = js.split("commercialCreadasMonthTableHtml", 1)[1][:2500]
    assert "forecast-bar-cell" not in hier
    assert "commercial-creadas-dist" in hier
    assert ".commercial-creadas-dist .bar-track { display: block;" in css
    # Y el header de Distribucion arranca donde arranca su barra.
    assert '<th class="left">Distribución</th>' in js
