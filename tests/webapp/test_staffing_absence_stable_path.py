"""
test_staffing_absence_stable_path.py — `Solicitudes.xlsx` se resuelve SIEMPRE por la
ruta estable (sitio Operaciones + `General/Template Dashboard/Solicitudes.xlsx`), nunca
por un driveItem id: el archivo se regenera periódicamente y su id cambia entre
generaciones. Cubre archivo encontrado, regenerado con otro id, ausente, sin permiso,
item que no es un Excel, contenido inválido y el aviso específico de la UI. Graph
simulado y datos ficticios.
"""
from __future__ import annotations

import urllib.error
import uuid
from pathlib import Path

import pytest

from tests.webapp.test_staffing_absence_repository import _cells, _workbook_bytes
from webapp.services import staffing_repository as repo_module
from webapp.services.staffing_repository import AbsenceGraphError, AbsenceSchemaError, StaffingRepository

_STABLE = "/drive/root:/General/Template%20Dashboard/Solicitudes.xlsx"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class _Library:
    """Biblioteca de Operaciones simulada: la ruta estable apunta al archivo VIGENTE,
    cuyo id cambia en cada regeneración. Una URL con un id de item nunca resuelve."""

    def __init__(self):
        self.calls: list[str] = []
        self.status: int | None = None
        self.item: dict = {}
        self.content = b""
        self.regenerate(_workbook_bytes([_cells()]))

    def regenerate(self, content: bytes, **item_overrides):
        self.item = {"id": f"item-{uuid.uuid4().hex[:8]}", "eTag": f"\"{uuid.uuid4()}\",1", "name": "Solicitudes.xlsx",
                     "file": {"mimeType": _XLSX_MIME}, **item_overrides}
        self.content = content

    def install(self, monkeypatch):
        def fake_json(url, access_token=None, **kw):
            self.calls.append(url)
            if url.endswith("/sites/danwor.sharepoint.com:/sites/Operaciones"):
                return {"id": "site-ops"}
            if self.status:
                raise urllib.error.HTTPError(url, self.status, "fixture", None, None)
            assert url.endswith("/sites/site-ops" + _STABLE), url
            return dict(self.item)

        def fake_bytes(url, access_token=None, **kw):
            self.calls.append(url)
            if self.status:
                raise urllib.error.HTTPError(url, self.status, "fixture", None, None)
            assert url.endswith("/sites/site-ops" + _STABLE + ":/content"), url
            return self.content

        monkeypatch.setattr(repo_module.microsoft365, "graph_get_json", fake_json)
        monkeypatch.setattr(repo_module.microsoft365, "graph_get_bytes", fake_bytes)
        return self


@pytest.fixture
def library(monkeypatch):
    return _Library().install(monkeypatch)


def _scope():
    return f"scope-{uuid.uuid4().hex}"           # el caché de workbook es de módulo: un scope propio por test


@pytest.mark.parametrize("cached", [False, True])
def test_the_current_file_is_read_through_the_stable_path(library, cached):
    periods = StaffingRepository().get_absences("tok", cache_scope=_scope() if cached else None)
    assert [p.consultant_name for p in periods] == ["Matías Gómez"]
    assert all(library.item["id"] not in url for url in library.calls)    # nunca se pide por id
    assert any(url.endswith(_STABLE + ":/content") for url in library.calls)


def test_a_regenerated_file_with_another_id_and_the_same_name_is_read_again(library):
    repo, scope = StaffingRepository(), _scope()
    first_id = library.item["id"]
    assert [p.consultant_name for p in repo.get_absences("tok", cache_scope=scope)] == ["Matías Gómez"]
    library.regenerate(_workbook_bytes([_cells(Nombre="Lucía", Apellido="Ferreyra")]))
    assert library.item["id"] != first_id
    assert [p.consultant_name for p in repo.get_absences("tok", cache_scope=scope)] == ["Lucía Ferreyra"]
    assert repo.last_diagnostics["ausencias"]["workbook_hit"] is False      # la versión nueva se descargó
    assert all(first_id not in url and library.item["id"] not in url for url in library.calls)


