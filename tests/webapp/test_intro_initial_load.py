"""P4: la intro inmersiva (00 Intro: un iframe de storytelling) no lee el Catálogo ni los Data Products que no consume; los demás módulos y `?tab=catalogo` los cargan como siempre."""
import pytest
from fastapi.testclient import TestClient

from webapp import access
from webapp.main import app
from webapp.routers import modules
from webapp.services import auth_service

FULL = "full@danwor.com"


def _client(email: str) -> TestClient:
    c = TestClient(app)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "U", "first_name": "U", "family_name": "X"}))
    return c


@pytest.fixture(autouse=True)
def profiles(monkeypatch):
    monkeypatch.setattr(access, "APP_OWNER", FULL)
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", FULL)
    monkeypatch.setattr(auth_service, "ACADEMY_USERS", "ALL")


LOADERS = ["_catalog_domains", "_catalog_clients", "_catalog_model_domains", "_catalog_connection_types", "_catalog_security_families", "_catalog_data_products_list"]


@pytest.fixture
def spy(monkeypatch):
    calls = {n: 0 for n in LOADERS}
    for name in LOADERS:
        real = getattr(modules.catalog_reads, name)
        def wrap(*a, _n=name, _real=real, **k):
            calls[_n] += 1
            return _real(*a, **k)
        monkeypatch.setattr(modules.catalog_reads, name, wrap)
    return calls


def test_the_intro_does_not_read_the_catalog_or_data_products(spy):
    r = _client(FULL).get("/modules/00_Intro?pack=datasphere_core")
    assert r.status_code == 200 and 'class="content-embed academy-embed"' in r.text               # sigue siendo la intro inmersiva (iframe de storytelling)
    assert spy == {n: 0 for n in LOADERS}, spy


def test_a_regular_module_still_loads_the_catalog_inventories(spy):
    r = _client(FULL).get("/modules/01_Arquitectura_y_Conectividad?pack=datasphere_core")
    assert r.status_code == 200
    assert all(spy[n] >= 1 for n in LOADERS), spy                                                  # carga normal al acceder a los demás módulos


def test_explicitly_asking_for_a_valid_catalog_tab_on_the_intro_keeps_loading_it(spy, monkeypatch):
    monkeypatch.setattr(modules.catalog_reads, "_catalog_domains", lambda: ["Dom"])               # con catálogo generado la pestaña es válida (en un entorno vacío se descarta, como siempre)
    _client(FULL).get("/modules/00_Intro?pack=datasphere_core&tab=catalogo")
    assert spy["_catalog_data_products_list"] >= 1 and spy["_catalog_clients"] >= 1


def test_intro_navigation_and_shell_are_unchanged():
    r = _client(FULL).get("/modules/00_Intro?pack=datasphere_core")
    from core.content_registry import DEFAULT_PACK
    from webapp.services.content_service import list_modules
    stems = [m.stem for m in list_modules(DEFAULT_PACK)]
    assert all(f"/modules/{s}" in r.text for s in stems[:3])
    assert "no-copilot" in r.text                                                                   # sin panel derecho en la intro (como antes)
