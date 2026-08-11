"""
test_module_registry.py — MODULE IDENTIFIER CONVENTION V2, Phase 1 (registry
foundation). Prueba el registro/resolver canónico puramente ADITIVO — ningún
test de este archivo toca rutas, Activity, Feedback, nav Jinja ni contenido
físico (esos consumidores NO se migran en esta fase, ver HANDOFF).
"""
from __future__ import annotations

import inspect

from webapp.services import module_registry as mr

_COPILOT_MODULE_STEMS = {
    "00_Intro": "CP00",
    "01_Arquitectura_y_Conectividad": "CP01",
    "02_Seguridad_y_Gobernanza": "CP02",
    "03_Integracion_y_Estrategia_de_Datos": "CP03",
    "04_Modelado_y_Desarrollo": "CP04",
    "05_Reporting_y_Consumo": "CP05",
}
_COPILOT_APPENDIX_STEMS = {
    "A01_Arquitectura_y_Modelado": "CPA01",
    "A02_Modernizacion_BW_Greenfield": "CPA02",
    "A03_Hub_de_Integracion_AWS": "CPA03",
    "A04_Biblioteca_de_Referencias": "CPA04",
    "A05_Troubleshooting_y_Performance": "CPA05",
    "A06_SAP_Analytics_Cloud": "CPA06",
}


# test 1
def test_every_main_copilot_stem_resolves_to_its_approved_cp_id():
    for stem, expected_id in _COPILOT_MODULE_STEMS.items():
        entry = mr.resolve(stem)
        assert entry is not None, stem
        assert entry.module_id == expected_id, stem


# test 2
def test_every_appendix_stem_resolves_to_its_approved_cpa_id():
    for stem, expected_id in _COPILOT_APPENDIX_STEMS.items():
        entry = mr.resolve(stem)
        assert entry is not None, stem
        assert entry.module_id == expected_id, stem


# test 3
def test_assignments_resolves_to_op01():
    entry = mr.resolve("assignments")
    assert entry is not None
    assert entry.module_id == "OP01"


# test 4
def test_canonical_id_and_legacy_alias_resolve_to_the_same_descriptor():
    for entry in mr.all_entries():
        by_id = mr.resolve(entry.module_id)
        for alias in entry.legacy_aliases:
            by_alias = mr.resolve(alias)
            assert by_alias is by_id or by_alias == by_id
            assert by_alias.module_id == entry.module_id


# test 5 (reverse lookup: module_id -> descriptor is the same resolve() call)
def test_reverse_lookup_by_module_id_returns_the_descriptor():
    entry = mr.resolve("CP01")
    assert entry is not None
    assert entry.module_id == "CP01"
    assert entry.content_key == "01_Arquitectura_y_Conectividad"
    op01 = mr.resolve("OP01")
    assert op01 is not None
    assert op01.content_key is None


# test 6
def test_unknown_key_behaves_deterministically():
    assert mr.resolve("no_existe_este_modulo") is None
    assert mr.resolve("no_existe_este_modulo") is None
    assert mr.resolve("") is None
    assert mr.resolve("CPAC01") is None  # namespace reservado, sin entradas activas
    assert mr.resolve("CM01") is None
    assert mr.resolve("CH01") is None


# test 7
def test_no_duplicate_module_id():
    ids = [e.module_id for e in mr.all_entries()]
    assert len(ids) == len(set(ids)), ids


# test 8
def test_no_duplicate_legacy_alias():
    aliases = [a for e in mr.all_entries() for a in e.legacy_aliases]
    assert len(aliases) == len(set(aliases)), aliases


# test 9
def test_no_canonical_id_collides_with_another_entrys_alias():
    ids = {e.module_id for e in mr.all_entries()}
    aliases = {a for e in mr.all_entries() for a in e.legacy_aliases}
    assert ids.isdisjoint(aliases), ids & aliases


# test 10
def test_current_visible_labels_are_preserved():
    # Reusa la fuente existente (content_service.title / activity_context.
    # module_label) — no duplica texto humano a mano en el registro.
    assert mr.resolve("CP01").visible_label == "Arquitectura y Conectividad"
    assert mr.resolve("CPA01").visible_label == "Fundamentos EA y Paradigmas"
    assert mr.resolve("OP01").visible_label == "Asignaciones"


