"""Regresión: una consulta que nombra una LÍNEA DE NEGOCIO («¿qué catálogos de Ventas tenemos?») la responde la clasificación EFECTIVA del Catálogo, nunca el emparejamiento por rótulo de dominio legado.

Caso real: el dominio legado «Ventas» agrupa entradas con líneas de negocio efectivas DISTINTAS (Ventas y Sin clasificar). La ruta de disponibilidad (`best_domain_match`) devolvía la de mejor mirada Danwor
del dominio —una SIN clasificar— como «ya tenemos esto implementado: Cliente · Ventas», contradiciendo el Catálogo. Fixtures SINTÉTICAS: el dominio legado «Ventas» con una entrada sin clasificar de mejor
veredicto (consumo PL_RESP_COM) y otras clasificadas como Ventas."""
from __future__ import annotations

import re
import types

import pytest

import webapp.main as main
from tools.catalog import catalog_service as cs
from webapp.services import assistant_navigation as nav
from webapp.services import assistant_routing, catalog_reads
from webapp.services import domain_classification_service as dcs
from webapp.services.content_service import load_module

PACK = "datasphere_core"
HOST = "04_Modelado_y_Desarrollo"
UNCLASSIFIED_TWIN = "PL_RESP_COM"                  # consumo de la entrada «Ventas» (legado) que NO tiene clasificación efectiva y es la de mejor veredicto


