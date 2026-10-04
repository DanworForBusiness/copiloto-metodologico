"""test_datasphere_publication.py — herramienta "Publicar modelo" (alta del Catálogo).

Contrato probado:
  * la herramienta es el HERMANO del Assessment pero PUBLICA: su ruta POST es propia y el modo lo FUERZA el servidor
    (``mode="publish"``, ``generate_catalog=True``) contra las raíces CANÓNICAS; el navegador no puede cruzar un flujo con el otro;
  * NO existe una pestaña "Publicar": la publicación es un ESTADO del Catálogo ("Herramientas de decisión → Catálogo"), que se abre por
    el "+" del encabezado y por el paso 5 de la guía (``?tab=catalogo&catalog_action=publish``): UN solo formulario, DOS puertas;
  * el formulario comparte campos y estilo con el Assessment (partial ``datasphere_form_fields``);
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


# ───────────────────────── navegación: la publicación es un ESTADO del Catálogo ─────────────────────────
CATALOG_PUBLISH_URL = f"{MODULE_URL}&tab=catalogo&catalog_action=publish"


def _demo_catalog(monkeypatch, tmp_path, *, empty=False):
    root = tmp_path / "catalog_local"
    if empty:
        root.mkdir()
    else:
        d = root / "ClienteDemo" / "DominioDemo"
        d.mkdir(parents=True)
        (d / "flujo_DominioDemo.md").write_text(
            "---\nauto:\n  tipo: flujo\n  dominio: DominioDemo\nmanual:\n  cliente: ClienteDemo\n---\n# demo\n", encoding="utf-8")
    # El autouse de conftest redirige config.BASE_DIR a un tmp; eso rompe la carga dinámica de catalog_service
    # (vive en BASE_DIR/tools/catalog). Apuntamos BASE_DIR al repo real para ejercer el Catálogo de verdad.
    monkeypatch.setattr(config, "BASE_DIR", REPO)
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(root))
    main._invalidate_catalog_cache()


def test_there_is_no_publicar_tab_or_tool(owner, monkeypatch, tmp_path):
    _demo_catalog(monkeypatch, tmp_path)
    try:
        html = owner.get(MODULE_URL).text
        assert "toolTab==='publicar'" not in html and "toolTab='publicar'" not in html     # ni pestaña ni atajo a una pestaña
        assert ">Publicar</button>" not in html                                              # el nav sigue siendo Sección|Árbol|Catálogo|Asistente
        assert "toolTab==='catalogo'" in html
        js = (REPO / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
        tool_group_line = next(ln for ln in js.splitlines() if ln.startswith("const TOOL_GROUP"))
        assert "publicar" not in tool_group_line
        # el viejo contrato `?tool=publicar` ya no abre nada
        assert "copilotLayout('core', false, '', '')" in owner.get(f"{MODULE_URL}&tool=publicar").text
    finally:
        main._invalidate_catalog_cache()


def test_catalog_has_two_states_and_the_form_lives_inside_the_catalog_tab(owner, monkeypatch, tmp_path):
    _demo_catalog(monkeypatch, tmp_path)
    try:
        html = owner.get(MODULE_URL).text
        cat = html[html.index("toolTab==='catalogo'"):]
        assert 'id="catalog-publish-state"' in cat and "catalogView === 'publish'" in cat          # estado B (alta)
        assert "catalogView !== 'publish'" in cat                                                  # estado A (explorar)
        assert cat.count('id="dsp-form"') == 1                                                     # UN solo formulario
        assert "Volver al catálogo" in cat and "catalogView = 'browse'" in cat                     # salida natural
        # el dropdown de catálogo queda en el estado A, NO apilado debajo del formulario
        assert cat.index('id="catalog-publish-state"') < cat.index('id="catalog-kind"')
        assert "copilotLayout('core', false, '', '')" in html                                      # por defecto: explorar
    finally:
        main._invalidate_catalog_cache()


def test_deeplink_requests_the_catalog_publication_state(owner, monkeypatch, tmp_path):
    _demo_catalog(monkeypatch, tmp_path)
    try:
        html = owner.get(CATALOG_PUBLISH_URL).text
        assert "copilotLayout('core', false, 'catalogo', '', 'publish')" in html                   # Catálogo + subestado
        js = (REPO / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
        assert 'catalogView: initialCatalogAction === "publish" ? "publish" : "browse"' in js
    finally:
        main._invalidate_catalog_cache()


def test_deeplink_does_not_let_the_browser_pick_the_extractor_mode(owner, monkeypatch, tmp_path):
    _demo_catalog(monkeypatch, tmp_path)
    try:
        html = owner.get(CATALOG_PUBLISH_URL).text
        assert 'name="mode"' not in html and "name='mode'" not in html                             # ningún input de modo
        assert 'value="publish"' not in html                                                       # "publish" no es un campo del formulario
        # un valor desconocido del subestado se ignora (lista blanca)
        assert "'publish')" not in owner.get(f"{MODULE_URL}&tab=catalogo&catalog_action=evaluate").text
    finally:
        main._invalidate_catalog_cache()


def test_plus_icon_is_a_neutral_svg_inside_the_catalog_heading_row(owner, monkeypatch, tmp_path):
    _demo_catalog(monkeypatch, tmp_path)
    try:
        html = owner.get(MODULE_URL).text
        head = html[html.index('for="catalog-kind"'):]
        head = head[: head.index("</select>")]
        assert "Catálogo de datos" in head
        assert 'class="table-action-btn' in head and "<svg" in head and 'aria-label="Publicar en el catálogo"' in head
        assert '@click="catalogView = \'publish\'"' in head
        assert "catalog-add-btn" not in html                                                       # sin la caja dorada anterior
        css = (REPO / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
        assert ".catalog-add-btn" not in css
        assert "var(--gold)" not in css.split(".table-action-btn {")[1].split("}")[0]              # primitivo neutro (sin dorado)
    finally:
        main._invalidate_catalog_cache()


def test_guide_step5_and_plus_land_in_the_same_catalog_state(owner, monkeypatch, tmp_path):
    _demo_catalog(monkeypatch, tmp_path)
    try:
        g = owner.get(f"/catalog/guide/como-publicar-dominios?embed=1&pack={PACK}").text
        urls = [u.replace("&amp;", "&") for u in re.findall(r'href="(/modules/[^"]+)"[^>]*target="_parent"', g)]
        url = f"/modules/{STEM}?pack={PACK}&tab=catalogo&catalog_action=publish"
        assert url in urls                                                                         # el CTA del paso 5 (el otro es "Volver")
        assert not any("tool=assessment" in u or "tool=publicar" in u for u in urls)
        # la URL de la guía abre el estado de alta del Catálogo (la MISMA puerta que el "+")
        assert "copilotLayout('core', false, 'catalogo', '', 'publish')" in owner.get(url).text
    finally:
        main._invalidate_catalog_cache()


def test_publication_state_is_reachable_even_with_empty_catalog(owner, monkeypatch, tmp_path):
    # Con el Catálogo VACÍO el dueño igual ve la pestaña Catálogo (en el módulo con publicación) y puede publicar el PRIMER dominio.
    _demo_catalog(monkeypatch, tmp_path, empty=True)
    try:
        html = owner.get(CATALOG_PUBLISH_URL).text
        assert "toolTab==='catalogo'" in html and 'id="dsp-form"' in html
        assert "copilotLayout('core', false, 'catalogo', '', 'publish')" in html
    finally:
        main._invalidate_catalog_cache()


def test_form_fields_are_the_same_shared_partial_as_the_assessment():
    tpl = REPO / "webapp" / "templates" / "partials"
    shared = (tpl / "datasphere_form_fields.html").read_text(encoding="utf-8")
    pub_tpl = (tpl / "datasphere_publication_tool.html").read_text(encoding="utf-8")
    dsa_tpl = (tpl / "datasphere_assessment_tool.html").read_text(encoding="utf-8")
    for t, prefix in ((pub_tpl, "dsp"), (dsa_tpl, "dsa")):
        assert "import datasphere_form_fields" in t and f'datasphere_form_fields("{prefix}"' in t
        assert "{% macro field(" not in t                                                         # ningún segundo formulario aproximado
    for label in ("URL del tenant", "Token URL (OAuth)", "Client ID", "Client Secret", "Space ID", "Artefacto / Objeto",
                  "Alcance de evidencia", "Nombre de Cliente"):
        assert label in shared


def test_publication_and_assessment_render_the_same_field_set(owner, monkeypatch, tmp_path):
    _demo_catalog(monkeypatch, tmp_path)
    try:
        html = owner.get(CATALOG_PUBLISH_URL).text

        def names(form_id):
            f = html[html.index(f'id="{form_id}"'):]
            f = f[: f.index("</form>")]
            return re.findall(r'name="([a-z_]+)"', f)
        pub_names, dsa_names = names("dsp-form"), names("dsa-form")
        assert pub_names == dsa_names                                                             # mismos campos y mismo orden
        for needed in ("host", "token_url", "datasphere_client_id", "datasphere_client_secret", "space", "datasphere_root_object",
                       "client", "datasphere_features_submitted", "datasphere_features"):
            assert needed in pub_names
    finally:
        main._invalidate_catalog_cache()


def test_publication_inputs_get_the_same_styling_as_the_assessment_inputs():
    css = (REPO / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert '.dsa-panel input:is([type="text"], [type="url"], [type="password"]) { height: 42px;' in css or \
        '.dsa-panel input:is([type="text"], [type="url"], [type="password"]), ' in css
    for t in ("datasphere_publication_tool.html", "datasphere_assessment_tool.html"):
        assert 'class="dsa-panel"' in (REPO / "webapp" / "templates" / "partials" / t).read_text(encoding="utf-8")


# ───────────────────────── seguridad / secretos ─────────────────────────
def test_non_owner_sees_no_publication_tool(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    other = _client("intruso@danwor.com")
    html = other.get(CATALOG_PUBLISH_URL).text
    assert 'id="dsp-form"' not in html and 'id="catalog-publish-state"' not in html
    assert "'publish')" not in html                                  # el subestado no se concede a un no dueño


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
    partial = (REPO / "webapp" / "templates" / "partials" / "datasphere_form_fields.html").read_text(encoding="utf-8")
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
