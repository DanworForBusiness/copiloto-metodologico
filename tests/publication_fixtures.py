"""Fixture común de las pruebas de publicación segura: tenant simulado conectado al exportador REAL, directorios aislados y el interruptor/candado bajo control."""
from __future__ import annotations

import pytest

import config
from tests.publication_fakes import Ctx, Tenant, seed_pricing
from tools.catalog import DSP_CLI_Export_Lineage as dsp
from tools.catalog import publication_txn as txn


@pytest.fixture()
def ctx(tmp_path, monkeypatch):
    tenant = Tenant()
    seed_pricing(tenant)

    def _list(self, space, otype):
        self.last_list_incomplete = otype in tenant.incomplete_lists
        return tenant.list(space, otype)

    monkeypatch.setattr(dsp, "resolve_datasphere_cli_launcher", lambda root: object())
    monkeypatch.setattr(dsp.DatasphereCli, "oauth_preflight", lambda self: None)
    monkeypatch.setattr(dsp.DatasphereCli, "login", lambda self: None)
    monkeypatch.setattr(dsp.DatasphereCli, "authorization_preflight", lambda self, space: None)
    monkeypatch.setattr(dsp.DatasphereCli, "list_objects", _list)
    monkeypatch.setattr(dsp.DatasphereCli, "read_object", lambda self, space, otype, name: tenant.read(space, otype, name))
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog_local"))
    monkeypatch.delenv("DANWOR_CATALOG_CSN_CACHE", raising=False)
    monkeypatch.delenv(txn.SAFE_ENV, raising=False)
    monkeypatch.setenv(txn.LOCK_DIR_ENV, str(tmp_path / "locks"))
    monkeypatch.setattr(txn, "FAULT_HOOK", None)
    monkeypatch.setattr(config, "SPACES_CONFIGURED", False, raising=False)          # hermético: las credenciales del .env local no cambian el resultado (la prueba que las necesita las fija)
    return Ctx(tmp_path, tenant)
