"""Cumpleaños de Cartelera Virtual con el Excel de SharePoint como PERSISTENCIA (lectura y escritura): parser, identidad estable, autorización
de publicación, refresh, escrituras puntuales (Excel REST API simulada), concurrencia y errores. La experiencia funcional no cambia."""
from __future__ import annotations

import io
import re
import urllib.error
from datetime import datetime

import pytest

import config
from webapp.services import cartelera_birthdays_source as sp
from webapp.services import cartelera_repository as repo
from webapp.services import cartelera_service as svc

HEAD = ["Nombre", "Apellido", "Cumpleaños", "Autorizo"]


def _xlsx(rows: list[list], sheet: str = "Sheet1") -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class FakeWorkbook:
    """SharePoint + Excel REST API mínimos: metadata, /content, usedRange, PATCH de rango y borrado de fila."""

    def __init__(self, grid: list[list]):
        self.grid, self.ctag, self.calls, self.fail_write = [list(r) for r in grid], 1, [], None

    def _addr(self, url):
        return re.search(r"range\(address='([^']+)'\)", url).group(1)

    def get_json(self, url, token):
        self.calls.append(("GET", url))
        if "usedRange" in url:
            width = max(len(r) for r in self.grid)
            vals = [r + [""] * (width - len(r)) for r in self.grid]
            return {"address": "Sheet1!A1", "values": vals, "text": [[str(c) for c in r] for r in vals], "rowIndex": 0, "columnIndex": 0}
        if url.endswith("/tables?$select=id,name"):
            return {"value": []}
        return {"id": "ITEM1", "name": "Cumpleaños.xlsx", "webUrl": "https://example/x", "cTag": f"c{self.ctag}", "eTag": "e",
                "lastModifiedDateTime": "2026-11-26T12:00:00Z", "parentReference": {"driveId": "DRIVE1", "path": "/drive/root:/CCHH HUB"}}

    def get_bytes(self, url, token):
        self.calls.append(("CONTENT", url))
        return _xlsx(self.grid)

    def patch_json(self, url, token, body, **kw):
        self.calls.append(("PATCH", url))
        if self.fail_write:
            raise self.fail_write
        addr = self._addr(url)
        m = re.match(r"([A-Z]+)(\d+)(?::([A-Z]+)(\d+))?$", addr)
        col, row = ord(m.group(1)) - 65, int(m.group(2))
        for i, vals in enumerate(body["values"]):
            r = row - 1 + i
            while len(self.grid) <= r:
                self.grid.append([])
            line = self.grid[r]
            for j, v in enumerate(vals):
                while len(line) <= col + j:
                    line.append("")
                line[col + j] = v
        self.ctag += 1
        return {}

    def post_accepted(self, url, token, body, **kw):
        self.calls.append(("DELETE-ROW", url))
        n = int(self._addr(url).split(":")[0])
        del self.grid[n - 1]
        self.ctag += 1
        return 204

    def install(self, monkeypatch):
        monkeypatch.setattr(sp.microsoft365, "graph_get_json", self.get_json)
        monkeypatch.setattr(sp.microsoft365, "graph_get_bytes", self.get_bytes)
        monkeypatch.setattr(sp.microsoft365, "graph_patch_json", self.patch_json)
        monkeypatch.setattr(sp.microsoft365, "graph_post_accepted", self.post_accepted)
        return self


@pytest.fixture(autouse=True)
def _runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(sp, "ENABLED", True)
    monkeypatch.setattr(sp, "MIN_CHECK_SECONDS", 0)
    monkeypatch.setattr(sp, "DRIVE_ID", "")                                  # el flujo de resolución por enlace se prueba sin la identidad fijada
    monkeypatch.setattr(sp, "ITEM_ID", "")


@pytest.fixture
def wb(monkeypatch):
    w = FakeWorkbook([HEAD, ["Ana", "Pérez", "14/03", "SI"], ["Juan", "Gómez", "02/11", "NO"], ["Notas", "", "", ""]]).install(monkeypatch)
    sp.refresh("tok", actor="qa")
    w.calls.clear()
    return w


def _by_name(name):
    return next(b for b in svc.list_birthdays() if b["name"] == name)


# ── lectura ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_parser_reads_structure_identity_and_consent_without_keeping_the_year_or_email():
    out = sp.parse_workbook(_xlsx([["Nombre y apellido", "Fecha de nacimiento", "Email", "Autoriza publicación"],
                                   ["Ana Pérez", datetime(1990, 3, 14), "ana@danwor.com", "Sí"],
                                   ["Juan Gómez", "02/11/1985", "", ""]]))
    assert out["identity_basis"] == "email" and out["consent_column"] == "Autoriza publicación"
    [ana] = out["rows"]                                                                        # Juan no tiene Email: no se incorpora
    assert (ana["name"], ana["day"], ana["month"], ana["authorized"]) == ("Ana Pérez", 14, 3, True)
    assert "year" not in ana and any("sin Email" in w for w in out["warnings"])
    plain = sp.parse_workbook(_xlsx([HEAD, ["Juan", "Gómez", "02/11", ""]]))["rows"][0]
    assert (plain["day"], plain["month"], plain["authorized"]) == (2, 11, False)              # vacío ≠ autorizado
    assert "email" not in plain and any("sin correo ni legajo" in w for w in sp.parse_workbook(_xlsx([HEAD, ["Juan", "Gómez", "02/11", ""]]))["warnings"])


