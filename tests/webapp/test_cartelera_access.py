"""Cartelera Virtual — comprobaciones INDISPENSABLES de autorización y visibilidad (la batería completa llega en la etapa de consolidación)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, OPS_A, env, remote  # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import operations_users, session_client  # noqa: F401
from webapp.services import cartelera_repository as repo, cartelera_service as svc

ORIGIN = {"Origin": "http://testserver"}


@pytest.fixture
def editions(monkeypatch):
    monkeypatch.setattr(svc, "notify_published", lambda edition: "disabled")
    now = svc.now_utc()
    cur, nxt, prv = svc.month_key(), svc.shift_month(svc.month_key(), 1), svc.shift_month(svc.month_key(), -1)
    base = {"title": "T", "subtitle": "", "sections": [], "history": []}
    repo.save("editions", cur, {**base, "title": "Publicada", "state": "published", "published_at": now.isoformat()}, expected_revision=None)
    repo.save("editions", nxt, {**base, "title": "Futura", "state": "scheduled", "publish_at": (now + timedelta(days=3)).isoformat()}, expected_revision=None)
    repo.save("editions", prv, {**base, "title": "Borrador", "state": "draft"}, expected_revision=None)
    return cur, nxt, prv


def test_during_homologation_the_portal_is_limited_to_the_human_capital_workspace(editions):
    from webapp.routers import cartelera as router
    assert router.PORTAL_LAUNCHED is False                         # Fase I: sin lanzamiento institucional
    ops = session_client(OPS_A)                                   # admitido al Hub, sin Capital Humano ni COMMUNITY_OWNER
    assert ops.get("/cartelera").status_code == 403
    assert ops.get("/cartelera/comunidad").status_code == 403
    assert 'href="/cartelera"' not in ops.get("/onedanwor", follow_redirects=True).text
    cchh = session_client(CCHH_EMAIL)
    assert cchh.get("/cartelera").status_code == 200


def test_any_admitted_collaborator_reads_the_portal_without_any_vertical(editions, monkeypatch):
    from webapp.routers import cartelera as router
    monkeypatch.setattr(router, "PORTAL_LAUNCHED", True)          # F11, tras el lanzamiento institucional
    cur, nxt, prv = editions
    ops = session_client(OPS_A)                                   # admitido al Hub, sin Capital Humano
    assert ops.get("/cartelera").status_code == 200
    assert ops.get(f"/cartelera/novedades/{cur}").status_code == 200
    for hidden in (nxt, prv):                                     # programada futura y borrador: nunca visibles
        assert ops.get(f"/cartelera/novedades/{hidden}").status_code == 404
    html = ops.get("/cartelera/novedades").text
    assert "Publicada" in html and "Futura" not in html and "Borrador" not in html


def test_management_requires_human_capital_and_verified_origin(editions):
    cur, _, _ = editions
    ops = session_client(OPS_A)
    assert ops.get("/cchh/cartelera").status_code == 403
    assert ops.post("/cchh/cartelera/ediciones", headers=ORIGIN, data={"month": "2030-01"}).status_code == 403
    cchh = session_client(CCHH_EMAIL)
    assert cchh.get("/cchh/cartelera").status_code == 200
    assert cchh.post("/cchh/cartelera/ediciones", data={"month": "2030-01"}).status_code == 403       # sin Origin verificado
    anonymous = session_client(CCHH_EMAIL)
    anonymous.cookies.clear()
    assert anonymous.get("/cartelera").status_code in (302, 303, 307)


def test_a_due_scheduled_edition_is_visible_and_the_scheduler_consolidates_it(editions):
    _, nxt, _ = editions
    edition = repo.get("editions", nxt)
    repo.save("editions", nxt, {"publish_at": (svc.now_utc() - timedelta(minutes=1)).isoformat()}, expected_revision=edition["revision"])
    assert svc.is_visible(repo.get("editions", nxt))              # visible por lectura, aunque el programador no haya corrido
    assert svc.run_due() == 1 and repo.get("editions", nxt)["state"] == "published"


def test_stale_revision_never_overwrites(editions):
    cur, _, _ = editions
    with pytest.raises(repo.CarteleraConcurrentModificationError):
        svc.save_content(cur, 99, title="x", subtitle="", sections=[], actor="t")


def test_the_portal_always_uses_its_light_identity_whatever_the_hub_content_theme(editions):
    """Abrir la Cartelera desde el Hub con contenido oscuro (cookie `dw-content-theme=dark`) o desde el wrapper institucional: siempre clara."""
    cchh = session_client(CCHH_EMAIL)
    for path in ("/cartelera", "/cartelera/novedades", "/cartelera/nosotros", "/cartelera/comunidad"):
        for theme in ("dark", "light", None):
            cchh.cookies.delete("dw-content-theme")
            if theme:
                cchh.cookies.set("dw-content-theme", theme)
            html = cchh.get(path).text
            assert '<html lang="es" data-theme="light">' in html, (path, theme)


def test_home_quick_links_carry_every_configured_link_for_client_side_search_and_paging(editions):
    """Accesos rápidos de Inicio: sin tope fijo de 4 — el HTML trae TODOS los accesos (orden de Gestión) y el script de búsqueda/paginación."""
    svc.save_links(None, [{"title": f"Acceso {n:02d}", "group": "Grupo", "url": "https://www.danwor.com/"} for n in range(1, 13)], actor="t")
    html = session_client(CCHH_EMAIL).get("/cartelera").text
    card = html[html.index("data-qlinks"):html.index("data-qlinks-pager")]
    assert [f"Acceso {n:02d}" for n in range(1, 13)] == [t for t in (f"Acceso {n:02d}" for n in range(1, 13)) if t in card]
    assert card.index("Acceso 01") < card.index("Acceso 12")
    assert "data-qlinks-q" in card and "cartelera_quicklinks.js" in html


def test_home_texts_are_editorial_content_of_each_edition_with_defaults_copy_and_drafts(editions):
    """Bienvenida y bajadas de Comunidad/Nosotros: viven en la edición (`home`), con predeterminados; se copian; el borrador y la vista previa
    los respetan; sin `home` en el pedido (modo Ordenar) se conservan los guardados. El tile Novedades sigue siendo el título de portada."""
    cur, _, _ = editions
    cchh = session_client(CCHH_EMAIL)
    html = cchh.get("/cartelera").text
    assert "Esto es lo que está pasando en Danwor." in html and "Nuestra misión, visión y valores." in html
    assert "celebraciones, reconocimientos, proyectos e ideas." in html and "data-ct-home" not in html
    rev = repo.get("editions", cur)["revision"]
    home = {"welcome": "Bienvenidos a noviembre.", "community": "logros del equipo.", "about": "Quiénes somos.", "otro": "x"}
    saved = svc.save_content(cur, rev, title="Portada", subtitle="", sections=[], actor="t", home=home)
    assert saved["home"] == {"welcome": "Bienvenidos a noviembre.", "community": "logros del equipo.", "about": "Quiénes somos."}
    saved = svc.save_content(cur, saved["revision"], title="Portada", subtitle="", sections=[], actor="t")     # sin `home`: se conserva
    assert saved["home"]["welcome"] == "Bienvenidos a noviembre."
    html = cchh.get("/cartelera").text
    assert "Hola, " in html and "Bienvenidos a noviembre." in html and "Esto es lo que está pasando" not in html
    assert "logros del equipo." in html and "Quiénes somos." in html and "Portada" in html
    assert svc.home_text({"home": {"welcome": "  "}}, "welcome") == "Esto es lo que está pasando en Danwor."      # vacío ⇒ predeterminado
    copy = svc.copy_edition(cur, "2031-01", actor="t")
    assert copy["home"]["about"] == "Quiénes somos." and copy["title"] == "Portada"
    draft = svc.draft_edition(cur, title="Portada", subtitle="", sections=[], home={"welcome": "Borrador local"})
    assert draft["home"]["welcome"] == "Borrador local" and draft["home"]["about"] == "Quiénes somos."
    assert svc.preview_edition(cur, title="Portada", subtitle="", sections=[], home={"about": "Vista previa"})["home"]["about"] == "Vista previa"


def test_about_inherits_institutional_content_with_optional_per_edition_personalization(editions):
    """Nosotros por edición: hereda el institucional; guarda SOLO lo personalizado; restablecer = dejar de enviarlo; nunca toca el institucional;
    los valores se identifican por su nombre institucional; se copia con la edición; el portal respeta la edición del contexto."""
    cur, _, prv = editions
    svc.save_institutional(None, mission="Misión global", vision="Visión global", values=[{"title": "Integridad", "text": "Ser íntegros"},
                           {"title": "Empatía", "text": "Ponerse en el lugar"}], albums=[], actor="t", intro="Intro global", values_intro="Intro valores")
    assert svc.about_view(repo.get("editions", cur))["custom"] == set()
    rev = repo.get("editions", cur)["revision"]
    about = {"title": "Nuestra esencia", "mission": "Misión de noviembre", "vision": "Visión global",            # igual al institucional: no se guarda
             "values": {"integridad": {"title": "Integridad siempre", "text": "Ser íntegros"}, "inexistente": {"title": "x"}}}
    saved = svc.save_content(cur, rev, title="P", subtitle="", sections=[], actor="t", about=about)
    assert saved["about"] == {"title": "Nuestra esencia", "mission": "Misión de noviembre", "values": {"integridad": {"title": "Integridad siempre"}}}
    inst = svc.institutional()
    assert inst["mission"] == "Misión global" and inst["values"][0]["title"] == "Integridad"                      # el institucional, intacto
    view = svc.about_view(saved)
    assert view["fields"]["vision"] == "Visión global" and view["custom"] == {"title", "mission"}
    assert view["values"][0]["title"] == "Integridad siempre" and view["values"][0]["text"] == "Ser íntegros" and view["values"][1]["title"] == "Empatía"
    cchh = session_client(CCHH_EMAIL)
    html = cchh.get("/cartelera/nosotros").text                                                                      # sin edición: la vigente
    assert "Nuestra esencia" in html and "Misión de noviembre" in html and "data-ct-about" not in html
    copy = svc.copy_edition(cur, "2031-02", actor="t")
    assert copy["about"] == saved["about"]
    svc.save_institutional(svc.institutional()["revision"], mission="Misión global 2", vision="Visión nueva", values=inst["values"], albums=[],
                           actor="t", intro="Intro global", values_intro="Intro valores")
    view = svc.about_view(repo.get("editions", "2031-02"))
    assert view["fields"]["vision"] == "Visión nueva" and view["fields"]["mission"] == "Misión de noviembre"        # hereda lo no personalizado
    saved = svc.save_content(cur, saved["revision"], title="P", subtitle="", sections=[], actor="t", about={"title": "Nuestra esencia"})
    assert saved["about"] == {"title": "Nuestra esencia"}                                                            # restablecer = ya no se envía
    saved = svc.save_content(cur, saved["revision"], title="P", subtitle="", sections=[], actor="t")
    assert saved["about"] == {"title": "Nuestra esencia"}                                                            # sin `about` (Ordenar): se conserva
    draft = svc.draft_edition(cur, title="P", subtitle="", sections=[], about={"mission": "Borrador"})
    assert svc.about_view(draft)["fields"]["mission"] == "Borrador"


def test_items_can_sit_beside_the_previous_one_in_sections_without_full_width(editions):
    """Distribución horizontal: `beside` por contenido (solo se guarda si está activo); la sección sin ancho completo pasa a dos columnas;
    con ancho completo se apila pero la preferencia se conserva; se copia; el borrador la respeta."""
    cur, _, _ = editions
    items = [{"title": "A"}, {"title": "B", "beside": True}, {"title": "C"}]
    rev = repo.get("editions", cur)["revision"]
    saved = svc.save_content(cur, rev, title="P", subtitle="", sections=[{"key": "comunicados", "items": items}], actor="t")
    sec = next(s for s in saved["sections"] if s["key"] == "comunicados")
    assert [i.get("beside") for i in sec["items"]] == [None, True, None]
    assert svc.beside_layout(sec["items"]) == ["left", "right", "full"]
    html = session_client(CCHH_EMAIL).get(f"/cartelera/novedades/{cur}").text
    assert "cat-sec duo" in html and "item pos-left" in html and "item pos-right" in html
    saved = svc.save_content(cur, saved["revision"], title="P", subtitle="", actor="t",
                             sections=[{"key": "comunicados", "wide": True, "items": items}])
    sec = next(s for s in saved["sections"] if s["key"] == "comunicados")
    assert sec["wide"] and sec["items"][1].get("beside") is True                          # ancho completo: se conserva la preferencia
    assert "cat-sec duo" not in session_client(CCHH_EMAIL).get(f"/cartelera/novedades/{cur}").text
    copy = svc.copy_edition(cur, "2031-03", actor="t")
    assert next(s for s in copy["sections"] if s["key"] == "comunicados")["items"][1].get("beside") is True
    draft = svc.draft_edition(cur, title="P", subtitle="", sections=[{"key": "comunicados", "items": items}])
    assert next(s for s in draft["sections"] if s["key"] == "comunicados")["items"][1].get("beside") is True


def test_fallback_preview_sends_human_capital_to_the_latest_edition_in_preparation_only_without_a_published_one(editions):
    """`/cartelera?fallback=preview` (iframe institucional): con edición vigente, normal; sin vigente, Capital Humano → vista previa de la
    edición en preparación más reciente (Novedades); otros usuarios y la ruta sin el parámetro, comportamiento de siempre."""
    from webapp.routers import cartelera as router
    cur, nxt, prv = editions
    cchh = session_client(CCHH_EMAIL)
    assert cchh.get("/cartelera?fallback=preview", follow_redirects=False).status_code == 200                   # hay vigente: normal
    repo.save("editions", cur, {"state": "draft", "published_at": None}, expected_revision=repo.get("editions", cur)["revision"])
    r = cchh.get("/cartelera?fallback=preview", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/cchh/cartelera/ediciones/{nxt}/vista-previa?pagina=novedades"
    assert cchh.get("/cartelera", follow_redirects=False).status_code == 200                                    # sin el parámetro: igual que hoy
    router_launched = router.PORTAL_LAUNCHED
    try:
        router.PORTAL_LAUNCHED = True
        ops = session_client(OPS_A)                                                                              # sin Capital Humano
        r = ops.get("/cartelera?fallback=preview", follow_redirects=False)
        assert r.status_code == 200 and "vista-previa" not in r.text
    finally:
        router.PORTAL_LAUNCHED = router_launched
    repo.save("editions", prv, {"state": "published", "published_at": svc.now_utc().isoformat()},
              expected_revision=repo.get("editions", prv)["revision"])
    assert cchh.get("/cartelera?fallback=preview", follow_redirects=False).status_code == 200                   # vigente anterior: normal


def test_home_birthdays_tile_uses_the_month_of_the_edition_without_past_dates(monkeypatch):
    """Tile de cumpleaños de Inicio: mes en curso = hoy + días posteriores (nunca pasados); futuro = todo el mes; histórico = todo el mes
    rotulado como del mes; orden cronológico."""
    from datetime import date
    people = [{"id": "a", "name": "Ana", "day": 26, "month": 11}, {"id": "b", "name": "Beto", "day": 3, "month": 11},
              {"id": "c", "name": "Ceci", "day": 30, "month": 11}, {"id": "d", "name": "Dani", "day": 28, "month": 11},
              {"id": "e", "name": "Eva", "day": 5, "month": 12}, {"id": "f", "name": "Fede", "day": 9, "month": 10}]
    monkeypatch.setattr(svc, "public_birthdays", lambda: people)
    today = date(2026, 11, 26)
    cur = svc.home_birthdays("2026-11", today)
    assert cur["mode"] == "current" and [b["name"] for b in cur["today"]] == ["Ana"]
    assert [(b["name"], b["day"], b["month"]) for b in cur["upcoming"][:3]] == [("Dani", 28, 11), ("Ceci", 30, 11), ("Eva", 5, 12)]   # cruza el mes
    assert "Beto" not in [b["name"] for b in cur["upcoming"][:4]]                                                # el 3/11 ya pasó: va al final (año próximo)
    fut = svc.home_birthdays("2026-12", today)
    assert fut["mode"] == "future" and [b["name"] for b in fut["upcoming"]][:1] == ["Eva"] and not fut["today"]
    past = svc.home_birthdays("2026-10", today)
    assert past["mode"] == "past" and [b["name"] for b in past["month_list"]] == ["Fede"] and not past["upcoming"]
    assert svc.home_birthdays(None, today)["mode"] == "current"
