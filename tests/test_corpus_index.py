"""
test_corpus_index.py — Contrato del índice de corpus (core.corpus).

Verifica forma y consistencia del índice. La equivalencia con el parseo inline
anterior la cubre la suite de caracterización (vía _find_cross_module_section).
"""
from __future__ import annotations

from core.corpus import get_corpus_index, ModuleEntry, SectionEntry


def test_indice_no_vacio_y_bien_formado():
    modules = get_corpus_index()
    assert modules, "El índice no debería estar vacío para el pack activo."
    assert all(isinstance(m, ModuleEntry) for m in modules)

    for m in modules:
        assert m.stem and m.path
        for s in m.sections:
            assert isinstance(s, SectionEntry)
            assert s.heading, "Toda SectionEntry tiene heading no vacío."
            assert isinstance(s.section_md, str)
            assert isinstance(s.keywords, tuple)
            assert s.module == m.stem


def test_indice_estable_entre_llamadas():
    # Mismo contenido -> mismo resultado (idéntico valor; en prod además cacheado).
    assert get_corpus_index() == get_corpus_index()


def test_cubre_modulos_core_del_pack():
    stems = {m.stem for m in get_corpus_index()}
    # El pack datasphere_core expone los módulos 01..05 con section_meta.
    assert any(s.startswith("01_") for s in stems)
    assert any(s.startswith("03_") for s in stems)
