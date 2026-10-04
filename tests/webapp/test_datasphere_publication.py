"""test_datasphere_publication.py — herramienta "Publicar modelo" (alta del Catálogo).

Contrato probado:
  * la herramienta es el HERMANO del Assessment pero PUBLICA: su ruta POST es propia y el modo lo FUERZA el servidor
    (``mode="publish"``, ``generate_catalog=True``) contra las raíces CANÓNICAS; el navegador no puede cruzar un flujo con el otro;
  * se abre por su contrato de navegación ``?tool=publicar`` (grupo "decisión"), por el "+" del Catálogo y por el paso 5 de la guía,
    y las TRES resuelven a la MISMA herramienta;
  * publicar es posible aunque el Catálogo esté vacío (alta del PRIMER dominio);
  * el Client Secret es transitorio; se mantienen los permisos (solo el dueño);
  * el Assessment sigue sin publicar.
"""
from __future__ import annotations

import inspect
import re
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import webapp.main as main
from webapp import access
from webapp.routers import datasphere_publication as pub_router
from webapp.services import auth_service
from webapp.services import datasphere_assessment_service as asd
from webapp.services import datasphere_publication_service as pub

REPO = Path(__file__).resolve().parents[2]
STEM = "04_Modelado_y_Desarrollo"
PACK = "datasphere_core"
MODULE_URL = f"/modules/{STEM}?pack={PACK}"
RUN_URL = f"/modules/{STEM}/datasphere-publication/run"


def _client(email):
    c = TestClient(main.app)
    if email:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session(
            {"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


@pytest.fixture()
def owner(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    return _client("owner@danwor.com")


def _valid_values():
    return asd.FormValues.from_mapping({
        "host": "https://t.eu10.hcs.cloud.sap",
        "token_url": "https://t.authentication.eu10.hana.ondemand.com/oauth/token",
        "client_id": "sb-abc", "space": "PROD", "root_object": "AM_Compras", "client": "Pampa",
        "features": None})


class _FakeOk(types.SimpleNamespace):
    ok = True
    error_code = None
    message = ""
    duration_s = 1.0
    exported_files = 3


# ───────────────────────── contrato de PUBLICACIÓN (servidor fuerza publish) ─────────────────────────
def test_server_forces_publish_against_canonical_roots(monkeypatch, tmp_path):
    canonical = tmp_path / "catalog_local"
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(canonical))
    monkeypatch.delenv("DANWOR_CATALOG_CSN_CACHE", raising=False)
    monkeypatch.delenv("DANWOR_PUBLICATION_MIRROR_UP", raising=False)
    monkeypatch.setattr(asd, "cli_available", lambda: True)

    captured = {}
    invalidated = {"n": 0}
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    monkeypatch.setattr(dsp, "run_export", lambda req: (captured.setdefault("req", req), _FakeOk())[1])
    monkeypatch.setattr(main, "_invalidate_catalog_cache", lambda: invalidated.__setitem__("n", invalidated["n"] + 1))

    outcome = pub.run(_valid_values(), "supersecret", user_email="owner@danwor.com")
    assert outcome.state == "SUCCESS", (outcome.state, outcome.error_code)
    req = captured["req"]
    # El SERVIDOR fuerza el modo publish y la regeneración del catálogo; nunca lo elige la UI.
    assert req.mode == "publish"
    assert req.generate_catalog is True
    assert req.mirror_up is False                      # Spaces apagado por defecto
    # Raíces CANÓNICAS (no un workspace efímero por operación como el Assessment).
    assert Path(req.catalog_root).resolve() == canonical.resolve()
    assert Path(req.csn_cache_dir).resolve() == (canonical / ".csn_cache").resolve()
    # El secreto se pasa al extractor pero no vive en FormValues.
    assert req.client_secret == "supersecret"
    # Publicar invalida la caché del catálogo (para que el nuevo dominio aparezca).
    assert invalidated["n"] == 1


def test_mirror_up_is_opt_in_by_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "c"))
    monkeypatch.setenv("DANWOR_PUBLICATION_MIRROR_UP", "1")
    monkeypatch.setattr(asd, "cli_available", lambda: True)
    captured = {}
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    monkeypatch.setattr(dsp, "run_export", lambda req: (captured.setdefault("req", req), _FakeOk())[1])
    monkeypatch.setattr(main, "_invalidate_catalog_cache", lambda: None)
    pub.run(_valid_values(), "s", user_email="o@danwor.com")
    assert captured["req"].mirror_up is True


