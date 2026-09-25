"""COMMERCIAL LICENSING — las acciones de fila tienen que ser ALCANZABLES.

Regresión: "Ver detalle de licencias" se renderizaba bien y al clickearlo no
pasaba nada — sin navegación, sin filtrado, sin error en consola. Misma causa
mataba "Mover a sin prioridad" y "Restaurar".

Los botones llevaban `onclick="event.stopPropagation()"` inline para no
disparar TAMBIÉN el click de la fila que los contiene. Pero el único delegado
de Comercial escucha en `document` en fase de BUBBLE: cortar la propagación en
el botón lo deja fuera de su alcance. Medido en browser sobre el markup real,
el evento llegaba a `document` en CAPTURE (true) y no en BUBBLE (false).

El aislamiento que ese guard intentaba dar YA lo daba el ORDEN de los checks
dentro del delegado: cada botón se evalúa ANTES que la fila que lo contiene y
corta con `return`. El guard era redundante y, encima, letal.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_HARNESS = Path(__file__).resolve().parent / "commercial_licensing_action_wiring.node.js"
_COMMERCIAL_JS = (_ROOT / "webapp" / "static" / "commercial.js").read_text(encoding="utf-8")


def _sin_comentarios(src: str) -> str:
    """Los comentarios de `commercial.js` NOMBRAN el patrón prohibido a
    propósito (explican por qué se fue). Contarlos daría un falso positivo."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"//[^\n]*", "", src)


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_licensing_action_wiring_invariants_hold():
    r = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, f"Licensing action wiring failed:\n{r.stdout}\n{r.stderr}"
    assert "ALL COMMERCIAL LICENSING ACTION WIRING INVARIANTS PASSED" in r.stdout


def test_ningun_control_de_comercial_corta_la_propagacion_inline():
    """EL GUARD DE LA CLASE DE BUG, no sólo de estos tres botones.

    Comercial tiene UN solo delegado de click y cuelga de `document`. Cualquier
    `onclick` inline que corte la propagación deja su propio control inerte —
    se renderiza, se clickea y no pasa nada, sin error que lo delate.
    """
    codigo = _sin_comentarios(_COMMERCIAL_JS)
    assert "onclick=" not in codigo, (
        "hay un `onclick` inline en commercial.js: si corta la propagación, el "
        "delegado de `document` (bubble) nunca lo ve y la acción queda inerte"
    )


def test_el_delegado_sigue_siendo_uno_solo_y_en_document():
    """La premisa de la que depende el test de arriba. Si Comercial pasara a
    delegar en un contenedor más cercano —o en capture— habría que rehacer el
    razonamiento antes de volver a permitir `stopPropagation` inline."""
    assert _COMMERCIAL_JS.count('document.addEventListener("click"') == 1
    assert _sin_comentarios(_COMMERCIAL_JS).count('addEventListener("click"') == 1


def test_el_aislamiento_lo_da_el_orden_de_los_checks():
    """Cada acción se evalúa ANTES que la fila que la contiene, y corta con
    `return`. Ese orden es el que sustituye al guard inline: si alguien lo
    invierte, el botón volvería a disparar la acción de la fila."""
    cuerpo = _COMMERCIAL_JS
    pos = {
        nombre: cuerpo.index(f'closest("[data-{nombre}]")')
        for nombre in (
            "commercial-licensing-account-row",
            "commercial-licencias-opportunity-row",
            "commercial-licprofile-defer",
            "commercial-licprofile-restore",
            "commercial-licensing-profile-account-row",
        )
    }
    assert pos["commercial-licensing-account-row"] < pos["commercial-licencias-opportunity-row"], (
        "el botón de licencias debe evaluarse antes que la fila de Oportunidades"
    )
    for boton in ("commercial-licprofile-defer", "commercial-licprofile-restore"):
        assert pos[boton] < pos["commercial-licensing-profile-account-row"], (
            f"{boton} debe evaluarse antes que la fila del Perfil"
        )
