"""
test_platform_hub_icon_reuse.py — Danwor Hub hero (revisión visual, ver
HANDOFF): el ícono de 4 cuadrados usado por el switcher de workspace debe
ser la MISMA fuente que el hero de Danwor Hub, nunca un duplicado
visualmente similar. Se verifica leyendo el template fuente (no hay motor
de layout en la suite) — mismo criterio ya usado para otros invariantes de
Staffing en este directorio.

Corrective posterior (Danwor Hub Login — Canonical Brand Lockup, ver
HANDOFF): el bloque canónico completo (kicker + ícono + "Danwor Hub") se
extrajo a `hub_brand_lockup()`, en el MISMO partial que `hub_icon()` — el
hero autenticado (`platform_selector.html`) y la landing anónima de Danwor
Hub (`landing.html`) ahora importan/llaman ese macro en vez de repetir el
`<span class="hub-hero__icon">{{ hub_icon() }}</span>Danwor Hub` a mano.
"""
from __future__ import annotations

from pathlib import Path

_TEMPLATES = Path(__file__).resolve().parents[2] / "webapp" / "templates"
_TOPBAR = (_TEMPLATES / "partials" / "platform_topbar.html").read_text(encoding="utf-8")
_SELECTOR = (_TEMPLATES / "platform_selector.html").read_text(encoding="utf-8")
_LANDING = (_TEMPLATES / "landing.html").read_text(encoding="utf-8")

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
    # Una sola definición del markup del SVG en todo el partial — cada
    # consumidor (switcher, hub_brand_lockup) llama al macro, ninguno
    # repite el <svg> inline.
    assert _TOPBAR.count(_ICON_SVG) == 1


def test_topbar_switcher_and_brand_lockup_both_call_the_macro_not_inline_svg():
    # Dos llamadas reales al macro dentro del propio partial: el switcher
    # (SOLO con 2+ workspaces disponibles — Danwor Hub Workspace Switcher
    # — Single-Workspace UX Correction, ver HANDOFF) y `hub_brand_lockup()`
    # (kicker+ícono+título canónico) — ninguna repite el <svg> inline.
    assert _TOPBAR.count("{{ hub_icon() }}") == 2
    assert "<rect" not in _TOPBAR.split("{% endmacro %}", 1)[1]


def test_hub_brand_lockup_macro_defined_once_and_reuses_hub_icon():
    assert _TOPBAR.count("{% macro hub_brand_lockup()") == 1
    lockup_body = _TOPBAR.split("{% macro hub_brand_lockup()", 1)[1].split("{% endmacro %}", 1)[0]
    assert "{{ hub_icon() }}" in lockup_body
    assert "<rect" not in lockup_body
    # El kicker y el título "Danwor Hub" son fijos (no parametrizados) —
    # es LA identidad canónica, no una plantilla genérica de marca.
    assert "Una plataforma · Todos los servicios Danwor" in lockup_body
    assert "Danwor Hub" in lockup_body


def test_platform_selector_imports_and_reuses_the_brand_lockup_macro():
    assert 'from "partials/platform_topbar.html" import hub_brand_lockup' in _SELECTOR
    assert "{{ hub_brand_lockup() }}" in _SELECTOR
    # El hero NO define su propio <svg> ni repite el kicker/título a
    # mano — reusa el macro completo; el texto del kicker ya no vive
    # copiado en este archivo, solo referenciado vía la llamada al macro.
    assert "<rect" not in _SELECTOR
    assert "Una plataforma · Todos los servicios Danwor" not in _SELECTOR


def test_landing_login_imports_and_reuses_the_same_brand_lockup_macro():
    # Segundo consumidor real (Danwor Hub Login — Canonical Brand Lockup
    # Corrective, ver HANDOFF): la landing anónima de Danwor Hub usa el
    # MISMO macro que el hero autenticado — no una aproximación visual
    # separada.
    assert 'from "partials/platform_topbar.html" import hub_brand_lockup' in _LANDING
    assert "{{ hub_brand_lockup() }}" in _LANDING
    assert "<rect" not in _LANDING


def test_optional_card_eyebrow_capability_still_present_in_template():
    # La capacidad sigue disponible — la decisión de contenido (ningún
    # valor hoy) vive en _WORKSPACE_CARD_COPY (platform.py), no en la
    # remoción de esta feature del template.
    assert "{% if card.meta.eyebrow %}" in _SELECTOR
    assert "{{ card.meta.eyebrow }}" in _SELECTOR


def test_hub_brand_lockup_usage_has_no_switcher_button_chrome():
    # Uso gráfico puro del ícono dentro del lockup — nunca la clase
    # .theme-toggle (34x34px, borde + fondo de botón) que sí envuelve al
    # ícono en el switcher del topbar.
    lockup_body = _TOPBAR.split("{% macro hub_brand_lockup()", 1)[1].split("{% endmacro %}", 1)[0]
    assert "theme-toggle" not in lockup_body
