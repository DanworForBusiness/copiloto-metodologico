"""test_security_dac_catalog_browse_ux.py — con 20+ DACs, Módulo 02 →
Seguridad y Gobernanza → Catálogo → Seguridad de Datos es una lista plana
difícil de escanear. Se agrega (100% client-side, reusando primitives
Hub-wide ya existentes -- `.whatsnew-filters`/`.whatsnew-filter` para el
buscador, `.dashboard-view-switch` + `.dashboard-section` para la
agrupación, ver `webapp/static/app.js::initSecurityDacBrowse`):
- filtro de texto realtime;
- agrupación por Scoring (default) o por Cliente;
- ambas sobre la MISMA colección de <li> ya renderizadas por el servidor
  (nunca se clonan/duplican).

Esta suite cubre el CONTRATO SERVER-SIDE (qué HTML/atributos emite el
template, para que el JS tenga con qué trabajar) -- el comportamiento
interactivo real (filtrar mientras se tipea, reagrupar, estado combinado)
se verifica con aceptación manual real en navegador (ver handoff), porque
no hay runner de JS en este repo (confirmado: sin package.json) y pytest
no ejecuta JavaScript."""
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


def _dac(cliente: str, name: str, veredicto_fields: dict | None = None) -> sec.SecurityArtifact:
    return sec.SecurityArtifact(
        cliente=cliente,
        criterio=f"Criterio {name}",
        dac_name=name,
        dac_label=name,
        source_entity="VP_SOURCE",
        principal_element="EMAIL",
        fields=[{"name": "CAMPO1"}],
        **(veredicto_fields or {}),
    )


def _write_entry(base: Path, art: sec.SecurityArtifact, manual: dict | None = None) -> None:
    md = sec.render_security(art, manual=manual or {})
    slug = art.dac_name.lower()
    path = base / "seguridad" / art.cliente.lower() / f"criterio_{slug}__{slug}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")


@pytest.fixture
def security_fixture(tmp_path, monkeypatch):
    from webapp.services import catalog_reads
    catalog_reads._catalog_security_entries.cache_clear()
    monkeypatch.setenv("DANWOR_CATALOG_DIR", str(tmp_path))
    yield tmp_path
    catalog_reads._catalog_security_entries.cache_clear()


def _seed_mixed(base: Path) -> None:
    # Biogenesis: 2 PARCIAL (sin lineage/control -> _verdict() real da parcial)
    _write_entry(base, _dac("Biogenesis", "AC_UNO"))
    _write_entry(base, _dac("Biogenesis", "AC_DOS"))
    # Danone: 1 PLENA (con lineage + control fields -> _verdict() real da plena)
    art = _dac("Danone", "DAC_SND_PBI")
    art.control_fields = [{"name": "EMAIL", "type": "cds.String", "length": 60, "key": True, "not_null": True}]
    art.lineage_rows = [{"space": "GOVERNED", "view": "3VF_ALGO"}]
    _write_entry(base, art)


# ── A. Controles presentes / ausentes por familia ──────────────────────────

def test_controls_render_for_data_family(security_fixture):
    _seed_mixed(security_fixture)
    html = client.get("/catalog/security", params={"family": "data"}).text
    assert "data-security-dac-wrapper" in html
    assert "data-security-dac-search" in html
    assert "data-security-dac-group-switch" in html
    assert 'data-group-by="scoring"' in html
    assert 'data-group-by="cliente"' in html
    assert "data-security-dac-source" in html
    assert "data-security-dac-groups" in html
    # Default = Scoring (is-active en el botón correcto)
    scoring_idx = html.index('data-group-by="scoring"')
    scoring_tag = html[max(0, scoring_idx - 80):scoring_idx]
    assert "is-active" in scoring_tag