# test 11
def test_workspace_metadata_is_correct():
    for stem in _COPILOT_MODULE_STEMS:
        assert mr.resolve(stem).workspace == "copilot"
    for stem in _COPILOT_APPENDIX_STEMS:
        assert mr.resolve(stem).workspace == "copilot"
    assert mr.resolve("assignments").workspace == "operations"


# test 12
def test_area_metadata_is_correct():
    for stem in _COPILOT_MODULE_STEMS:
        assert mr.resolve(stem).area == "methodology"
    assert mr.resolve("assignments").area == "staffing"


# test 13
def test_kind_metadata_reflects_actual_current_grouping():
    for stem in _COPILOT_MODULE_STEMS:
        assert mr.resolve(stem).kind == "module"
    for stem in _COPILOT_APPENDIX_STEMS:
        assert mr.resolve(stem).kind == "appendix"
    # OP01: sin evidencia de un consumidor que necesite distinguirlo de
    # "module" — no se inventa un tercer valor solo por prolijidad.
    assert mr.resolve("assignments").kind == "module"


# test 14
def test_content_key_exists_for_copilot_content_backed_modules():
    for stem in _COPILOT_MODULE_STEMS:
        assert mr.resolve(stem).content_key == stem
    for stem in _COPILOT_APPENDIX_STEMS:
        assert mr.resolve(stem).content_key == stem


# test 15
def test_op01_supports_content_key_none():
    assert mr.resolve("assignments").content_key is None


# test 16
def test_display_order_is_independent_metadata_not_derived_from_module_id():
    orders = [e.display_order for e in mr.all_entries()]
    # Asignado por POSICIÓN de iteración (orden de nav actual), no parseado
    # de los dígitos del module_id — secuencial y sin huecos.
    assert orders == list(range(len(orders)))
    # Ningún módulo con ID no-numérico consistente (OP01 comparte "01" con
    # CP01/CPA01) tiene el mismo display_order — si display_order viniera de
    # parsear el ID, CP01/CPA01/OP01 colisionarían en "1".
    by_id = {e.module_id: e.display_order for e in mr.all_entries()}
    assert len({by_id["CP01"], by_id["CPA01"], by_id["OP01"]}) == 3


# test 17
def test_resolver_source_never_parses_module_id_prefix():
    source = inspect.getsource(mr)
    forbidden_patterns = (
        'startswith("CP")', "startswith('CP')",
        'startswith("CPA")', "startswith('CPA')",
        'startswith("OP")', "startswith('OP')",
        "module_id[", "module_id.split",
    )
    for pattern in forbidden_patterns:
        assert pattern not in source, pattern


# test 18
def test_legacy_stems_behind_hardcoded_startswith_logic_resolve_as_expected():
    # module.html / partials/catalog_tab.html hardcodean
    # stem.startswith("01_"/"02_"/"05_") para decidir el tab de Catálogo —
    # deuda conocida, NO reemplazada en esta fase (ver HANDOFF). Esta prueba
    # ancla que esos stems exactos siguen resolviendo a los descriptores
    # canónicos esperados, para que un futuro reemplazo tenga una referencia
    # de verdad against la cual validarse.
    assert mr.resolve("01_Arquitectura_y_Conectividad").module_id == "CP01"
    assert mr.resolve("02_Seguridad_y_Gobernanza").module_id == "CP02"
    assert mr.resolve("05_Reporting_y_Consumo").module_id == "CP05"


def test_registry_covers_every_currently_discovered_copilot_stem():
    # Guardia de drift: si content_service descubre un módulo/apéndice nuevo
    # sin ID canónico asignado, este test lo señala explícitamente en vez de
    # dejarlo desaparecer en silencio del registro.
    from webapp.services import content_service
    discovered_modules = {m.stem for m in content_service.list_modules()}
    discovered_appendices = {a.stem for a in content_service.list_appendices()}
    assert discovered_modules == set(_COPILOT_MODULE_STEMS)
    assert discovered_appendices == set(_COPILOT_APPENDIX_STEMS)


def test_all_entries_count_matches_approved_inventory():
    assert len(mr.all_entries()) == len(_COPILOT_MODULE_STEMS) + len(_COPILOT_APPENDIX_STEMS) + 1
