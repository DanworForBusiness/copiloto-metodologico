"""Cumpleaños de Cartelera Virtual desde el Excel de SharePoint (solo lectura): parser, identidad estable, autorización de publicación,
refresh con Graph simulado (token delegado) y bloqueo de las escrituras locales."""
from __future__ import annotations

import io
import urllib.error
from datetime import datetime

import pytest

import config
from webapp.services import cartelera_birthdays_source as sp
from webapp.services import cartelera_service as svc


def _xlsx(rows: list[list], sheet: str = "Cumpleaños") -> bytes:
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = sheet
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture(autouse=True)
def _runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "BASE_DIR", tmp_path)
    monkeypatch.setattr(sp, "ENABLED", True)
    monkeypatch.setattr(sp, "MIN_CHECK_SECONDS", 0)
    monkeypatch.setattr(sp, "DRIVE_ID", "")                                  # el flujo de resolución por enlace se prueba sin la identidad fijada
    monkeypatch.setattr(sp, "ITEM_ID", "")


class FakeGraph:
    def __init__(self, xlsx: bytes, ctag: str = "c1"):
        self.xlsx, self.ctag, self.calls, self.fail = xlsx, ctag, [], None

    def get_json(self, url, token):
        self.calls.append(("meta", url))
        if self.fail:
            raise self.fail
        return {"id": "ITEM1", "name": "Cumpleaños.xlsx", "webUrl": "https://example/x", "cTag": self.ctag, "eTag": "e",
                "lastModifiedDateTime": "2026-11-26T12:00:00Z", "parentReference": {"driveId": "DRIVE1", "path": "/drive/root:/CCHH HUB"}}

    def get_bytes(self, url, token):
        self.calls.append(("content", url))
        return self.xlsx


@pytest.fixture
def graph(monkeypatch):
    g = FakeGraph(_xlsx([["Nombre y apellido", "Fecha de nacimiento", "Email", "Autoriza publicación"],
                         ["Ana Pérez", datetime(1990, 3, 14), "ana@danwor.com", "Sí"],
                         ["Juan Gómez", "02/11/1985", "JUAN@danwor.com", "No"]]))
    monkeypatch.setattr(sp.microsoft365, "graph_get_json", g.get_json)
    monkeypatch.setattr(sp.microsoft365, "graph_get_bytes", g.get_bytes)
    return g


def test_parser_reads_structure_identity_by_email_and_explicit_consent_without_keeping_the_year_or_email():
    out = sp.parse_workbook(_xlsx([["Nombre y apellido", "Fecha de nacimiento", "Email", "Autoriza publicación"],
                                   ["Ana Pérez", datetime(1990, 3, 14), "ana@danwor.com", "Sí"],
                                   ["Juan Gómez", "02/11/1985", "juan@danwor.com", ""]]))
    assert out["identity_basis"] == "email" and out["consent_column"] == "Autoriza publicación"
    ana, juan = out["rows"]
    assert (ana["name"], ana["day"], ana["month"], ana["authorized"]) == ("Ana Pérez", 14, 3, True)
    assert (juan["day"], juan["month"], juan["authorized"]) == (2, 11, False)                 # vacío ≠ autorizado
    assert set(ana) == {"id", "name", "day", "month", "authorized", "row"}                     # sin año ni correo
    assert out["structure"]["sheets"][0]["types"]["Fecha de nacimiento"] in ("fecha, texto", "texto, fecha")


def test_identity_is_stable_across_row_order_and_independent_of_the_row_number():
    a = sp.parse_workbook(_xlsx([["Nombre", "Apellido", "Día", "Mes", "Legajo", "Autorizado"], ["Ana", "Pérez", 14, 3, "L-1", "x"],
                                 ["Juan", "Gómez", 2, "noviembre", "L-2", ""]]))
    b = sp.parse_workbook(_xlsx([["Nombre", "Apellido", "Día", "Mes", "Legajo", "Autorizado"], ["Juan", "Gómez", 2, 11, "L-2", ""],
                                 ["Ana", "Pérez", 14, 3, "L-1", "x"]]))
    assert a["identity_basis"] == "employee_id"
    assert {r["name"]: r["id"] for r in a["rows"]} == {r["name"]: r["id"] for r in b["rows"]}
    assert {r["name"]: (r["day"], r["month"]) for r in a["rows"]} == {"Ana Pérez": (14, 3), "Juan Gómez": (2, 11)}


