"""Inventario histórico de CVs (SOLO LECTURA): recorrido recursivo paginado con Graph simulado, agregados por especialidad/formato/tamaño, compatibilidad, repetidos por identidad y por hash,
errores de acceso/paginación, límites, y las garantías de seguridad (solo GET, sin descarga, sin nombres de archivo/token/link en resultado ni logs, solo APP_OWNER)."""
from __future__ import annotations

import email.message
import io
import itertools
import json
import logging
import re
import subprocess
import sys
import urllib.error
import urllib.parse
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core import microsoft365
from webapp import access
from webapp.main import app
from webapp.services import auth_service, cv_history_inventory as inv

OWNER = "owner-cvinv@danwor.com"
HC_USER = "hc-cvinv@danwor.com"
TOKEN = "eyJhbGciOiJub25lIn0.SECRET-READ-TOKEN-VALUE.sig"
SHARE = "https://danwor.sharepoint.com/:f:/s/Comunicaciones/IgSECRETSHARELINK?e=tokenlike"
PERSON = "Juan Pérez Gómez"
KB, MB = 1024, 1024 * 1024


def http_error(code, *, headers=None, body=None):
    hdrs = email.message.Message()
    for k, v in (headers or {}).items():
        hdrs[k] = v
    raw = json.dumps(body if body is not None else {"error": {"code": "accessDenied", "message": f"Access denied to {PERSON}", "innerError": {"request-id": "req-1"}}}).encode()
    return urllib.error.HTTPError("https://graph.microsoft.com/v1.0/x", code, "Err", hdrs, io.BytesIO(raw))


def folder(fid, name, **extra):
    return {"id": fid, "name": name, "folder": {"childCount": 0}, **extra}


def file(fid, name, size, qx=None, **extra):
    f = {"id": fid, "name": name, "size": size, "file": {"mimeType": "application/pdf", **({"hashes": {"quickXorHash": qx}} if qx else {})}}
    f.update(extra)
    return f


class FakeGraph:
    """`tree`: {folder_id: [pagina0_items, pagina1_items, …]}. `errors`: {(folder_id, pagina): [excepciones sucesivas]}. Registra TODAS las URLs pedidas."""

    def __init__(self, tree, errors=None, root_is_folder=True, loop=False):
        self.tree, self.errors, self.loop, self.urls = tree, {k: list(v) for k, v in (errors or {}).items()}, loop, []
        self.root = {"id": "ROOT", "name": "Historico", "webUrl": "https://danwor.sharepoint.com/x", "parentReference": {"siteId": "h,s,w", "driveId": "b!DRIVE"}}
        if root_is_folder:
            self.root["folder"] = {}
        else:
            self.root["file"] = {}

    def __call__(self, url, token, **kw):
        assert token == TOKEN
        self.urls.append(url)
        path, _, query = url.partition("?")
        if "/shares/" in path:
            return self.root
        m = re.search(r"/drives/([^/]+)/items/([^/]+)/children$", path)
        assert m, f"URL inesperada: {path}"
        fid = urllib.parse.unquote(m.group(2))
        page = int(urllib.parse.parse_qs(query).get("p", ["0"])[0])
        queue = self.errors.get((fid, page))
        if queue:
            raise queue.pop(0)
        pages = self.tree[fid]
        out = {"value": pages[page]}
        if self.loop:
            out["@odata.nextLink"] = url
        elif page + 1 < len(pages):
            out["@odata.nextLink"] = f"{path}?$skiptoken=tok&p={page + 1}"
        return out


@pytest.fixture()
def graph(monkeypatch):
    def install(tree, **kw):
        fake = FakeGraph(tree, **kw)
        monkeypatch.setattr(microsoft365, "graph_get_json", fake)
        for forbidden in ("graph_post_json", "graph_put_bytes", "graph_delete", "graph_get_bytes"):
            monkeypatch.setattr(microsoft365, forbidden, lambda *a, **k: (_ for _ in ()).throw(AssertionError("el inventario es de SOLO LECTURA")))
        return fake
    return install


