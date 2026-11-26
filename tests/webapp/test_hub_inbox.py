"""Centro de Notificaciones del Hub: id estable con origen, lectura individual y total, contador compartido, migración que preserva lo ya
visto en Cartelera, navegación solo a lo propio y eventos persistidos idempotentes."""
from __future__ import annotations

from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, OPS_A, env, remote  # noqa: F401
from tests.webapp.test_cartelera_access import ORIGIN, editions  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import operations_users, session_client  # noqa: F401
from webapp.services import cartelera_social as social, hub_inbox


def _mention_posts(n: int, author: dict, target_name: str) -> None:
    for i in range(n):
        social.save_post(author, {"category": "reconocimientos", "title": f"Aviso {i}", "body": f"Hola @[{target_name}]"})


def test_unified_inbox_read_state_and_migration(editions, monkeypatch):  # noqa: F811
    from webapp.routers import cartelera as router
    monkeypatch.setattr(router, "PORTAL_LAUNCHED", True)
    ops_user, ch_user = {"sub": OPS_A, "email": OPS_A, "name": "Ops A"}, {"sub": CCHH_EMAIL, "email": CCHH_EMAIL, "name": "CH QA"}
    social.touch_person(ch_user)
    monkeypatch.setattr(social, "mention_ids", lambda body: [CCHH_EMAIL.lower()] if "@[" in (body or "") else [])
    _mention_posts(2, ops_user, "CH QA")
    # Migración: lo que CH ya había visto en Cartelera (seen_at posterior a esas menciones) queda LEÍDO en la bandeja del Hub.
    social.save_prefs(ch_user, seen=True)
    cchh = session_client(CCHH_EMAIL)
    r = cchh.get("/notificaciones").json()
    assert len(r["items"]) == 2 and r["unread"] == 0 and all(i["id"].startswith("cartelera:post-mention:") for i in r["items"])
    assert r["items"][0]["source_label"] == "Cartelera Virtual"
    _mention_posts(2, ops_user, "CH QA")                                                   # nuevas después de la migración
    r = cchh.get("/notificaciones").json()
    assert r["unread"] == 2
    first = next(i for i in r["items"] if i["unread"])
    assert cchh.post("/notificaciones/leida", json={"id": first["id"]}).status_code == 403            # sin origen verificado
    assert cchh.post("/notificaciones/leida", headers=ORIGIN, json={"id": first["id"]}).json()["unread"] == 1   # individual
    opened = cchh.get(next(i for i in cchh.get("/notificaciones").json()["items"] if i["unread"])["href"], follow_redirects=False)
    assert opened.status_code == 303 and opened.headers["location"].startswith("/cartelera/comunidad#post-")
    assert cchh.get("/notificaciones").json()["unread"] == 0                               # abrir = leída
    html = cchh.get("/cartelera/comunidad").text
    assert 'class="badge-dot"' not in html                                                 # contador COMPARTIDO con el portal
    _mention_posts(1, ops_user, "CH QA")
    assert cchh.post("/notificaciones/todas", headers=ORIGIN).json()["unread"] == 0 and hub_inbox.unread_count(ch_user) == 0
    ops = session_client(OPS_A)
    foreign = cchh.get("/notificaciones").json()["items"][0]["href"]
    assert ops.get(foreign, follow_redirects=False).status_code == 404                     # nunca la bandeja de otro


def test_persisted_events_are_idempotent_and_appear_in_the_inbox(editions):  # noqa: F811
    user = {"sub": OPS_A, "email": OPS_A, "name": "Ops A"}
    assert hub_inbox.notify(OPS_A, source="operaciones", feature="solicitud", key="sol-1", text="Tu solicitud fue rechazada", url="/operaciones/solicitudes")
    assert not hub_inbox.notify(OPS_A, source="operaciones", feature="solicitud", key="sol-1", text="dup", url="/x")
    rows = [n for n in hub_inbox.items(user) if n["source"] == "operaciones"]
    assert len(rows) == 1 and rows[0]["id"] == "operaciones:sol-1" and rows[0]["unread"]
    assert hub_inbox.mark_read(user, "operaciones:sol-1") and not hub_inbox.mark_read(user, "operaciones:otra")