def test_assessment_remains_non_publishing():
    # Guardia estática: el Assessment nunca regenera el catálogo ni cambia a publish.
    src = (REPO / "webapp" / "services" / "datasphere_assessment_service.py").read_text(encoding="utf-8")
    assert 'generate_catalog=False' in src
    assert 'mode="assessment"' in src
    assert 'mode="publish"' not in src


def test_browser_cannot_switch_publication_into_another_mode():
    # La ruta de publicación NO acepta un parámetro `mode` manipulable: el modo es del servidor.
    sig = inspect.signature(pub_router.run_publication)
    assert "mode" not in sig.parameters
    # Y el servicio codifica publish de forma literal.
    src = (REPO / "webapp" / "services" / "datasphere_publication_service.py").read_text(encoding="utf-8")
    assert 'mode="publish"' in src and 'generate_catalog=True' in src


def test_invalid_input_does_not_touch_the_extractor(monkeypatch):
    monkeypatch.setattr(asd, "cli_available", lambda: True)
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    monkeypatch.setattr(dsp, "run_export", lambda req: (_ for _ in ()).throw(AssertionError("no debe ejecutarse")))
    bad = asd.FormValues.from_mapping({"host": "", "token_url": "", "client_id": "", "space": "", "root_object": "", "client": "", "features": None})
    outcome = pub.run(bad, "", user_email="o@danwor.com")
    assert outcome.state == "INVALID" and outcome.error_code == "INVALID_PARAMETER"


# ───────────────────────── navegación / entrypoints (mismo tool) ─────────────────────────
def test_deeplink_tool_publicar_opens_form_in_decision_group(owner):
    html = owner.get(f"{MODULE_URL}&tool=publicar").text
    assert 'id="dsp-form"' in html                                   # el formulario de publicación está presente
    assert RUN_URL in html                                           # postea a su PROPIA ruta
    # initial_tool='publicar' llega a copilotLayout (que abre el grupo decisión y colapsa los otros).
    assert "'publicar')" in html


