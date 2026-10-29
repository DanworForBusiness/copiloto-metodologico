"""
test_module_registry.py — MODULE IDENTIFIER CONVENTION V2, Phase 1 (registry
foundation). Prueba el registro/resolver canónico puramente ADITIVO — ningún
test de este archivo toca rutas, Activity, Feedback, nav Jinja ni contenido
físico (esos consumidores NO se migran en esta fase, ver HANDOFF).
"""
from __future__ import annotations

import inspect
import re

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


# test 3b — Commercial Hub Workspace Foundation (Batch 0): CMxx se activa,
# MISMO criterio exacto que OP01 arriba.
def test_cm01_commercial_entry_registered():
    entry = mr.resolve("crm")
    assert entry is not None
    assert entry.module_id == "CM01"
    assert entry.workspace == "commercial"
    assert entry.area == "crm"
    assert entry.content_key is None
    by_id = mr.resolve("CM01")
    assert by_id is entry or by_id == entry


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
    # CMxx (Comercial) se activó en Commercial Hub Workspace Foundation
    # (Batch 0) — CM01 YA es una entrada real (ver test_cm01_commercial_entry_registered
    # más abajo); CHxx (Capital Humano) sigue reservado, sin entradas.
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
    # Copiloto reusa la fuente existente (content_service.title) — no
    # duplica texto humano a mano. OP01 es un literal propio del registro
    # (ver dirección de dependencia) que hoy coincide con
    # activity_context.MODULE_LABELS — duplicación temporal deliberada.
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


# final display-order semantics corrective — display_order means "orden
# DENTRO de la colección navegable actual del propio descriptor", NUNCA una
# secuencia Hub-wide. Copiloto principal, apéndices de Copiloto y Operations
# son tres colecciones separadas hoy — comparten legítimamente los mismos
# números (display_order=1 en las tres) sin colisionar, porque module_id/
# workspace/kind ya distinguen cada entrada.

# tests 1/2/3
def test_display_order_is_scoped_per_collection_not_global():
    cp01 = mr.resolve("CP01")
    cpa01 = mr.resolve("CPA01")
    op01 = mr.resolve("OP01")
    assert cp01.display_order == 1
    assert cpa01.display_order == 1
    assert op01.display_order == 1
    # Comparten el número "1" en sus propias colecciones — no es una colisión:
    # module_id/workspace/kind ya los distinguen sin ambigüedad.
    assert {cp01.module_id, cpa01.module_id, op01.module_id} == {"CP01", "CPA01", "OP01"}
    assert {cp01.kind, cpa01.kind} == {"module", "appendix"}
    assert {cp01.workspace, op01.workspace} == {"copilot", "operations"}


# test 4
def test_copilot_main_modules_have_explicit_current_display_order():
    expected = {"CP00": 0, "CP01": 1, "CP02": 2, "CP03": 3, "CP04": 4, "CP05": 5}
    for module_id, order in expected.items():
        assert mr.resolve(module_id).display_order == order, module_id


# test 5
def test_copilot_appendices_have_explicit_current_display_order():
    expected = {"CPA01": 1, "CPA02": 2, "CPA03": 3, "CPA04": 4, "CPA05": 5, "CPA06": 6}
    for module_id, order in expected.items():
        assert mr.resolve(module_id).display_order == order, module_id
    # Los valores NO son los números de frontmatter por entrada ("10","08",
    # "09","07","06" para A01..A05) — esa inconsistencia de presentación es
    # un problema separado, documentado, NO corregido en esta fase.
    frontmatter_numbers = {"CPA01": 10, "CPA02": 8, "CPA03": 9, "CPA04": 7, "CPA05": 6}
    for module_id, frontmatter_number in frontmatter_numbers.items():
        assert mr.resolve(module_id).display_order != frontmatter_number, module_id


# test 6
def test_op01_has_explicit_display_order_one():
    assert mr.resolve("OP01").display_order == 1


# test 7
def test_source_never_derives_display_order_from_module_id():
    source = inspect.getsource(mr)
    forbidden_patterns = ("module_id[", "module_id.split", "int(module_id")
    for pattern in forbidden_patterns:
        assert pattern not in source, pattern


# test 8
def test_source_never_derives_display_order_from_content_key_or_filename():
    source = inspect.getsource(mr)
    forbidden_patterns = (
        "content_key[", "content_key.split", "int(content_key",
        "m.stem.split", "a.stem.split", "m.stem[", "a.stem[",
    )
    for pattern in forbidden_patterns:
        assert pattern not in source, pattern


# test 9
def test_registry_construction_never_uses_enumeration_position_for_display_order():
    source = inspect.getsource(mr)
    assert "enumerate(" not in source
    assert re.search(r"\border\s*=\s*0\b", source) is None
    assert re.search(r"\border\s*\+=", source) is None


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
    # "+ 4" = OP01 (Operations/Asignaciones) + CM01 (Comercial/CRM, Batch 0) +
    # CM02 (Comercial/Estrategia, Commercial Strategy — Phase 1) + CM03
    # (Comercial/Presentaciones, V1) — las cuatro únicas entradas sin
    # Markdown backing, fuera de las colecciones de Copiloto/apéndices.
    assert len(mr.all_entries()) == len(_COPILOT_MODULE_STEMS) + len(_COPILOT_APPENDIX_STEMS) + 4