def test_identity_is_stable_across_row_order_and_does_not_depend_on_the_row_number():
    a = sp.parse_workbook(_xlsx([HEAD, ["Ana", "Pérez", "14/03", "SI"], ["Juan", "Gómez", "02/11", "SI"]]))
    b = sp.parse_workbook(_xlsx([HEAD, ["Juan", "Gómez", "02/11", "SI"], ["Ana", "Pérez", "14/03", "SI"]]))
    assert {r["name"]: r["id"] for r in a["rows"]} == {r["name"]: r["id"] for r in b["rows"]}


def test_without_consent_column_nobody_is_published_and_a_sheet_without_name_and_date_fails_closed():
    out = sp.parse_workbook(_xlsx([["Colaborador", "Cumpleaños"], ["Ana Pérez", "14/03"]]))
    assert [r["authorized"] for r in out["rows"]] == [False] and any("autorización" in w for w in out["warnings"])
    with pytest.raises(sp.SourceError):
        sp.parse_workbook(_xlsx([["Foo", "Bar"], [1, 2]]))


def test_refresh_materializes_the_read_model_and_every_experience_reads_it(wb):
    assert [b["name"] for b in svc.list_birthdays()] == ["Ana Pérez", "Juan Gómez"]
    assert [b["name"] for b in svc.public_birthdays()] == ["Ana Pérez"]
    st = sp.status()
    assert (st["source"]["drive_id"], st["source"]["item_id"]) == ("DRIVE1", "ITEM1") and st["structure"]["used_sheet"] == "Sheet1"


def test_unchanged_file_is_not_reread_and_an_edit_made_in_excel_is_picked_up_without_restart(wb):
    assert sp.refresh("tok", actor="qa") == {"changed": False, "checked": True}
    assert [c[0] for c in wb.calls] == ["GET"]                                                 # solo metadata
    wb.grid[1][2], wb.ctag = "15/03", wb.ctag + 1                                              # alguien edita el Excel
    assert sp.refresh("tok", actor="qa")["changed"] is True
    assert _by_name("Ana Pérez")["day"] == 15


def test_read_errors_keep_the_last_valid_read_model(wb, monkeypatch):
    def boom(url, token):
        raise urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b'{"error":{"code":"accessDenied","message":"Access denied"}}'))
    monkeypatch.setattr(sp.microsoft365, "graph_get_json", boom)
    with pytest.raises(sp.SourceError) as exc:
        sp.refresh("tok", actor="qa", force=True)
    assert exc.value.kind == "forbidden" and "accessDenied" in exc.value.detail
    assert len(svc.list_birthdays()) == 2 and sp.status()["last_error"]["status"] == 403


# ── escritura ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_edit_writes_only_the_changed_cells_keeping_format_and_identity_for_the_greetings(wb):
    ana = _by_name("Ana Pérez")
    svc.save_birthday(ana["id"], ana["revision"], first_name="Ana", last_name="Pérez", day=14, month=3, authorized=False, actor="qa", write_token="w")
    assert len([c for c in wb.calls if c[0] == "PATCH"]) == 1                                   # una sola celda
    assert wb.grid[1] == ["Ana", "Pérez", "14/03", "NO"]                                       # solo «Autorizo», con el vocabulario del Excel
    assert wb.grid[3] == ["Notas", "", "", ""]                                                  # el resto del contenido intacto
    assert _by_name("Ana Pérez")["id"] == ana["id"] and not _by_name("Ana Pérez")["authorized"]


def test_date_is_written_in_the_same_format_the_excel_uses(wb):
    juan = _by_name("Juan Gómez")
    svc.save_birthday(juan["id"], juan["revision"], first_name="Juan", last_name="Gómez", day=5, month=1, authorized=False, actor="qa", write_token="w")
    assert wb.grid[2][2] == "05/01"


def test_a_change_made_in_excel_after_opening_the_dialog_is_never_overwritten(wb):
    ana = _by_name("Ana Pérez")
    wb.grid[1][3] = "NO"                                                                       # un tercero la cambió en el Excel
    with pytest.raises(repo.CarteleraConcurrentModificationError):
        svc.save_birthday(ana["id"], ana["revision"], first_name="Ana", last_name="Pérez", day=20, month=3, authorized=True, actor="qa", write_token="w")
    assert wb.grid[1] == ["Ana", "Pérez", "14/03", "NO"] and not [c for c in wb.calls if c[0] == "PATCH"]


def test_add_appends_a_row_after_the_used_range_and_refreshes_gestion_and_portal(wb):
    svc.save_birthday(None, None, first_name="María José", last_name="Ruiz", day=1, month=12, authorized=True, actor="qa", write_token="w")
    assert wb.grid[4] == ["María José", "Ruiz", "01/12", "SI"]                                 # cada campo en su columna, sin inferir
    assert [b["name"] for b in svc.public_birthdays()] == ["Ana Pérez", "María José Ruiz"]
    with pytest.raises(svc.CarteleraValidationError):                                          # duplicado
        svc.save_birthday(None, None, first_name="María José", last_name="Ruiz", day=1, month=12, authorized=True, actor="qa", write_token="w")


