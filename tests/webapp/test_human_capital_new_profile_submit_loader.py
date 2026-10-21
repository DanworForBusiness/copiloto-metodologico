"""
test_human_capital_new_profile_submit_loader.py — CCHH: estado de carga del submit del alta de candidato (UX).

PATRÓN REUTILIZADO (no se diseñó ningún loader nuevo): el «botón con carga INTEGRADA» del Hub, `.asr-generate` (tools.css; usado por «Generar informe» del Catálogo): el botón que dispara la
operación recibe `.htmx-request`, muestra spinner + texto y oculta el label, conserva su ancho y queda deshabilitado legible. El alta es un POST plano (sin htmx), así que el estado lo activa el
script inline del form; todo lo demás (markup, clases, keyframes) es el existente. Sólo se adapta el color del spinner al fondo dorado de `.btn-primary` y se respeta `prefers-reduced-motion`.

Cubre: markup normal, contrato de comportamiento (harness node contra el JS REAL inline), accesibilidad, CSS y que la edición de candidatos NO cambia. La lógica de alta/persistencia/MSAL/subida
a SharePoint no se toca (sus suites siguen verdes)."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp.main import app
from webapp.services import auth_service, profile_repository

_ROOT = Path(__file__).resolve().parents[2]
_FORM = (_ROOT / "webapp" / "templates" / "partials" / "human_capital_profile_new_form.html").read_text(encoding="utf-8")
_EDIT = (_ROOT / "webapp" / "templates" / "partials" / "human_capital_profile_edit_form.html").read_text(encoding="utf-8")
_HC_CSS = (_ROOT / "webapp" / "static" / "human_capital.css").read_text(encoding="utf-8")
_TOOLS_CSS = (_ROOT / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
_HARNESS = Path(__file__).resolve().parent / "human_capital_new_profile_submit_loader.node.js"
_EMAIL = "hc-submit-loader-test@danwor.com"


@pytest.fixture(autouse=True)
def _authorized_and_isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _EMAIL)
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(profile_repository.remote_store, "push_path", lambda path: True)
    monkeypatch.setattr(profile_repository.remote_store, "enabled", lambda: False)


def _client() -> TestClient:
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": _EMAIL, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


# ── markup en estado normal ───────────────────────────────────────────────────────────────────────────────────
def test_the_rendered_page_has_the_submit_in_its_normal_state_with_the_existing_hub_pattern():
    text = _client().get("/cchh/candidatos/nuevo").text
    m = re.search(r'<button type="submit" class="btn-primary asr-generate" aria-live="polite">(.*?)</button>', text, re.S)
    assert m, "el submit principal debe usar el patrón `.asr-generate` del Hub"
    inner = m.group(1)
    assert '<span class="asr-generate__busy htmx-indicator" role="status"><span class="asr-generate__spinner" aria-hidden="true"></span>Guardando candidato…</span>' in inner
    assert '<span class="asr-generate__label">Cargar CV</span>' in inner
    assert "htmx-request" not in text.split("<script>")[0]                      # en estado normal ningún botón está en carga
    assert " disabled" not in inner and "aria-busy" not in text.split("<script>")[0]


def test_the_alternative_submitter_of_the_duplicate_advisory_uses_the_same_component_and_keeps_its_name_and_value():
    assert _FORM.count(">Guardando candidato…</span>") == 2                               # principal + «Cargar de todos modos»
    m = re.search(r'<button type="submit" form="hc-profile-form" name="duplicate_confirmed" value="true" class="btn-primary asr-generate" aria-live="polite">', _FORM)
    assert m and "Cargar de todos modos" in _FORM


def test_the_text_and_markup_are_the_existing_pattern_not_a_new_component():
    # ninguna clase nueva de loading: sólo las del patrón `.asr-generate` (tools.css) + `.btn-primary`
    new_classes = set(re.findall(r'class="([^"]*)"', _FORM[_FORM.index("<button type=\"submit\" class=\"btn-primary asr-generate\""):])) - {"btn-primary asr-generate", "asr-generate__busy htmx-indicator", "asr-generate__spinner", "asr-generate__label"}
    assert not [c for c in new_classes if "load" in c or "spin" in c or "busy" in c or "overlay" in c]
    for rule in (".asr-generate.htmx-request .asr-generate__busy", ".asr-generate.htmx-request .asr-generate__label", ".asr-generate:disabled", ".asr-generate__spinner", "@keyframes analysis-spin"):
        assert rule in _TOOLS_CSS                                                  # el patrón sigue existiendo en el CSS Hub-wide (no se redeclara acá)
    assert ".asr-generate {" not in _HC_CSS and "@keyframes" not in _HC_CSS


# ── CSS específico mínimo ─────────────────────────────────────────────────────────────────────────────────────
def test_css_only_adapts_the_spinner_color_and_respects_reduced_motion():
    assert re.search(r"\.hc-profile-form \.btn-primary\.asr-generate \.asr-generate__spinner \{[^}]*border-top-color: #0c0c0d", _HC_CSS)    # visible sobre el dorado de .btn-primary
    media = re.search(r"@media \(prefers-reduced-motion: reduce\) \{\s*\.hc-profile-form \.btn-primary\.asr-generate \.asr-generate__spinner \{([^}]*)\}", _HC_CSS)
    assert media and "animation: none" in media.group(1)


# ── accesibilidad y flujo en el script ────────────────────────────────────────────────────────────────────────
def test_the_script_sets_aria_busy_blocks_duplicates_and_restores_on_pageshow():
    script = re.search(r"<script>([\s\S]*?)</script>", _FORM).group(1)
    for needle in ('setAttribute("aria-busy", "true")', 'removeAttribute("aria-busy")', 'classList.add("htmx-request")', 'classList.remove("htmx-request")',
                   'form.dataset.hcBusy === "true") { event.preventDefault(); return; }', '"pageshow"', "event.persisted"):
        assert needle in script
    # `disabled` sólo dentro del setTimeout (después del evento): no se pierde name/value del submitter
    assert script.count("b.disabled = true") == 1 and script.index("b.disabled = true") > script.index("setTimeout(function () {")
    # el flujo de token acotado y de submit único NO se tocó
    for needle in ("dwHumanCapitalDocumentWriteToken({ interactive: false })", "Promise.race", "data-hc-token-bound-ms", "if (submitted) return;"):
        assert needle in script


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_submit_loader_behaviour_contract_holds():
    result = subprocess.run(["node", str(_HARNESS)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, f"CCHH new-profile submit loader invariants failed:\n{result.stdout}\n{result.stderr}"
    assert "ALL CCHH NEW-PROFILE SUBMIT LOADER INVARIANTS PASSED" in result.stdout


# ── alcance: la edición NO cambia ─────────────────────────────────────────────────────────────────────────────
def test_the_edit_form_is_untouched():
    assert "asr-generate" not in _EDIT and "Guardando candidato" not in _EDIT and "hcBusy" not in _EDIT
