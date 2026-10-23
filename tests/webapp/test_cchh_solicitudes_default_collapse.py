"""«Solicitudes recibidas» (landing de Búsquedas): COLAPSADO por defecto con 0 solicitudes por atender / pendientes; ABIERTO solo con > 0 (o si falló la lectura). El fragmento HTMX de los filtros siempre vuelve abierto."""
from __future__ import annotations

import re

from webapp.services import busqueda_repository as repo, human_capital_service as svc
from tests.webapp.test_busqueda_solicitud_i1 import CCHH_EMAIL, SID, SID2, env, remote, rev, send, to_state   # noqa: F401
from tests.webapp.test_operations_busquedas_i2 import session_client                                          # noqa: F401

HX = {"HX-Request": "true"}


def _details(html: str) -> str:
    m = re.search(r'<details class="dashboard-section" data-section-id="solicitudes-recibidas"[^>]*>', html)
    assert m, "no se renderizó el colapsable «Solicitudes recibidas»"
    return m.group(0)


def _landing() -> str:
    return session_client(CCHH_EMAIL).get("/cchh/busquedas").text


def test_with_zero_solicitudes_the_section_loads_collapsed():
    tag = _details(_landing())
    assert " open" not in tag and "data-persist-collapse" in tag


def test_with_a_pending_solicitud_the_section_loads_open():
    send(SID)
    assert " open" in _details(_landing())


def test_only_observed_or_cancelled_solicitudes_do_not_open_it():
    to_state("observada", SID)
    to_state("cancelada", SID2)
    html = _landing()
    assert " open" not in _details(html)
    assert 'data-solicitud-filtro="observadas"' in html and "Observadas · 1" in html and "Canceladas · 1" in html


def test_taken_solicitudes_leave_the_inbox_and_collapse_it_again():
    send(SID)
    svc.take_solicitud(SID, rev(SID), CCHH_EMAIL)
    assert " open" not in _details(_landing())


def test_a_read_error_keeps_it_open_so_the_message_is_visible(monkeypatch):
    monkeypatch.setattr(svc, "list_solicitudes_view", lambda: (_ for _ in ()).throw(repo.BusquedaStorageError("boom")))
    assert " open" in _details(_landing())


def test_the_htmx_filter_fragment_is_always_open_even_with_nothing_to_show():
    c = session_client(CCHH_EMAIL)
    for filtro in ("recibidas", "observadas", "canceladas"):
        assert " open" in _details(c.get(f"/cchh/solicitudes?solicitud={filtro}", headers=HX).text)


def test_counts_and_header_are_unchanged_by_the_default_state():
    html = _landing()
    assert "0 solicitudes" in html and "Por atender · 0" in html and "Pendientes · 0" in html
