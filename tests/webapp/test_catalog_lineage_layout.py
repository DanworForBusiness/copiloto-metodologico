"""
test_catalog_lineage_layout.py — El diagrama de linaje se mantiene legible sin
overflow horizontal cuando los nombres/cadenas son largos.

Contrato UX (no funcional): los nombres técnicos largos se envuelven en ≤2 líneas
(nombre COMPLETO, sin truncado agresivo), el SVG es responsive (viewBox + la hoja
de estilos lo escala con max-width) y no se fuerza un ancho por nodo que garantice
overflow. Fixtures sintéticas.
"""
import re
from pathlib import Path

from webapp.main import _wrap_label, _catalog_lineage_svg


def _node(name, layer, upstreams=()):
    return {"name": name, "num": 0, "slug": name.lower(), "title": name, "label": name,
            "layer": layer, "is_factcube": False,
            "upstreams": list(upstreams), "assoc_upstreams": []}


def test_wrap_label_short_stays_one_line():
    assert _wrap_label("2VD_MD_PRODUCT", max_chars=20) == ["2VD_MD_PRODUCT"]


def test_wrap_label_long_splits_two_lines_preserving_full_name():
    label = "1TR_SAP_SND_PRODUCTSALESDELIVERY_Delta"
    lines = _wrap_label(label)
    assert len(lines) == 2
    assert "".join(lines) == label          # nombre completo preservado (sin pérdida)
    assert all(len(ln) < len(label) for ln in lines)


def test_wrap_label_no_underscore_hard_split():
    lines = _wrap_label("A" * 40)
    assert len(lines) == 2 and "".join(lines) == "A" * 40


def test_lineage_svg_wraps_long_names_and_is_responsive():
    # Cadena real de 5 nodos con nombres largos (caso PRODUCTSALESDELIVERY).
    chain = [
        "1TR_SAP_SND_PRODUCTSALESDELIVERY_Delta",
        "1TR_SAP_SND_PRODUCTSALESDELIVERY",
        "2VR_SND_PRODUCTSALESDELIVERY",
        "3VF_SND_PRODUCTSALESDELIVERY",
        "4AM_SND_PRODUCTSALESDELIVERY",
    ]
    nodes = [_node(chain[0], 0)]
    for i in range(1, len(chain)):
        nodes.append(_node(chain[i], i, upstreams=[chain[i - 1]]))
    keep = {n["name"] for n in nodes}
    svg = _catalog_lineage_svg(nodes, show_assoc=False, keep=keep)

    assert "viewBox=" in svg                 # responsive (escala con max-width en CSS)
    assert "<tspan" in svg                   # nombres largos envueltos
    assert "…" not in svg                    # sin truncado agresivo
    # nombre completo preservado (ambas mitades presentes) para el output
    for half in _wrap_label("4AM_SND_PRODUCTSALESDELIVERY"):
        assert half in svg
    # ancho por nodo acotado (ningún rect fuerza > WMAX=260)
    widths = [int(w) for w in re.findall(r'class="ln-node[^"]*"[^>]*width="(\d+)"', svg)]
    assert widths and max(widths) <= 260


def test_lineage_css_is_responsive():
    css = Path(__file__).resolve().parents[2] / "webapp" / "static" / "tools.css"
    text = css.read_text(encoding="utf-8")
    m = re.search(r"\.lineage-svg\s*\{([^}]*)\}", text)
    assert m and "max-width" in m.group(1) and "100%" in m.group(1)