def test_layout_maps_publicar_to_decision_group_and_opens_its_tab():
    js = (REPO / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    assert 'publicar: "decision"' in js
    # El tab se abre con el id de la herramienta cuando el grupo es "decisión".
    assert 'toolGroup === "decision" && initialTool ? initialTool' in js


def test_catalog_plus_and_guide_step5_resolve_to_the_same_tool(owner, monkeypatch, tmp_path):
    # "+" del Catálogo (requiere catálogo con datos) y paso 5 de la guía apuntan a la MISMA herramienta (?tool=publicar).
    canonical = tmp_path / "catalog_local" / "ClienteDemo" / "DominioDemo"
    canonical.mkdir(parents=True)
    (canonical / "flujo_DominioDemo.md").write_text(
        "---\nauto:\n  tipo: flujo\n  dominio: DominioDemo\nmanual:\n  cliente: ClienteDemo\n---\n# demo\n", encoding="utf-8")
    # El autouse de conftest redirige config.BASE_DIR a un tmp; eso rompe la carga dinámica de catalog_service
    # (vive en BASE_DIR/tools/catalog). Apuntamos BASE_DIR al repo real para ejercer el Catálogo de verdad.
    monkeypatch.setattr(config, "BASE_DIR", REPO)
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog_local"))
    main._invalidate_catalog_cache()
    try:
        html = owner.get(MODULE_URL).text
        assert "catalog-add-btn" in html                             # el "+" aparece con catálogo cargado
        assert "toolTab='publicar'" in html                          # el "+" cambia a la pestaña Publicar (mismo tool)
        # El paso 5 de la guía resuelve a la MISMA herramienta (tool=publicar) en la misma pestaña.
        g = owner.get(f"/catalog/guide/como-publicar-dominios?embed=1&pack={PACK}").text
        assert re.search(r'href="/modules/[^"]+&(amp;)?tool=publicar"[^>]*target="_parent"', g)
    finally:
        main._invalidate_catalog_cache()


def test_publication_available_even_with_empty_catalog(owner, monkeypatch, tmp_path):
    # §7: con el Catálogo VACÍO, la pestaña "Catálogo" no aparece, pero la herramienta de publicación SÍ (alta del primer dominio).
    (tmp_path / "empty_catalog").mkdir()
    monkeypatch.setattr(config, "BASE_DIR", REPO)        # catalog_service cargable (ver nota en test del "+")
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "empty_catalog"))
    main._invalidate_catalog_cache()
    try:
        html = owner.get(f"{MODULE_URL}&tool=publicar").text
        assert "toolTab==='catalogo'" not in html                    # catálogo vacío → sin pestaña Catálogo
        assert 'id="dsp-form"' in html                               # pero sí el formulario de publicación
        assert "toolTab==='publicar'" in html
    finally:
        main._invalidate_catalog_cache()


# ───────────────────────── seguridad / secretos ─────────────────────────
def test_non_owner_sees_no_publication_tool(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    other = _client("intruso@danwor.com")
    html = other.get(f"{MODULE_URL}&tool=publicar").text
    assert 'id="dsp-form"' not in html
    assert "toolTab==='publicar'" not in html                        # ni la pestaña Publicar


def test_post_run_requires_session(monkeypatch):
    anon = _client(None)
    r = anon.post(RUN_URL, data={"pack": PACK}, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code in (401, 403)


def test_post_run_rejects_non_owner(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    other = _client("intruso@danwor.com")
    r = other.post(RUN_URL, data={"pack": PACK}, headers={"Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code in (401, 403)                               # nunca 200


def test_client_secret_is_transient_and_not_in_context(owner):
    # FormValues no tiene campo de secreto; el contexto de la herramienta no lo expone.
    assert "client_secret" not in asd.FormValues.__dataclass_fields__
    ctx = pub.tool_context(_owner_request(owner), STEM)
    assert "client_secret" not in (ctx.get("form") or {})
    # El partial no re-renderiza un value para el password.
    partial = (REPO / "webapp" / "templates" / "partials" / "datasphere_publication_tool.html").read_text(encoding="utf-8")
    assert 'kind != "password"' in partial                           # el macro nunca emite value para password


def _owner_request(client):
    # Un Request mínimo con la cookie de sesión del owner (para tool_context).
    token = client.cookies.get(auth_service.COOKIE_NAME)
    scope = {"type": "http", "headers": [(b"cookie", f"{auth_service.COOKIE_NAME}={token}".encode())]}
    from starlette.requests import Request
    return Request(scope)


# ───────────────────────── rutas distintas / assets ─────────────────────────
def test_publication_and_assessment_routes_are_distinct():
    pub_paths = {r.path for r in pub_router.router.routes}
    from webapp.routers import datasphere_assessment as asr
    asr_paths = {r.path for r in asr.router.routes}
    assert "/modules/{stem}/datasphere-publication/run" in pub_paths
    assert "/modules/{stem}/datasphere-assessment/run" in asr_paths
    assert pub_paths.isdisjoint(asr_paths)                           # ninguna ruta compartida


def test_publication_js_is_loaded_and_distinct():
    base = (REPO / "webapp" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "datasphere_publication.js" in base and "datasphere_assessment.js" in base
    assert (REPO / "webapp" / "static" / "datasphere_publication.js").exists()
