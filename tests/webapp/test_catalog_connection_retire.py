"""Retiro de conexiones del Catálogo de conectividad (módulo 01): MISMO retiro LÓGICO de tiles de Dominios (`catalog_governance`, tombstone en
`.retired/`), mismo botón y diálogo. Identidad = cliente + Space + conexión; nunca toca SAP ni borra fichas; una regeneración no la resucita."""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
from tools.catalog import generate_space_catalog as gsc
from webapp import access
from webapp.main import app
from webapp.services import auth_service, catalog_reads

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "tests" / "fixtures" / "connectivity_reconciliation" / "Sintetico" / "Conexiones"
ORIGIN = {"origin": "http://testserver"}


@pytest.fixture()
def cat(tmp_path, monkeypatch):
    root = tmp_path / "catalog_local"
    pool = root / ".csn_cache" / "Sintetico" / "Conexiones"
    pool.mkdir(parents=True)
    for p in SRC.glob("conn_space_*.json"):
        shutil.copy(p, pool / p.name)
    gsc.generate_all(root / ".csn_cache", root)
    sys.path.insert(0, str(REPO / "tools" / "catalog"))                    # el cargador del Catálogo se importa desde BASE_DIR/tools/catalog
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.delenv("DANWOR_CATALOG_DIR", raising=False)
    monkeypatch.setattr(access, "APP_OWNER", "owner@danwor.com")
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "consultor@danwor.com")
    catalog_reads._catalog_architecture_connections.cache_clear()
    yield root
    catalog_reads._catalog_architecture_connections.cache_clear()


def _client(email=None):
    c = TestClient(app)
    if email:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": email, "first_name": "T", "family_name": "U"}))
    return c


def _visible():
    return sorted((e.space, e.connection) for e in catalog_reads._catalog_architecture_connections()[1])


def _id(name):
    return next(e.entry_id for e in catalog_reads._catalog_architecture_connections()[1] if e.connection == name)


def test_only_the_owner_sees_the_bin_and_can_retire(cat):
    entries = catalog_reads._catalog_architecture_connections()[1]
    type_id = next(e.type_id for e in entries if e.connection == "ERP_ABAP")
    url = f"/catalog/connections?type_id={type_id}"
    assert "/catalog/connections/retire" in _client("owner@danwor.com").get(url).text
    assert "/catalog/connections/retire" not in _client("consultor@danwor.com").get(url).text
    ident = _id("ERP_ABAP")
    assert _client().post("/catalog/connections/retire", data={"ids": ident}, headers=ORIGIN).status_code == 401
    assert _client("consultor@danwor.com").post("/catalog/connections/retire", data={"ids": ident}, headers=ORIGIN).status_code == 403
    assert _client("owner@danwor.com").post("/catalog/connections/retire", data={"ids": ident}, headers={"origin": "http://evil.example"}).status_code == 403
    assert len(_visible()) == 3


def test_retiring_one_connection_keeps_the_others_and_survives_regeneration(cat):
    before = _visible()
    ident = _id("ERP_ABAP")
    assert ident == "conn-sintetico--dev--erp-abap"                                     # cliente + Space + conexión
    r = _client("owner@danwor.com").post("/catalog/connections/retire", data={"ids": ident}, headers=ORIGIN)
    assert r.status_code == 200 and 'data-catalog-retired="ok"' in r.text and "volvé a importarla" in r.text
    assert _visible() == [x for x in before if x[1] != "ERP_ABAP"]                      # las demás intactas
    assert (cat / "arquitectura" / "Sintetico" / "conexiones" / "DEV__ERP_ABAP.md").exists()   # retiro lógico: la ficha no se borra
    assert json.loads((cat / ".retired" / f"{ident}.json").read_text(encoding="utf-8"))["retired_by"] == "owner@danwor.com"
    gsc.generate_all(cat / ".csn_cache", cat)                                            # regeneración desde el pool: no la resucita
    catalog_reads._catalog_architecture_connections.cache_clear()
    assert "ERP_ABAP" not in [c for _s, c in _visible()]
    assert _client("owner@danwor.com").post("/catalog/connections/retire", data={"ids": ident}, headers=ORIGIN).status_code == 404