def test_bulk_import_writes_all_new_rows_in_one_write(wb):
    created, rejected = svc.import_birthdays(chr(10).join(["Pedro;Sosa;03/04", "Ana;Pérez;14/03", "mal"]), "qa", authorized=False, write_token="w")
    assert (created, rejected) == (1, ["mal"]) and len([c for c in wb.calls if c[0] == "PATCH"]) == 1
    assert wb.grid[4] == ["Pedro", "Sosa", "03/04", "NO"]


def test_delete_removes_the_row_and_its_greetings(wb):
    juan = _by_name("Juan Gómez")
    svc.delete_birthday(juan["id"], write_token="w", actor="qa")
    assert [r[0] for r in wb.grid] == ["Nombre", "Ana", "Notas"] and [b["name"] for b in svc.list_birthdays()] == ["Ana Pérez"]


def test_without_write_token_or_permission_nothing_is_confirmed(wb):
    ana = _by_name("Ana Pérez")
    with pytest.raises(sp.SourceError) as exc:
        svc.save_birthday(ana["id"], ana["revision"], first_name="Ana", last_name="Pérez", day=14, month=3, authorized=False, actor="qa", write_token="")
    assert exc.value.kind == "auth_required"
    wb.fail_write = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b'{"error":{"code":"accessDenied"}}'))
    with pytest.raises(sp.SourceError) as exc:
        svc.save_birthday(ana["id"], ana["revision"], first_name="Ana", last_name="Pérez", day=14, month=3, authorized=False, actor="qa", write_token="w")
    assert exc.value.kind == "forbidden" and "edición" in str(exc.value)
    assert _by_name("Ana Pérez")["authorized"] is True                                         # el read-model no cambió


# ── identidad por Email ─────────────────────────────────────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def wb_email(monkeypatch):
    w = FakeWorkbook([["Nombre", "Apellido", "Email", "Cumpleaños", "Autorizo"],
                      ["Ana", "Pérez", "ana.perez@danwor.com", "14/03", "SI"],
                      ["Juan", "Gómez", "", "02/11", "SI"],
                      ["Juana", "Gómez", "ana.perez@danwor.com", "03/11", "SI"],
                      ["Leo", "Paz", "leo@gmail.com", "04/11", "SI"]]).install(monkeypatch)
    sp.refresh("tok", actor="qa")
    w.calls.clear()
    return w


def test_with_an_email_column_rows_without_email_duplicates_or_external_mail_are_not_incorporated(wb_email):
    assert [b["name"] for b in svc.list_birthdays()] == ["Ana Pérez"]
    warnings = " ".join(sp.status()["warnings"])
    assert "sin Email" in warnings and "repite el Email" in warnings and "no es un correo corporativo" in warnings
    assert all("email" not in b for b in svc.list_birthdays() + svc.public_birthdays())       # el correo nunca llega al consumo
    assert sp.emails() == {_by_name("Ana Pérez")["id"]: "ana.perez@danwor.com"}                 # solo para la gestión


def test_email_identity_survives_renaming_and_date_changes_so_greetings_stay_linked(wb_email):
    ana = _by_name("Ana Pérez")
    svc.save_birthday(ana["id"], ana["revision"], first_name="Ana María", last_name="Pérez Soto", day=15, month=4, authorized=True,
                      actor="qa", write_token="w")
    renamed = _by_name("Ana María Pérez Soto")
    assert renamed["id"] == ana["id"] and (renamed["day"], renamed["month"]) == (15, 4)
    assert wb_email.grid[1][:4] == ["Ana María", "Pérez Soto", "ana.perez@danwor.com", "15/04"]  # el Email no se reescribe


def test_new_people_need_a_unique_corporate_email_when_the_excel_identifies_by_email(wb_email):
    with pytest.raises(svc.CarteleraValidationError, match="correo corporativo"):
        svc.save_birthday(None, None, first_name="Sol", last_name="Otero", day=1, month=2, authorized=True, actor="qa", write_token="w")
    with pytest.raises(svc.CarteleraValidationError, match="Ya hay una persona"):
        svc.save_birthday(None, None, first_name="Sol", last_name="Otero", email="ana.perez@danwor.com", day=1, month=2, authorized=True,
                          actor="qa", write_token="w")
    svc.save_birthday(None, None, first_name="Sol", last_name="Otero", email="Sol.Otero@danwor.com", day=1, month=2, authorized=True,
                      actor="qa", write_token="w")
    assert wb_email.grid[-1] == ["Sol", "Otero", "sol.otero@danwor.com", "01/02", "SI"]
    rows = svc.preview_birthday_import(chr(10).join(["Pía;Ríos;05/05;pia@danwor.com", "Pía;Ríos;05/05", "X;Y;06/06;sol.otero@danwor.com"]))
    assert [r["status"] for r in rows] == ["valid", "invalid", "duplicate"]
