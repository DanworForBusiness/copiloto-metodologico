"""
test_hub_tile_info.py — HUB: TILE INFO CONTRACT (opcional, trust /
explainability).

Los tiles KPI reusables del Hub aceptan ahora metadata informativa OPCIONAL
que explica el CONTRATO DE DATO del número: qué mide, de qué fuente, con qué
grano, y bajo qué condiciones es comparable con otra vista. No es un
indicador de error ni de estado.

Las invariantes de comportamiento viven en hub_tile_info.node.js, que ejecuta
el generador REAL (`renderDashboardKpi`). Acá quedan las de ARCHIVO: que se
extendió el primitivo existente en vez de inventar un componente paralelo,
que se reusó el tooltip compartido del Hub, que la afordancia no puede mover
la geometría del tile, y que este incremento NO tocó nada de datos.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"
_HARNESS = Path(__file__).resolve().parent / "hub_tile_info.node.js"
_REPORTING_JS = (_STATIC / "reporting.js").read_text(encoding="utf-8")
_TOOLS_CSS = (_STATIC / "tools.css").read_text(encoding="utf-8")


def _css_rules(raw: str) -> str:
    """CSS sin comentarios: los bloques explicativos citan a propósito los
    literales que estos tests prohíben (patrón ya conocido en este repo)."""
    return re.sub(r"/\*.*?\*/", "", raw, flags=re.S)


def _js_code(raw: str) -> str:
    return "\n".join(l for l in raw.splitlines() if not l.strip().startswith("//"))


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_hub_tile_info_invariants_hold():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"Hub tile info invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL HUB TILE INFO INVARIANTS PASSED" in result.stdout


def test_the_existing_tile_primitive_was_extended_not_duplicated():
    code = _js_code(_REPORTING_JS)
    # UN solo generador de tiles KPI, el de siempre, ahora con `info` opcional.
    assert code.count("function renderDashboardKpi(") == 1
    assert "smartAccent, info })" in code
    assert code.count("function renderKpiInfoHtml(") == 1
    # Ningún componente "tile informativo" paralelo.
    for banned in ("renderInfoTile", "activity-kpi--info", "info-tile", "kpi-info-card"):
        assert banned not in code, banned


def test_the_affordance_reuses_the_shared_hub_tooltip():
    code = _js_code(_REPORTING_JS)
    rules = _css_rules(_TOOLS_CSS)
    # Reusa la CAJA compartida `.chart-tooltip` (tools.css) — cero caja
    # flotante nueva. NO usa `.chart-tooltip-anchor`: ese patrón envuelve al
    # disparador, y acá el disparador es un icono en una esquina, así que el
    # tooltip se ancla al CARD (medido: anclado al icono se salía 27,5px del
    # viewport a 375px en la primera columna). El disparo va por hermano
    # adyacente, y la caja compartida queda intacta para sus otros consumidores.
    assert 'class="chart-tooltip"' in code
    assert "chart-tooltip-anchor" not in code.split("function renderKpiInfoHtml(", 1)[1].split("\n}", 1)[0]
    assert ".activity-kpi > .chart-tooltip {" in rules
    assert "max-width: min(280px, 100%)" in rules      # nunca mas ancho que el card
    for pseudo in (":hover", ":focus", ":focus-within", ":focus-visible"):
        assert f".activity-kpi__info{pseudo} + .chart-tooltip" in rules, pseudo
    # Y el anchor compartido sigue existiendo para sus consumidores de siempre.
    assert ".chart-tooltip-anchor { position: relative; }" in rules
    assert ".chart-tooltip-anchor:focus-within .chart-tooltip" in rules
    # Y no se redeclara el tooltip compartido: la regla BASE existe una sola
    # vez. Anclada a inicio de línea a propósito — el substring suelto matchea
    # también los selectores compuestos, que sí son legítimos.
    assert len(re.findall(r"^\.chart-tooltip \{", rules, re.M)) == 1


def test_the_affordance_lives_in_shared_css_so_the_primitive_is_hub_reusable():
    # El contrato de clase vive en tools.css (compartido), no en el CSS de una
    # vertical: cualquier consumidor de `.activity-kpi` puede adoptarlo sin CSS
    # nuevo — mismo criterio que `.activity-kpi--clickable`.
    rules = _css_rules(_TOOLS_CSS)
    assert ".activity-kpi__info {" in rules
    assert ".activity-kpi--clickable {" in rules      # el precedente
    for local in ("reporting.css", "commercial.css"):
        assert "activity-kpi__info" not in (_STATIC / local).read_text(encoding="utf-8"), local


def test_the_affordance_cannot_change_tile_geometry():
    rules = _css_rules(_TOOLS_CSS)
    block = rules.split(".activity-kpi__info {", 1)[1].split("}", 1)[0]
    # Absolutamente posicionada abajo a la derecha: fuera del flex column del
    # card, así que un tile con info mide lo mismo que uno sin info.
    assert "position: absolute" in block
    assert "right:" in block and "bottom:" in block
    # El card es el contenedor de posicionamiento.
    base = rules.split(".activity-kpi {", 1)[1].split("}", 1)[0]
    assert "position: relative" in base
    # Nada de footer ni de cambio de grilla para alojar el icono.
    for banned in ("activity-kpi__footer", "grid-template-columns", "min-height"):
        assert banned not in block, banned
    # La grilla de tiles queda igual.
    assert ".activity-kpi-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));" in rules


def test_the_icon_follows_the_hub_icon_convention_and_stays_secondary():
    rules = _css_rules(_TOOLS_CSS)
    svg = rules.split(".activity-kpi__info svg {", 1)[1].split("}", 1)[0]
    # Trazo, nunca relleno — misma convención que `.theme-toggle svg`.
    assert "fill: none" in svg and "stroke: currentColor" in svg
    layout = (_STATIC / "layout.css").read_text(encoding="utf-8")
    assert "fill: none; stroke: currentColor" in layout      # el precedente existe
    # Secundario al valor: más chico que el botón-ícono del topbar (17px).
    ancho = int(re.search(r"width:\s*(\d+)px", svg).group(1))
    assert ancho < 17, ancho


def test_only_the_two_proven_operations_tiles_are_wired():
    code = _js_code(_REPORTING_JS)
    # Dos contratos documentados, ni uno más: la adopción es incremental.
    assert code.count("const _KPI_INFO = {") == 1
    assert code.count("_KPI_INFO.carteraTrabajadas") == 1
    assert code.count("_KPI_INFO.rentabilidadTrabajadas") == 1
    assert code.count("_KPI_INFO.") == 2
    # Ningún otro `info:` colado en otro tile.
    assert code.count("info: _KPI_INFO") == 1               # Rentabilidad, inline
    assert code.count("? _KPI_INFO.carteraTrabajadas : undefined") == 1
    # Dos CALL SITES con `info` — la definición de la función no cuenta.
    llamadas = re.findall(r"(?<!function )renderDashboardKpi\(\{[^}]*\binfo\b", code)
    assert len(llamadas) == 2, llamadas


def test_the_copy_explains_the_contract_and_never_excuses_a_difference():
    # Sobre el CODIGO, no la prosa: los comentarios que documentan este
    # contrato citan a propósito los mismos literales que se cuentan acá
    # (patrón ya conocido en este repo).
    info = _js_code(_REPORTING_JS).split("const _KPI_INFO = {", 1)[1].split("\n};", 1)[0]
    assert info.count("Time Logs") == 2                     # la FUENTE, en las dos
    assert info.count("Grano: proyecto") == 2               # el GRANO, en las dos
    assert info.count("Tipo Proyecto") == 2                 # la DIMENSIÓN, en las dos
    cartera = info.split("carteraTrabajadas: [", 1)[1].split("],", 1)[0]
    rentab = info.split("rentabilidadTrabajadas: [", 1)[1].split("],", 1)[0]
    # Cartera/Mantenimiento FIJA su tipo — su alcance es fijo por construcción.
    assert "Tipo Proyecto = Mantenimiento" in cartera
    # Rentabilidad es AGNÓSTICA (owner adjustment): su alcance lo determina el
    # filtro activo, así que no puede nombrar Mantenimiento como si fuera suyo.
    assert "Mantenimiento" not in rentab
    # Y nadie promete todavía comparabilidad con la perspectiva Proyecto: esa
    # sigue leyendo el campo manual `hsTrabajadas` (ver el audit de linaje).
    assert "Cartera / Proyecto" not in info and "Tipo Proyecto = Proyecto" not in info
    # Nunca una disculpa: el icono no explica una inconsistencia.
    for banned in ("pueden diferir", "puede variar", "aproximad", "no coincid",
                   "error", "puede que", "estimado"):
        assert banned not in info.lower(), banned


def test_this_increment_touched_no_data_logic():
    # PRESENTACIÓN pura: ni el service, ni el repository, ni el router saben
    # que existe este contrato de presentación.
    for rel in ("webapp/services/reporting_service.py",
                "webapp/services/reporting_repository.py",
                "webapp/routers/reporting.py"):
        src = (_ROOT / rel).read_text(encoding="utf-8")
        for banned in ("_KPI_INFO", "activity-kpi__info", "renderKpiInfoHtml", "chart-tooltip"):
            assert banned not in src, (rel, banned)
    code = _js_code(_REPORTING_JS)
    # Y el generador de tiles no toca datos: solo formatea lo que recibe.
    fn = code.split("function renderKpiInfoHtml(", 1)[1].split("\n}", 1)[0]
    assert "fetch(" not in fn and "state." not in fn and "trab" not in fn


def test_the_reconciliation_contracts_are_untouched_by_this_increment():
    # Los correctives de datos ya probados siguen exactamente donde estaban —
    # este incremento no puede haberlos rozado.
    service = (_ROOT / "webapp" / "services" / "reporting_service.py").read_text(encoding="utf-8")
    assert "grouped: dict[tuple[str, str, int | None], dict] = {}" in service   # anti fan-out
    assert "UNIVERSE SYMMETRY corrective" in service                            # unión de universo
    assert 'trabajadas = _round2(trab_by_proyecto.get(key, {}).get(anio, [0.0] * 12)[mes_num - 1])' in service