def _flow(root, cliente, dom_dir, dominio, consumo, veredicto, lob):
    d = root / cliente / dom_dir
    d.mkdir(parents=True)
    lob_line = f"  linea_de_negocio: {lob}\n" if lob else ""
    (d / f"flujo_{dom_dir}.md").write_text(
        f"---\nauto:\n  tipo: flujo\n  dominio: {dominio}\n  artefactos:\n  - 4AM_{consumo}\nmanual:\n  cliente: {cliente}\n  veredicto: {veredicto}\n{lob_line}---\nCuerpo.\n", encoding="utf-8")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    _flow(tmp_path, "Alfa", "V0", "Ventas", UNCLASSIFIED_TWIN, "plena", None)           # mejor veredicto del dominio legado «Ventas»… pero SIN clasificar
    for i in range(1, 8):
        _flow(tmp_path, "Alfa" if i % 2 else "Beta", f"V{i}", "Ventas", f"BASE_{i}", "buena", "SALES")
    _flow(tmp_path, "Beta", "F1", "Finanzas", "GASTOS", "buena", "FINANCE")
    _flow(tmp_path, "Beta", "D1", "Despachos", "DESPACHOS", "plena", None)               # dominio legado que NO es una línea de negocio
    entries = cs.load_catalog(str(tmp_path))
    monkeypatch.setattr(assistant_routing, "_catalog_module", lambda: (cs, str(tmp_path)))     # la ruta de disponibilidad legada carga el catálogo por su cuenta (mismo módulo y directorio)
    monkeypatch.setattr(catalog_reads, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(main, "_catalog_entries", lambda: (cs, entries))
    monkeypatch.setattr(catalog_reads, "_catalog_data_products_entries", lambda: (cs, [types.SimpleNamespace(product_id="DP", title="X")]))
    return entries


def _route(question, stem=HOST):
    return assistant_routing.AssistantRoutingService().route(load_module(stem, PACK), question, PACK)


def _listed(route) -> set[str]:
    return set(re.findall(r"Consumo: ([A-Za-z0-9_]+)", route.message))


def _effective(entries) -> dict[str, str]:
    """consumo → grupo efectivo, por el contrato real (`dcs.effective_lob_by_entry`)."""
    from webapp.services import assessment_service
    eff = dcs.effective_lob_by_entry(entries, assessment_service.statuses_for_entries(entries))
    return {e.consumo[0]: eff[e.entry_id]["group"] for e in entries}


def test_the_fixture_reproduces_the_contradiction_the_legacy_route_used_to_make(world):
    """El dominio legado «Ventas» mezcla líneas efectivas distintas, y la entrada de mejor veredicto NO está clasificada como Ventas."""
    groups = _effective(world)
    assert groups[UNCLASSIFIED_TWIN] == dcs.UNCLASSIFIED and groups["BASE_1"] == "SALES"
    best = cs.best_domain_match("¿Qué catálogos de Ventas tenemos?", world)
    assert best is not None and best.consumo == [UNCLASSIFIED_TWIN]                    # lo que la ruta legada presentaba como «Ventas»


@pytest.mark.parametrize("question", ["¿Qué catálogos de Ventas tenemos?", "¿Qué modelos de Ventas tenemos?", "¿Tenemos algo de Ventas?", "Modelos de Ventas", "catálogos de Ventas"])
def test_questions_naming_a_line_of_business_list_only_entries_effectively_classified_there(world, question):
    r = _route(question)
    assert r is not None and "en la línea de negocio **Ventas** (clasificación efectiva)" in r.message
    assert "ya tenemos esto implementado" not in r.message and not any(a.label == "Ver en el catálogo" for a in r.actions)      # no la ruta de disponibilidad legada
    groups = _effective(world)
    listed = _listed(r)
    assert listed and all(groups[c] == "SALES" for c in listed)                          # TODO lo atribuido a Ventas pertenece efectivamente a Ventas
    assert UNCLASSIFIED_TWIN not in listed and UNCLASSIFIED_TWIN not in r.message         # P&L x Responsable Comercial: sin clasificar → no aparece en Ventas
    assert f"Hay **{sum(1 for g in groups.values() if g == 'SALES')} entradas navegables**" in r.message


def test_all_three_phrasings_return_the_same_universe(world):
    sets = {q: _listed(_route(q)) for q in ("¿Qué catálogos de Ventas tenemos?", "¿Qué modelos de Ventas tenemos?", "¿Tenemos algo de Ventas?")}
    assert len({frozenset(v) for v in sets.values()}) == 1


@pytest.mark.parametrize("label", [o["label"] for o in dcs.lob_options() if o["id"] != dcs.UNCLASSIFIED])
def test_for_every_line_of_business_only_its_own_entries_are_attributed(world, label):
    """Propiedad general (sin hardcodear una línea ni un modelo): lo listado bajo cualquier línea tiene esa clasificación efectiva."""
    lob_id = next(o["id"] for o in dcs.lob_options() if o["label"] == label)
    groups = _effective(world)
    for q in (f"¿Qué catálogos de {label} tenemos?", f"¿Qué modelos de {label} tenemos?"):
        r = nav._catalog_inventory(q, PACK, HOST)
        assert r is not None
        assert all(groups[c] == lob_id for c in _listed(r)), q


def test_the_unclassified_question_returns_only_unclassified_entries(world):
    r = _route("¿Qué modelos sin clasificar hay?")
    groups = _effective(world)
    assert "**sin clasificar**" in r.message and _listed(r) == {c for c, g in groups.items() if g == dcs.UNCLASSIFIED}
    assert UNCLASSIFIED_TWIN in _listed(r)                                                # y ahí SÍ está: su clasificación efectiva es «Sin clasificar»


def test_the_broad_summary_is_unchanged(world):
    r = _route("¿Qué dominios hay disponibles?")
    groups = _effective(world)
    assert r.message.startswith(f"El Catálogo contiene **{len(groups)} entradas navegables**")
    assert f"Ventas: {sum(1 for g in groups.values() if g == 'SALES')}" in r.message and "**Sin clasificar**: 2" in r.message and len(r.actions) == 1


def test_an_ambiguous_line_of_business_question_cannot_fall_back_to_the_legacy_domain_match(world):
    """Dos líneas nombradas: el resolver gobernado no elige por el usuario y la ruta legada tampoco puede contestar por rótulo de dominio."""
    for q in ("¿Qué catálogos de Ventas y Finanzas tenemos?", "¿Hay algo de Ventas o Finanzas?"):
        assert nav.names_line_of_business(q)
        r = _route(q)
        assert r is None or ("ya tenemos esto implementado" not in r.message and not any(a.label == "Ver en el catálogo" for a in r.actions)), q


def test_the_legacy_availability_route_still_serves_questions_that_do_not_name_a_line_of_business(world):
    r = _route("¿Tenemos algo de Despachos?")
    assert r is not None and "ya tenemos esto implementado" in r.message and "Despachos" in r.message and any(a.label == "Ver en el catálogo" for a in r.actions)
    assert not nav.names_line_of_business("¿Tenemos algo de Despachos?")


@pytest.mark.parametrize("question", [
    "¿Qué es la clasificación por línea de negocio?", "¿Cómo se clasifican los modelos por línea de negocio?", "¿Cómo diseñar un catálogo de Ventas?",
    "¿Qué es una arquitectura por capas?", "¿Cómo definir los dominios de negocio de Ventas?", "buenas prácticas para modelos de Ventas", "para qué sirve la línea de negocio Ventas"])
def test_conceptual_architecture_and_classification_questions_keep_working(world, question):
    assert nav._catalog_inventory(question, PACK, HOST) is None
    r = _route(question)
    assert r is None or ("entradas navegables" not in r.message and "ya tenemos esto implementado" not in r.message)


def test_client_report_navigation_and_other_catalogs_are_unchanged(world):
    r = _route("Ver informe de cliente para Alfa")
    assert r.actions[0].label == "Ver informe" and r.actions[0].embed
    for q in ("¿Qué catálogos de conexiones hay?", "ver el catálogo de APIs", "qué catálogos de seguridad hay"):
        assert nav._catalog_inventory(q, PACK, HOST) is None, q
    assert _route("¿Qué catálogos disponibles hay?").actions[0].label == "Abrir Catálogo"
