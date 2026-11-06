"""test_publication_registers_assessment.py — puente PUBLICAR → assessment canónico.

Contrato del PUENTE ``register_assessment``: registra el assessment CANÓNICO de un modelo desde la evidencia ya adquirida: reusa
``assessment_service.generate`` (misma resolución de AM, misma vigencia, misma caché ``(cliente, AM)``), NO reconecta a Datasphere,
NO vuelve a extraer y nunca lanza. La publicación (unidad transaccional, único camino) ya NO lo llama después del commit: el assessment
se prepara y activa DENTRO de la unidad (ver test_datasphere_publication_safe).
"""
from __future__ import annotations

import types

import pytest

import webapp.main as main
from webapp.services import assessment_service as assess
from webapp.services import catalog_reads
from webapp.services import datasphere_assessment_service as asd
from webapp.services import datasphere_publication_service as pub


def _values(client="Pampa", root="AM_Compras"):
    return asd.FormValues.from_mapping({
        "host": "https://t.eu10.hcs.cloud.sap", "token_url": "https://t.authentication.eu10.hana.ondemand.com/oauth/token",
        "client_id": "sb-abc", "space": "PROD", "root_object": root, "client": client, "features": None})


class _FakeOk(types.SimpleNamespace):
    ok = True
    error_code = None
    message = ""
    duration_s = 1.0
    exported_files = 3


def _entry(eid, cliente, artefactos):
    return types.SimpleNamespace(entry_id=eid, cliente=cliente, dominio="D", artefactos=list(artefactos))


@pytest.fixture()
def published_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog_local"))
    monkeypatch.delenv("DANWOR_CATALOG_CSN_CACHE", raising=False)
    monkeypatch.setattr(asd, "cli_available", lambda: True)
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    calls = {"export": 0}
    monkeypatch.setattr(dsp, "run_export", lambda req: (calls.__setitem__("export", calls["export"] + 1), _FakeOk())[1])
    monkeypatch.setattr(main, "_invalidate_catalog_cache", lambda: None)
    return calls


def _patch_catalog(monkeypatch, entries):
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (object(), entries))


def test_the_bridge_registers_the_assessment_of_the_published_root_only(monkeypatch, published_env):
    entries = [_entry("e1", "Pampa", ["AM_Compras", "VF_X"]), _entry("e2", "Pampa", ["AM_Otro"]), _entry("e3", "Otro", ["AM_Compras"])]
    _patch_catalog(monkeypatch, entries)
    generated = []
    monkeypatch.setattr(assess, "generate", lambda e: (generated.append(e.entry_id), types.SimpleNamespace(state="CURRENT"))[1])
    assert pub.register_assessment(_valid()) == [("e1", "CURRENT")]
    assert generated == ["e1"]                       # solo la entrada del MISMO cliente que contiene la raíz publicada
    assert published_env["export"] == 0              # el puente no reconecta ni re-extrae


def _valid():
    return _values()


def test_client_match_is_case_insensitive(monkeypatch, published_env):
    _patch_catalog(monkeypatch, [_entry("e1", "PAMPA", ["AM_Compras"])])
    generated = []
    monkeypatch.setattr(assess, "generate", lambda e: (generated.append(e.entry_id), types.SimpleNamespace(state="CURRENT"))[1])
    assert pub.register_assessment(_valid()) == [("e1", "CURRENT")] and generated == ["e1"]


def test_an_assessment_failure_never_raises_from_the_bridge(monkeypatch, published_env):
    _patch_catalog(monkeypatch, [_entry("e1", "Pampa", ["AM_Compras"])])

    def boom(_entry):
        raise RuntimeError("motor caído")
    monkeypatch.setattr(assess, "generate", boom)
    assert isinstance(pub.register_assessment(_valid()), list)                # nunca lanza: el estado estable lo resuelve el Catálogo


def test_the_bridge_without_a_matching_catalog_entry_registers_nothing(monkeypatch, published_env):
    _patch_catalog(monkeypatch, [_entry("e9", "Otro", ["AM_Compras"])])
    monkeypatch.setattr(assess, "generate", lambda e: pytest.fail("no debe generar un assessment ajeno"))
    assert pub.register_assessment(_valid()) == []


def test_failed_extraction_registers_no_assessment(monkeypatch, tmp_path):
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path / "catalog_local"))
    monkeypatch.setattr(asd, "cli_available", lambda: True)
    from tools.catalog import DSP_CLI_Export_Lineage as dsp
    monkeypatch.setattr(dsp, "run_export", lambda req: types.SimpleNamespace(ok=False, error_code="EXTRACTION_FAILED", message="x", duration_s=1.0))
    monkeypatch.setattr(assess, "generate", lambda e: pytest.fail("no se registra assessment si la publicación falló"))
    assert pub.run(_valid(), "secret").state == "FAILED"


def test_register_assessment_reuses_the_catalog_integration_and_has_no_datasphere_access():
    import inspect
    src = inspect.getsource(pub.register_assessment)
    assert "assess.generate" in src                                          # misma integración del Catálogo, sin reimplementar
    for forbidden in ("run_export", "ExportRequest", "client_secret", "requests.", "urllib"):
        assert forbidden not in src


def test_registered_assessment_is_stored_in_the_catalog_cache_key(monkeypatch, published_env):
    """Con el motor falseado: el estado queda en la MISMA caché `(cliente, AM)` que lee el Catálogo (no un store paralelo)."""
    entry = _entry("e1", "Pampa", ["AM_Compras"])
    _patch_catalog(monkeypatch, [entry])
    seen = {}
    monkeypatch.setattr(assess, "generate", lambda e: (seen.setdefault("entry", e), types.SimpleNamespace(state="CURRENT"))[1])
    assert pub.register_assessment(_valid()) == [("e1", "CURRENT")]
    assert seen["entry"] is entry
