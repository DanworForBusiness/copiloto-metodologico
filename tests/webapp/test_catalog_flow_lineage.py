"""
test_catalog_flow_lineage.py — El output declarado del flow siempre visible.

Defecto A (graph defensive): el diagrama "Linaje del flujo" debe incluir SIEMPRE
el artefacto de salida declarado (capa de consumo / cube), aunque la metadata de
lineage no traiga un upstream DIRECTO hacia él. Antes, `_catalog_flow_keep`
anclaba en "la capa más profunda CON aristas directas", así que un output cuyo
único vínculo es una asociación (p.ej. 4AM_SND_* con upstreams=[] y assoc=[dim])
desaparecía del propio diagrama de su linaje.

No se inventan aristas: si el output está desconectado, se muestra AISLADO. Las
asociaciones siguen siendo opcionales (toggle "Incluir asociaciones").

Fixtures sintéticas — no dependen de catalog_local ni del generador.
"""
from webapp.main import (
    _catalog_flow_keep,
    _catalog_lineage_svg,
    _catalog_should_default_associations,
)


def _node(name, layer, upstreams=(), assoc=(), is_factcube=False):
    return {
        "name": name, "num": 0, "slug": name.lower(), "title": name, "label": name,
        "layer": layer, "is_factcube": is_factcube,
        "upstreams": list(upstreams), "assoc_upstreams": list(assoc),
    }


def test_declared_output_kept_even_without_direct_upstream():
    # Output (cube, capa más profunda) con SOLO asociación a una dimensión: hoy se
    # cae del grafo; debe permanecer con asociaciones OFF y ON.
    nodes = [
        _node("SRC", 0),
        _node("DIM", 1, upstreams=["SRC"]),
        _node("OUT", 3, assoc=["DIM"], is_factcube=True),
    ]
    for show_assoc in (False, True):
        keep = _catalog_flow_keep(nodes, show_assoc)
        assert "OUT" in keep, f"el output declarado debe estar en keep (assoc={show_assoc})"


def test_isolated_output_is_shown_without_fabricated_edge():
    nodes = [
        _node("SRC", 0),
        _node("DIM", 1, upstreams=["SRC"]),
        _node("OUT", 3, assoc=["DIM"], is_factcube=True),
    ]
    keep = _catalog_flow_keep(nodes, show_assoc=False)
    svg = _catalog_lineage_svg(nodes, show_assoc=False, keep=keep)
    assert "OUT" in svg, "el output debe dibujarse aunque esté aislado"
    # No se fabrica upstream directo para el output (no mutación de datos).
    out = next(n for n in nodes if n["name"] == "OUT")
    assert out["upstreams"] == []
    # Con asociaciones OFF no hay arista directa inventada hacia el output.
    direct_into_out = [u for u in out["upstreams"] if u in keep]
    assert direct_into_out == []


def test_fully_connected_output_unchanged():
    # Control: flow completo (output con upstream directo) → keep = cadena completa,
    # igual que antes del fix (no duplica ni altera).
    nodes = [
        _node("SRC", 0),
        _node("DIM", 1, upstreams=["SRC"]),
        _node("FACT", 2, upstreams=["DIM"]),
        _node("OUT", 3, upstreams=["FACT"], is_factcube=True),
    ]
    keep = _catalog_flow_keep(nodes, show_assoc=False)
    assert keep == {"SRC", "DIM", "FACT", "OUT"}


def test_branching_chain_unchanged():
    # Control branching: dos fuentes convergen en el hecho; el output se alcanza normal.
    nodes = [
        _node("A", 0),
        _node("B", 0),
        _node("DIM", 1, upstreams=["A"]),
        _node("FACT", 2, upstreams=["DIM", "B"]),
        _node("OUT", 3, upstreams=["FACT"], is_factcube=True),
    ]
    keep = _catalog_flow_keep(nodes, show_assoc=False)
    assert keep == {"A", "B", "DIM", "FACT", "OUT"}


def test_should_default_associations_only_when_output_disconnected_but_associated():
    # Fallback contextual: output sin dependencia directa hacia el flow pero con una
    # asociación que lo vincula → asociaciones por defecto ON.
    disconnected = [
        _node("A", 0),
        _node("B", 1, upstreams=["A"]),
        _node("C", 2, upstreams=["B"]),
        _node("OUT", 3, assoc=["C"], is_factcube=True),
    ]
    assert _catalog_should_default_associations(disconnected) is True

    # Control A: output conectado directo → default OFF.
    connected = [
        _node("A", 0),
        _node("B", 1, upstreams=["A"]),
        _node("OUT", 3, upstreams=["B"], is_factcube=True),
    ]
    assert _catalog_should_default_associations(connected) is False

    # Control B: output aislado SIN asociación → default OFF (visible por eb22057).
    isolated = [
        _node("A", 0),
        _node("B", 1, upstreams=["A"]),
        _node("OUT", 3, is_factcube=True),
    ]
    assert _catalog_should_default_associations(isolated) is False

    # Control C: asociación lateral de texto pero output conectado directo → default OFF.
    lateral_text = [
        _node("A", 0),
        _node("TXT", 0),
        _node("DIM", 1, upstreams=["A"], assoc=["TXT"]),
        _node("OUT", 3, upstreams=["DIM"], is_factcube=True),
    ]
    assert _catalog_should_default_associations(lateral_text) is False


def test_disconnected_output_renders_association_edge_only_when_shown():
    nodes = [
        _node("A", 0),
        _node("B", 1, upstreams=["A"]),
        _node("C", 2, upstreams=["B"]),
        _node("OUT", 3, assoc=["C"], is_factcube=True),
    ]
    # Con asociaciones (el default contextual de este flow): OUT conectado por
    # asociación (arista punteada), NO como dependencia directa.
    keep_on = _catalog_flow_keep(nodes, show_assoc=True)
    svg_on = _catalog_lineage_svg(nodes, show_assoc=True, keep=keep_on)
    assert "OUT" in svg_on
    assert "ln-edge--assoc" in svg_on          # arista de asociación presente
    # Sin asociaciones: OUT sigue visible pero aislado (sin arista C→OUT).
    keep_off = _catalog_flow_keep(nodes, show_assoc=False)
    svg_off = _catalog_lineage_svg(nodes, show_assoc=False, keep=keep_off)
    assert "OUT" in svg_off
    assert "ln-edge--assoc" not in svg_off
    # Nunca se fabrica una arista directa hacia el output.
    out = next(n for n in nodes if n["name"] == "OUT")
    assert out["upstreams"] == []


def test_incomplete_output_connects_via_association_when_toggled_on():
    # Con asociaciones ON, el output aislado se conecta por su asociación REAL
    # (no inventada): la cadena conectada + el output quedan en keep.
    nodes = [
        _node("SRC", 0),
        _node("DIM", 1, upstreams=["SRC"]),
        _node("OUT", 3, assoc=["DIM"], is_factcube=True),
    ]
    keep = _catalog_flow_keep(nodes, show_assoc=True)
    assert {"SRC", "DIM", "OUT"} <= keep
