"""test_security_dac_purpose_detail_ux.py — el tile de un DAC en Módulo 02 →
Seguridad y Gobernanza → Catálogo → Seguridad de Datos mostraba el texto de
propósito (summary) siempre expandido, ocupando demasiado espacio vertical y
dificultando escanear una lista grande. Se mueve detrás de un nuevo expander
"+ VER DETALLE" (mismo primitive `.catalog-card__notes` que "+ VER CRITERIOS",
inmediatamente arriba) y se agrega el MISMO texto como subtítulo/blockquote en
la página de "Definición DAC · <nombre>" — una sola fuente
(`SecurityArtifact.summary` en el generador → `auto.summary`/`manual.summary`
en el frontmatter → `CatalogSecurityEntry.summary`, consumido en AMBOS
lugares), nunca un segundo campo ni una segunda copia.

Fixtures generadas con el generador REAL (`render_security`), no markdown
tipeado a mano — así un drift futuro del pipeline real rompe el test."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from webapp import access
from webapp.main import app
from webapp.services import auth_service

access.APP_OWNER = "test@danwor.com"
auth_service.ALLOWED_USERS = "ALL"
_SESSION = auth_service.create_session(
    {"email": "test@danwor.com", "name": "Test User", "first_name": "Test", "family_name": "User"}
)
client = TestClient(app)
client.cookies.set(auth_service.COOKIE_NAME, _SESSION)

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "catalog"))
import generate_security_catalog as sec  # noqa: E402


def _dac(name: str, summary: str = "") -> sec.SecurityArtifact:
    return sec.SecurityArtifact(
        cliente="ClienteTest",
        criterio=f"Criterio {name}",
        dac_name=name,
        dac_label=name,
        source_entity="VP_SOURCE",
        principal_element="EMAIL",
        summary=summary,
        fields=[{"name": "CAMPO1"}],
    )


def _write_entry(base: Path, name: str, summary: str = "") -> None:
    art = _dac(name, summary)
    md = sec.render_security(art, manual={})
    path = base / "seguridad" / "clientetest" / f"criterio_{name.lower()}__{name.lower()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")


@pytest.fixture
def security_fixture(tmp_path, monkeypatch):
    from webapp.services import catalog_reads
    catalog_reads._catalog_security_entries.cache_clear()
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path))
    yield tmp_path
    catalog_reads._catalog_security_entries.cache_clear()


PURPOSE = "Este artefacto restringe los datos de consumo por área de ventas, aplicado en la capa de consumo."


# ── A. DAC con purpose ──────────────────────────────────────────────────────

def test_tile_hides_purpose_by_default_and_offers_ver_detalle(security_fixture):
    _write_entry(security_fixture, "AC_CON_PROPOSITO", PURPOSE)
    resp = client.get("/catalog/security", params={"family": "data"})
    assert resp.status_code == 200
    html = resp.text
    assert "VER DETALLE" in html
    # El propósito vive DENTRO de un <details> (expandible -- un <details>
    # cerrado sigue enviando su contenido al cliente, el navegador sólo lo
    # oculta visualmente hasta abrirlo), nunca como <p> suelto fuera de él.
    assert PURPOSE in html
    before_summary = html.split(PURPOSE)[0]
    assert before_summary.rstrip().endswith('<p class="catalog-card__summary">')


def test_ver_detalle_expander_contains_exactly_the_purpose(security_fixture):
    _write_entry(security_fixture, "AC_CON_PROPOSITO", PURPOSE)
    html = client.get("/catalog/security", params={"family": "data"}).text
    assert (
        '<details class="catalog-card__notes catalog-security-detail-toggle" onclick="event.stopPropagation()">'
        in html
    )
    assert f'<summary>VER DETALLE</summary>\n      <p class="catalog-card__summary">{PURPOSE}</p>' in html


def test_ver_detalle_is_above_ver_criterios(security_fixture):
    _write_entry(security_fixture, "AC_CON_PROPOSITO", PURPOSE)
    html = client.get("/catalog/security", params={"family": "data"}).text
    assert html.index("VER DETALLE") < html.index("VER CRITERIOS")


# ── B. DAC sin purpose ──────────────────────────────────────────────────────

def test_no_ver_detalle_when_dac_has_no_purpose(security_fixture):
    _write_entry(security_fixture, "AC_SIN_PROPOSITO", "")
    html = client.get("/catalog/security", params={"family": "data"}).text
    assert "VER DETALLE" not in html
    assert "catalog-security-detail-toggle" not in html


def test_detail_has_no_empty_block_when_dac_has_no_purpose(security_fixture):
    _write_entry(security_fixture, "AC_SIN_PROPOSITO", "")
    resp = client.get("/catalog/security/detail", params={
        "cliente": "ClienteTest", "criterio": "Criterio AC_SIN_PROPOSITO", "dac": "AC_SIN_PROPOSITO",
    })
    assert resp.status_code == 200
    definicion_segment = resp.text.split("Definición DAC")[1].split("Tabla Control")[0]
    assert "<blockquote" not in definicion_segment
    assert "section-subtitle" not in definicion_segment


# ── C. Detail page ───────────────────────────────────────────────────────────

def test_detail_purpose_appears_once_before_the_table_with_subtitle_style(security_fixture):
    _write_entry(security_fixture, "AC_CON_PROPOSITO", PURPOSE)
    resp = client.get("/catalog/security/detail", params={
        "cliente": "ClienteTest", "criterio": "Criterio AC_CON_PROPOSITO", "dac": "AC_CON_PROPOSITO",
    })
    assert resp.status_code == 200
    html = resp.text
    assert html.count(PURPOSE) == 1  # una sola vez en toda la página
    definicion_idx = html.index("Definición DAC")
    table_idx = html.index("<table")
    purpose_idx = html.index(PURPOSE)
    assert definicion_idx < purpose_idx < table_idx
    # Reusa el primitive de subtítulo de sección existente (blockquote con
    # línea vertical gold) -- nunca un div/estilo nuevo específico de DAC.
    assert 'class="section-subtitle"' in html
    assert PURPOSE in html[html.index('class="section-subtitle"'):html.index('class="section-subtitle"') + len(PURPOSE) + 200]


# ── D. VER CRITERIOS sigue intacto ──────────────────────────────────────────

def test_ver_criterios_unaffected_by_purpose_toggle(security_fixture):
    art = sec.SecurityArtifact(
        cliente="ClienteTest", criterio="Criterio AC_CON_PROPOSITO", dac_name="AC_CON_PROPOSITO",
        dac_label="AC_CON_PROPOSITO", source_entity="VP_SOURCE", principal_element="EMAIL",
        summary=PURPOSE, fields=[{"name": "CAMPO1"}, {"name": "CAMPO2"}],
    )
    md = sec.render_security(art, manual={})
    path = security_fixture / "seguridad" / "clientetest" / "criterio_ac_con_proposito__ac_con_proposito.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    html = client.get("/catalog/security", params={"family": "data"}).text
    assert "VER CRITERIOS (2)" in html
    assert "CAMPO1" in html and "CAMPO2" in html


# ── E. Single source of truth ───────────────────────────────────────────────

def test_tile_and_detail_purpose_come_from_the_same_value(security_fixture):
    _write_entry(security_fixture, "AC_CON_PROPOSITO", PURPOSE)
    tile_html = client.get("/catalog/security", params={"family": "data"}).text
    detail_html = client.get("/catalog/security/detail", params={
        "cliente": "ClienteTest", "criterio": "Criterio AC_CON_PROPOSITO", "dac": "AC_CON_PROPOSITO",
    }).text
    assert PURPOSE in tile_html
    assert PURPOSE in detail_html
    # Mismo view-model: CatalogSecurityEntry.summary es la ÚNICA fuente --
    # confirmado leyendo directo el entry parseado, no sólo el HTML.
    from webapp.services import catalog_reads
    mod, entries = catalog_reads._catalog_security_entries()
    entry = mod.find_security_entry(entries, "ClienteTest", "Criterio AC_CON_PROPOSITO", "AC_CON_PROPOSITO")
    assert entry.summary == PURPOSE
