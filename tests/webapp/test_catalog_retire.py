"""«Retirar del catálogo» (ícono de papelera en los tiles): política de permisos exacta del owner, retiro LÓGICO sin borrar nada, procedencia de publicación y UI.

Política: APP_OWNER retira cualquier tile; un usuario con perfil completo solo el tile cuyo ÚLTIMO publicador es él (dato que el sistema registra desde ahora: antes no existía ninguna procedencia fiable);
cualquier otro caso: sin ícono y el endpoint responde 403. Datos sintéticos bajo ``tmp_path``; sin Datasphere ni Spaces.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import types
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import webapp.main as main
from webapp import access
from webapp.services import auth_service, catalog_governance as gov, datasphere_assessment_service as asd, datasphere_publication_service as pub

OWNER, ANA, BEA, READER = "owner@danwor.com", "ana@danwor.com", "bea@danwor.com", "lector@danwor.com"
ORIGIN = {"Origin": "http://testserver"}
RETIRE = "/catalog/flow/retire"
REPO = Path(__file__).resolve().parents[2]
FLOW = "---\nauto:\n  tipo: flujo\n  dominio: {dom}\n  artefactos: [{art}]\nmanual:\n  cliente: {cli}\n---\n# demo\n"


def _client(email):
    c = TestClient(main.app)
    if email:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


@pytest.fixture()
def world(monkeypatch, tmp_path):
    root = tmp_path / "catalog_local"
    for cli, dom, art in (("Biogenesis", "Gastos", "4AM_CONSOLIDADO_GASTOS"), ("Kensing", "Gastos", "4AM_FNC_GL")):
        d = root / cli / dom
        d.mkdir(parents=True)
        (d / f"flujo_{dom}.md").write_text(FLOW.format(dom=dom, art=art, cli=cli), encoding="utf-8")
    monkeypatch.setattr(config, "BASE_DIR", REPO)                                                 # el catálogo se carga con el servicio real del repo
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(root))
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(access, "APP_OWNER", OWNER)
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", f"{OWNER},{ANA},{BEA}")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", READER)
    main._invalidate_catalog_cache()
    yield types.SimpleNamespace(root=root, ids=sorted(e.entry_id for e in main._catalog_entries()[1]))
    main._invalidate_catalog_cache()


def entry_id(world, cliente):
    return next(e.entry_id for e in main._catalog_entries()[1] if e.cliente == cliente)


def publish_as(world, cliente, actor, *, op="op1"):
    """Registra la procedencia como lo hace la publicación (``record_provenance``): quién fue el último en publicar ese tile."""
    values = asd.FormValues.from_mapping({"host": "https://t.eu10.hcs.cloud.sap", "token_url": "https://t.authentication.eu10.hana.ondemand.com/oauth/token", "client_id": "sb",
                                          "space": "PROD", "root_object": "4AM_CONSOLIDADO_GASTOS" if cliente == "Biogenesis" else "4AM_FNC_GL", "client": cliente, "features": None})
    return pub.record_provenance(values, actor, op)


def tiles(client, url):
    return client.get(url).text


# ═══════════════ qué sabe hoy el sistema: sin procedencia, solo el dueño ═══════════════
def test_without_any_recorded_provenance_only_the_owner_can_retire(world):
    eid = entry_id(world, "Biogenesis")
    assert gov.provenance(eid) is None
    assert gov.can_retire({"sub": OWNER}, eid) and not gov.can_retire({"sub": ANA}, eid)           # nunca se infiere un autor
    assert _client(ANA).post(RETIRE, data={"flow": eid}, headers=ORIGIN).status_code == 403
    assert _client(OWNER).post(RETIRE, data={"flow": eid}, headers=ORIGIN).status_code == 200


def test_publishing_records_who_published_first_and_last(world):
    eid = entry_id(world, "Biogenesis")
    assert publish_as(world, "Biogenesis", ANA, op="o1") == [eid]
    first = gov.provenance(eid)
    assert (first["first_publisher"], first["last_publisher"], first["operation_id"], first["publications"]) == (ANA, ANA, "o1", 1)
    publish_as(world, "Biogenesis", BEA, op="o2")
    again = gov.provenance(eid)
    assert (again["first_publisher"], again["last_publisher"], again["publications"]) == (ANA, BEA, 2)     # el último actualizador es BEA; el autor original se conserva
    assert gov.provenance(entry_id(world, "Kensing")) is None                                               # otro tile: intacto


def test_a_failed_provenance_write_never_breaks_a_publication(world, monkeypatch):
    monkeypatch.setattr(gov, "record_publication", lambda *a, **k: (_ for _ in ()).throw(gov.GovernanceStorageError("x")))
    values = asd.FormValues.from_mapping({"host": "https://t.eu10.hcs.cloud.sap", "token_url": "https://t.authentication.eu10.hana.ondemand.com/oauth/token", "client_id": "sb",
                                          "space": "PROD", "root_object": "4AM_CONSOLIDADO_GASTOS", "client": "Biogenesis", "features": None})
    assert pub.record_provenance(values, ANA, "o") == []                                                    # no lanza; el tile queda sin procedencia (solo el dueño)


# ═══════════════ política exacta ═══════════════
def test_the_policy_matrix(world):
    bio = entry_id(world, "Biogenesis")
    kens = entry_id(world, "Kensing")
    publish_as(world, "Biogenesis", ANA)
    publish_as(world, "Kensing", BEA)
    can = gov.can_retire
    assert can({"sub": OWNER}, bio) and can({"sub": OWNER}, kens)                                           # el dueño: cualquiera
    assert can({"sub": ANA}, bio) and not can({"sub": ANA}, kens)                                           # quien lo publicó: solo el suyo
    assert can({"sub": BEA}, kens) and not can({"sub": BEA}, bio)
    assert not can({"sub": READER}, bio) and not can({"sub": "ajeno@otro.com"}, bio) and not can(None, bio)
    assert not can({"sub": ANA}, "no-existe") and not can({"sub": OWNER}, "../etc/passwd") and not can({"sub": OWNER}, "")


def test_the_last_updater_is_the_basis_of_the_rule(world):
    bio = entry_id(world, "Biogenesis")
    publish_as(world, "Biogenesis", ANA)
    publish_as(world, "Biogenesis", BEA)
    assert gov.can_retire({"sub": BEA}, bio) and not gov.can_retire({"sub": ANA}, bio)                      # el actor MÁS RECIENTE; el autor original ya no puede


def test_a_user_who_loses_the_full_profile_loses_the_right_at_the_next_request(world, monkeypatch):
    bio = entry_id(world, "Biogenesis")
    publish_as(world, "Biogenesis", ANA)
    c = _client(ANA)
    assert c.post(RETIRE, data={"flow": bio, "pack": "datasphere_core"}, headers=ORIGIN).status_code == 200
    gov_dir = world.root / gov.RETIRED_DIR
    for f in gov_dir.glob("*.json"):
        f.unlink()
    main._invalidate_catalog_cache()
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", f"{OWNER},{BEA}")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", ANA)                                                 # pasa a solo lectura
    assert c.post(RETIRE, data={"flow": bio}, headers=ORIGIN).status_code == 403


# ═══════════════ endpoint: 403 / 401 / origen ═══════════════
def test_a_manual_post_without_permission_gets_403_and_changes_nothing(world):
    bio = entry_id(world, "Biogenesis")
    publish_as(world, "Biogenesis", ANA)
    for who in (BEA, READER, "ajeno@otro.com"):
        assert _client(who).post(RETIRE, data={"flow": bio}, headers=ORIGIN).status_code == 403, who
    assert not (world.root / gov.RETIRED_DIR).exists() and bio in {e.entry_id for e in main._catalog_entries()[1]}


def test_anonymous_and_cross_origin_requests_are_rejected(world):
    bio = entry_id(world, "Biogenesis")
    assert _client(None).post(RETIRE, data={"flow": bio}, headers=ORIGIN).status_code == 401
    owner = _client(OWNER)
    assert owner.post(RETIRE, data={"flow": bio}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert owner.post(RETIRE, data={"flow": bio}).status_code == 403
    assert not (world.root / gov.RETIRED_DIR).exists()


def test_the_owner_gets_404_for_a_tile_that_no_longer_exists_and_others_get_403(world):
    assert _client(OWNER).post(RETIRE, data={"flow": "no-existe"}, headers=ORIGIN).status_code == 404
    assert _client(ANA).post(RETIRE, data={"flow": "no-existe"}, headers=ORIGIN).status_code == 403          # no revela si existe


# ═══════════════ retiro LÓGICO: no se borra nada ═══════════════
def test_retiring_hides_the_tile_everywhere_but_deletes_no_file_and_is_reversible(world):
    bio = entry_id(world, "Biogenesis")
    files_before = sorted(p.relative_to(world.root).as_posix() for p in world.root.rglob("*") if p.is_file())
    out = _client(OWNER).post(RETIRE, data={"flow": bio, "pack": "datasphere_core"}, headers=ORIGIN)
    assert out.status_code == 200 and 'data-catalog-retired="ok"' in out.text and "Retirado del Catálogo" in out.text      # feedback visible del resultado
    assert bio not in {e.entry_id for e in main._catalog_entries()[1]}                                                   # fuera del catálogo para todos los consumidores
    from tools.catalog import catalog_service as cs
    assert bio in {e.entry_id for e in cs.load_catalog(str(world.root))}                                                  # el cargador del piloto/scoring legacy NO se toca: sigue viendo todo el disco
    assert bio not in {e.entry_id for e in gov.visible_entries(cs.load_catalog(str(world.root)), world.root)}
    files_after = sorted(p.relative_to(world.root).as_posix() for p in world.root.rglob("*") if p.is_file())
    assert set(files_before) <= set(files_after)                                                                          # NINGÚN archivo del catálogo se borró
    assert set(files_after) - set(files_before) == {f"{gov.RETIRED_DIR}/{bio}.json"}                                      # solo se agregó el tombstone
    record = json.loads((world.root / gov.RETIRED_DIR / f"{bio}.json").read_text(encoding="utf-8"))
    assert record["retired_by"] == OWNER and record["entry_id"] == bio
    (world.root / gov.RETIRED_DIR / f"{bio}.json").unlink()                                                               # reversible: quitar el tombstone lo restaura
    main._invalidate_catalog_cache()
    assert bio in {e.entry_id for e in main._catalog_entries()[1]}


def test_retiring_twice_is_idempotent_and_keeps_the_first_record(world):
    bio = entry_id(world, "Biogenesis")
    owner = _client(OWNER)
    owner.post(RETIRE, data={"flow": bio}, headers=ORIGIN)
    first = (world.root / gov.RETIRED_DIR / f"{bio}.json").read_text(encoding="utf-8")
    assert owner.post(RETIRE, data={"flow": bio}, headers=ORIGIN).status_code == 404                                       # ya no está en el catálogo
    assert (world.root / gov.RETIRED_DIR / f"{bio}.json").read_text(encoding="utf-8") == first


def test_republishing_a_retired_tile_lifts_the_retirement(world):
    bio = entry_id(world, "Biogenesis")
    _client(OWNER).post(RETIRE, data={"flow": bio}, headers=ORIGIN)
    assert bio not in {e.entry_id for e in main._catalog_entries()[1]}
    publish_as(world, "Biogenesis", ANA)                                                                                   # la publicación es la intención contraria
    main._invalidate_catalog_cache()
    assert bio in {e.entry_id for e in main._catalog_entries()[1]} and gov.provenance(bio)["last_publisher"] == ANA
    assert not (world.root / gov.RETIRED_DIR / f"{bio}.json").exists()


def test_a_storage_failure_reports_the_error_and_keeps_the_tile(world, monkeypatch):
    bio = entry_id(world, "Biogenesis")
    monkeypatch.setattr(gov, "retire", lambda *a, **k: (_ for _ in ()).throw(gov.GovernanceStorageError("x")))
    out = _client(OWNER).post(RETIRE, data={"flow": bio}, headers=ORIGIN)
    assert 'data-catalog-retired="error"' in out.text and "sigue publicado" in out.text
    assert bio in {e.entry_id for e in main._catalog_entries()[1]}


def test_the_governed_assessment_and_the_manifest_are_left_untouched(world):
    bio = entry_id(world, "Biogenesis")
    for rel in (".lineage_runs/Biogenesis/PROD/analytic-model/4AM/lineage-manifest.json", ".csn_cache/Biogenesis/views/V.json"):
        f = world.root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text('{"x": 1}', encoding="utf-8")
    before = {p: p.read_bytes() for p in world.root.rglob("*") if p.is_file()}
    _client(OWNER).post(RETIRE, data={"flow": bio}, headers=ORIGIN)
    after = {p: p.read_bytes() for p in world.root.rglob("*") if p.is_file()}
    assert all(after.get(p) == data for p, data in before.items())                                                         # pool, manifiesto y flujo: byte a byte iguales


# ═══════════════ UI: el ícono solo cuando corresponde ═══════════════
def _client_tiles(client):
    return client.get("/catalog/client?cliente=Biogenesis&pack=datasphere_core").text


def test_the_icon_appears_only_when_the_current_user_can_retire_that_tile(world):
    publish_as(world, "Biogenesis", ANA)
    owner_html, ana_html, bea_html, anon = _client_tiles(_client(OWNER)), _client_tiles(_client(ANA)), _client_tiles(_client(BEA)), _client(None)
    assert "data-catalog-retire" in owner_html and "data-catalog-retire" in ana_html                  # dueño y último publicador
    assert "data-catalog-retire" not in bea_html                                                      # otro consultor: sin ícono
    assert anon.get("/catalog/client?cliente=Biogenesis").status_code == 401
    kens = lambda c: c.get("/catalog/client?cliente=Kensing&pack=datasphere_core").text
    assert "data-catalog-retire" in kens(_client(OWNER)) and "data-catalog-retire" not in kens(_client(ANA))      # el tile de otro: solo el dueño
    assert "data-catalog-retire" not in _client_tiles(_client(READER))


def test_the_icon_follows_the_hub_pattern_and_has_no_text_label(world):
    html = _client_tiles(_client(OWNER))
    start = html.index("data-catalog-retire")
    button = html[html.rindex("<button", 0, start): html.index("</button>", start) + 9]
    assert "table-action-btn" in button and "table-action-btn--danger" in button and "chart-tooltip-anchor" in button     # MISMO primitivo que la papelera de Capital Humano
    assert 'aria-label="Retirar del catálogo"' in button and '<span class="chart-tooltip" aria-hidden="true"><p>Retirar del catálogo</p></span>' in button
    assert "M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" in button                                                           # la misma papelera SVG
    visible_text = main.re.sub(r"<[^>]+>", "", button.replace(button[button.index('<span class="chart-tooltip"'):], ""))
    assert visible_text.strip() == ""                                                                                    # sin texto «Eliminar» visible: solo ícono (+ tooltip)
    head = html[html.rindex('<p class="catalog-card__head">', 0, start): html.index("</p>", start)]
    assert head.rindex("catalog-card__verdict") < head.index("catalog-card__action")                                    # va DESPUÉS del texto del tile (borde derecho por CSS)
    css = (REPO / "webapp" / "static" / "tools.css").read_text(encoding="utf-8")
    assert ".catalog-card__head .catalog-card__action { margin-left: auto" in css


# ═══════════════ casilla OPCIONAL: eliminar también sus archivos ═══════════════
def _dir_of(world, cliente):
    e = next(e for e in main._catalog_entries()[1] if e.cliente == cliente)
    return world.root / cliente / Path(e.path).parent.name


def _with_views(world, cliente):
    d = _dir_of(world, cliente)
    (d / "vistas").mkdir(exist_ok=True)
    (d / "vistas" / "V1.md").write_text("vista", encoding="utf-8")
    return d


def _snapshot(root, skip=()):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file() and p.relative_to(root).parts[0] not in skip}


def test_without_the_checkbox_no_file_is_deleted_even_if_the_param_is_empty_or_false(world):
    bio = entry_id(world, "Biogenesis")
    d = _with_views(world, "Biogenesis")
    out = _client(OWNER).post(RETIRE, data={"flow": bio, "delete_files": ""}, headers=ORIGIN)
    assert 'data-catalog-files="kept"' in out.text and d.is_dir() and (d / "vistas" / "V1.md").is_file()


def test_with_the_checkbox_the_tiles_own_folder_is_deleted_and_nothing_else(world):
    bio, kens = entry_id(world, "Biogenesis"), entry_id(world, "Kensing")
    d = _with_views(world, "Biogenesis")
    pool = world.root / ".csn_cache" / "Biogenesis" / "views" / "V.json"
    pool.parent.mkdir(parents=True)
    pool.write_text("{}", encoding="utf-8")
    other_before = _snapshot(world.root, skip=("Biogenesis", gov.RETIRED_DIR))
    out = _client(OWNER).post(RETIRE, data={"flow": bio, "delete_files": "true"}, headers=ORIGIN)
    assert out.status_code == 200 and 'data-catalog-files="deleted"' in out.text and "archivos fueron eliminados" in out.text
    assert not d.exists()                                                                                       # flujo + vistas, fuera
    assert _snapshot(world.root, skip=("Biogenesis", gov.RETIRED_DIR)) == other_before                          # pool, manifiestos y los demás tiles: byte a byte iguales
    assert kens not in gov.retired_ids(world.root) and (world.root / "Kensing").is_dir()
    record = json.loads((world.root / gov.RETIRED_DIR / f"{bio}.json").read_text(encoding="utf-8"))
    assert record["files_deleted"]["count"] == 2 and record["files_deleted"]["dir"].startswith("Biogenesis/")  # constancia durable; el tombstone se conserva (una regeneración no resucita el tile)
    assert bio not in {e.entry_id for e in main._catalog_entries()[1]}


def test_the_checkbox_does_not_widen_authorization(world):
    bio, kens = entry_id(world, "Biogenesis"), entry_id(world, "Kensing")
    publish_as(world, "Biogenesis", ANA)
    d_kens = _with_views(world, "Kensing")
    before = _snapshot(world.root)
    for who, flow in ((BEA, bio), (ANA, kens), (READER, bio), (None, bio)):
        out = _client(who).post(RETIRE, data={"flow": flow, "delete_files": "true"}, headers=ORIGIN)
        assert out.status_code in (401, 403)
    assert _snapshot(world.root) == before and d_kens.is_dir()                                                  # nada cambió
    ok = _client(ANA).post(RETIRE, data={"flow": bio, "delete_files": "true"}, headers=ORIGIN)                  # el último publicador sí puede, con la misma política
    assert 'data-catalog-files="deleted"' in ok.text and not _dir_of_missing(world, "Biogenesis")


def _dir_of_missing(world, cliente):
    return (world.root / cliente).exists() and any((world.root / cliente).iterdir())


def test_remote_failure_keeps_every_file_and_reports_it(world, monkeypatch):
    bio = entry_id(world, "Biogenesis")
    d = _with_views(world, "Biogenesis")
    monkeypatch.setattr(gov.remote_store, "write_enabled", lambda: True)
    monkeypatch.setattr(gov.remote_store, "delete_path", lambda p: False)                                       # Spaces no confirma el borrado
    before = _snapshot(world.root, skip=(gov.RETIRED_DIR,))
    out = _client(OWNER).post(RETIRE, data={"flow": bio, "delete_files": "true"}, headers=ORIGIN)
    assert 'data-catalog-retired="ok"' in out.text and 'data-catalog-files="failed"' in out.text and "se conservan" in out.text
    assert _snapshot(world.root, skip=(gov.RETIRED_DIR,)) == before and d.is_dir()                              # fallo cerrado: el disco queda intacto (retirado, con sus archivos)


def test_remote_objects_are_deleted_before_the_disk(world, monkeypatch):
    bio = entry_id(world, "Biogenesis")
    d = _with_views(world, "Biogenesis")
    seen = []
    monkeypatch.setattr(gov.remote_store, "write_enabled", lambda: True)
    monkeypatch.setattr(gov.remote_store, "delete_path", lambda p: seen.append((Path(p).name, Path(p).exists())) or True)
    _client(OWNER).post(RETIRE, data={"flow": bio, "delete_files": "true"}, headers=ORIGIN)
    assert sorted(n for n, _ in seen) == ["V1.md", f"flujo_{d.name}.md"] and all(existed for _, existed in seen)  # primero Spaces (el archivo aún existe), después el disco
    assert not d.exists()


def test_an_unsafe_folder_is_never_deleted(world):
    bio = entry_id(world, "Biogenesis")
    d = _dir_of(world, "Biogenesis")
    (d / "flujo_otro.md").write_text(FLOW.format(dom="X", art="4AM_X", cli="Biogenesis"), encoding="utf-8")    # otro flujo dentro de la misma carpeta: no es «propia» del tile
    main._invalidate_catalog_cache()
    out = _client(OWNER).post(RETIRE, data={"flow": bio, "delete_files": "true"}, headers=ORIGIN)
    assert 'data-catalog-files="failed"' in out.text and d.is_dir() and (d / "flujo_otro.md").is_file()
    entry = types.SimpleNamespace(entry_id="x", path=str(world.root / "Biogenesis" / "flujo_fuera.md"))
    assert gov.entry_directory(entry) is None                                                                  # inexistente / fuera de profundidad
    assert gov.entry_directory(types.SimpleNamespace(entry_id="x", path=str(world.root.parent))) is None


def test_the_dialog_offers_an_unchecked_optional_checkbox_and_posts_delete_files_only_when_checked():
    tpl = (REPO / "webapp" / "templates" / "partials" / "catalog_flows.html").read_text(encoding="utf-8")
    assert "data-dw-confirm-check-label=\"Eliminar también sus archivos\"" in tpl and "data-dw-confirm-check-hint=" in tpl
    base = (REPO / "webapp" / "templates" / "base.html").read_text(encoding="utf-8")
    assert "data-dw-confirm-check-wrap hidden" in base and 'type="checkbox" data-dw-confirm-check>' in base and 'data-dw-confirm-check name' not in base      # sin `name`, oculta por defecto
    js = (REPO / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    assert "box.checked = false" in js and 'vals.delete_files = "true"' in js and "else delete vals.delete_files" in js
    assert "function dwConfirm(opts) {\n  return dwConfirmChoice(opts).then(r => r.ok);" in js      # el diálogo compartido sigue devolviendo un booleano


@pytest.mark.skipif(shutil.which("node") is None, reason="node no disponible en este entorno")
def test_app_js_still_parses():
    out = subprocess.run(["node", "--check", str(REPO / "webapp" / "static" / "app.js")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_the_confirmation_uses_the_shared_dialog_and_never_fires_the_tile_detail():
    js = (REPO / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
    block = js[js.index("data-catalog-retire"): js.index("function _dwConfirmOptsFrom")]
    assert "dwConfirmChoice(" in block and "}, true);" in block and "stopPropagation" in block                                # confirmación compartida + captura (la tarjeta no abre el detalle)
    assert "keyup" in block
    tpl = (REPO / "webapp" / "templates" / "partials" / "catalog_flows.html").read_text(encoding="utf-8")
    assert 'hx-trigger="confirmed"' in tpl and 'hx-target="closest li"' in tpl and "data-dw-confirm-title" in tpl


def test_route_surface_declares_the_new_endpoint():
    surface = json.loads((REPO / "tests" / "route_surface.json").read_text(encoding="utf-8"))
    assert "POST /catalog/flow/retire" in surface
