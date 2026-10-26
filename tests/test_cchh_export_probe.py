"""Sonda de medición de la descarga de CVs (`tools/cchh_export_probe.py`) validada con un Graph SIMULADO en localhost y archivos SINTÉTICOS: lectura acotada (antes y durante la transferencia),
clasificación de 403/404/429/cortes/plazos, privacidad de la salida (solo tiempos, tamaños y códigos), autorización explícita, token solo por entorno, limpieza del temporal y que el token no viaje a un host ajeno."""
from __future__ import annotations

import importlib.util
import io
import json
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parents[1] / "tools" / "cchh_export_probe.py"
_spec = importlib.util.spec_from_file_location("cchh_export_probe", _PATH)
probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(probe)

TOKEN = "tok-sintetico-NO-REAL-123"
SECRET_NAME = "Juan Perez CV confidencial.pdf"
PAYLOAD = (b"%PDF-sintetico" + bytes(range(256)) * 2000)            # ~512 KB


class Graph:
    """Graph simulado: cuenta pedidos y bytes servidos, registra el encabezado Authorization y reproduce los casos de falla."""

    def __init__(self):
        self.hits: list[str] = []
        self.auth: list[str] = []
        self.served: dict[str, int] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):  # noqa: D401
                pass

            def _send(self, code, body=b"", headers=None, length=True):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                if length:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                path = self.path
                outer.hits.append(path)
                outer.auth.append(self.headers.get("Authorization", ""))
                item = re.search(r"/items/([^/]+)/content", path)
                name = item.group(1) if item else path.rsplit("/", 1)[-1]
                if path.startswith("/dl/"):                                      # URL de descarga pre-autenticada (destino del redirect de Graph)
                    outer.served["ok"] = len(PAYLOAD)
                    return self._send(200, PAYLOAD)
                if name == "ok":
                    return self._send(302, headers={"Location": "/dl/ok"})
                if name == "big":
                    outer.served["big"] = 0
                    return self._send(200, b"x" * 3_000_000)                       # Content-Length 3 MB (tope de la prueba: 1 MB)
                if name == "nolen":                                              # sin Content-Length: se corta al superar el tope
                    self.send_response(200)
                    self.end_headers()
                    sent = 0
                    try:
                        while sent < 6_000_000:
                            self.wfile.write(b"y" * 65536)
                            sent += 65536
                            outer.served["nolen"] = sent
                    except OSError:
                        pass
                    return None
                if name == "slow":
                    self.send_response(200)
                    self.send_header("Content-Length", "100000")
                    self.end_headers()
                    try:
                        for _ in range(100):
                            self.wfile.write(b"z" * 1000)
                            self.wfile.flush()
                            time.sleep(0.05)
                    except OSError:
                        pass
                    return None
                if name == "short":
                    self.send_response(200)
                    self.send_header("Content-Length", "100000")
                    self.end_headers()
                    self.wfile.write(b"q" * 500)
                    return None
                if name == "hang":
                    time.sleep(3)
                    return self._send(200, b"late")
                if name == "forbidden":
                    return self._send(403, b"{}")
                if name == "missing":
                    return self._send(404, b"{}")
                if name == "throttled":
                    return self._send(429, b"{}", headers={"Retry-After": "7"})
                return self._send(500, b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def graph():
    g = Graph()
    yield g
    g.close()


def fetch(graph, name, *, cap=1_000_000, declared=None, socket_timeout=2.0, deadline=5.0):
    sink = io.BytesIO()
    result = probe.read_capped(f"{graph.base}/drives/d1/items/{name}/content", TOKEN, sink, cap=cap, declared_size=declared, socket_timeout=socket_timeout, deadline_s=deadline)
    return result, sink.getvalue()


# ───────────────────────── lectura acotada ─────────────────────────
def test_ok_follows_the_download_redirect_and_sends_the_bearer_token(graph):
    result, data = fetch(graph, "ok")
    assert result["outcome"] == "ok" and result["http_status"] == 200 and result["bytes"] == len(PAYLOAD) == len(data) and data == PAYLOAD
    assert result["ttfb_ms"] is not None and result["transfer_ms"] is not None and result["total_ms"] >= result["ttfb_ms"]
    assert graph.auth[0] == f"Bearer {TOKEN}"


def test_declared_size_over_the_cap_is_rejected_before_any_request(graph):
    result, data = fetch(graph, "ok", declared=2_000_000)
    assert result["outcome"] == "oversize_declared" and data == b"" and graph.hits == []


def test_content_length_over_the_cap_is_rejected_without_reading_the_body(graph):
    result, data = fetch(graph, "big")
    assert result["outcome"] == "oversize_header" and result["content_length"] == 3_000_000 and data == b"" and result["bytes"] == 0


def test_stream_without_content_length_is_cut_when_it_exceeds_the_cap(graph):
    result, data = fetch(graph, "nolen")
    assert result["outcome"] == "oversize_stream"
    assert len(data) <= 1_000_000                                                    # nunca se escribe más que el tope (el trozo que lo supera se descarta)
    assert result["bytes"] <= 1_000_000 + probe.CHUNK


def test_total_deadline_cuts_a_slow_transfer_even_if_the_socket_never_times_out(graph):
    started = time.perf_counter()
    result, data = fetch(graph, "slow", deadline=0.4)
    assert result["outcome"] == "deadline" and time.perf_counter() - started < 3 and len(data) < 100_000


def test_short_read_and_socket_timeout_and_http_codes_are_classified(graph):
    assert fetch(graph, "short")[0]["outcome"] == "short_read"
    assert fetch(graph, "hang", socket_timeout=0.5)[0]["outcome"] == "timeout"
    assert fetch(graph, "forbidden")[0]["outcome"] == "http_403"
    assert fetch(graph, "missing")[0]["outcome"] == "http_404"
    throttled = fetch(graph, "throttled")[0]
    assert throttled["outcome"] == "http_429" and throttled["retry_after_s"] == 7
    assert fetch(graph, "boom")[0]["outcome"] == "http_500"


def test_connection_refused_is_a_code_not_an_exception_text():
    sink = io.BytesIO()
    result = probe.read_capped("http://127.0.0.1:9/drives/d/items/i/content", TOKEN, sink, cap=1000, socket_timeout=1.0, deadline_s=2.0)
    assert result["outcome"] in {"url_error", "timeout"} and "127.0.0.1" not in json.dumps(result)


# ───────────────────────── ejecución real (simulada) ─────────────────────────
@pytest.fixture()
def docs_dir(tmp_path):
    d = tmp_path / "profile-documents"
    d.mkdir()
    for doc_id, item, size in (("doc-a", "ok", len(PAYLOAD)), ("doc-b", "ok", len(PAYLOAD)), ("doc-c", "forbidden", 1000), ("doc-d", "ok", 50 * 1024 * 1024)):
        (d / f"{doc_id}.json").write_text(json.dumps({"document_id": doc_id, "profile_id": "p1", "drive_id": "b!drive", "item_id": item, "filename": SECRET_NAME, "size_bytes": size,
                                                      "web_url": "https://tenant.sharepoint.invalid/secreto.pdf", "uploaded_by": "rrhh@sintetico.test"}), encoding="utf-8")
    return d


def run(capsys, monkeypatch, graph, docs_dir, *extra, token=TOKEN, confirm=probe.CONFIRM_PHRASE, docs=("doc-a", "doc-b")):
    if token:
        monkeypatch.setenv(probe.TOKEN_ENV, token)
    else:
        monkeypatch.delenv(probe.TOKEN_ENV, raising=False)
    argv = ["--real", "--documents-dir", str(docs_dir), "--graph-base", graph.base, "--disk-bench-mb", "0", "--socket-timeout", "2", "--doc-deadline", "5"]
    for d in docs:
        argv += ["--document-id", d]
    if confirm is not None:
        argv += ["--confirm", confirm]
    code = probe.main(argv + list(extra))
    out = capsys.readouterr().out
    return code, json.loads(out), out


def test_real_run_measures_everything_and_leaves_no_temporary_files(capsys, monkeypatch, graph, docs_dir, tmp_path):
    monkeypatch.setattr(probe.tempfile, "tempdir", str(tmp_path))
    before = set(tmp_path.iterdir())
    code, report, _ = run(capsys, monkeypatch, graph, docs_dir)
    assert code == 0 and report["all_ok"] is True and report["mode"] == "real"
    docs = report["run"]["documents"]
    assert [d["n"] for d in docs] == [1, 2] and all(d["outcome"] == "ok" and d["bytes"] == len(PAYLOAD) for d in docs)
    assert all(k in docs[0] for k in ("ttfb_ms", "transfer_ms", "total_ms", "zip_add_ms", "mb_per_s", "declared_size"))
    z = report["run"]["zip"]
    assert z["files"] == 2 and z["input_bytes"] == 2 * len(PAYLOAD) and z["output_bytes"] > 0 and z["build_ms"] >= 0
    assert report["run"]["total_elapsed_ms"] >= docs[0]["total_ms"] and report["run"]["projection"]["basis_documents"] == 2
    assert set(tmp_path.iterdir()) == before                                         # el directorio temporal (CVs y ZIP) se borra siempre


def test_output_has_only_times_sizes_and_codes_never_names_tokens_ids_or_urls(capsys, monkeypatch, graph, docs_dir):
    _, _, raw = run(capsys, monkeypatch, graph, docs_dir, docs=("doc-a", "doc-c"))
    for forbidden in (TOKEN, "Juan", "Perez", "confidencial", "sharepoint", "rrhh@", "b!drive", "doc-a", "doc-c", "127.0.0.1", "profile", "p1"):
        assert forbidden not in raw, forbidden


def test_zip_entries_have_fixed_names_and_contain_exactly_the_downloaded_bytes(monkeypatch, graph, docs_dir, tmp_path):
    kept = {}
    real_write = probe.zipfile.ZipFile.write

    def spy(self, filename, arcname=None, *a, **k):
        kept[arcname] = Path(filename).read_bytes()
        return real_write(self, filename, arcname, *a, **k)

    monkeypatch.setattr(probe.zipfile.ZipFile, "write", spy)
    report = probe.real_run(document_ids=["doc-a", "doc-b"], documents_dir=docs_dir, token=TOKEN, graph_base=graph.base, cap=1_000_000, compression="deflated", socket_timeout=2, deadline_s=5)
    assert report["zip"]["files"] == 2 and sorted(kept) == ["doc-1.bin", "doc-2.bin"] and all(v == PAYLOAD for v in kept.values())


def test_a_failing_document_does_not_hide_the_others_and_sets_the_exit_code(capsys, monkeypatch, graph, docs_dir):
    code, report, _ = run(capsys, monkeypatch, graph, docs_dir, docs=("doc-a", "doc-c"))
    outcomes = [d["outcome"] for d in report["run"]["documents"]]
    assert code == 2 and outcomes == ["ok", "http_403"] and report["all_ok"] is False and report["run"]["zip"]["files"] == 1


def test_declared_size_over_the_cap_never_reaches_the_network(capsys, monkeypatch, graph, docs_dir):
    code, report, _ = run(capsys, monkeypatch, graph, docs_dir, docs=("doc-d",))
    assert report["run"]["documents"][0]["outcome"] == "oversize_declared" and graph.hits == []


# ───────────────────────── autorización y seguridad ─────────────────────────
def test_real_mode_requires_the_explicit_confirmation_phrase_before_any_network_or_file_access(capsys, monkeypatch, graph, docs_dir):
    for confirm in (None, "", "si", "autorizado-por-el-owner"):
        code, report, _ = run(capsys, monkeypatch, graph, docs_dir, confirm=confirm)
        assert code == 3 and "autorización" in report["error"] and graph.hits == []


def test_real_mode_needs_the_token_in_the_environment_never_as_an_argument(capsys, monkeypatch, graph, docs_dir):
    code, report, _ = run(capsys, monkeypatch, graph, docs_dir, token="")
    assert code == 3 and probe.TOKEN_ENV in report["error"] and graph.hits == []
    with pytest.raises(SystemExit):
        probe.main(["--real", "--token", TOKEN])                                    # no existe --token


def test_document_count_is_limited_to_three_and_ids_are_validated(capsys, monkeypatch, graph, docs_dir):
    code, report, _ = run(capsys, monkeypatch, graph, docs_dir, docs=("doc-a", "doc-b", "doc-a", "doc-b"))
    assert code == 3 and "entre 1 y 3" in report["error"]
    code, report, _ = run(capsys, monkeypatch, graph, docs_dir, docs=("../../etc/passwd",))
    assert code == 3 and "inválido" in report["error"] and graph.hits == []
    code, report, _ = run(capsys, monkeypatch, graph, docs_dir, docs=("no-existe",))
    assert code == 3 and "ilegible" in report["error"]


def test_the_token_can_only_go_to_official_graph_or_to_localhost(capsys, monkeypatch, docs_dir):
    class Dummy:
        base = "https://evil.example.com/v1.0"
        hits: list = []

    for base in ("https://evil.example.com/v1.0", "http://10.0.0.5:8000", "http://localhost.evil.com", "ftp://127.0.0.1"):
        Dummy.base = base
        code, report, _ = run(capsys, monkeypatch, Dummy, docs_dir)
        assert code == 3 and "graph-base" in report["error"]
    assert probe._validate_graph_base(probe.DEFAULT_GRAPH_BASE) == probe.DEFAULT_GRAPH_BASE
    assert probe._validate_graph_base("http://127.0.0.1:8123") == "http://127.0.0.1:8123"


def test_content_url_quotes_ids_and_the_probe_source_has_no_repo_imports_and_does_not_log_tokens():
    assert probe.content_url("http://127.0.0.1:1", "b!d,r", "01A/B") == "http://127.0.0.1:1/drives/b!d,r/items/01A%2FB/content"
    src = _PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+(webapp|core|tools|config)\b", src, re.M)         # standalone: se puede copiar solo a la consola de DO
    assert "logging" not in src and "graph_get_bytes(" not in src.replace("`core.microsoft365.graph_get_bytes`", "")


# ───────────────────────── modo sintético ─────────────────────────
def test_synthetic_bench_reports_environment_and_zip_cost_without_network_or_token(capsys, monkeypatch):
    monkeypatch.delenv(probe.TOKEN_ENV, raising=False)
    code = probe.main(["--synthetic-bench", "--bench-files", "2", "--bench-mb", "1", "--disk-bench-mb", "4"])
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["mode"] == "synthetic"
    assert set(report["zip_bench"]["runs"]) == {"stored", "deflated"} and report["zip_bench"]["files"] == 2
    env = report["environment"]
    assert env["cpu_count"] and "cgroup" in env and "free_mb" in env["tmp"]
    assert probe.main(["--synthetic-bench", "--bench-mb", "999"]) == 3
