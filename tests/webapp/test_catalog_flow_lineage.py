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
from webapp.main import _catalog_flow_keep, _catalog_lineage_svg


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
