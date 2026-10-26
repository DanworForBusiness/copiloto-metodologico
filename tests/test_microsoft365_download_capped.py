"""`core.microsoft365.graph_download_capped`: GET binario de Graph con LECTURA ACOTADA (tamaño antes y durante la transferencia, plazo total, cancelación) contra un Graph SIMULADO en localhost."""
from __future__ import annotations

import io
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from core import microsoft365

TOKEN = "tok-sintetico-NO-REAL"
PAYLOAD = b"%PDF-sintetico" + bytes(range(256)) * 1500          # ~384 KB


class Graph:
    def __init__(self):
        self.auth: list[str] = []
        self.hits: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def log_message(self, *a):
                pass

            def _send(self, code, body=b"", headers=None):
                self.send_response(code)
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_GET(self):  # noqa: N802
                outer.hits.append(self.path)
                outer.auth.append(self.headers.get("Authorization", ""))
                if self.path.startswith("/dl/"):
                    return self._send(200, PAYLOAD)
                name = re.search(r"/items/([^/]+)/content", self.path).group(1)
                if name == "ok":
                    return self._send(302, headers={"Location": "/dl/ok"})
                if name == "big":
                    return self._send(200, b"x" * 2_000_000)
                if name == "nolen":
                    self.send_response(200)
                    self.end_headers()
                    try:
                        for _ in range(100):
                            self.wfile.write(b"y" * 65536)
                    except OSError:
                        pass
                    return None
                if name in ("slow", "slow2"):
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
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def graph():
    g = Graph()
    yield g
    g.close()


def fetch(graph, name, *, cap=1_000_000, declared=None, socket_timeout=2.0, deadline=5.0, cancel=None):
    sink = io.BytesIO()
    result = microsoft365.graph_download_capped(f"{graph.base}/drives/d1/items/{name}/content", TOKEN, sink, cap_bytes=cap, declared_size=declared, socket_timeout=socket_timeout,
                                                deadline_s=deadline, cancel=cancel)
    return result, sink.getvalue()


def test_ok_follows_the_download_redirect_sends_the_bearer_and_returns_the_exact_bytes(graph):
    result, data = fetch(graph, "ok")
    assert result["outcome"] == "ok" and result["http_status"] == 200 and data == PAYLOAD and result["bytes"] == len(PAYLOAD)
    assert result["ttfb_ms"] is not None and result["total_ms"] >= result["ttfb_ms"]
    assert graph.auth[0] == f"Bearer {TOKEN}"


def test_declared_size_over_the_cap_is_never_requested(graph):
    result, data = fetch(graph, "ok", declared=2_000_000)
    assert result["outcome"] == "oversize_declared" and data == b"" and graph.hits == []


def test_content_length_over_the_cap_is_rejected_without_reading_the_body(graph):
    result, data = fetch(graph, "big")
    assert result["outcome"] == "oversize_header" and result["content_length"] == 2_000_000 and data == b"" and result["bytes"] == 0


def test_a_stream_without_content_length_is_cut_during_the_transfer(graph):
    result, data = fetch(graph, "nolen")
    assert result["outcome"] == "oversize_stream" and len(data) <= 1_000_000 and result["bytes"] <= 1_000_000 + 64 * 1024


def test_total_deadline_bounds_a_slow_transfer(graph):
    started = time.perf_counter()
    result, data = fetch(graph, "slow", deadline=0.4)
    assert result["outcome"] == "deadline" and time.perf_counter() - started < 3 and len(data) < 100_000


def test_cancellation_is_checked_between_chunks_and_before_connecting(graph):
    flag = threading.Event()
    threading.Timer(0.3, flag.set).start()
    started = time.perf_counter()
    result, data = fetch(graph, "slow2", cancel=flag, deadline=10)
    assert result["outcome"] == "cancelled" and time.perf_counter() - started < 3 and len(data) < 100_000
    result2, _ = fetch(graph, "ok", cancel=flag)
    assert result2["outcome"] == "cancelled" and result2["ttfb_ms"] is None


def test_short_read_timeout_and_http_codes_are_classified_without_exception_text(graph):
    assert fetch(graph, "short")[0]["outcome"] == "short_read"
    assert fetch(graph, "hang", socket_timeout=0.5)[0]["outcome"] == "timeout"
    assert fetch(graph, "forbidden")[0]["outcome"] == "http_403"
    assert fetch(graph, "missing")[0]["outcome"] == "http_404"
    throttled = fetch(graph, "throttled")[0]
    assert throttled["outcome"] == "http_429" and throttled["retry_after_s"] == 7
    assert fetch(graph, "boom")[0]["outcome"] == "http_500"
    refused = microsoft365.graph_download_capped("http://127.0.0.1:9/drives/d/items/i/content", TOKEN, io.BytesIO(), cap_bytes=10, socket_timeout=1, deadline_s=2)
    assert refused["outcome"] in {"url_error", "timeout"} and "127.0.0.1" not in str(refused) and TOKEN not in str(refused)


def test_the_capped_download_never_persists_anything_itself_and_does_not_retry(graph, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    fetch(graph, "throttled")
    assert list(tmp_path.iterdir()) == [] and len([h for h in graph.hits if "throttled" in h]) == 1       # sin reintentos propios: la política vive en el consumidor