def test_controls_absent_for_roles_family(security_fixture):
    matrix = sec.SecurityRoleMatrix(
        cliente="Danone", source_file="Matriz.xlsx",
        roles=[{"role_id": "GLOBAL_1", "category": "global"}],
        global_roles=[{"role_id": "GLOBAL_1", "category": "global"}],
    )
    md = sec.render_role_matrix(matrix, manual={})
    path = security_fixture / "seguridad" / "danone" / "seguridad_de_roles__matriz.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(md, encoding="utf-8")
    html = client.get("/catalog/security", params={"family": "roles"}).text
    assert "Roles globales: 1" in html  # confirma que SÍ hay items (roles real)
    assert "data-security-dac-wrapper" not in html
    assert "data-security-dac-search" not in html
    assert "data-security-dac-group-switch" not in html


# ── E. data-* attributes: contrato para el JS ───────────────────────────────

def test_cards_carry_search_cliente_and_verdict_data_attributes(security_fixture):
    _seed_mixed(security_fixture)
    html = client.get("/catalog/security", params={"family": "data"}).text
    assert 'data-cliente="Biogenesis"' in html
    assert 'data-cliente="Danone"' in html
    assert 'data-verdict="parcial"' in html
    assert 'data-verdict="plena"' in html
    # El search string ya precalculado (CatalogSecurityEntry._search) debe
    # incluir cliente, nombre técnico, identificador y entidad de permisos --
    # sin construir un segundo índice de búsqueda en el template.
    assert 'data-search="' in html
    ac_uno_idx = html.index("AC_UNO")
    surrounding = html[max(0, ac_uno_idx - 2000):ac_uno_idx + 200]
    assert 'data-search="' in surrounding
    search_attr_start = surrounding.rindex('data-search="') + len('data-search="')
    search_attr = surrounding[search_attr_start:surrounding.index('"', search_attr_start)]
    assert "ac_uno" in search_attr
    assert "biogenesis" in search_attr
    assert "email" in search_attr  # principal_element
    assert "vp_source" in search_attr  # source_entity


# ── G. Empty state reusa primitive existente ────────────────────────────────

def test_no_family_selected_reuses_empty_panel_primitive(security_fixture):
    html = client.get("/catalog/security", params={"family": ""}).text
    assert 'class="empty-panel"' in html


# ── F. VER DETALLE / VER CRITERIOS siguen intactos con el wrapper nuevo ────

def test_ver_detalle_and_ver_criterios_survive_the_new_wrapper(security_fixture):
    art = _dac("Danone", "DAC_SND_PBI")
    art.summary = "Propósito de prueba."
    _write_entry(security_fixture, art)
    html = client.get("/catalog/security", params={"family": "data"}).text
    assert "VER DETALLE" in html
    assert "Propósito de prueba." in html
    assert "VER CRITERIOS" in html
    assert "catalog-card__notes catalog-security-detail-toggle" in html
    assert "catalog-card__notes catalog-security-criteria" in html


def test_detail_route_unaffected_by_the_browse_ux_change(security_fixture):
    # La ruta de detalle (/catalog/security/detail) no se tocó -- confirmar
    # que sigue sirviendo el mismo contenido de siempre.
    _seed_mixed(security_fixture)
    resp = client.get("/catalog/security/detail", params={
        "cliente": "Danone", "criterio": "Criterio DAC_SND_PBI", "dac": "DAC_SND_PBI",
    })
    assert resp.status_code == 200
    assert "Definición DAC" in resp.text


# ── Canonical scoring order confirmado a nivel de datos (backend) ──────────

def test_backend_already_orders_entries_by_canonical_verdict_rank(security_fixture):
    # security_for_family ya ordena por _VERDICT_RANK (plena=0, buena=1,
    # parcial=2) -- el frontend NUNCA debe recalcular este orden, sólo
    # agrupar preservando la secuencia que ya llega. Confirmado leyendo el
    # view-model directo (no sólo el HTML).
    _seed_mixed(security_fixture)
    from webapp.services import catalog_reads
    mod, entries = catalog_reads._catalog_security_entries()
    hits = mod.security_for_family(entries, "data")
    veredictos = [h.veredicto for h in hits]
    # plena antes que parcial, sin importar orden de inserción/alfabético de cliente
    assert veredictos.index("plena") < veredictos.index("parcial")