@pytest.mark.parametrize("status", [401, 403, 404])
@pytest.mark.parametrize("cached", [False, True])
def test_missing_file_or_insufficient_permission_is_a_controlled_graph_error(library, status, cached):
    library.status = status
    with pytest.raises(AbsenceGraphError) as exc:
        StaffingRepository().get_absences("tok", cache_scope=_scope() if cached else None)
    assert exc.value.status_code == status
    assert exc.value.stage == ("metadata" if cached else "download")
    if cached:
        assert not any(url.endswith(":/content") for url in library.calls)        # respuesta definitiva: no se intenta descargar
    assert "tok" not in str(exc.value)


@pytest.mark.parametrize("overrides", [
    {"name": "Solicitudes.xlsx", "folder": {"childCount": 3}, "file": None},     # una carpeta con ese nombre
    {"name": "Solicitudes.csv"},
    {"file": {"mimeType": "text/csv"}},
])
def test_a_matching_name_is_not_enough_the_item_must_be_an_excel_file(library, overrides):
    library.regenerate(library.content, **overrides)
    with pytest.raises(AbsenceSchemaError) as exc:
        StaffingRepository().get_absences("tok", cache_scope=_scope())
    assert exc.value.stage == "file"
    assert not any(url.endswith(":/content") for url in library.calls)


def test_metadata_without_name_or_file_facet_is_still_accepted(library):
    library.item = {"id": "x", "eTag": "e1"}                                   # Graph puede omitirlos: no invalida
    assert len(StaffingRepository().get_absences("tok", cache_scope=_scope())) == 1


@pytest.mark.parametrize("content,stage", [
    (b"not an excel workbook", "workbook"),
    (_workbook_bytes([_cells()], sheet_name="Otra hoja"), "worksheet"),
    (_workbook_bytes([], headers=["Apellido", "Nombre"]), "headers"),
])
def test_unreadable_or_unexpected_workbook_is_a_schema_error(library, content, stage):
    library.regenerate(content)
    with pytest.raises(AbsenceSchemaError) as exc:
        StaffingRepository().get_absences("tok", cache_scope=_scope())
    assert exc.value.stage == stage


def test_transient_metadata_failure_still_falls_back_to_the_download(library, monkeypatch):
    real_json = repo_module.microsoft365.graph_get_json

    def flaky(url, access_token=None, **kw):
        if url.endswith(_STABLE):
            raise urllib.error.HTTPError(url, 503, "fixture", None, None)
        return real_json(url, access_token, **kw)
    monkeypatch.setattr(repo_module.microsoft365, "graph_get_json", flaky)
    assert len(StaffingRepository().get_absences("tok", cache_scope=_scope())) == 1


# ── UI: aviso específico por causa, sin afectar Asignaciones ──────────────────

_ROOT = Path(__file__).resolve().parents[2]
_APP_JS = (_ROOT / "webapp" / "static" / "app.js").read_text(encoding="utf-8")
_HTML = (_ROOT / "webapp" / "templates" / "operations" / "staffing_dashboard.html").read_text(encoding="utf-8")


def test_the_absence_notice_names_the_cause_and_keeps_assignments_available():
    block = _APP_JS[_APP_JS.index("async _loadAbsences() {"):_APP_JS.index("async _loadInactiveHistory() {")]
    for fragment in ('"auth_required" ? "auth"', '"schema_error" ? "format"', '=== 403 ? "forbidden"', '=== 404 ? "not_found"'):
        assert fragment in block
    notice = _HTML[_HTML.index('class="staffing-absence-error"'):]
    notice = notice[:notice.index("</p>")]
    for kind in ("auth", "forbidden", "not_found", "format"):
        assert f"absencesErrorKind === '{kind}'" in notice
    assert "Solicitudes.xlsx" in notice and "!absencesErrorKind" in notice
    assert notice.count("Las asignaciones siguen disponibles.") == 1
