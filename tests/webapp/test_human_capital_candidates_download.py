"""CCHH → Candidatos → «Descargar CVs» (Fase 2): rutas, permisos, resumen previo, descarga efectiva del ZIP con Graph SIMULADO, errores parciales con segunda confirmación, auditoría durable,
concurrencia, cancelación por desconexión, limpieza del temporal y privacidad. La capa de servicio tiene sus propias pruebas (`tests/test_human_capital_export.py`)."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import threading
import time
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import config
from core import microsoft365, remote_store
from webapp import access
from webapp.main import app
from webapp.routers import human_capital as router_module
from webapp.services import auth_service, human_capital_export as export, profile_document_repository, profile_repository

_ROOT = Path(__file__).resolve().parents[2]
_HARNESS = Path(__file__).resolve().parent / "human_capital_candidates_download.node.js"
_HC = "hc-download-test@danwor.com"
_ORIGIN = {"Origin": "http://testserver"}
TOKEN = "tok-sintetico-NO-REAL-descarga-123"
SECRET_NAME = "Ana Pérez"


@pytest.fixture(autouse=True)
def hub(tmp_path, monkeypatch):
    base, tmp = tmp_path / "base", tmp_path / "tmp"
    base.mkdir()
    tmp.mkdir()
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", _HC + ",otro-cchh@danwor.com")
    monkeypatch.setattr(auth_service, "OPERATIONS_ALLOWED_USERS", "")
    monkeypatch.setattr(auth_service, "COMMERCIAL_ALLOWED_USERS", "")
    monkeypatch.setattr(access, "APP_OWNER", "owner-download@danwor.com")
    monkeypatch.setattr(config, "BASE_DIR", base)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "local")
    monkeypatch.setattr(export.tempfile, "tempdir", str(tmp))
    return {"base": base, "tmp": tmp}


def client(email: str | None = _HC) -> TestClient:
    c = TestClient(app)
    if email:
        c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "U"}))
    return c


def make_profile(first="Ana", last="Pérez", **extra) -> dict:
    return profile_repository.create({"first_name": first, "last_name": last, "email": f"{first}.{last}@sintetico.test".lower(), "phone": "+54 11 5555-0000", "location": "CABA",
                                      "profile_skills": "SAP", "seniority": "Senior", "source": "LinkedIn", "tags": "x", "notes": "nota privada", "potential_rating": 3, **extra})


def add_doc(profile_id, document_id, filename="cv.pdf", size=1000, item=None, uploaded="2026-10-01T10:00:00+00:00") -> dict:
    doc = {"document_id": document_id, "profile_id": profile_id, "provider": "sharepoint", "site_id": "s", "drive_id": "b!drive", "item_id": item or f"item-{document_id}", "filename": filename,
           "content_type": "application/pdf", "size_bytes": size, "uploaded_at": uploaded, "uploaded_by": "rrhh@test", "web_url": "https://danwor.sharepoint.com/sites/x/secreto.pdf"}
    return profile_document_repository.create_with_explicit_id(document_id, doc)[0]


class FakeGraph:
    def __init__(self, behaviors=None):
        self.behaviors, self.calls, self.tokens, self.hold, self.entered = behaviors or {}, [], [], None, threading.Event()

    def __call__(self, url, token, sink, *, cap_bytes, socket_timeout, deadline_s, cancel=None, declared_size=None, **_):
        item = re.search(r"/items/([^/]+)/content", url).group(1)
        self.calls.append(item)
        self.tokens.append(token)
        self.entered.set()
        if self.hold is not None:
            self.hold.wait(5)
        outcome = self.behaviors.get(item, "ok")
        res = {"outcome": outcome, "http_status": 200 if outcome == "ok" else int(outcome.split("_")[1]) if outcome.startswith("http_") else None, "bytes": 0}
        if outcome == "ok":
            data = f"%PDF-{item}".encode() * 40
            sink.write(data)
            res["bytes"] = len(data)
        return res


@pytest.fixture()
def graph(monkeypatch):
    g = FakeGraph()
    monkeypatch.setattr(microsoft365, "graph_download_capped", g)
    return g


def seed():
    a, b = make_profile("Ana", "Pérez"), make_profile("Luis", "Gómez")
    add_doc(a["profile_id"], "doc-a1", "CV Ana 2026.pdf", uploaded="2026-10-20T00:00:00+00:00")
    add_doc(a["profile_id"], "doc-a2", "CV Ana 2025.pdf", uploaded="2025-03-01T00:00:00+00:00")
    add_doc(b["profile_id"], "doc-b1", "CV Luis.docx", size=2048)
    return a, b


def summary(c, ids, origin=True):
    return c.post("/cchh/candidatos/descargar/resumen", data={"profile_ids": ids}, headers=_ORIGIN if origin else {})


def digest_of(html: str) -> str:
    return re.search(r'data-plan-digest="([0-9a-f]{64})"', html).group(1)


def download(c, ids, digest, *, token=TOKEN, ack_omitted="1", allow_partial="", ack_failed=(), parent="", origin=True):
    data = {"profile_ids": ids, "graph_token": token, "plan_digest": digest, "ack_omitted": ack_omitted}
    if allow_partial:
        data["allow_partial"] = allow_partial
    if ack_failed:
        data["ack_failed"] = list(ack_failed)
    if parent:
        data["parent_operation_id"] = parent
    return c.post("/cchh/candidatos/descargar", data=data, headers=_ORIGIN if origin else {})


def zip_of(response) -> zipfile.ZipFile:
    return zipfile.ZipFile(BytesIO(response.content))


def audit_dirs(env):
    root = env["base"] / "human-capital" / "cv-exports"
    return sorted(p.name for p in root.iterdir()) if root.exists() else []


def tmp_dirs(env):
    return [p.name for p in env["tmp"].iterdir()]


# ───────────────────────── permisos ─────────────────────────
@pytest.mark.parametrize("path", ["/cchh/candidatos/descargar/resumen", "/cchh/candidatos/descargar"])
def test_both_routes_require_session_cchh_authorization_and_same_origin(path, graph, hub):
    a, _ = seed()
    data = {"profile_ids": [a["profile_id"]], "graph_token": TOKEN, "plan_digest": "x"}
    anon = TestClient(app).post(path, data=data, headers=_ORIGIN, follow_redirects=False)
    assert anon.status_code == 303 and anon.headers["location"].startswith("/platform")
    assert client("fuera-de-cchh@danwor.com").post(path, data=data, headers=_ORIGIN).status_code == 403
    assert client().post(path, data=data).status_code == 403
    assert client().post(path, data=data, headers={"Origin": "https://evil.example"}).status_code == 403
    assert graph.calls == [] and audit_dirs(hub) == []                                    # ninguna de las negadas leyó Graph ni auditó


def test_get_is_not_allowed_on_the_download_routes():
    for path in ("/cchh/candidatos/descargar/resumen", "/cchh/candidatos/descargar"):
        assert client().get(path).status_code in (404, 405)                              # `/cchh/candidatos/{profile_id}` (ficha) no ensombrece: «descargar» no es un id


# ───────────────────────── resumen previo ─────────────────────────
def test_summary_shows_candidates_documents_size_limits_and_never_touches_graph_or_the_audit(graph, hub):
    a, b = seed()
    empty = make_profile("Sin", "Cvs")
    r = summary(client(), [a["profile_id"], b["profile_id"], empty["profile_id"]])
    html = r.text
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and "data-dl-root" in html
    assert re.search(r'data-dl-count="candidates">3<', html) and re.search(r'data-dl-count="documents">3<', html) and re.search(r'data-dl-count="size">0,0 MB<', html)
    assert "Sin Cvs" in html and "sin CVs para descargar" in html and "25 candidatos, 60 archivos, 100 MB en total y 25 MB por archivo" in html
    assert re.search(r"Descargar ZIP \(3 archivos\)", html) and "data-dl-confirm" in html and digest_of(html)
    assert graph.calls == [] and audit_dirs(hub) == [] and not (hub["base"] / "human-capital" / "cv-exports").exists()


def test_summary_reports_omitted_documents_and_blocks_selections_over_the_limits(graph, hub):
    a = make_profile("Ana", "Pérez")
    add_doc(a["profile_id"], "ok1", "ok.pdf")
    add_doc(a["profile_id"], "big", "enorme.pdf", size=26 * 1024 * 1024)
    html = summary(client(), [a["profile_id"]]).text
    assert "data-dl-omitted" in html and "enorme.pdf" in html and "supera los 25 MB por archivo" in html and 'data-has-omitted="1"' in html
    many = [make_profile(f"C{i}", "X")["profile_id"] for i in range(26)]
    over = summary(client(), many).text
    assert 'data-dl-error="over_candidates"' in over and "hasta 25 candidatos" in over and "data-dl-confirm" not in over
    b = make_profile("Bru", "Files")
    for i in range(61):
        add_doc(b["profile_id"], f"f{i}", f"f{i}.pdf", size=10)
    assert 'data-dl-error="over_files"' in summary(client(), [b["profile_id"]]).text
    c = make_profile("Big", "Total")
    for i in range(5):
        add_doc(c["profile_id"], f"t{i}", f"t{i}.pdf", size=24 * 1024 * 1024)
    assert 'data-dl-error="over_total"' in summary(client(), [c["profile_id"]]).text
    assert 'data-dl-error="empty"' in summary(client(), []).text
    assert graph.calls == []


def test_summary_escapes_hostile_names_and_does_not_leak_contact_data_or_urls(graph):
    p = make_profile("O'Hara <b>x</b>", "\"Q\"")
    add_doc(p["profile_id"], "d1", '"><img src=x onerror=alert(1)>.pdf')
    html = summary(client(), [p["profile_id"]]).text
    assert "<b>x</b>" not in html and "<img src=x" not in html
    for leak in (p["email"], "+54 11 5555-0000", "nota privada", "https://", "sharepoint", "b!drive"):
        assert leak not in html, leak


def test_summary_reports_an_unreadable_bank_without_details(monkeypatch, graph):
    a, _ = seed()

    def boom(_pid):
        raise OSError("detalle interno /srv/secreto")
    monkeypatch.setattr(export.profile_document_service, "list_documents_for_profile", boom)
    html = summary(client(), [a["profile_id"]]).text
    assert 'data-dl-state="source_error"' in html and "secreto" not in html


def test_panel_bar_has_the_download_button_and_the_page_has_its_dialog_and_script():
    page = client().get("/cchh/candidatos").text
    assert re.search(r"<button[^>]*data-hc-download-open[^>]*>[^<]*<svg.*?</svg>\s*Descargar CVs", page, re.S)
    assert 'id="hc-download-dialog"' in page and 'id="hc-download-content"' in page and "human_capital_candidates_download.js" in page and "human_capital_candidates_selection.js" in page
    bar = re.search(r'<div class="hc-selection-bar[^>]*>.*?data-hc-clear-selection', page, re.S).group(0)
    assert "data-hc-share-open" in bar and "data-hc-download-open" in bar                  # conviven «Compartir perfiles» y «Descargar CVs» en la misma barra


# ───────────────────────── descarga efectiva ─────────────────────────
def test_a_confirmed_download_streams_a_stored_zip_with_the_exact_bytes_and_leaves_an_audit(graph, hub):
    a, b = seed()
    ids = [a["profile_id"], b["profile_id"]]
    digest = digest_of(summary(client(), ids).text)
    r = download(client(), ids, digest)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip" and r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert re.fullmatch(r'attachment; filename="cvs-candidatos-\d{8}-\d{4}\.zip"', r.headers["content-disposition"]) and r.headers["x-export-status"] == "full" and r.headers["x-export-files"] == "3"
    assert int(r.headers["content-length"]) == len(r.content)
    z = zip_of(r)
    assert sorted(z.namelist()) == ["Ana Pérez/CV Ana 2025.pdf", "Ana Pérez/CV Ana 2026.pdf", "Luis Gómez/CV Luis.docx"] and {i.compress_type for i in z.infolist()} == {zipfile.ZIP_STORED}
    assert z.read("Luis Gómez/CV Luis.docx") == b"%PDF-item-doc-b1" * 40 and z.testzip() is None
    assert set(graph.tokens) == {TOKEN}
    ops = audit_dirs(hub)
    assert ops == [r.headers["x-export-operation"]]
    audit = export.read_audit(ops[0])
    assert audit["intent"]["actor"] == _HC and audit["result"]["outcome"] == "prepared_full" and audit["result"]["files_ok"] == 3
    assert tmp_dirs(hub) == []                                                            # sin ZIP ni parciales en el temporal tras la entrega


def test_the_token_never_appears_in_the_response_headers_audit_or_disk(graph, hub):
    a, _ = seed()
    ids = [a["profile_id"]]
    digest = digest_of(summary(client(), ids).text)
    r = download(client(), ids, digest)
    assert TOKEN not in str(r.headers) and TOKEN.encode() not in r.content
    for path in hub["base"].rglob("*"):
        if path.is_file():
            assert TOKEN not in path.read_text(encoding="utf-8", errors="ignore"), path
    raw = json.dumps(export.read_audit(audit_dirs(hub)[0]), ensure_ascii=False)
    for forbidden in ("Ana", "Pérez", ".pdf", "sharepoint", TOKEN, "b!drive"):
        assert forbidden not in raw, forbidden


def test_nothing_is_persisted_outside_the_audit_no_zip_no_cv_copies(graph, hub):
    a, b = seed()
    ids = [a["profile_id"], b["profile_id"]]
    download(client(), ids, digest_of(summary(client(), ids).text))
    audit_files = sorted(str(p.relative_to(hub["base"])).replace("\\", "/") for p in (hub["base"] / "human-capital" / "cv-exports").rglob("*") if p.is_file())
    assert [f.rsplit("/", 1)[-1] for f in audit_files] == ["intent.json", "result.json"]
    assert not list(hub["base"].rglob("*.zip")) and not list(hub["base"].rglob("*.pdf")) and not list(hub["base"].rglob("*.docx"))
    assert (hub["base"] / "human-capital" / "profile-documents").exists() and len(list((hub["base"] / "human-capital" / "profile-documents").glob("*.json"))) == 3     # los registros originales, intactos


def test_missing_token_stale_digest_and_unacknowledged_omissions_do_nothing(graph, hub):
    a, _ = seed()
    ids = [a["profile_id"]]
    digest = digest_of(summary(client(), ids).text)
    assert 'data-dl-state="no_token"' in download(client(), ids, digest, token="").text
    stale = download(client(), ids, "0" * 64)
    assert stale.headers["content-type"].startswith("text/html") and 'data-dl-state="stale"' in stale.text and "data-dl-reopen" in stale.text
    big = make_profile("Con", "Omitido")
    add_doc(big["profile_id"], "x1", "ok.pdf")
    add_doc(big["profile_id"], "x2", "enorme.pdf", size=26 * 1024 * 1024)
    d2 = digest_of(summary(client(), [big["profile_id"]]).text)
    assert 'data-dl-state="stale"' in download(client(), [big["profile_id"]], d2, ack_omitted="").text
    assert graph.calls == [] and audit_dirs(hub) == []


def test_a_selection_changed_after_the_summary_is_refused(graph, hub):
    a, _ = seed()
    ids = [a["profile_id"]]
    digest = digest_of(summary(client(), ids).text)
    add_doc(a["profile_id"], "doc-new", "nuevo.pdf")                                      # alguien subió un CV después del resumen
    assert 'data-dl-state="stale"' in download(client(), ids, digest).text and graph.calls == [] and audit_dirs(hub) == []


# ───────────────────────── errores parciales: segunda confirmación ─────────────────────────
def test_partial_failures_deliver_nothing_until_an_explicit_second_confirmation(graph, hub):
    a, b = seed()
    ids = [a["profile_id"], b["profile_id"]]
    digest = digest_of(summary(client(), ids).text)
    graph.behaviors = {"item-doc-a2": "http_404", "item-doc-b1": "http_403"}
    first = download(client(), ids, digest)
    assert first.headers["content-type"].startswith("text/html") and first.status_code == 200 and "application/zip" not in first.headers["content-type"]
    html = first.text
    assert "No se entregó ningún archivo" in html and "CV Ana 2025.pdf" in html and "no se encontró en SharePoint (404)" in html and "sin permiso en SharePoint (403)" in html
    ack = re.search(r'data-ack-failed="([^"]+)"', html).group(1).split()
    parent = re.search(r'data-parent-op="([0-9a-f]{32})"', html).group(1)
    assert sorted(ack) == ["doc-a2", "doc-b1"] and "data-dl-partial" in html and "data-dl-confirm" in html
    assert export.read_audit(parent)["result"]["outcome"] == "withheld_partial"
    # un POST con allow_partial pero SIN los ids reconocidos tampoco entrega
    assert "application/zip" not in download(client(), ids, digest, allow_partial="1").headers["content-type"]
    # con la segunda confirmación: ZIP parcial + LEEME, enlazado con la operación anterior
    second = download(client(), ids, digest, allow_partial="1", ack_failed=ack, parent=parent)
    assert second.headers["content-type"] == "application/zip" and second.headers["x-export-status"] == "partial" and second.headers["x-export-files"] == "1"
    z = zip_of(second)
    assert sorted(z.namelist()) == ["Ana Pérez/CV Ana 2026.pdf", "LEEME_OMITIDOS.txt"] and "CV Luis.docx" in z.read("LEEME_OMITIDOS.txt").decode("utf-8")
    intent = export.read_audit(second.headers["x-export-operation"])["intent"]
    assert intent["parent_operation_id"] == parent and intent["mode"] == "partial_consented" and sorted(intent["acknowledged_failed_ids"]) == sorted(ack)
    assert tmp_dirs(hub) == []


def test_every_document_failing_returns_a_failure_state_and_no_zip(graph, hub):
    a, _ = seed()
    ids = [a["profile_id"]]
    digest = digest_of(summary(client(), ids).text)
    graph.behaviors = {"item-doc-a1": "http_403", "item-doc-a2": "http_403"}
    r = download(client(), ids, digest)
    assert "application/zip" not in r.headers["content-type"] and "No se pudo obtener ningún CV" in r.text and "data-dl-partial" not in r.text and tmp_dirs(hub) == []


# ───────────────────────── auditoría fail-closed ─────────────────────────
def test_when_the_audit_cannot_be_confirmed_the_export_does_not_start(graph, hub, monkeypatch):
    a, _ = seed()
    ids = [a["profile_id"]]
    digest = digest_of(summary(client(), ids).text)
    monkeypatch.setattr(config, "PERSISTENCE_MODE", "remote")
    monkeypatch.setattr(remote_store, "local_only", lambda: False)
    monkeypatch.setattr(remote_store, "read_only_mode", lambda: False)
    monkeypatch.setattr(remote_store, "write_through", lambda path, data, **k: remote_store.DURABLE_LOCAL)
    r = download(client(), ids, digest)
    assert r.status_code == 503 and 'data-dl-state="audit_unavailable"' in r.text and "no se leyó ningún CV" in r.text
    assert graph.calls == [] and tmp_dirs(hub) == []


def test_when_only_the_result_cannot_be_confirmed_nothing_is_delivered_and_the_message_is_accurate(graph, hub, monkeypatch):
    a, _ = seed()
    ids = [a["profile_id"]]
    digest = digest_of(summary(client(), ids).text)
    real = remote_store.write_through

    def flaky(path, data, **k):
        if Path(path).name == "result.json":
            raise PermissionError("sin espacio")
        return real(path, data, **k)
    monkeypatch.setattr(remote_store, "write_through", flaky)
    r = download(client(), ids, digest)
    assert r.status_code == 503 and "application/zip" not in r.headers["content-type"] and 'data-dl-state="audit_result_unavailable"' in r.text
    assert "Se leyeron los CVs" in r.text and "no se leyó ningún CV" not in r.text and len(graph.calls) == 2 and tmp_dirs(hub) == []


def test_an_unexpected_error_is_reported_without_details_and_cleans_up(graph, hub, monkeypatch, caplog):
    a, _ = seed()
    ids = [a["profile_id"]]
    digest = digest_of(summary(client(), ids).text)

    def boom(url, token, sink, **k):
        sink.write(b"parcial")
        raise RuntimeError("fallo con https://leak.example y " + TOKEN)
    monkeypatch.setattr(microsoft365, "graph_download_capped", boom)
    with caplog.at_level("DEBUG"):
        r = download(client(), ids, digest)
    assert r.status_code == 500 and 'data-dl-state="error"' in r.text and "leak" not in r.text and TOKEN not in r.text
    assert TOKEN not in "\n".join(rec.getMessage() for rec in caplog.records) and tmp_dirs(hub) == []
    ok = download(client(), ids, digest_of(summary(client(), ids).text))                     # el slot y el temporal quedaron libres
    assert ok.status_code == 500 or ok.status_code == 200


# ───────────────────────── concurrencia ─────────────────────────
def test_a_second_concurrent_export_by_the_same_user_is_refused_with_429_and_the_first_completes(graph, hub):
    a, _ = seed()
    ids = [a["profile_id"]]
    digest = digest_of(summary(client(), ids).text)
    graph.hold = threading.Event()
    results = {}
    t = threading.Thread(target=lambda: results.setdefault("first", download(client(), ids, digest)))
    t.start()
    assert graph.entered.wait(5)
    busy = download(client(), ids, digest)
    assert busy.status_code == 429 and busy.headers["retry-after"] == "5" and 'data-dl-state="busy"' in busy.text
    graph.hold.set()
    t.join(15)
    assert results["first"].headers["content-type"] == "application/zip" and len(audit_dirs(hub)) == 1
    again = download(client(), ids, digest)                                                 # liberado
    assert again.headers["content-type"] == "application/zip"


# ───────────────────────── cancelación por desconexión ─────────────────────────
def test_a_client_disconnect_cancels_the_read_cleans_up_and_records_the_cancellation(graph, hub):
    a, b = seed()
    ids = [a["profile_id"], b["profile_id"]]
    digest = digest_of(summary(client(), ids).text)
    started = threading.Event()

    def slow_graph(url, token, sink, *, cancel=None, **k):
        item = re.search(r"/items/([^/]+)/content", url).group(1)
        graph.calls.append(item)
        started.set()
        deadline = time.time() + 8
        while time.time() < deadline and not cancel.is_set():                                 # el CV «tarda»: solo termina si se cancela
            time.sleep(0.05)
        return {"outcome": "cancelled" if cancel.is_set() else "ok", "http_status": None, "bytes": 0}
    original = microsoft365.graph_download_capped
    microsoft365.graph_download_capped = slow_graph
    try:
        body = "&".join([f"profile_ids={i}" for i in ids] + [f"graph_token={TOKEN}", f"plan_digest={digest}", "ack_omitted=1"]).encode()
        sent = {"done": False}

        async def receive():
            if not sent["done"]:
                sent["done"] = True
                return {"type": "http.request", "body": body, "more_body": False}
            if started.is_set():
                return {"type": "http.disconnect"}                                            # el navegador cortó la solicitud (como un servidor real: disponible sin esperar)
            await asyncio.sleep(0)                                                            # todavía no hay mensaje: `is_disconnected()` lo ve como «nada nuevo»
            return {"type": "http.request", "body": b"", "more_body": False}
        cookie = auth_service.create_session({"email": _HC, "name": "T", "first_name": "T", "family_name": "U"})
        scope = {"type": "http", "method": "POST", "path": "/cchh/candidatos/descargar", "raw_path": b"/cchh/candidatos/descargar", "query_string": b"", "scheme": "http", "server": ("testserver", 80),
                 "headers": [(b"content-type", b"application/x-www-form-urlencoded"), (b"origin", b"http://testserver"), (b"host", b"testserver"),
                             (b"cookie", f"{auth_service.COOKIE_NAME}={cookie}".encode()), (b"content-length", str(len(body)).encode())], "app": app, "client": ("testclient", 1)}
        request = Request(scope, receive)
        t0 = time.time()
        response = asyncio.run(router_module.human_capital_profiles_download(request, profile_ids=ids, graph_token=TOKEN, plan_digest=digest, ack_omitted="1", allow_partial="", ack_failed=[],
                                                                            parent_operation_id=""))
    finally:
        microsoft365.graph_download_capped = original
    assert time.time() - t0 < 6 and graph.calls == ["item-doc-a1"]                           # no siguió con los demás CVs
    assert "application/zip" not in response.headers.get("content-type", "") and tmp_dirs(hub) == []
    op = audit_dirs(hub)[0]
    assert export.read_audit(op)["result"]["outcome"] == "cancelled"


# ───────────────────────── JS ─────────────────────────
def test_download_js_module_invariants_hold():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node no disponible")
    result = subprocess.run([node, str(_HARNESS)], capture_output=True, text=True, timeout=120, encoding="utf-8")
    assert result.returncode == 0, f"Human Capital candidates download JS failed:\n{result.stdout}\n{result.stderr}"
