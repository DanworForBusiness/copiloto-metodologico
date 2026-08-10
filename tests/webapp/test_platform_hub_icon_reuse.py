"""
test_platform_hub_icon_reuse.py — Danwor Hub hero (revisión visual, ver
HANDOFF): el ícono de 4 cuadrados usado por el switcher de workspace debe
ser la MISMA fuente que el hero de Danwor Hub, nunca un duplicado
visualmente similar. Se verifica leyendo el template fuente (no hay motor
de layout en la suite) — mismo criterio ya usado para otros invariantes de
Staffing en este directorio.
"""
from __future__ import annotations

from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates"
_TOPBAR = (_TEMPLATES / "partials" / "platform_topbar.html").read_text(encoding="utf-8")
_SELECTOR = (_TEMPLATES / "platform_selector.html").read_text(encoding="utf-8")

_ICON_SVG = (
    '<svg viewBox="0 0 24 24" aria-hidden="true">'
    '<rect x="3" y="3" width="7" height="7" rx="1.5"/>'
    '<rect x="14" y="3" width="7" height="7" rx="1.5"/>'
    '<rect x="3" y="14" width="7" height="7" rx="1.5"/>'
    '<rect x="14" y="14" width="7" height="7" rx="1.5"/></svg>'
)


def test_hub_icon_macro_defined_exactly_once_in_the_topbar_partial():
    assert _TOPBAR.count("{% macro hub_icon()") == 1
    assert _ICON_SVG in _TOPBAR
    # Una sola definición del markup del SVG en todo el partial — el
    # switcher (2 usos posibles: con/sin otros workspaces) llama al macro,
    # nunca repite el <svg> inline una segunda vez.
    assert _TOPBAR.count(_ICON_SVG) == 1


def test_topbar_switcher_usage_calls_the_macro_not_an_inline_svg():
    # Un solo uso: el switcher SOLO existe con 2+ workspaces disponibles
    # (Danwor Hub Workspace Switcher — Single-Workspace UX Correction, ver
    # HANDOFF) — sin fallback/ícono deshabilitado con 1 solo workspace.
    assert _TOPBAR.count("{{ hub_icon() }}") == 1
    assert "<rect" not in _TOPBAR.split("{% endmacro %}", 1)[1]


def test_platform_selector_imports_and_reuses_the_same_macro():
    assert 'from "partials/platform_topbar.html" import hub_icon' in _SELECTOR
    assert "{{ hub_icon() }}" in _SELECTOR
    # El hero NO define su propio <svg> — reusa el macro, nunca copia el
    # markup del ícono a un segundo lugar.
    assert "<rect" not in _SELECTOR


def test_optional_card_eyebrow_capability_still_present_in_template():
    # La capacidad sigue disponible — la decisión de contenido (ningún
    # valor hoy) vive en _WORKSPACE_CARD_COPY (platform.py), no en la
    # remoción de esta feature del template.
    assert "{% if card.meta.eyebrow %}" in _SELECTOR
    assert "{{ card.meta.eyebrow }}" in _SELECTOR


def test_hub_icon_hero_usage_has_no_switcher_button_chrome():
    # Uso gráfico puro en el hero — nunca la clase .theme-toggle (34x34px,
    # borde + fondo de botón) que sí envuelve al ícono en el switcher.
    hero_line = next(line for line in _SELECTOR.splitlines() if "hub_icon()" in line)
    assert "theme-toggle" not in hero_line
