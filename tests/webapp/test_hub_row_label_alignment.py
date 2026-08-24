"""HUB — el contrato de alineación de `.row-label-col`, cerrado en la RAIZ.

Owner final transversal corrective. El smoke de Creadas expuso que
`th.row-label-col` tenía la alineación izquierda intencional y `td.row-label-col`
NO: la celda caía en el `text-align: right` genérico de `td`. O sea que la clase
que nombra "la columna del rótulo de fila" sólo cumplía su contrato en la mitad
de la tabla.

AUDITORIA ANTES DE TOCAR LA RAIZ (el requisito del owner: si algún consumidor
necesita otra alineación, se reporta en vez de forzar la regla compartida):

* Reporting usa `.row-label-col` SOLO en `<th>` — 8 sitios, CERO `<td>`. Sus
  celdas de texto usan `.left`/`.row-label`, así que esta regla no lo alcanza.
* Los únicos `<td class="row-label-col">` del Hub son 3, todos en Comercial:
  Forecast (la etiqueta de mes y el rótulo "Total (N)") y Origen (el nombre del
  origen). Los tres son rótulos TEXTUALES — "Septiembre de 2026", "Total (8)",
  "Base Instalada" — y ninguno pedía otra alineación.

Conclusión: se arregla la RAIZ, sin overrides por feature.

GEOMETRIA: los `<th>` de Forecast y Origen pasan a `.left` para que el header
acompañe a su celda. Eso NO mueve anchos porque esas dos tablas fijan sus
columnas con `<colgroup>` en porcentajes, que gana sobre las reglas de `width`
de `th`. Verificado midiendo en el browser antes y después:
Forecast 450/177/379/177 y Origen 473/177/355/177 en ambos casos, ancho de
tabla 1183 en ambos.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_STATIC = _ROOT / "webapp" / "static"

# La clase que nombra la columna del rótulo de fila.
ROW_LABEL = "row-label-col"


def _tools_css() -> str:
    return (_STATIC / "tools.css").read_text(encoding="utf-8")


def _commercial_js() -> str:
    return (_STATIC / "commercial.js").read_text(encoding="utf-8")


def _js_code(raw: str) -> str:
    """JS sin comentarios —de línea y de bloque—. El comentario que documenta
    este defecto cita a propósito el literal que se cuenta más abajo."""
    sin_bloque = re.sub(r"/\*.*?\*/", "", raw, flags=re.S)
    return "\n".join(
        l for l in sin_bloque.splitlines() if not l.strip().startswith("//"))


def _css_rules(raw: str) -> str:
    """CSS sin comentarios: los bloques que documentan este contrato citan a
    propósito los selectores que estos tests cuentan."""
    return re.sub(r"/\*.*?\*/", "", raw, flags=re.S)


# --- A: la raíz -----------------------------------------------------------

def test_row_label_cells_are_left_aligned_like_their_header():
    """EL FIX. `th.row-label-col` ya alineaba a la izquierda; ahora `td` también.
    Un "rótulo de fila" con header y cuerpo en lados opuestos no es una
    alineación: es un descuido."""
    rules = _css_rules(_tools_css())
    # El header ya lo tenía.
    assert re.search(r"table\.dashboard-table th\.left,\s*table\.dashboard-table th\.row-label-col\s*\{[^}]*text-align:\s*left", rules)
    # Y ahora la celda comparte la MISMA regla que `.left`/`.row-label` — no una
    # regla paralela que pueda divergir.
    m = re.search(r"table\.dashboard-table td\.left,[^{]*\{([^}]*)\}", rules)
    assert m, "no se encontró la regla de celdas de texto"
    selector_block = rules[rules.index("table.dashboard-table td.left,"):rules.index(m.group(1))]
    assert "td.row-label-col" in selector_block, \
        "td.row-label-col debe compartir la regla de td.left/td.row-label"
    assert "text-align: left" in m.group(1)


def test_no_feature_local_override_reintroduces_the_split():
    """El owner fue explicito: fix de raiz, cero overrides por feature. Si
    aparece un `text-align` para `.row-label-col` en el CSS de una vertical, la
    regla compartida dejo de ser la fuente de verdad."""
    for name in ("commercial.css", "reporting.css"):
        path = _STATIC / name
        if not path.exists():
            continue
        rules = _css_rules(path.read_text(encoding="utf-8"))
        for m in re.finditer(r"([^{}]*row-label-col[^{}]*)\{([^}]*)\}", rules):
            assert "text-align" not in m.group(2), f"{name}: override de alineación en {m.group(1).strip()[:60]}"


# --- B: los consumidores --------------------------------------------------

def test_reporting_is_untouched_because_it_has_no_row_label_cells():
    """La razon por la que tocar la raiz es seguro: Reporting usa la clase SOLO
    en `<th>`. Si algun dia agrega un `<td class="row-label-col">`, heredara el
    contrato correcto — pero este test documenta por que el cambio no lo
    alcanzaba."""
    reporting = (_STATIC / "reporting.js").read_text(encoding="utf-8")
    assert f'<td class="{ROW_LABEL}"' not in reporting
    assert f'<th class="{ROW_LABEL}"' in reporting     # sí lo usa, en headers


def test_forecast_and_origin_headers_match_their_label_cells():
    """Los `<th>` de Forecast y Origen eran `<th>` pelados, o sea alineados a la
    derecha igual que sus celdas: consistentes entre si, pero en la alineacion
    equivocada para una columna de texto. Ahora los dos lados van a la
    izquierda."""
    js = _commercial_js()
    assert '<thead><tr><th class="left">Mes</th>' in js, "Forecast: header del rótulo"
    assert '<thead><tr><th class="left">Origen</th>' in js, "Origen: header del rótulo"
    # Y sus celdas siguen marcadas como rótulo (no se cambiaron a `.left`: la
    # clase semántica se conserva, lo que se arregló es su regla).
    assert _js_code(js).count(f'<td class="{ROW_LABEL}">') == 3, \
        "los 3 rótulos de Comercial (Forecast x2, Origen) conservan su clase semántica"


def test_numeric_columns_keep_their_alignment():
    """Lo que NO cambia: las columnas de medida siguen a la derecha, que es el
    default de `dashboard-table` para th y td. El fix toca UNA clase, no la
    alineacion por defecto de la tabla."""
    rules = _css_rules(_tools_css())
    assert re.search(r"table\.dashboard-table th\s*\{[^}]*text-align:\s*right", rules)
    assert re.search(r"table\.dashboard-table td\s*\{[^}]*text-align:\s*right", rules)
    js = _commercial_js()
    # Los headers numéricos de las dos tablas afectadas siguen sin clase.
    assert "<th>Opps.</th>" in js
    assert "<th>% del total</th>" in js


# --- C: geometría intacta -------------------------------------------------

def test_the_affected_tables_still_fix_their_columns_with_a_colgroup():
    """Por que marcar el header NO movio la geometria: esas dos tablas fijan sus
    columnas con `<colgroup>` en porcentajes, que gana sobre las reglas de
    `width` de `th` (`th.row-label-col { width: 340px }` y
    `th:not(.left):not(.row-label-col) { width: 88px }`).

    Medido en el browser antes y despues del cambio: Forecast
    450/177/379/177 y Origen 473/177/355/177 en los dos casos, ancho de tabla
    1183 en los dos. Si alguien saca estos `<colgroup>`, los anchos pasan a
    depender de las reglas de `th` y el `.left` que este corrective agrego SI
    cambiaria la geometria — de ahi que quede fijado.
    """
    js = _commercial_js()
    assert '<colgroup><col style="width:38%"><col style="width:15%"><col style="width:32%"><col style="width:15%"></colgroup>' in js, \
        "Forecast perdió su colgroup"
    assert '<colgroup><col style="width:40%"><col style="width:15%"><col style="width:30%"><col style="width:15%"></colgroup>' in js, \
        "Origen perdió su colgroup"


def test_creadas_detail_still_wraps_instead_of_truncating():
    """La regla de raiz trae `white-space: nowrap` + `ellipsis` junto con la
    alineacion —correcto para una matriz de medidas, donde la columna tiene que
    mantener su ancho—. El detalle de Creadas es una tabla de TEXTO y su
    override lo neutraliza con mas especificidad: sin esto volveria el truncado
    de "MURCHISON S.A. ESTIBAJES Y CARG…" que el owner reporto.

    Verificado en el browser tras el fix: `white-space: normal`, cero celdas
    truncadas.
    """
    rules = _css_rules((_STATIC / "commercial.css").read_text(encoding="utf-8"))
    m = re.search(
        r"table\.dashboard-table\.commercial-creadas-detail-table td\.left,\s*"
        r"table\.dashboard-table\.commercial-creadas-detail-table td\.row-label-col\s*\{([^}]*)\}",
        rules)
    assert m, "el override de wrap del detalle de Creadas desapareció"
    assert "white-space: normal" in m.group(1)


# --- D: los primitivos de TABLA compartidos no se duplican por vertical ----

def test_expandable_rows_use_the_shared_hub_affordance():
    """GUARD ARQUITECTONICO. El chevron de expandir/colapsar es UNA interaccion
    del Hub, no una de cada vertical.

    `.dashboard-table-toggle` + `.caret` ya vivia en `tools.css` —no hubo que
    extraer nada— y lo consumian Reporting (arboles de Rentabilidad/Horas
    Vendidas) y Staffing (detalle por consultor). Creadas tenia su propio boton
    gris, que es como aparecen los segundos lenguajes visuales.

    El invariante es CONCEPTUAL: ninguna vertical define su propio control de
    expansion de fila. Se comprueba por la forma —un control que anuncia
    `aria-expanded` desde una tabla— y no por un nombre de clase copiado.
    """
    for name in ("commercial.js", "reporting.js"):
        code = _js_code((_STATIC / name).read_text(encoding="utf-8"))
        # Todo control de expansion de fila declara el primitivo compartido.
        for m in re.finditer(r'<button[^>]*aria-expanded=', code):
            ventana = code[max(0, m.start() - 200):m.start() + 200]
            assert "dashboard-table-toggle" in ventana,                 f"{name}: control de expansion sin el primitivo compartido"
    # Y ninguna vertical define CSS propio para ese control.
    for name in ("commercial.css", "reporting.css"):
        path = _STATIC / name
        if not path.exists():
            continue
        rules = _css_rules(path.read_text(encoding="utf-8"))
        for m in re.finditer(r"([^{}]*(?:caret|toggle)[^{}]*)\{([^}]*)\}", rules):
            decl = m.group(2)
            for propiedad in ("color:", "font-size:", "cursor:", "outline:"):
                assert propiedad not in decl,                     f"{name}: redefine {propiedad} del control de expansion — es del primitivo"


def test_the_shared_toggle_keeps_its_gold_chevron_and_native_semantics():
    """El contrato visual e interactivo que las dos verticales comparten: caret
    dorado del token del Hub, `<button>` real (Enter/Space nativos, sin JS de
    teclado a mano) y foco visible."""
    rules = _css_rules(_tools_css())
    assert re.search(r"\.dashboard-table-toggle \.caret\s*\{[^}]*color:\s*var\(--gold\)", rules),         "el caret compartido perdió el dorado del Hub"
    assert re.search(r"button\.dashboard-table-toggle:focus-visible\s*\{[^}]*outline:", rules)
    # Los dos consumidores lo usan como `<button>`, no como `<div role=button>`.
    for name in ("commercial.js", "reporting.js"):
        code = _js_code((_STATIC / name).read_text(encoding="utf-8"))
        if "dashboard-table-toggle" not in code:
            continue
        assert '<button type="button" class="dashboard-table-toggle"' in code, name


def test_the_cascade_filter_interaction_is_shared_not_copied():
    """La otra interaccion transversal de este increment: "click para filtrar en
    cascada", que nacio en Metricas de Uso. El Hub tiene DOS primitivos de barra
    (`.activity-bar-row` y `.bar-row`) y la interaccion es la MISMA, asi que
    comparten el bloque de reglas en vez de duplicar valores.

    Si alguien copia los valores a un bloque propio de Comercial, este test lo
    encuentra."""
    rules = _css_rules(_tools_css())
    for selector in (".activity-bar-row--clickable, .bar-row--clickable",
                     ".activity-bar-row--clickable:hover, .bar-row--clickable:hover",
                     ".activity-bar-row--clickable.is-active, .bar-row--clickable.is-active"):
        assert selector in rules, f"el bloque compartido perdió: {selector}"
    # Y Comercial no redefine la interaccion por su cuenta.
    commercial = _css_rules((_STATIC / "commercial.css").read_text(encoding="utf-8"))
    assert "bar-row--clickable" not in commercial,         "Comercial no debe redefinir el modificador de click — es compartido"
