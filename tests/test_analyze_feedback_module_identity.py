"""
test_analyze_feedback_module_identity.py — MODULE IDENTIFIER CONVENTION V2,
Phase 2 (final Feedback consumer consistency corrective + final Feedback CLI
filter compatibility corrective). Prueba que el script standalone
`analyze_feedback.py` (reportes offline, fuera de la webapp) trata feedback
histórico (stem legacy) y feedback nuevo (module_id canónico) del MISMO
módulo como una sola identidad lógica — tanto al AGRUPAR (`analyze_modules`/
`analyze_sections`/`analyze_assistant`) como al FILTRAR
(`load_feedback(module_filter=...)`/`--module`).

NO cubre: analyze_academy (agrupa por academy_page, no por module — fuera
de alcance), rename de archivos, migración de YAML histórico.
"""
from __future__ import annotations

import yaml

import analyze_feedback as af


def _write(feedback_dir, name, payload):
    (feedback_dir / name).write_text(
        yaml.safe_dump(payload, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


# ── analyze_modules ──────────────────────────────────────────────────────────

# test 1: feedback histórico con stem legacy resuelve a CP01
def test_offline_report_sees_historical_legacy_module_feedback(tmp_path):
    _write(tmp_path, "a.yaml", {
        "type": "module_content", "module": "01_Arquitectura_y_Conectividad",
        "module_title": "Arquitectura y Conectividad", "rating": "up",
    })
    entries = af.load_feedback(tmp_path)
    report = af.analyze_modules(entries)
    modules = {row["module"] for row in report["by_module"]}
    assert "CP01" in modules


# test 2: feedback nuevo con module_id canónico resuelve a CP01
def test_offline_report_sees_new_canonical_module_feedback(tmp_path):
    _write(tmp_path, "b.yaml", {
        "type": "module_content", "module": "CP01",
        "module_title": "Arquitectura y Conectividad", "rating": "down",
    })
    entries = af.load_feedback(tmp_path)
    report = af.analyze_modules(entries)
    modules = {row["module"] for row in report["by_module"]}
    assert "CP01" in modules


# test 3: ambos (histórico legacy + nuevo canónico) del MISMO módulo
# agregan en UN solo bucket lógico, no en dos filas separadas.
def test_historical_and_new_module_feedback_aggregate_into_one_bucket(tmp_path):
    _write(tmp_path, "a.yaml", {
        "type": "module_content", "module": "01_Arquitectura_y_Conectividad",
        "module_title": "Arquitectura y Conectividad", "rating": "up",
    })
    _write(tmp_path, "b.yaml", {
        "type": "module_content", "module": "CP01",
        "module_title": "Arquitectura y Conectividad", "rating": "up",
    })
    entries = af.load_feedback(tmp_path)
    report = af.analyze_modules(entries)
    cp01_rows = [row for row in report["by_module"] if row["module"] == "CP01"]
    assert len(cp01_rows) == 1


# test 4: los conteos (up/down) se suman correctamente, sin dividirse
# entre las dos filas que existían antes de este corrective.
def test_historical_and_new_module_feedback_counts_are_summed_not_split(tmp_path):
    _write(tmp_path, "a.yaml", {
        "type": "module_content", "module": "01_Arquitectura_y_Conectividad",
        "module_title": "Arquitectura y Conectividad", "rating": "up",
    })
    _write(tmp_path, "b.yaml", {
        "type": "module_content", "module": "01_Arquitectura_y_Conectividad",
        "module_title": "Arquitectura y Conectividad", "rating": "up",
    })
    _write(tmp_path, "c.yaml", {
        "type": "module_content", "module": "CP01",
        "module_title": "Arquitectura y Conectividad", "rating": "down",
    })
    entries = af.load_feedback(tmp_path)
    report = af.analyze_modules(entries)
    cp01_rows = [row for row in report["by_module"] if row["module"] == "CP01"]
    assert len(cp01_rows) == 1
    assert cp01_rows[0]["up"] == 2
    assert cp01_rows[0]["down"] == 1


# test 5: otra correspondencia legacy↔canónico (CP04) resuelve igual.
def test_another_legacy_to_canonical_mapping_resolves_correctly(tmp_path):
    _write(tmp_path, "a.yaml", {
        "type": "module_content", "module": "04_Modelado_y_Desarrollo",
        "module_title": "Modelado y Desarrollo", "rating": "up",
    })
    _write(tmp_path, "b.yaml", {
        "type": "module_content", "module": "CP04",
        "module_title": "Modelado y Desarrollo", "rating": "up",
    })
    entries = af.load_feedback(tmp_path)
    report = af.analyze_modules(entries)
    cp04_rows = [row for row in report["by_module"] if row["module"] == "CP04"]
    assert len(cp04_rows) == 1
    assert cp04_rows[0]["up"] == 2


# test 6: un valor de módulo desconocido queda sin cambios (lossless),
# nunca se fabrica un module_id ni se descarta el registro.
def test_unknown_module_value_stays_lossless_in_offline_report(tmp_path):
    _write(tmp_path, "a.yaml", {
        "type": "module_content", "module": "un_modulo_que_no_existe",
        "module_title": "???", "rating": "up",
    })
    entries = af.load_feedback(tmp_path)
    report = af.analyze_modules(entries)
    modules = {row["module"] for row in report["by_module"]}
    assert modules == {"un_modulo_que_no_existe"}


# ── analyze_sections / analyze_assistant también usan el mismo seam ─────────

def test_offline_section_report_aggregates_legacy_and_canonical_together(tmp_path):
    _write(tmp_path, "a.yaml", {
        "type": "section_content", "module": "01_Arquitectura_y_Conectividad",
        "module_title": "Arquitectura y Conectividad",
        "section_slug": "espacios-transportes", "section_title": "Espacios y transportes",
        "rating": "up",
    })
    _write(tmp_path, "b.yaml", {
        "type": "section_content", "module": "CP01",
        "module_title": "Arquitectura y Conectividad",
        "section_slug": "espacios-transportes", "section_title": "Espacios y transportes",
        "rating": "up",
    })
    entries = af.load_feedback(tmp_path)
    report = af.analyze_sections(entries)
    cp01_rows = [row for row in report["by_section"] if row["module"] == "CP01"]
    assert len(cp01_rows) == 1
    assert cp01_rows[0]["up"] == 2


def test_offline_assistant_report_aggregates_legacy_and_canonical_together(tmp_path):
    _write(tmp_path, "a.yaml", {
        "type": "assistant_response", "module": "01_Arquitectura_y_Conectividad",
        "action": "explicame", "topic": "x", "rating": "up",
    })
    _write(tmp_path, "b.yaml", {
        "type": "assistant_response", "module": "CP01",
        "action": "explicame", "topic": "x", "rating": "up",
    })
    entries = af.load_feedback(tmp_path)
    report = af.analyze_assistant(entries)
    assert report["by_module"]["CP01"]["up"] == 2
    assert "01_Arquitectura_y_Conectividad" not in report["by_module"]


# ── Inmutabilidad de la fuente ───────────────────────────────────────────────

# test 9: leer y generar reportes NUNCA reescribe el YAML fuente.
def test_reading_and_reporting_never_rewrites_source_yaml(tmp_path):
    payload = {
        "type": "module_content", "module": "01_Arquitectura_y_Conectividad",
        "module_title": "Arquitectura y Conectividad", "rating": "up",
    }
    _write(tmp_path, "a.yaml", payload)
    before = (tmp_path / "a.yaml").read_text(encoding="utf-8")

    entries = af.load_feedback(tmp_path)
    af.analyze_modules(entries)
    af.analyze_sections(entries)
    af.analyze_assistant(entries)

    after = (tmp_path / "a.yaml").read_text(encoding="utf-8")
    assert after == before


# ── El mapeo canónico sigue centralizado en module_registry ─────────────────

# test 8 (a nivel del helper local): el script no duplica el mapeo, solo
# reimplementa la orquestación mínima de resolve().
def test_canonical_module_helper_delegates_to_module_registry(monkeypatch):
    from webapp.services import module_registry
    calls = []
    original_resolve = module_registry.resolve

    def _spy(key):
        calls.append(key)
        return original_resolve(key)

    monkeypatch.setattr(module_registry, "resolve", _spy)
    result = af._canonical_module("01_Arquitectura_y_Conectividad")
    assert result == "CP01"
    assert calls == ["01_Arquitectura_y_Conectividad"]


def test_canonical_module_helper_is_lossless_for_empty_value():
    assert af._canonical_module("") == ""
    assert af._canonical_module("?") == "?"


# ── --module / load_feedback(module_filter=...) — filtro canónico ───────────
# MODULE IDENTIFIER CONVENTION V2 — Fase 2 (final Feedback CLI filter
# compatibility corrective): --module acepta stem legacy o module_id
# canónico indistintamente — mismo criterio ya validado arriba para el
# agrupamiento de reportes, ahora aplicado al filtro.

def _seed_cp01_pair(tmp_path):
    _write(tmp_path, "legacy.yaml", {
        "type": "module_content", "module": "01_Arquitectura_y_Conectividad",
        "module_title": "Arquitectura y Conectividad", "rating": "up",
    })
    _write(tmp_path, "canonical.yaml", {
        "type": "module_content", "module": "CP01",
        "module_title": "Arquitectura y Conectividad", "rating": "down",
    })


# test: filtrar por el stem legacy devuelve AMBOS registros (histórico +
# nuevo canónico), no solo el histórico.
def test_module_filter_with_legacy_stem_returns_both_historical_and_canonical(tmp_path):
    _seed_cp01_pair(tmp_path)
    entries = af.load_feedback(tmp_path, module_filter="01_Arquitectura_y_Conectividad")
    assert len(entries) == 2
    assert {e["module"] for e in entries} == {"01_Arquitectura_y_Conectividad", "CP01"}


# test: filtrar por el module_id canónico devuelve AMBOS registros también.
def test_module_filter_with_canonical_id_returns_both_historical_and_canonical(tmp_path):
    _seed_cp01_pair(tmp_path)
    entries = af.load_feedback(tmp_path, module_filter="CP01")
    assert len(entries) == 2
    assert {e["module"] for e in entries} == {"01_Arquitectura_y_Conectividad", "CP01"}


# test: ambas invocaciones (legacy y canónico) devuelven el MISMO conjunto
# lógico de registros — no solo el mismo count, los mismos _file.
def test_module_filter_legacy_and_canonical_return_identical_record_set(tmp_path):
    _seed_cp01_pair(tmp_path)
    by_legacy = af.load_feedback(tmp_path, module_filter="01_Arquitectura_y_Conectividad")
    by_canonical = af.load_feedback(tmp_path, module_filter="CP01")
    assert {e["_file"] for e in by_legacy} == {e["_file"] for e in by_canonical}


# test: otra correspondencia legacy↔canónico (CP04) se comporta igual.
def test_module_filter_another_mapping_cp04_behaves_identically(tmp_path):
    _write(tmp_path, "legacy.yaml", {
        "type": "module_content", "module": "04_Modelado_y_Desarrollo",
        "module_title": "Modelado y Desarrollo", "rating": "up",
    })
    _write(tmp_path, "canonical.yaml", {
        "type": "module_content", "module": "CP04",
        "module_title": "Modelado y Desarrollo", "rating": "up",
    })
    by_legacy = af.load_feedback(tmp_path, module_filter="04_Modelado_y_Desarrollo")
    by_canonical = af.load_feedback(tmp_path, module_filter="CP04")
    assert len(by_legacy) == len(by_canonical) == 2


# test: un valor de filtro desconocido sigue matcheando el mismo valor
# desconocido persistido (pass-through exacto, lossless).
def test_module_filter_unknown_value_matches_identical_unknown_persisted_value(tmp_path):
    _write(tmp_path, "a.yaml", {
        "type": "module_content", "module": "custom_unknown",
        "module_title": "???", "rating": "up",
    })
    entries = af.load_feedback(tmp_path, module_filter="custom_unknown")
    assert len(entries) == 1


# test: un valor de filtro desconocido NO matchea registros no relacionados
# (ni otros desconocidos distintos, ni valores legacy/canónicos resolubles).
def test_module_filter_unknown_value_does_not_match_unrelated_records(tmp_path):
    _seed_cp01_pair(tmp_path)
    _write(tmp_path, "other_unknown.yaml", {
        "type": "module_content", "module": "otro_valor_desconocido",
        "module_title": "???", "rating": "up",
    })
    entries = af.load_feedback(tmp_path, module_filter="custom_unknown")
    assert entries == []


# test: el histórico lowercase-typo conocido (variante real observada en
# datos de producción) NO se adivina como CP01 — sigue sin resolver, y por
# lo tanto NO matchea --module CP01 ni --module 01_Arquitectura_y_
# Conectividad (comparación exacta, sin case folding adicional).
def test_module_filter_does_not_guess_lowercase_typo_variant_into_canonical(tmp_path):
    _write(tmp_path, "typo.yaml", {
        "type": "module_content", "module": "01_arquitectura_y_conectividad",
        "module_title": "Arquitectura y Conectividad", "rating": "up",
    })
    assert af.load_feedback(tmp_path, module_filter="CP01") == []
    assert af.load_feedback(tmp_path, module_filter="01_Arquitectura_y_Conectividad") == []
    # pero sigue siendo descubrible por su propio valor crudo, sin cambios.
    entries = af.load_feedback(tmp_path, module_filter="01_arquitectura_y_conectividad")
    assert len(entries) == 1


# test: filtrar con el YAML fuente nunca lo reescribe.
def test_module_filter_never_rewrites_source_yaml(tmp_path):
    _seed_cp01_pair(tmp_path)
    before = {p.name: p.read_text(encoding="utf-8") for p in tmp_path.glob("*.yaml")}
    af.load_feedback(tmp_path, module_filter="CP01")
    af.load_feedback(tmp_path, module_filter="01_Arquitectura_y_Conectividad")
    after = {p.name: p.read_text(encoding="utf-8") for p in tmp_path.glob("*.yaml")}
    assert after == before
