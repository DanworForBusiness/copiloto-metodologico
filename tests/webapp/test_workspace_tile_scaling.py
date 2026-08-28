"""HUB — GEOMETRÍA DEL SELECTOR DE WORKSPACES POR CANTIDAD VISIBLE.

El defecto: `aspect-ratio: 1/1` era global, y estaba tuneado para 3 tiles. El
ANCHO del grid ya era correcto —`auto-fit` colapsa la pista sobrante y reparte
los 1080px (medido: 3 tiles 345px, 2 tiles 529px, cero hueco)—, pero con 2
tiles el cuadrado arrastraba el alto a 529px y la tarjeta quedaba
desproporcionadamente alta.

El corrective hace count-aware SÓLO la proporción. Medido en browser a 1280px:

    3 tiles   345 × 345   (idéntico a antes)
    2 tiles   529 × 397   (antes 529 × 529)

La regla depende de la CANTIDAD visible y de nada más — nunca de qué vertical
esté oculta.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp import workspaces
from webapp.routers import platform as platform_router
from webapp.services import auth_service

_ROOT = Path(__file__).resolve().parents[2]
_CSS = (_ROOT / "webapp" / "static" / "brand-hero.css").read_text(encoding="utf-8")
_TEMPLATE = (_ROOT / "webapp" / "templates" / "platform_selector.html").read_text(encoding="utf-8")
_STATIC = _ROOT / "webapp" / "static"

_VERTICALES = ("comercial", "commercial", "crm", "copilot", "copiloto",
               "operations", "operaciones", "reporting", "staffing")


def _sin_comentarios(css: str) -> str:
    """Los comentarios de esta hoja NOMBRAN a propósito lo que está prohibido
    (explican por qué). Contarlos como código da un falso positivo."""
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _regla(patron: str) -> str:
    """Cuerpo de la primera regla CSS cuyo selector matchea `patron`."""
    m = re.search(patron + r"\s*\{([^}]*)\}", _CSS)
    return m.group(1) if m else ""


# ── El conteo viene del RENDER, no del DOM ────────────────────────────────

def test_el_contenedor_expone_la_cantidad_visible_desde_el_render():
    assert 'data-visible-count="{{ cards|length }}"' in _TEMPLATE, \
        "el conteo tiene que salir de `cards`, que el router ya calculó"


def test_ningun_javascript_cuenta_los_tiles():
    """Sin JS: el servidor ya sabe cuántos workspaces ve este usuario, contar
    en el cliente sería derivar algo que ya está resuelto."""
    for js in _STATIC.glob("*.js"):
        texto = js.read_text(encoding="utf-8")
        assert "workspace-tile" not in texto, f"{js.name} toca los tiles del selector"
        assert "visibleCount" not in texto, f"{js.name} cuenta tiles en el cliente"


@pytest.mark.parametrize("cantidad", [2, 3])
def test_el_html_renderizado_declara_la_cantidad(monkeypatch, cantidad):
    """Comportamiento, no sólo plantilla: la página realmente sale con el
    número puesto."""
    sesion = auth_service.create_session({
        "email": "test@danwor.com", "name": "Test User",
        "first_name": "Test", "family_name": "User",
    })
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    from webapp.main import app

    subconjunto = list(workspaces._ALL_WORKSPACES)[:cantidad]
    monkeypatch.setattr(platform_router, "available_workspaces",
                        lambda _user: list(subconjunto))
    cliente = TestClient(app)
    cliente.cookies.set(auth_service.COOKIE_NAME, sesion)
    r = cliente.get("/onedanwor", follow_redirects=False)
    assert r.status_code == 200
    assert f'data-visible-count="{cantidad}"' in r.text
    assert r.text.count('class="glass workspace-tile"') == cantidad


# ── La geometría, por cantidad ────────────────────────────────────────────

def test_tres_tiles_conservan_el_contrato_cuadrado():
    """El caso de 3 no se toca: sigue siendo el default de la hoja."""
    assert re.search(r"aspect-ratio\s*:\s*1\s*/\s*1", _regla(r"\.workspace-tile"))
    # Y ninguna regla por cantidad pisa el 3.
    assert 'data-visible-count="3"' not in _CSS, \
        "el 3 es el default, no una excepción — si aparece, alguien lo bifurcó"


def test_dos_tiles_usan_la_proporcion_apaisada():
    cuerpo = _regla(r'\.workspace-tiles\[data-visible-count="2"\] \.workspace-tile')
    assert re.search(r"aspect-ratio\s*:\s*4\s*/\s*3", cuerpo), \
        "con 2 tiles el ancho crece a ~529px; el cuadrado los hacía 529 de alto"
    assert "height:" not in cuerpo and "min-height" not in cuerpo, \
        "la geometría se expresa como proporción, nunca como alto arbitrario"


def test_un_tile_no_genera_un_cuadrado_gigante():
    """Hoy es inalcanzable —el router redirige con un solo workspace— pero si
    ese redirect se sacara, el default cuadrado daría una tarjeta de 1080×1080:
    el mismo defecto a mayor escala. Se deja `auto` (alto por contenido)."""
    cuerpo = _regla(r'\.workspace-tiles\[data-visible-count="1"\] \.workspace-tile')
    assert re.search(r"aspect-ratio\s*:\s*auto", cuerpo)


def test_con_un_solo_workspace_el_selector_ni_se_renderiza(monkeypatch):
    """La evidencia detrás del contrato de 1 tile."""
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    from webapp.main import app

    uno = list(workspaces._ALL_WORKSPACES)[:1]
    monkeypatch.setattr(platform_router, "available_workspaces", lambda _user: list(uno))
    sesion = auth_service.create_session({
        "email": "test@danwor.com", "name": "Test User",
        "first_name": "Test", "family_name": "User",
    })
    cliente = TestClient(app)
    cliente.cookies.set(auth_service.COOKIE_NAME, sesion)
    r = cliente.get("/onedanwor", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == uno[0].home_url


# ── Nada de lógica por vertical ───────────────────────────────────────────

def test_ninguna_regla_nombra_una_vertical():
    """La regla es por CANTIDAD. Un `if Comercial oculto` volvería a atarnos a
    qué se oculta en vez de a cuántos quedan."""
    inicio = _CSS.index(".workspace-tiles{")
    bloque = _CSS[inicio:inicio + 2500].lower()
    for vertical in _VERTICALES:
        assert vertical not in bloque, f"el CSS del selector nombra '{vertical}'"
    # En la plantilla: ninguna vertical nombrada, y el conteo es una expresión
    # directa sobre `cards` — no un condicional. Los `{% if card.meta.* %}` que
    # sí hay son campos de copy OPCIONALES, iguales para las tres cards.
    plantilla = _TEMPLATE.lower()
    for vertical in _VERTICALES:
        assert vertical not in plantilla, f"la plantilla nombra '{vertical}'"
    assert re.search(r'data-visible-count="\{\{\s*cards\|length\s*\}\}"', _TEMPLATE), \
        "el conteo tiene que ser `cards|length` pelado, sin ramas"


# ── El layout sano no se tocó ─────────────────────────────────────────────

def test_el_ancho_del_grid_sigue_igual():
    """El ancho ya era correcto y este corrective NO lo cambia: una sola
    declaración de `.workspace-tiles`, mismo cap y mismo auto-fit."""
    assert _CSS.count(".workspace-tiles{") == 1
    cuerpo = _regla(r"\.workspace-tiles")
    assert "width:min(1080px,100%)" in cuerpo
    assert "grid-template-columns:repeat(auto-fit,minmax(" in cuerpo
    assert "gap:22px" in cuerpo


def test_la_proporcion_por_cantidad_solo_aplica_con_multi_columna():
    """Owner §7: abajo del corte los tiles apilan a una columna y ahí manda la
    regla responsive de siempre. Las reglas por cantidad viven DENTRO de un
    `min-width`, así que una tarjeta apilada no hereda una proporción pensada
    para dos columnas."""
    bloques = re.findall(r"@media\s*\(min-width:\s*(\d+)px\)\s*\{(.*?)\n\}", _CSS, re.S)
    # Dos bloques legítimos y separados: la PROPORCIÓN (todo el rango
    # multi-columna) y la DENSIDAD original (sólo donde la tarjeta entra).
    contenedores = [(ancho, cuerpo) for ancho, cuerpo in bloques
                    if "aspect-ratio:4/3" in cuerpo]
    assert len(contenedores) == 1, "la proporción de 2 tiles debe vivir en UN media query"
    ancho, cuerpo = contenedores[0]
    # El corte tiene que estar por encima del ancho donde el grid apila: dos
    # columnas necesitan 2*260 + 22 = 542px de contenedor.
    assert int(ancho) >= 542
    assert 'data-visible-count="1"' in cuerpo, "el caso de 1 comparte el mismo corte"


# ══ DENSIDAD INTERNA POR CANTIDAD ═════════════════════════════════════════
# La geometría exterior no alcanzaba: con 2 tiles la tarjeta ya medía 529×397
# pero el CONTENIDO seguía en la escala comprimida que se introdujo en a7c54a7
# para que entrara el tercer workspace. Comprimir tiene sentido cuando hace
# falta lugar para una capacidad más; con 2 no hace falta.

# Valores ORIGINALES, recuperados de a7c54a7^ — no estimados de una captura.
# `padding` y `h2` eran las únicas reglas propias del tile; el resto heredaba
# los defaults compartidos de `.access-brand`/`.access-list`.
_ROOMY = {
    "padding": "38px 34px",
    "h2": "clamp(1.5rem,2.6vw,2.1rem)",
    "p": "1rem",
    "li": ".96rem",
    "check": "26px",
    "list_gap": "14px",
}
_COMPACTA = {
    "padding": "22px 26px",
    "h2": "clamp(1.05rem,1.7vw,1.32rem)",
    "p": ".82rem",
    "li": ".78rem",
    "check": "17px",
    "list_gap": "7px",
}


def _bloque_roomy() -> str:
    """El media query donde vive la densidad original de 2 tiles."""
    m = re.search(r"@media\s*\(min-width:\s*(\d+)px\)\s*\{(.*?)\n\}", _CSS, re.S)
    bloques = re.findall(r"@media\s*\(min-width:\s*(\d+)px\)\s*\{(.*?)\n\}", _CSS, re.S)
    for ancho, cuerpo in bloques:
        if "access-brand{padding:38px 34px}" in cuerpo.replace(" ", " "):
            return cuerpo
    return ""


def test_dos_tiles_recuperan_la_densidad_original():
    cuerpo = _bloque_roomy()
    assert cuerpo, "no hay bloque de densidad para 2 tiles"
    assert _ROOMY["padding"] in cuerpo
    assert _ROOMY["h2"] in cuerpo
    assert f'font-size:{_ROOMY["p"]}' in cuerpo
    assert f'font-size:{_ROOMY["li"]}' in cuerpo
    assert f'width:{_ROOMY["check"]}' in cuerpo
    assert f'gap:{_ROOMY["list_gap"]}' in cuerpo
    # Y TODA regla de ese bloque está calificada por la cantidad.
    for regla in re.findall(r"^\s*(\.[^{]+)\{", cuerpo, re.M):
        assert 'data-visible-count="2"' in regla, f"regla sin calificar: {regla.strip()}"


def test_tres_tiles_conservan_la_densidad_compacta():
    """El bloque compacto es el DEFAULT de la hoja y no se tocó: la densidad de
    2 se agregó como override aditivo, así que 3 no se puede haber movido."""
    for clave, valor in _COMPACTA.items():
        assert valor in _CSS, f"se perdió el valor compacto de {clave}: {valor}"
    # Los overrides compactos siguen SIN calificar por cantidad (son el default).
    assert ".workspace-tile .access-brand{height:100%;display:flex" in _CSS
    assert f'.workspace-tile .access-brand{{height:100%;display:flex;flex-direction:column;justify-content:center;padding:{_COMPACTA["padding"]}}}' in _CSS


def test_la_densidad_no_usa_transform_scale():
    """Escala REAL, no render escalado: `transform:scale` desenfoca texto y no
    reflowea."""
    codigo = _sin_comentarios(_CSS)
    inicio = codigo.index(".workspace-tiles{")
    assert "transform:scale" not in codigo[inicio:]


def test_la_densidad_original_solo_donde_entra():
    """Medido: el contenido a escala original entra con contenedor 1080
    (529×397) y 1000, y desborda 12px a 900. El corte es el ancho donde el
    contenedor llega a su tope — 1080 + los 24px de padding de `.screen` a cada
    lado — o sea cuando la tarjeta mide los 529 de la referencia."""
    bloques = re.findall(r"@media\s*\(min-width:\s*(\d+)px\)\s*\{(.*?)\n\}", _CSS, re.S)
    anchos = [int(a) for a, cuerpo in bloques if "padding:38px 34px" in cuerpo]
    assert anchos == [1128], f"el corte de la densidad original debería ser 1128, es {anchos}"


def test_mobile_conserva_la_densidad_compacta():
    """Owner §7: un viewport que apila dos workspaces en una columna NO es lo
    mismo que tener un solo producto. Ninguna regla de densidad puede vivir
    fuera de un `min-width`."""
    fuera = re.sub(r"@media[^{]*\{(?:[^{}]|\{[^{}]*\})*\}", "", _CSS, flags=re.S)
    assert 'data-visible-count="2"' not in fuera, \
        "hay densidad por cantidad fuera de un media query: llegaría a mobile"


def test_la_densidad_no_nombra_ninguna_vertical():
    cuerpo = _bloque_roomy().lower()
    for vertical in _VERTICALES:
        assert vertical not in cuerpo, f"la densidad nombra '{vertical}'"
