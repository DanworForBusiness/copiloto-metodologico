"""Higiene de templates: ningún .html debe empezar con BOM UTF-8.

Un BOM (U+FEFF) al inicio de un partial incluido con Jinja se renderiza como un
nodo de texto NO colapsable dentro del panel, generando una línea (line box) con
el line-height del contenedor → un "gap falso" antes del primer bloque. Ya pasó
con catalog_tab.html / catalog_data_product_content.html (editor que guarda con
BOM en Windows). Este test lo detecta en la raíz para que no vuelva a filtrarse.
"""
from pathlib import Path

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "webapp" / "templates"


def test_ningun_template_html_empieza_con_bom():
    offenders = [
        str(p.relative_to(_TEMPLATES_DIR))
        for p in _TEMPLATES_DIR.rglob("*.html")
        if p.read_bytes()[:3] == b"\xef\xbb\xbf"
    ]
    assert not offenders, (
        "Templates con BOM UTF-8 (generan un gap falso al incluirse; guardalos como "
        f"UTF-8 sin BOM): {offenders}"
    )
