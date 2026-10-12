"""Calculadora de distribución de cuota (Módulo 01, sección «Configuración de Espacios»).

Contrato: colapsable CERRADO por defecto bajo el «Ratio orientativo», con el embed interactivo del Hub; sin backend; fórmulas EXACTAS de la tabla publicada (DEV 10 %, QA 20 %,
PRD 25 %, dominio `D×35%/N` y `M×25%/N`, reserva sin asignar); validación de entradas vacías, no numéricas, negativas y N=0; la tabla metodológica original no cambia."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from webapp.services import content_service as cs

REPO = Path(__file__).resolve().parents[1]
ASSET = REPO / "content_packs" / "datasphere_core" / "assets" / "danwor-calculadora-cuota.html"
M01 = "01_Arquitectura_y_Conectividad"


def _section(title_part: str) -> str:
    return next(s.html for s in cs.load_module(M01, "datasphere_core").sections if title_part in s.title)


# ── integración en el contenido ────────────────────────────────────────────────────────────
def test_the_collapsible_sits_below_the_ratio_table_with_the_hub_disclosure_closed_by_default():
    html = _section("Configuración de Espacios")
    assert html.count('<details class="dashboard-section"') == 1 and '<details class="dashboard-section" open' not in html                # patrón del Hub, cerrado por defecto
    assert "content-collapsible" not in html.split("Ratio orientativo", 1)[1]                                                          # no el contenedor gris propio de .content-collapsible
    assert '<span class="dashboard-section__title">Calculadora de distribución de cuota</span>' in html and "danwor-calculadora-cuota.html" in html
    assert html.index("Reserva tenant") < html.index("Calculadora de distribución de cuota")                                         # debajo de la tabla original
    assert html.index("Calculadora de distribución de cuota") < html.index("content-embed")                                           # el embed vive dentro del disclosure


def test_the_calculator_starts_with_editable_example_values():
    src = ASSET.read_text(encoding="utf-8")
    assert re.search(r'id="qc-d"[^>]*value="256"', src) and re.search(r'id="qc-m"[^>]*value="64"', src) and re.search(r'id="qc-n"[^>]*value="5"', src)
    assert "Valores de ejemplo" in src


def test_the_original_methodological_table_is_untouched():
    md = (REPO / "content_packs/datasphere_core/content/01_Arquitectura_y_Conectividad.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    for row in ("| **DEV / Desarrollo** | `D x 10%` | `M x 10%` |", "| **QA / Calidad** | `D x 20%` | `M x 20%` |", "| **PRD / Producción** | `D x 25%` | `M x 25%` |",
                "| **Espacios de dominio / negocio** | `(D x 35%) / N` | `(M x 25%) / N` |", "| **Reserva tenant** | No asignar inicialmente | No asignar inicialmente |"):
        assert row in md
    assert md.count("Calculadora de distribución de cuota") == 1                                                                       # sin duplicar explicaciones


def test_the_asset_has_no_backend_no_storage_and_the_orientative_disclaimer():
    src = ASSET.read_text(encoding="utf-8")
    assert not re.search(r"fetch\(|XMLHttpRequest|localStorage|sessionStorage|navigator\.sendBeacon|WebSocket|<form", src)
    assert "no es un sizing oficial de SAP" in src and "Simulación orientativa" in src
    assert '<link rel="stylesheet" href="danwor-shared.css">' in src                                                                  # estilos del Hub


# ── lógica (se ejecuta el MISMO script del asset con Node) ───────────────────────────────────
NODE = shutil.which("node")


def _run(cases: list[list[str]]) -> list[dict]:
    src = ASSET.read_text(encoding="utf-8")
    logic = re.search(r"<script>\s*/\* Lógica pura.*?</script>", src, re.S).group(0)[len("<script>"):-len("</script>")]
    program = logic + "\nconsole.log(JSON.stringify(" + json.dumps(cases) + ".map(function(c){return quotaCompute(c[0],c[1],c[2]);})));"
    out = subprocess.run([NODE, "-e", program], capture_output=True, text=True, check=True, timeout=30)
    return json.loads(out.stdout)


pytestmark_node = pytest.mark.skipif(NODE is None, reason="node no disponible")


@pytestmark_node
def test_formulas_match_the_published_ratio_exactly():
    (res,) = _run([["1000", "400", "5"]])
    assert res["ok"] is True
    r = res["rows"]
    assert (r["dev"]["disk"], r["dev"]["mem"]) == pytest.approx((100, 40))
    assert (r["qa"]["disk"], r["qa"]["mem"]) == pytest.approx((200, 80))
    assert (r["prd"]["disk"], r["prd"]["mem"]) == pytest.approx((250, 100))
    assert (r["dom"]["disk"], r["dom"]["mem"]) == pytest.approx((1000 * 0.35 / 5, 400 * 0.25 / 5))               # D×35%/N y M×25%/N: por espacio


@pytest.mark.parametrize("inputs,field", [
    (["", "400", "5"], "d"), (["1000", "", "5"], "m"), (["1000", "400", ""], "n"),                                  # vacías
    (["abc", "400", "5"], "d"), (["1000", "4x0", "5"], "m"), (["1000", "400", "cinco"], "n"), (["1e3", "400", "5"], "d"),   # no numéricas
    (["-1", "400", "5"], "d"), (["1000", "-5", "5"], "m"), (["1000", "400", "-2"], "n"),                            # negativas
    (["1000", "400", "0"], "n"), (["1000", "400", "2.5"], "n"),                                                     # N = 0 y N no entero
])
@pytestmark_node
def test_invalid_inputs_are_rejected_with_a_message_on_the_right_field(inputs, field):
    (res,) = _run([inputs])
    assert res["ok"] is False and res["errors"][field] and "rows" not in res


@pytestmark_node
def test_decimal_comma_zero_capacity_and_whitespace_are_accepted():
    ok, zero = _run([[" 1000,5 ", "400.25", " 4 "], ["0", "0", "1"]])
    assert ok["ok"] is True and ok["rows"]["dev"]["disk"] == pytest.approx(100.05)
    assert zero["ok"] is True and zero["rows"]["dom"]["disk"] == 0


def test_the_embed_fits_its_own_height_to_the_content_without_touching_other_embeds():
    src = ASSET.read_text(encoding="utf-8")
    assert "window.frameElement" in src and '"--embed-height"' in src and "ResizeObserver" in src             # altura a medida: sólo el contenedor de ESTE asset
    assert "if (!window.innerWidth) return;" in src                                                           # colapsable cerrado: no mide con ancho 0
