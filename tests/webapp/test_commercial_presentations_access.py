"""
test_commercial_presentations_access.py — Presentaciones V1: AUTORIZACIÓN por requests directos (nunca por visibilidad de botones).

Contrato ratificado:
  * R1 (ver/presentar por URL): sesión + `auth_service.is_authorized` (ALLOWED_USERS ∪ ACADEMY_USERS). NO exige Comercial.
  * Catálogo / detalle / copia / edición: Comercial.
  * COPIAR no requiere nada más que Comercial (cualquier presentación). EDITAR: responsable de la instancia o `CONTENT_OWNER` (siempre dentro de Comercial). Los seeds no se editan nunca.

T1 sin sesión · T2 sesión no admitida · T3 usuario Hub sin Comercial abre por URL · T4 ese usuario no entra al catálogo/copia/edición · T5 Comercial opera ·
T6 Comercial no responsable no edita (salvo CONTENT_OWNER) · T7 seeds inmutables · T8 origen ajeno. Más los gates de A copiando la presentación de B.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from webapp import access
from webapp.main import app
from webapp.services import auth_service
from webapp.services import commercial_presentations as cp

SEED_ID = "vanture-bdc"
ORIGIN = {"Origin": "http://testserver"}
SEED_YAML = Path(cp.__file__).parent / "commercial_presentations_data" / "vanture-bdc.yaml"


class World:
    """Usuarios y almacenamiento aislado de UN test."""

    def __init__(self, directory: Path):
        self.directory = directory
        self._clients: dict[str, TestClient] = {}

    def client(self, email: str | None) -> TestClient:
        if email is None:
            return TestClient(app)
        if email not in self._clients:
            session = auth_service.create_session({"email": email, "name": email.split("@")[0].title(), "first_name": "T", "family_name": "U"})
            c = TestClient(app)
            c.cookies.set(auth_service.COOKIE_NAME, session)
            self._clients[email] = c
        return self._clients[email]

    def files(self) -> dict[str, bytes]:
        return {p.name: p.read_bytes() for p in sorted(self.directory.glob("*.json"))} if self.directory.exists() else {}


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, "_dir", lambda: tmp_path / "presentations")
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "com-a,com-b,owner-c,hub-h")
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "academy-r@danwor.com")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "com-a,com-b,owner-c")
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "owner-c")
    monkeypatch.setattr(access, "APP_OWNER", "app-owner")
    return World(tmp_path / "presentations")


A, B, OWNER_C = "com-a@danwor.com", "com-b@danwor.com", "owner-c@danwor.com"
HUB_ONLY, ACADEMY, INTRUDER, APP_OWNER = "hub-h@danwor.com", "academy-r@danwor.com", "intruder@danwor.com", "app-owner@danwor.com"


def _copy(world, email, source=SEED_ID, title="Mi copia"):
    r = world.client(email).post(f"/comercial/presentaciones/{source}/copiar", json={"title": title}, headers=ORIGIN)
    assert r.status_code == 200, r.text
    return r.json()["presentation_id"]


def _delete(world, email, pid, headers=ORIGIN):
    return world.client(email).post(f"/comercial/presentaciones/{pid}/eliminar", json={}, headers=headers)


def _save(world, email, pid, fields, revision):
    return world.client(email).post(f"/comercial/presentaciones/{pid}", json={"revision": revision, "fields": fields}, headers=ORIGIN)


def _all_routes(pid):
    return {
        "R1": ("GET", f"/presentaciones/{pid}"),
        "R2": ("GET", "/comercial/presentaciones"),
        "R3": ("GET", f"/comercial/presentaciones/{pid}"),
        "R5": ("GET", f"/comercial/presentaciones/{pid}/editar"),
        "R4": ("POST", f"/comercial/presentaciones/{pid}/copiar"),
        "R6": ("POST", f"/comercial/presentaciones/{pid}"),
        "R7": ("POST", f"/comercial/presentaciones/{pid}/eliminar"),
        "R8": ("GET", f"/presentaciones/{pid}/logo"),
    }


def _hit(client, method, path):
    if method == "GET":
        return client.get(path, follow_redirects=False)
    return client.post(path, json={"title": "x", "revision": 1, "fields": {"hero_lead": "x"}}, headers=ORIGIN, follow_redirects=False)


# ── T1 — sin sesión ────────────────────────────────────────────────────────────────────────────────────────────────────

def test_t1_without_a_session_every_route_is_denied_and_leaks_nothing(world):
    pid = _copy(world, A)
    before = world.files()
    for name, (method, path) in _all_routes(pid).items():
        r = _hit(world.client(None), method, path)
        assert r.status_code == (303 if method == "GET" else 401), (name, r.status_code)
        assert r.text in ("", "{\"detail\":\"Sin sesión\"}") or "Sin sesión" in r.text, name      # sin contenido de la presentación
        assert "Vanture" not in r.text and "datos" not in r.text, name
    assert world.files() == before


# ── T2 — con sesión pero no admitido por ALLOWED_USERS / ACADEMY_USERS ──────────────────────────────────────────────────

def test_t2_a_session_that_is_not_admitted_by_allowed_users_is_denied_everywhere(world):
    pid = _copy(world, A)
    before = world.files()
    intruder = world.client(INTRUDER)                       # sesión válida fabricada: el login real jamás la crearía
    assert not auth_service.is_authorized(INTRUDER)
    for name, (method, path) in _all_routes(pid).items():
        r = _hit(intruder, method, path)
        assert r.status_code == 403, (name, r.status_code)
    assert world.files() == before


# ── T3 / D3 — un usuario del Hub SIN Comercial abre y presenta por URL ────────────────────────────────────────────────────

@pytest.mark.parametrize("email", [HUB_ONLY, ACADEMY])
def test_t3_a_hub_user_without_commercial_opens_the_seed_and_a_copy_by_url(world, email):
    pid = _copy(world, A)
    assert not auth_service.is_commercial_user(email)
    for target in (SEED_ID, pid):
        r = world.client(email).get(f"/presentaciones/{target}")
        assert r.status_code == 200, (email, target)
        assert "La empresa autónoma comienza con los" in r.text
        assert r.headers["cache-control"] == "private, no-store"
        assert r.headers["x-content-type-options"] == "nosniff"


def test_t3_the_academy_tier_is_admitted_by_the_same_hub_contract(world):
    assert auth_service.user_tier(ACADEMY) == "academy"
    assert world.client(ACADEMY).get(f"/presentaciones/{SEED_ID}").status_code == 200


# ── T4 — ese mismo usuario NO entra al catálogo, ni copia, ni edita ───────────────────────────────────────────────────────

def test_t4_the_hub_only_user_cannot_enter_the_catalog_copy_edit_or_administer(world):
    pid = _copy(world, A)
    before = world.files()
    c = world.client(HUB_ONLY)
    for name in ("R2", "R3", "R5"):
        method, path = _all_routes(pid)[name]
        assert _hit(c, method, path).status_code == 403, name
    assert c.post(f"/comercial/presentaciones/{pid}/copiar", json={"title": "robada"}, headers=ORIGIN).status_code == 403
    assert c.post(f"/comercial/presentaciones/{SEED_ID}/copiar", json={"title": "robada"}, headers=ORIGIN).status_code == 403
    assert _save(world, HUB_ONLY, pid, {"hero_lead": "hackeado"}, 1).status_code == 403
    assert world.files() == before                           # NO se creó ni se tocó nada


# ── T5 — Comercial opera ──────────────────────────────────────────────────────────────────────────────────────────────

def test_t5_a_commercial_user_browses_the_catalog_and_the_detail(world):
    r = world.client(A).get("/comercial/presentaciones")
    assert r.status_code == 200
    assert "Vanture · La empresa autónoma comienza con los datos" in r.text
    assert "Institucional" in r.text
    detail = world.client(A).get(f"/comercial/presentaciones/{SEED_ID}")
    assert detail.status_code == 200
    assert f'src="/presentaciones/{SEED_ID}?embed=1"' in detail.text
    assert 'href="/comercial/presentaciones" class="module-link is-active"' in detail.text


def test_t5_the_catalog_lists_a_copy_with_its_owner_and_its_origin(world):
    pid = _copy(world, A, title="Para Acme")
    page = world.client(B).get("/comercial/presentaciones").text
    assert "Para Acme" in page and pid in page
    assert "Copia de «Vanture · La empresa autónoma comienza con los datos»" in page


# ── D1 — gates: A copia la presentación de B ───────────────────────────────────────────────────────────────────────────────

def test_a_copies_bs_presentation_owns_the_copy_edits_it_and_cannot_edit_bs_original(world):
    b_id = _copy(world, B, title="De B")
    assert _save(world, B, b_id, {"hero_lead": "Texto de B"}, 1).status_code == 200            # B edita lo suyo → revisión 2
    b_file_before = world.files()[f"{b_id}.json"]

    a_id = _copy(world, A, source=b_id, title="Copia de A sobre B")                           # A copia la de B: permitido, sin permisos extra
    copy_json = json.loads(world.files()[f"{a_id}.json"])
    assert copy_json["owner_id"] == A and copy_json["created_by_id"] == A                      # la copia pertenece a A
    assert copy_json["copied_from"]["presentation_id"] == b_id and copy_json["copied_from"]["revision"] == 2
    assert copy_json["content"]["hero_lead"] == "Texto de B"                                   # snapshot del contenido
    assert copy_json["revision"] == 1 and copy_json["presentation_id"] != b_id

    assert _save(world, A, a_id, {"hero_lead": "Texto de A"}, 1).status_code == 200            # A edita SU copia
    assert world.client(A).get(f"/comercial/presentaciones/{a_id}/editar").status_code == 200

    assert world.client(A).get(f"/comercial/presentaciones/{b_id}/editar").status_code == 403  # A NO edita el original de B
    assert _save(world, A, b_id, {"hero_lead": "pisado por A"}, 2).status_code == 403
    assert world.files()[f"{b_id}.json"] == b_file_before                                      # B conserva contenido Y revisión
    assert json.loads(world.files()[f"{b_id}.json"])["revision"] == 2
    assert json.loads(world.files()[f"{a_id}.json"])["content"]["hero_lead"] == "Texto de A"


def test_content_owner_inside_commercial_edits_any_instance_regardless_of_the_creator(world):
    a_id, b_id = _copy(world, A), _copy(world, B)
    assert _save(world, OWNER_C, a_id, {"hero_lead": "editado por el content owner"}, 1).status_code == 200
    assert _save(world, OWNER_C, b_id, {"hero_lead": "editado por el content owner"}, 1).status_code == 200
    assert world.client(OWNER_C).get(f"/comercial/presentaciones/{a_id}/editar").status_code == 200
    saved = json.loads(world.files()[f"{a_id}.json"])
    assert saved["owner_id"] == A and saved["created_by_id"] == A                              # editar no transfiere ownership ni borra la procedencia
    assert saved["updated_by_id"] == OWNER_C and saved["revision"] == 2


def test_content_owner_without_commercial_cannot_edit(world, monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "owner-c,hub-h")                        # hub-h es CONTENT_OWNER pero NO Comercial
    a_id = _copy(world, A)
    assert _save(world, HUB_ONLY, a_id, {"hero_lead": "x"}, 1).status_code == 403              # la excepción administrativa vive DENTRO de Comercial
    assert world.client(HUB_ONLY).get(f"/comercial/presentaciones/{a_id}/editar").status_code == 403


def test_app_owner_administers_the_workspace_but_is_not_a_content_editor_by_being_the_administrator(world):
    a_id = _copy(world, A)
    c_id = _copy(world, APP_OWNER, source=a_id)                                                # entra a Comercial (superusuario del workspace) y copia
    assert _save(world, APP_OWNER, c_id, {"hero_lead": "lo mío"}, 1).status_code == 200        # edita lo suyo (es el responsable)
    assert world.client(APP_OWNER).get(f"/comercial/presentaciones/{a_id}/editar").status_code == 403      # contenido ajeno: NO por ser administrador (mismo contrato que Estrategia: sin bypass de APP_OWNER)
    assert _save(world, APP_OWNER, a_id, {"hero_lead": "ajeno"}, 1).status_code == 403
    assert _delete(world, APP_OWNER, a_id).status_code == 200                                  # eliminar SÍ: administración del workspace (commercial_access: APP_OWNER es su superusuario)
    assert _delete(world, APP_OWNER, SEED_ID).status_code == 403                               # …pero la semilla institucional nunca


def test_an_app_owner_who_is_explicitly_a_content_owner_edits_foreign_content_through_that_identity(world, monkeypatch):
    monkeypatch.setattr(auth_service, "CONTENT_OWNER", "owner-c,app-owner")                    # la capacidad se declara en CONTENT_OWNER, no se deduce de APP_OWNER
    a_id = _copy(world, A)
    assert _save(world, APP_OWNER, a_id, {"hero_lead": "editor de contenido"}, 1).status_code == 200


def test_copying_does_not_imply_editing_and_a_commercial_user_cannot_edit_the_copy_of_someone_else(world):
    b_id = _copy(world, B)
    assert _save(world, A, b_id, {"hero_lead": "x"}, 1).status_code == 403
    assert world.client(A).get(f"/comercial/presentaciones/{b_id}/editar").status_code == 403


# ── T7 — seeds inmutables por UI/API ──────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("email", [A, OWNER_C, APP_OWNER])
def test_t7_the_seed_is_immutable_for_everyone_including_the_content_owner(world, email):
    seed_bytes = SEED_YAML.read_bytes()
    render_before = world.client(A).get(f"/presentaciones/{SEED_ID}").text
    assert _save(world, email, SEED_ID, {"hero_lead": "pisado"}, 1).status_code == 403
    assert world.client(email).get(f"/comercial/presentaciones/{SEED_ID}/editar").status_code == 403
    assert SEED_YAML.read_bytes() == seed_bytes
    assert world.client(A).get(f"/presentaciones/{SEED_ID}").text == render_before
    assert world.files() == {}                                                                 # ni siquiera apareció un archivo de runtime


def test_t7_the_router_exposes_exactly_the_ten_contract_routes_and_none_to_administer_templates_or_seeds():
    from webapp.routers import commercial_presentations as router_module
    exposed = {(m, r.path) for r in router_module.router.routes for m in r.methods if m != "HEAD"}
    assert exposed == {
        ("GET", "/presentaciones/{presentation_id}"),
        ("GET", "/comercial/presentaciones"),
        ("GET", "/comercial/presentaciones/{presentation_id}"),
        ("GET", "/comercial/presentaciones/{presentation_id}/editar"),
        ("POST", "/comercial/presentaciones/{presentation_id}/copiar"),
        ("POST", "/comercial/presentaciones/{presentation_id}"),
        ("POST", "/comercial/presentaciones/{presentation_id}/eliminar"),
        ("GET", "/presentaciones/{presentation_id}/logo"),
        ("POST", "/comercial/presentaciones/{presentation_id}/qr"),
        ("GET", "/presentaciones/{presentation_id}/qr"),
    }
    assert not any(m in {"PUT", "PATCH", "DELETE"} for m, _ in exposed)


# ── T8 y capas de enforcement ───────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {}], ids=["foreign-origin", "no-origin-no-referer"])
def test_t8_writes_without_a_verified_same_origin_are_rejected(world, headers):
    pid = _copy(world, A)
    before = world.files()
    c = world.client(A)
    assert c.post(f"/comercial/presentaciones/{pid}", json={"revision": 1, "fields": {"hero_lead": "x"}}, headers=headers).status_code == 403
    assert c.post(f"/comercial/presentaciones/{pid}/copiar", json={"title": "x"}, headers=headers).status_code == 403
    assert world.files() == before


def test_unknown_ids_are_404_for_commercial_but_403_for_non_commercial_so_existence_is_not_a_hub_wide_oracle(world):
    ghost = "0b9f7e5a-1c2d-4e3f-8a4b-5c6d7e8f9a0b"
    assert world.client(A).get(f"/comercial/presentaciones/{ghost}").status_code == 404
    assert world.client(A).get(f"/presentaciones/{ghost}").status_code == 404
    assert _save(world, A, ghost, {"hero_lead": "x"}, 1).status_code == 404
    assert world.client(HUB_ONLY).get(f"/comercial/presentaciones/{ghost}").status_code == 403
    assert _save(world, HUB_ONLY, ghost, {"hero_lead": "x"}, 1).status_code == 403
    for bad in ("..%2Fsecret", "a%2Fb", "x" * 80, "NOT-A-UUID"):
        assert world.client(A).get(f"/presentaciones/{bad}").status_code == 404


def test_the_copy_quota_is_enforced(world, monkeypatch):
    monkeypatch.setattr(cp, "MAX_INSTANCES", 1)
    _copy(world, A)
    r = world.client(A).post(f"/comercial/presentaciones/{SEED_ID}/copiar", json={"title": "otra"}, headers=ORIGIN)
    assert r.status_code == 409 and r.json()["state"] == "quota"
    assert len(world.files()) == 1


# ── Validación del payload ───────────────────────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fields", [
    {"no_existe": "x"}, {"hero_lead": 7}, {"hero_lead": "   "}, {"hero_lead": "x" * 401}, {"hero_lead": "a\nb"}, {"hero_lead": "a b"},
    {"hero_lead": "a‮b"}, {}, "texto", None, {"hero_lead": "ok", "no_existe": "x"},
])
def test_invalid_payloads_are_rejected_atomically_with_422(world, fields):
    pid = _copy(world, A)
    before = world.files()
    r = _save(world, A, pid, fields, 1)
    assert r.status_code == 422, r.text
    assert world.files() == before
    assert "no_existe" not in r.text                                                           # el nombre recibido no se refleja


def test_malformed_requests_are_400(world):
    pid = _copy(world, A)
    c = world.client(A)
    url = f"/comercial/presentaciones/{pid}"
    assert c.post(url, content=b"{no json", headers={**ORIGIN, "Content-Type": "application/json"}).status_code == 400
    assert c.post(url, json=[1, 2], headers=ORIGIN).status_code == 400
    assert c.post(url, json={"fields": {"hero_lead": "x"}}, headers=ORIGIN).status_code == 400
    assert c.post(url, json={"revision": True, "fields": {"hero_lead": "x"}}, headers=ORIGIN).status_code == 400
    assert c.post(url, json={"revision": "1", "fields": {"hero_lead": "x"}}, headers=ORIGIN).status_code == 400


def test_a_stale_revision_is_409_and_writes_nothing(world):
    pid = _copy(world, A)
    assert _save(world, A, pid, {"hero_lead": "uno"}, 1).status_code == 200
    before = world.files()
    r = _save(world, A, pid, {"hero_lead": "dos"}, 1)
    assert r.status_code == 409 and r.json()["state"] == "stale" and r.json()["current_revision"] == 2
    assert world.files() == before


def test_copy_titles_are_validated(world):
    for bad in ("", "   ", "x" * 121, "a\nb", None, 5):
        r = world.client(A).post(f"/comercial/presentaciones/{SEED_ID}/copiar", json={"title": bad}, headers=ORIGIN)
        assert r.status_code == 422, bad
    assert world.files() == {}


# ── Registro de módulo y telemetría ──────────────────────────────────────────────────────────────────────────────────────

def test_the_module_is_registered_in_the_commercial_workspace_and_the_nav_links_it(world):
    from webapp.services import module_registry
    entry = module_registry.resolve("CM03")
    assert entry and entry.workspace == "commercial" and entry.area == "presentations" and entry.visible_label == "Presentaciones"
    assert module_registry.resolve("presentaciones") is entry
    nav = world.client(A).get("/comercial/presentaciones").text
    assert 'href="/comercial/presentaciones" class="module-link is-active"' in nav


# ── R7 — eliminar: el responsable o APP_OWNER; nunca la semilla ─────────────────────────────────────────────────────────────────

def test_the_owner_deletes_their_own_copy_and_the_shared_link_stops_working(world):
    pid = _copy(world, A)
    assert (world.directory / f"{pid}.json").exists()
    r = _delete(world, A, pid)
    assert r.status_code == 200 and r.json() == {"state": "ok", "url": "/comercial/presentaciones"}
    assert not (world.directory / f"{pid}.json").exists()
    assert world.client(HUB_ONLY).get(f"/presentaciones/{pid}").status_code == 404           # el enlace compartido deja de resolver
    assert pid not in world.client(A).get("/comercial/presentaciones").text
    assert _delete(world, A, pid).status_code == 404                                          # idempotencia visible: ya no existe


def test_a_commercial_user_cannot_delete_the_copy_of_someone_else_and_nothing_is_written(world):
    b_id = _copy(world, B)
    before = world.files()
    assert _delete(world, A, b_id).status_code == 403
    assert world.files() == before


def test_content_owner_edits_but_does_not_delete_what_is_not_theirs(world):
    a_id = _copy(world, A)
    assert world.client(OWNER_C).get(f"/comercial/presentaciones/{a_id}/editar").status_code == 200
    assert _delete(world, OWNER_C, a_id).status_code == 403
    assert (world.directory / f"{a_id}.json").exists()


def test_the_institutional_seed_is_never_deleted_not_even_by_the_app_owner(world):
    for email in (A, OWNER_C, APP_OWNER):
        assert _delete(world, email, SEED_ID).status_code == 403
    assert world.files() == {} and SEED_YAML.exists()


def test_a_hub_user_without_commercial_cannot_delete_even_by_direct_request(world):
    pid = _copy(world, A)
    assert _delete(world, HUB_ONLY, pid).status_code == 403 and _delete(world, ACADEMY, pid).status_code == 403
    assert (world.directory / f"{pid}.json").exists()


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example"}, {}], ids=["foreign-origin", "no-origin-no-referer"])
def test_deleting_without_a_verified_same_origin_is_rejected(world, headers):
    pid = _copy(world, A)
    assert _delete(world, A, pid, headers=headers).status_code == 403
    assert (world.directory / f"{pid}.json").exists()


def test_an_unknown_or_malformed_id_is_a_404_and_never_touches_the_disk(world):
    assert _delete(world, A, "no-existe").status_code == 404
    assert _delete(world, A, "../../etc/passwd").status_code in (404, 405)
    assert _delete(world, A, "00000000-0000-4000-8000-000000000000").status_code == 404


def test_the_owner_can_delete_an_instance_whose_template_is_unavailable(world):
    pid = _copy(world, A)
    path = world.directory / f"{pid}.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["template_version"] = 99                                                               # template «más nuevo»: la instancia queda no disponible, pero se puede limpiar
    raw["template_id"] = "template-que-ya-no-existe"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert world.client(A).get(f"/comercial/presentaciones/{pid}").status_code in (404, 409)
    assert _delete(world, A, pid).status_code == 200 and not path.exists()
