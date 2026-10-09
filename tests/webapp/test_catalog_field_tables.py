"""
test_catalog_field_tables.py — las tablas de campos de los artefactos del Catálogo (todas las capas) se muestran plegadas.

Contrato: cada tabla `Campos (N)` y, en los modelos analíticos, `Medidas (N)` / `Atributos (N)` queda dentro del plegable EXISTENTE del Hub (`.content-collapsible`, <details>/<summary> nativos),
COLAPSADO por defecto, con el conteo REAL de filas en el encabezado; cada una abre/cierra de forma independiente; la tabla queda byte a byte igual y ya renderizada (abrir no pide nada: sin hx-*,
sin scripts); solo presentación (sin cambios en Evidence/Judgment/APIs/persistencia). Fixtures sintéticas.
"""
from __future__ import annotations

import re
from pathlib import Path

import webapp.main as main
from tools.catalog import catalog_service as cs
from webapp.services import content_service as content

REPO = Path(__file__).resolve().parents[2]


def _table(names):
    rows = "".join(f'<tr>\n<td><code>{n}</code></td>\n<td>Desc {n}</td>\n<td>String</td>\n<td style="text-align: center;">🔑</td>\n</tr>\n' for n in names)
    return ('<table>\n<thead>\n<tr>\n<th>Campo</th>\n<th>Descripción</th>\n<th>Tipo</th>\n<th style="text-align: center;">Clave</th>\n</tr>\n</thead>\n<tbody>\n' + rows + '</tbody>\n</table>')


T3, T2, T5 = _table(["A", "B", "C"]), _table(["X", "Y"]), _table(list("12345"))


def _doc(*parts):
    return "\n".join(parts)


def test_each_field_table_is_wrapped_closed_with_its_real_row_count_and_the_table_is_byte_identical():
    html = _doc("<p>intro</p>", '<h4 id="campos-3">Campos (3)</h4>', T3, "<p>fin</p>")
    out = content.collapse_field_tables(html)
    assert out.count("<details") == 1 and "<h4" not in out
    assert 'class="content-collapsible" id="campos-3">' in out and "<details class=\"content-collapsible\" id=\"campos-3\" open" not in out and " open" not in out.split("<summary")[0]
    assert '<summary class="content-collapsible__summary">Campos (3)</summary><div class="content-collapsible__body">' + T3 + "</div></details>" in out   # tabla EXACTA dentro
    assert out.startswith("<p>intro</p>") and out.endswith("<p>fin</p>")


def test_the_header_count_is_the_real_number_of_rows_not_the_text_of_the_heading():
    out = content.collapse_field_tables(_doc('<h4 id="campos-9">Campos (9)</h4>', T2))
    assert "<summary class=\"content-collapsible__summary\">Campos (2)</summary>" in out


def test_campos_medidas_and_atributos_each_get_their_own_independent_collapsible():
    html = _doc('<h4 id="campos-3">Campos (3)</h4>', T3, '<h4 id="medidas-2">Medidas (2)</h4>', T2, '<h4 id="atributos-5">Atributos (5)</h4>', T5)
    out = content.collapse_field_tables(html)
    assert out.count("<details") == 3 and out.count("</details>") == 3 and out.count(" open") == 0
    assert re.findall(r'<summary class="content-collapsible__summary">([^<]+)</summary>', out) == ["Campos (3)", "Medidas (2)", "Atributos (5)"]
    blocks = re.findall(r"<details.*?</details>", out, re.S)
    assert len(blocks) == 3 and all(b.count("<details") == 1 for b in blocks)                       # sin anidar: abrir una no abre las otras


def test_other_headings_and_tables_are_left_untouched_and_the_pass_is_idempotent():
    meta = '<h4 id="propiedades">Propiedades</h4>\n' + _table(["P"])
    html = _doc(meta, '<h4 id="campos-3">Campos (3)</h4>', T3)
    out = content.collapse_field_tables(html)
    assert out.startswith(meta) and out.count("<details") == 1
    assert content.collapse_field_tables(out) == out
    assert content.collapse_field_tables("") == "" and content.collapse_field_tables("<p>sin tablas</p>") == "<p>sin tablas</p>"