def test_without_consent_column_nobody_is_published_and_weak_identity_is_reported():
    out = sp.parse_workbook(_xlsx([["Colaborador", "Cumpleaños"], ["Ana Pérez", "14/03"], ["Sin fecha", ""]]))
    assert out["identity_basis"] == "name_date" and out["consent_column"] is None
    assert [r["authorized"] for r in out["rows"]] == [False]
    assert any("autorización" in w for w in out["warnings"]) and any("nombre + día + mes" in w for w in out["warnings"])
    assert any("no se incorporaron" in w for w in out["warnings"])


def test_a_workbook_without_name_and_date_columns_fails_closed():
    with pytest.raises(sp.SourceError) as exc:
        sp.parse_workbook(_xlsx([["Foo", "Bar"], [1, 2]]))
    assert exc.value.kind == "workbook"


def test_refresh_materializes_the_read_model_and_every_experience_reads_it(graph):
    assert sp.refresh("tok", actor="qa")["changed"] is True
    st = sp.status()
    assert st["loaded"] and st["count"] == 2 and st["authorized"] == 1
    assert (st["source"]["drive_id"], st["source"]["item_id"]) == ("DRIVE1", "ITEM1")
    assert graph.calls[0][1].startswith(f"{sp.GRAPH}/shares/u!")                              # primera resolución: por enlace
    assert [b["name"] for b in svc.list_birthdays()] == ["Ana Pérez", "Juan Gómez"]
    assert [b["name"] for b in svc.public_birthdays()] == ["Ana Pérez"]                        # solo autorizados
    ana = svc.list_birthdays()[0]
    assert svc.get_birthday(ana["id"])["name"] == "Ana Pérez"


def test_unchanged_ctag_does_not_download_again_and_a_new_version_is_picked_up_without_restart(graph):
    sp.refresh("tok", actor="qa")
    graph.calls.clear()
    assert sp.refresh("tok", actor="qa") == {"changed": False, "checked": True}
    assert [c[0] for c in graph.calls] == ["meta"]
    assert "/drives/DRIVE1/items/ITEM1" in graph.calls[0][1]                                  # luego: por identidad estable
    graph.ctag, graph.xlsx = "c2", _xlsx([["Nombre y apellido", "Fecha de nacimiento", "Email", "Autoriza publicación"],
                                         ["Ana Pérez", datetime(1990, 3, 15), "ana@danwor.com", "Sí"]])
    assert sp.refresh("tok", actor="qa")["changed"] is True
    assert [(b["name"], b["day"]) for b in svc.list_birthdays()] == [("Ana Pérez", 15)]


def test_graph_errors_keep_the_last_valid_read_model_and_record_the_concrete_error(graph):
    sp.refresh("tok", actor="qa")
    graph.fail = urllib.error.HTTPError("u", 403, "Forbidden", {}, io.BytesIO(b'{"error":{"code":"accessDenied","message":"Access denied"}}'))
    with pytest.raises(sp.SourceError) as exc:
        sp.refresh("tok", actor="qa", force=True)
    assert exc.value.kind == "forbidden" and "accessDenied" in exc.value.detail
    st = sp.status()
    assert st["count"] == 2 and st["last_error"]["status"] == 403                              # nada se borra ni cae a DEMO


def test_without_a_delegated_token_graph_is_never_called(graph):
    with pytest.raises(sp.SourceError) as exc:
        sp.refresh("", actor="qa")
    assert exc.value.kind == "auth_required" and graph.calls == []


def test_local_writes_are_blocked_while_sharepoint_is_the_source(graph):
    sp.refresh("tok", actor="qa")
    for call in (lambda: svc.save_birthday(None, None, name="X", day=1, month=1, authorized=True, actor="qa"),
                 lambda: svc.import_birthdays("X;01/01", "qa", authorized=True),
                 lambda: svc.delete_birthday(svc.list_birthdays()[0]["id"])):
        with pytest.raises(svc.CarteleraValidationError, match="Excel de SharePoint"):
            call()
    assert len(svc.list_birthdays()) == 2
