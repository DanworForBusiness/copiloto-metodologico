"""test_module_roadmap_tooltip.py — Posicionamiento del tooltip del "Recorrido del módulo".

Fix transversal: el tooltip del paso DEBE anclarse al CÍRCULO del número, no al borde de la
columna de la grilla. Antes, el marcador ocupaba toda la celda (block, 100%), así que el último
paso (`:last-child { right: -4px }`) quedaba pegado al borde derecho del contenido, desprendido
del número. Con el marcador acotado al círculo (`width: max-content`), tanto el default (izquierda)
como el último paso (derecha) se anclan al círculo y abren hacia adentro, consistente entre pasos.

Es CSS puro (no se puede medir geometría en pytest): se valida el contrato estructural + la
ausencia de hardcodes por módulo. La aceptación visual se hace en localhost (ver handoff)."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CSS = (REPO / "webapp" / "static" / "content.css").read_text(encoding="utf-8")
MODULE_HTML = (REPO / "webapp" / "templates" / "module.html").read_text(encoding="utf-8")


def test_marker_wraps_only_the_circle():
    # La causa raíz del fix: el marcador se acota al círculo (no ocupa toda la columna).
    m = re.search(r"\.module-roadmap__marker\s*\{[^}]*\}", CSS)
    assert m, "falta la regla .module-roadmap__marker"
    assert "width: max-content" in m.group(0), "el marcador debe acotarse al círculo (width: max-content)"


def test_last_step_opens_inward():
    # El último paso ancla el tooltip a la derecha (abre hacia adentro). Ahora relativo al círculo.
    assert re.search(r"\.module-roadmap__stage:last-child\s+\.module-roadmap__marker\[data-tip\]::after\s*\{[^}]*right:\s*-4px", CSS)


def test_default_steps_keep_left_alignment():
    # Pasos por defecto (izquierda/medio): el tooltip abre a la derecha (comportamiento previo preservado).
    assert re.search(r"\.module-roadmap__marker\[data-tip\]::after\s*\{[^}]*left:\s*-4px", CSS)


def test_no_module_specific_hardcode_in_roadmap_tooltip():
    # El fix es transversal: ninguna regla del roadmap puede estar atada a un módulo puntual.
    roadmap_rules = re.findall(r"\.module-roadmap[^\n]*\{[^}]*\}", CSS)
    joined = "\n".join(roadmap_rules)
    assert "04" not in joined and "Modelado" not in joined
    assert ":nth-child(3)" not in joined and "step-3" not in joined


def test_shared_journey_component_is_used():
    # El componente de recorrido es compartido (markup único en module.html) y el tooltip sale de data-tip.
    assert 'class="module-roadmap"' in MODULE_HTML
    assert 'class="module-roadmap__marker" data-tip=' in MODULE_HTML
