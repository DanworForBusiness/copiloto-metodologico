"""test_module_roadmap_tooltip.py — Posicionamiento del tooltip del "Recorrido del módulo".

Fix transversal en DOS partes:
  1. el marcador envuelve SOLO el círculo del número (`width: max-content`): el tooltip se ancla al número y no al borde de la
     columna de la grilla (antes, el último paso quedaba pegado al borde del contenido, desprendido del número);
  2. el último paso NO tiene una regla direccional propia: todos los pasos usan el MISMO posicionamiento — el tooltip arranca apenas a
     la izquierda del círculo y se extiende a la derecha — como los pasos 1 y 2. No hay "abrir hacia adentro" ni lógica por geometría.

Es CSS puro (no se puede medir geometría en pytest): se valida el contrato estructural + la ausencia de hardcodes/variantes por
posición. La aceptación visual se hace en localhost."""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CSS = (REPO / "webapp" / "static" / "content.css").read_text(encoding="utf-8")
MODULE_HTML = (REPO / "webapp" / "templates" / "module.html").read_text(encoding="utf-8")


def _rule(selector_regex: str) -> str:
    m = re.search(selector_regex + r"\s*\{[^}]*\}", CSS)
    assert m, f"falta la regla {selector_regex}"
    return m.group(0)


def test_marker_wraps_only_the_circle():
    # Parte 1: el marcador se acota al círculo (no ocupa toda la columna).
    assert "width: max-content" in _rule(r"\.module-roadmap__marker")


def test_default_tooltip_rule_is_left_anchored_to_the_marker_for_every_step():
    # Los pasos 1 y 2 definen el comportamiento canónico: arranca apenas a la izquierda del círculo y se extiende a la derecha.
    assert "left: -4px" in _rule(r"\.module-roadmap__marker\[data-tip\]::after")
    assert "left: 9px" in _rule(r"\.module-roadmap__marker\[data-tip\]::before")


def test_tooltip_rules_never_flip_direction():
    # Parte 2: ninguna regla del tooltip fija el lado derecho ni `left: auto` (el default se aplica igual a todos).
    for pseudo in ("after", "before"):
        rule = _rule(rf"\.module-roadmap__marker\[data-tip\]::{pseudo}")
        assert "left: auto" not in rule
        assert not re.search(r"(?<![\w-])right\s*:", rule), rule


def test_last_item_has_no_special_opposite_direction_anchor():
    # El último/derecho NO recibe una regla propia sobre el tooltip: mismo contrato que los demás pasos.
    tooltip_selectors = re.findall(r"([^{}]*\.module-roadmap__marker\[data-tip\][^{}]*)\{", CSS)
    assert tooltip_selectors, "no se encontraron reglas del tooltip"
    for sel in tooltip_selectors:
        assert not re.search(r":(last|first|nth|only)-(child|of-type)", sel), f"variante por posición en el tooltip: {sel.strip()}"
    # y ninguna regla del recorrido ata el tooltip al último hijo de la grilla
    assert ".module-roadmap__stage:last-child .module-roadmap__marker" not in CSS


def test_no_module_specific_hardcode_in_roadmap_tooltip():
    # El fix es transversal: ninguna regla del recorrido puede estar atada a un módulo o paso puntual.
    roadmap_rules = re.findall(r"\.module-roadmap[^\n]*\{[^}]*\}", CSS)
    joined = "\n".join(roadmap_rules)
    assert "04" not in joined and "Modelado" not in joined
    assert ":nth-child(3)" not in joined and "step-3" not in joined


def test_shared_journey_component_is_used():
    # El componente de recorrido es compartido (markup único en module.html) y el tooltip sale de data-tip.
    assert 'class="module-roadmap"' in MODULE_HTML
    assert 'class="module-roadmap__marker" data-tip=' in MODULE_HTML