def standard_tree():
    return {
        "ROOT": [[folder("F-DS", "Datasphere"), folder("F-DB", "Databricks"), folder("F-EMPTY", "Vacía"), file("R1", "suelto.pdf", 50 * KB)]],
        "F-DS": [[file("A1", "CV Datasphere uno.pdf", 200 * KB, qx="HASH-1"), file("A2", "otro.DOCX", 3 * MB, qx="HASH-2"), folder("F-DS-SUB", "Senior")]],
        "F-DS-SUB": [[file("A3", "tercero.pdf", 7 * MB, qx="HASH-3"), file("A4", "~$lock.docx", 162), file("A5", "Thumbs.db", 3 * KB)]],
        "F-DB": [[file("B1", "dup-de-A1.pdf", 200 * KB, qx="HASH-1"), file("B2", "viejo.doc", 80 * KB, qx="HASH-4"), file("B3", "planilla.xlsx", 20 * KB),
                  file("B4", "enorme.pdf", 11 * MB, qx="HASH-5"), file("B5", "vacio.pdf", 0), file("B6", "sin-hash.pdf", 90 * KB)]],
        "F-EMPTY": [[]],
    }


# ═══════════════════ agregados ═══════════════════
def test_walk_counts_folders_files_depth_specialties_and_empty_folders(graph):
    graph(standard_tree())
    r = inv.run_inventory(TOKEN, SHARE)
    assert r["ok"] and r["complete"] and r["stopped_reason"] is None and r["errors"] == []
    t = r["totals"]
    assert (t["folders"], t["files"], t["max_depth"], t["empty_folders"], t["pending_folders"]) == (4, 12, 2, 1, 0)
    by = {s["name"]: s for s in r["specialties"]}
    assert by["Datasphere"]["files"] == 5 and by["Datasphere"]["subfolders"] == 1                                   # la subcarpeta hereda la especialidad de primer nivel
    assert by["Databricks"]["files"] == 6 and by["(raíz)"]["files"] == 1 and by["Vacía"]["files"] == 0
    assert [s["name"] for s in r["specialties"]][:2] == ["Databricks", "Datasphere"]                                  # orden por archivos desc


def test_classification_is_by_extension_and_size_with_the_hub_document_limits(graph):
    graph(standard_tree())
    c = inv.run_inventory(TOKEN, SHARE)["classification"]
    assert c == {"compatible": 6, "doc_manual": 1, "unsupported": 1, "too_large": 1, "empty": 1, "ignored": 2}
    assert inv.classify("CV.PDF", 1) == "compatible" and inv.classify("a.docx", 10 * MB) == "compatible" and inv.classify("a.docx", 10 * MB + 1) == "too_large"
    assert inv.classify("a.doc", 5) == "doc_manual" and inv.classify("a.odt", 5) == "unsupported" and inv.classify("a", 5) == "unsupported"
    assert inv.classify(".hidden.pdf", 5) == "ignored" and inv.classify("x.tmp", 5) == "ignored"


def test_formats_and_sizes(graph):
    graph(standard_tree())
    r = inv.run_inventory(TOKEN, SHARE)
    fmt = {f["ext"]: f["files"] for f in r["formats"]}
    assert fmt["pdf"] == 7 and fmt["docx"] == 2 and fmt["doc"] == 1 and fmt["xlsx"] == 1 and fmt["db"] == 1
    s = r["sizes"]
    assert s["limit_bytes"] == 10 * MB and s["considered_files"] == 9 and s["max"] == 11 * MB and s["min"] == 20 * KB                   # sin vacíos ni temporales
    assert s["buckets"] == {"<100 KB": 4, "100 KB–1 MB": 2, "1–5 MB": 1, "5–10 MB": 1, ">10 MB": 1}


def test_repeated_graph_identity_and_hash_duplicates(graph):
    tree = standard_tree()
    tree["ROOT"] = [tree["ROOT"][0], [file("A1", "repetido-por-paginacion.pdf", 200 * KB, qx="HASH-1")]]            # el MISMO id vuelve a aparecer en otra página
    graph(tree)
    d = inv.run_inventory(TOKEN, SHARE)["duplicates"]
    assert d["graph_identity_repeated"] == 1
    h = d["hash"]
    assert h["groups"] == 1 and h["files_in_groups"] == 2 and h["surplus_copies"] == 1 and h["cross_specialty_groups"] == 1          # A1 (Datasphere) = B1 (Databricks)
    assert h["without_hash"] == 3 and h["with_hash"] == 6                                                           # empty/ignored fuera; resto con/sin hash


def test_shortcuts_packages_and_remote_duplicates_are_not_counted_as_files(graph):
    tree = {"ROOT": [[{"id": "S1", "name": "Acceso", "remoteItem": {"id": "REMOTE-1"}}, {"id": "S2", "name": "Otro acceso", "remoteItem": {"id": "REMOTE-1"}},
                      {"id": "P1", "name": "Cuaderno", "package": {"type": "oneNote"}}, file("X1", "a.pdf", 10 * KB)]]}
    graph(tree)
    r = inv.run_inventory(TOKEN, SHARE)
    assert r["totals"]["shortcuts"] == 2 and r["totals"]["other_items"] == 1 and r["totals"]["files"] == 1 and r["duplicates"]["remote_item_repeated"] == 1


