"""
test_catalog_reads.py — Contrato observable de webapp/services/catalog_reads.py
(Fase 2I: cierre mínimo de dependencia de Catalog que necesita module_page).

No es una suite de Catalog HTTP — solo verifica las 5 funciones de lectura/
consulta relocadas: shape de salida, disponibilidad de security_criteria
(comparte cache con security_families) e identidad de cache_clear.
"""
from webapp.services import catalog_reads


def test_domains_returns_list_of_strings():
    result = catalog_reads._catalog_domains()
    assert isinstance(result, list)
    # catalog_local tiene datos reales (varios clientes) — no debería estar vacío.
    assert result
    assert all(isinstance(item, str) for item in result)


def test_connection_types_returns_list_semantics():
    result = catalog_reads._catalog_connection_types()
    assert isinstance(result, list)
    assert all(isinstance(item, dict) for item in result)


def test_security_families_returns_list_semantics():
    result = catalog_reads._catalog_security_families()
    assert isinstance(result, list)
    assert all(isinstance(item, dict) for item in result)


def test_security_criteria_still_available_sharing_security_cache():
    # _catalog_security_criteria comparte el cache de _catalog_security_entries
    # con _catalog_security_families — debe seguir funcionando tras la relocación.
    result = catalog_reads._catalog_security_criteria()
    assert isinstance(result, list)


def test_data_products_list_returns_list_semantics():
    result = catalog_reads._catalog_data_products_list()
    assert isinstance(result, list)


def test_cache_bearing_functions_retain_cache_clear():
    for name in (
        "_catalog_entries",
        "_catalog_architecture_connections",
        "_catalog_data_products_entries",
        "_catalog_security_entries",
    ):
        fn = getattr(catalog_reads, name)
        assert hasattr(fn, "cache_clear"), f"{name} perdió su @lru_cache"


def test_graceful_degradation_on_missing_catalog_dir(monkeypatch):
    # Mismo contrato que antes de la relocación: un directorio sin datos (o
    # inexistente) degrada a lista vacía, nunca excepción — sea porque rglob no
    # encuentra nada o porque el try/except interno lo absorbe.
    catalog_reads._catalog_entries.cache_clear()
    monkeypatch.setenv("DANWOR_CATALOG_DIR", "/ruta/inexistente/no-existe")
    try:
        result = catalog_reads._catalog_domains()
        assert result == []
    finally:
        catalog_reads._catalog_entries.cache_clear()
