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


def test_dismiss_hides_the_item_for_that_user_only_and_adjusts_the_counter(editions):  # noqa: F811
    user, other = {"sub": OPS_A, "email": OPS_A, "name": "Ops A"}, {"sub": CCHH_EMAIL, "email": CCHH_EMAIL, "name": "CH QA"}
    for k in ("d-1", "d-2"):
        hub_inbox.notify(OPS_A, source="operaciones", feature="solicitud", key=k, text=k, url="/operaciones/solicitudes")
    hub_inbox.notify(CCHH_EMAIL, source="operaciones", feature="solicitud", key="d-1", text="d-1", url="/operaciones/solicitudes")
    ops = session_client(OPS_A)
    r = ops.get("/notificaciones").json()
    assert r["unread"] == 2
    assert ops.post("/notificaciones/descartar", json={"id": "operaciones:d-1"}).status_code == 403          # sin origen verificado
    assert ops.post("/notificaciones/descartar", headers=ORIGIN, json={"id": "operaciones:d-1"}).json()["unread"] == 1   # no leída ⇒ baja el contador
    ids = [i["id"] for i in ops.get("/notificaciones").json()["items"]]
    assert "operaciones:d-1" not in ids and "operaciones:d-2" in ids                                         # deja de mostrarse
    assert ops.post("/notificaciones/descartar", headers=ORIGIN, json={"id": "operaciones:d-1"}).status_code == 404    # ya no está en su bandeja
    assert ops.get("/notificaciones/abrir/operaciones:d-1", follow_redirects=False).status_code == 404
    hub_inbox.notify(OPS_A, source="operaciones", feature="solicitud", key="d-1", text="otra vez", url="/x")    # el evento sigue existiendo: no se re-crea
    assert "operaciones:d-1" not in [n["id"] for n in hub_inbox.items(user)]                                   # y no vuelve a aparecer
    assert ops.post("/notificaciones/todas", headers=ORIGIN).json()["unread"] == 0
    assert "operaciones:d-1" not in [n["id"] for n in hub_inbox.items(user)]                                   # «todas leídas» no lo resucita
    assert "operaciones:d-1" in [n["id"] for n in hub_inbox.items(other)]                                      # descartar es POR usuario
    assert hub_inbox.mark_read(user, "operaciones:d-2") and hub_inbox.items(user)                              # leer ≠ descartar: sigue en la lista
    assert ops.post("/notificaciones/descartar", headers=ORIGIN, json={"id": "operaciones:ajena"}).status_code == 404


def test_dismissals_are_permanent_even_with_many_of_them(editions):  # noqa: F811
    user = {"sub": OPS_A, "email": OPS_A, "name": "Ops A"}
    hub_inbox.notify(OPS_A, source="operaciones", feature="solicitud", key="vieja", text="vieja", url="/operaciones/solicitudes", at="2020-01-01T00:00:00+00:00")
    assert hub_inbox.dismiss(user, "operaciones:vieja")
    state = hub_inbox._state(user)                                                        # muchísimos descartes posteriores (más que cualquier ventana)
    state["dismissed"].update({f"operaciones:x{i}": f"2026-11-27T00:{i // 60:02d}:{i % 60:02d}+00:00" for i in range(1500)})
    hub_inbox._save_state(OPS_A.lower(), state)
    hub_inbox.notify(OPS_A, source="operaciones", feature="solicitud", key="nueva", text="nueva", url="/operaciones/solicitudes")
    assert hub_inbox.dismiss(user, "operaciones:nueva")                                   # otro descarte más: no poda los anteriores
    persisted = hub_inbox._state(user)["dismissed"]
    assert "operaciones:vieja" in persisted and len(persisted) == 1502
    assert not [n for n in hub_inbox.items(user) if n["id"] in ("operaciones:vieja", "operaciones:nueva")]   # ninguna reaparece


def test_the_cartelera_bell_dismisses_with_the_same_hub_endpoint():
    js = open("webapp/static/cartelera_social.js", encoding="utf-8").read()
    assert 'data-sx="notifdismiss"' in js and 'api("/notificaciones/descartar", { id: el.dataset.id })' in js    # MISMO descarte, sin lógica propia
    assert "setBadge(r.unread || 0)" in js                                                                     # el contador del portal se ajusta
    hub = open("webapp/static/hub_notifications.js", encoding="utf-8").read()
    assert "'/notificaciones/descartar'" in hub                                                                # y la campana del Hub usa el mismo


def test_persisted_events_are_idempotent_and_appear_in_the_inbox(editions):  # noqa: F811
    user = {"sub": OPS_A, "email": OPS_A, "name": "Ops A"}
    assert hub_inbox.notify(OPS_A, source="operaciones", feature="solicitud", key="sol-1", text="Tu solicitud fue rechazada", url="/operaciones/solicitudes")
    assert not hub_inbox.notify(OPS_A, source="operaciones", feature="solicitud", key="sol-1", text="dup", url="/x")
    rows = [n for n in hub_inbox.items(user) if n["source"] == "operaciones"]
    assert len(rows) == 1 and rows[0]["id"] == "operaciones:sol-1" and rows[0]["unread"]
    assert hub_inbox.mark_read(user, "operaciones:sol-1") and not hub_inbox.mark_read(user, "operaciones:otra")