def test_specialty_variants_are_reported_not_merged(graph):
    tree = {"ROOT": [[folder("F1", "Ingeniería de Datos"), folder("F2", "Ingenieria de  datos"), folder("F3", "Cloud")]], "F1": [[]], "F2": [[]], "F3": [[]]}
    graph(tree)
    r = inv.run_inventory(TOKEN, SHARE)
    assert r["specialty_variants"] == [["Ingenieria de  datos", "Ingeniería de Datos"]] and {"Ingeniería de Datos", "Ingenieria de  datos", "Cloud"} <= {s["name"] for s in r["specialties"]}


# ═══════════════════ paginación, errores y límites ═══════════════════
def test_pagination_is_followed_across_pages(graph):
    pages = [[file(f"P{i}-{j}", f"c{i}-{j}.pdf", KB) for j in range(3)] for i in range(4)]
    fake = graph({"ROOT": pages})
    r = inv.run_inventory(TOKEN, SHARE)
    assert r["totals"]["files"] == 12 and r["stats"]["pages"] == 4 and r["complete"]
    assert all(("$top=200" in u) or "skiptoken" in u for u in fake.urls[1:]) and "$select=" in fake.urls[1]


def test_access_error_in_one_folder_is_reported_and_the_rest_is_still_inventoried(graph):
    graph(standard_tree(), errors={("F-DB", 0): [http_error(403)]})
    r = inv.run_inventory(TOKEN, SHARE)
    assert r["ok"] and not r["complete"]
    assert [(e["scope"], e["kind"], e["folder"], e["status"], e["code"]) for e in r["errors"]] == [("folder", "access", "Databricks", 403, "accessDenied")]
    assert r["totals"]["files"] == 6 and {s["name"] for s in r["specialties"]} >= {"Datasphere", "Databricks"}


def test_pagination_failure_on_a_later_page_keeps_what_was_read_and_flags_it(graph):
    tree = {"ROOT": [[file("a", "a.pdf", KB)], [file("b", "b.pdf", KB)]]}
    graph(tree, errors={("ROOT", 1): [http_error(500)] * 4})
    r = inv.run_inventory(TOKEN, SHARE, sleep=lambda s: None)
    assert r["totals"]["files"] == 1 and not r["complete"]
    assert [(e["kind"], e["page"], e["status"]) for e in r["errors"]] == [("pagination", 2, 500)]


def test_a_repeated_next_link_stops_the_folder_instead_of_looping(graph):
    graph({"ROOT": [[file("a", "a.pdf", KB)]]}, loop=True)
    r = inv.run_inventory(TOKEN, SHARE)
    assert r["totals"]["files"] == 1 and [e["kind"] for e in r["errors"]] == ["pagination_loop"] and not r["complete"]


def test_throttling_respects_retry_after_then_succeeds_and_gives_up_after_the_cap(graph):
    sleeps: list[float] = []
    graph({"ROOT": [[file("a", "a.pdf", KB)]]}, errors={("ROOT", 0): [http_error(429, headers={"Retry-After": "3"}), http_error(503)]})
    r = inv.run_inventory(TOKEN, SHARE, sleep=sleeps.append)
    assert r["complete"] and sleeps == [3.0, 2.0] and r["stats"]["throttled_retries"] == 2
    sleeps.clear()
    graph({"ROOT": [[file("a", "a.pdf", KB)]]}, errors={("ROOT", 0): [http_error(429)] * 6})
    r = inv.run_inventory(TOKEN, SHARE, sleep=sleeps.append)
    assert not r["complete"] and r["errors"][0]["kind"] == "throttled" and len(sleeps) == inv.MAX_ATTEMPTS - 1 and r["totals"]["files"] == 0
    graph({"ROOT": [[file("a", "a.pdf", KB)]]}, errors={("ROOT", 0): [http_error(429, headers={"Retry-After": "9999"})]})
    sleeps.clear()
    inv.run_inventory(TOKEN, SHARE, sleep=sleeps.append)
    assert sleeps == [inv.RETRY_AFTER_CAP_S]                                                                         # el Retry-After se acota