def test_opening_needs_no_request_the_table_is_already_rendered_and_nothing_is_wired():
    out = content.collapse_field_tables(_doc('<h4 id="campos-3">Campos (3)</h4>', T3))
    assert not re.search(r"\b(hx-|x-data|@click|onclick|<script)", out)                            # nativo: sin htmx, sin Alpine, sin JS
    assert "<table>" in out                                                                        # ya está en el DOM


def test_it_reuses_the_existing_hub_collapsible_component_and_does_not_add_new_css():
    css = (REPO / "webapp/static/content.css").read_text(encoding="utf-8")
    assert ".content-collapsible {" in css and ".content-collapsible__summary {" in css and ".content-collapsible__body {" in css
    assert "catalog-field" not in css and "catalog-field" not in (REPO / "webapp/static/tools.css").read_text(encoding="utf-8")
    # el plegable de contenido existente sigue generando EXACTAMENTE el mismo markup (misma función de apertura)
    legacy = content._collapsible_html("<!--DWCOLL:SHUT:Ver más-->x<!--DWCOLL:/-->")
    assert '<details class="content-collapsible"><summary class="content-collapsible__summary">Ver más</summary><div class="content-collapsible__body">' in legacy
    assert content._collapsible_html("<!--DWCOLL:OPEN:T-->x<!--DWCOLL:/-->").startswith('<details class="content-collapsible" open>')


# ── integración: el detalle del flujo (todas las capas) ─────────────────────
def _vista(root, name, capa, body):
    (root / "vistas").mkdir(parents=True, exist_ok=True)
    (root / "vistas" / f"{name}.md").write_text(f"---\nauto:\n  label: {name}\n  capa: {capa}\n---\n# {name}\n\n{body}\n", encoding="utf-8")


def test_the_flow_detail_collapses_the_field_tables_of_every_layer(tmp_path):
    d = tmp_path / "Cli" / "Dom"
    d.mkdir(parents=True)
    (d / "flujo_Dom.md").write_text("---\nauto:\n  tipo: flujo\n  dominio: Dom\n  artefactos:\n  - 1TR_X\n  - 3VF_X\n  - 4AM_X\nmanual:\n  cliente: Cli\n---\nC\n", encoding="utf-8")
    fields = lambda n: "| Campo | Descripción | Tipo | Long. | Clave | Relación |\n|---|---|---|---|:--:|---|\n" + "\n".join(f"| `F{i}` | d | String | 4 |  |  |" for i in range(n))     # noqa: E731
    _vista(d, "1TR_X", "Ingesta", f"| Propiedad | Valor |\n|---|---|\n| Capa | Ingesta |\n\n#### Campos (4)\n\n{fields(4)}")
    _vista(d, "3VF_X", "Propagación", f"#### Campos (3)\n\n{fields(3)}")
    _vista(d, "4AM_X", "Publicado", f"#### Medidas (2)\n\n{fields(2)}\n\n#### Atributos (5)\n\n{fields(5)}")
    entry = cs.load_catalog(str(tmp_path))[0]
    sections, *_ = main._catalog_flow_view(entry, None, "datasphere_core")
    html = "".join(s.html for s in sections)
    assert re.findall(r'<summary class="content-collapsible__summary">((?:Campos|Medidas|Atributos) \(\d+\))</summary>', html) == ["Campos (4)", "Campos (3)", "Medidas (2)", "Atributos (5)"]
    assert not re.search(r'<h4 id="[^"]*">(?:Campos|Medidas|Atributos) \(', html) and html.count("<details") >= 4
    assert " open" not in "".join(re.findall(r'<details class="content-collapsible"[^>]*>', html))                    # todas colapsadas
    assert "<td>Capa</td>" in html and html.count("<tbody>") == 5                                                       # la tabla de propiedades no se pliega ni se pierde ninguna tabla
    for s in sections:
        assert s.number and s.title and s.slug and s.markdown                                                          # el resto de la sección queda igual


def test_the_flow_view_applies_the_helper_to_its_rendered_sections_only():
    import inspect
    src = inspect.getsource(main._catalog_flow_view)
    assert "collapse_field_tables(s.html)" in src and "render_sections(composed" in src
