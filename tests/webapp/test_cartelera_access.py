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