def test_network_errors_are_retried_and_then_reported(graph):
    graph({"ROOT": [[file("a", "a.pdf", KB)]]}, errors={("ROOT", 0): [urllib.error.URLError("boom")] * 4})
    r = inv.run_inventory(TOKEN, SHARE, sleep=lambda s: None)
    assert [(e["kind"], e["reason"]) for e in r["errors"]] == [("network", "URLError")] and not r["complete"]


def test_time_budget_and_size_limits_never_present_a_partial_inventory_as_complete(graph):
    tree = {"ROOT": [[folder(f"F{i}", f"Esp{i}") for i in range(5)]], **{f"F{i}": [[file(f"x{i}", "a.pdf", KB)]] for i in range(5)}}
    graph(tree)
    ticks = itertools.count(0, 30)
    r = inv.run_inventory(TOKEN, SHARE, clock=lambda: next(ticks), time_budget=50)
    assert r["stopped_reason"] == "TIME_BUDGET" and not r["complete"] and r["totals"]["pending_folders"] > 0
    graph(tree)
    r = inv.run_inventory(TOKEN, SHARE, max_folders=2)
    assert r["stopped_reason"] == "MAX_FOLDERS" and not r["complete"]
    graph(tree)
    r = inv.run_inventory(TOKEN, SHARE, max_items=3)
    assert r["stopped_reason"] == "MAX_ITEMS" and not r["complete"]


# ═══════════════════ entrada y enlace ═══════════════════
def test_invalid_links_are_rejected_before_any_graph_call(graph):
    fake = graph({"ROOT": [[]]})
    for bad in ("http://danwor.sharepoint.com/:f:/s/x", "https://evil.example/:f:/s/x", "", "javascript:alert(1)", "https://danwor.sharepoint.com.evil.example/x"):
        with pytest.raises(inv.InventoryInputError):
            inv.run_inventory(TOKEN, bad)
    assert fake.urls == []


def test_a_file_link_or_an_unresolvable_link_is_reported_without_walking(graph):
    graph({"ROOT": [[]]}, root_is_folder=False)
    r = inv.run_inventory(TOKEN, SHARE)
    assert not r["ok"] and r["errors"][0]["kind"] == "not_a_folder"


# ═══════════════════ solo lectura, sin descarga, sin datos personales ═══════════════════
def test_only_get_children_and_share_resolution_are_ever_requested(graph):
    fake = graph(standard_tree())
    inv.run_inventory(TOKEN, SHARE)
    assert fake.urls and all(("/shares/" in u.split("?")[0]) or u.split("?")[0].endswith("/children") for u in fake.urls)
    assert not any(x in u for u in fake.urls for x in ("/content", "/copy", "/permissions", "/versions", "/createLink"))
    assert "SECRETSHARELINK" not in " ".join(fake.urls)                                                              # el link viaja codificado (u!base64url), nunca en claro


def test_module_has_no_dependency_on_profile_or_document_services_or_write_helpers():
    code = subprocess.run([sys.executable, "-c", "import sys, webapp.services.cv_history_inventory as m; bad=[n for n in ('webapp.services.profile_repository','webapp.services.profile_document_service','webapp.services.profile_document_repository','webapp.services.human_capital_service') if n in sys.modules]; print(bad)"],
                          capture_output=True, text=True, cwd=Path(__file__).resolve().parents[2])
    assert code.returncode == 0 and code.stdout.strip() == "[]", code.stderr
    src = (Path(__file__).resolve().parents[2] / "webapp/services/cv_history_inventory.py").read_text(encoding="utf-8")
    assert not re.search(r"graph_(post|put|delete|get_bytes)", src.split('"""', 2)[2])


def test_result_and_logs_carry_no_file_names_token_or_link(graph, caplog):
    tree = standard_tree()
    tree["F-DS"][0].append(file("PERS", f"CV {PERSON}.pdf", 100 * KB, qx="HASH-P"))
    graph(tree, errors={("F-DB", 0): [http_error(403)]})
    with caplog.at_level(logging.DEBUG):
        r = inv.run_inventory(TOKEN, SHARE)
    blob = json.dumps(r, ensure_ascii=False) + caplog.text
    for secret in (PERSON, "Pérez", "dup-de-A1", "CV Datasphere uno", TOKEN, "SECRET", "SECRETSHARELINK", "tokenlike", "Access denied"):
        assert secret not in blob, secret
    assert "Datasphere" in blob                                                                                      # la especialidad (nombre de carpeta de primer nivel) sí es el objeto del inventario


# ═══════════════════ interfaz administrativa ═══════════════════
def _client(email: str, monkeypatch) -> TestClient:
    monkeypatch.setattr(auth_service, "ALLOWED_USERS", "ALL")
    monkeypatch.setattr(auth_service, "HUMAN_CAPITAL_ALLOWED_USERS", f"{OWNER},{HC_USER}")
    monkeypatch.setattr(access, "APP_OWNER", OWNER)
    c = TestClient(app, follow_redirects=False)
    c.cookies.set(auth_service.COOKIE_NAME, auth_service.create_session({"email": email, "name": "T", "first_name": "T", "family_name": "T"}))
    return c


URL = "/cchh/diagnostico/sharepoint/inventario"


def test_route_is_owner_only_requires_a_token_and_never_echoes_it(monkeypatch, graph):
    graph(standard_tree())
    assert TestClient(app, follow_redirects=False).post(URL, data={"graph_token": TOKEN, "share_url": SHARE}).status_code in (302, 303, 307)
    assert _client(HC_USER, monkeypatch).post(URL, data={"graph_token": TOKEN, "share_url": SHARE}).status_code == 403
    owner = _client(OWNER, monkeypatch)
    missing = owner.post(URL, data={"graph_token": "", "share_url": SHARE})
    assert missing.status_code == 200 and 'data-error-kind="missing_token"' in missing.text and "Sin token" in missing.text and missing.headers["cache-control"] == "no-store"      # 200: HTMX no pinta 4xx
    bad = owner.post(URL, data={"graph_token": TOKEN, "share_url": "http://x"})
    assert bad.status_code == 200 and 'data-error-kind="invalid_link"' in bad.text and "https://*.sharepoint.com" in bad.text
    ok = owner.post(URL, data={"graph_token": TOKEN, "share_url": SHARE})
    assert ok.status_code == 200 and ok.headers["cache-control"] == "no-store" and TOKEN not in ok.text and "SECRETSHARELINK" not in ok.text


def test_route_renders_the_inventory_and_flags_partial_results(monkeypatch, graph):
    graph(standard_tree())
    owner = _client(OWNER, monkeypatch)
    html = owner.post(URL, data={"graph_token": TOKEN, "share_url": SHARE}).text
    assert 'data-inventory-state="complete"' in html and "Inventario completo" in html and "data-inventory-no-errors" in html
    assert re.search(r'data-inventory-totals.*?Carpetas</td><td class="left">4</td><td class="left">Archivos</td><td class="left">12</td>', html, re.S)
    assert "Datasphere" in html and "Databricks" in html and "(raíz)" in html and "DOC binario (revisión manual)" in html
    assert "CV Datasphere uno" not in html and "dup-de-A1" not in html and "viejo.doc" not in html                    # sin nombres de archivo
    graph(standard_tree(), errors={("F-DB", 0): [http_error(403)]})
    partial = owner.post(URL, data={"graph_token": TOKEN, "share_url": SHARE}).text
    assert 'data-inventory-state="partial"' in partial and "Inventario INCOMPLETO" in partial and "data-inventory-errors" in partial and "accessDenied" in partial


def test_route_reports_a_file_link_and_an_unresolvable_link_as_errors_not_crashes(monkeypatch, graph):
    owner = _client(OWNER, monkeypatch)
    graph({"ROOT": [[]]}, root_is_folder=False)
    assert "El link no es una carpeta" in owner.post(URL, data={"graph_token": TOKEN, "share_url": SHARE}).text

    def denied(url, token, **kw):
        raise http_error(403)
    monkeypatch.setattr(microsoft365, "graph_get_json", denied)
    r = owner.post(URL, data={"graph_token": TOKEN, "share_url": SHARE})
    assert r.status_code == 200 and "No se pudo resolver el link" in r.text and "Access denied" not in r.text


def test_page_exposes_the_read_only_inventory_form_with_the_shared_token_hook(monkeypatch):
    page = _client(OWNER, monkeypatch).get("/cchh/diagnostico/sharepoint").text
    form = re.search(r'<form id="dw-cvinv-form"[^>]*>', page).group(0)
    assert 'hx-post="/cchh/diagnostico/sharepoint/inventario"' in form and "data-dw-graph-token" in form and 'hx-target="#dw-cvinv-result"' in form
    assert "Inventario histórico de CVs (solo lectura)" in page and "dwHumanCapitalDocumentWriteToken" in page                  # la sonda existente sigue ahí
    assert TOKEN not in page and "sessionStorage.getItem" not in page
    assert _client(HC_USER, monkeypatch).get("/cchh/diagnostico/sharepoint").status_code == 403
