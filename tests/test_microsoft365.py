"""
test_microsoft365.py — Contrato del boundary compartido de transporte Graph
(core/microsoft365.py, Batch 1 de aceleración post-Fase 2).

Determinístico, sin dependencia de Graph real: monkeypatchea
`urllib.request.urlopen` con respuestas fabricadas. Cubre headers,
GET+decode, paginación por `@odata.nextLink`, y que `tools/catalog/
catalog_service.py` sigue funcionando tras delegar en este módulo.
"""
from __future__ import annotations

import json
import urllib.error

import pytest

from core import microsoft365


class _FakeResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_graph_headers_bearer_and_accept_json():
    headers = microsoft365.graph_headers("tok-123")
    assert headers == {"Authorization": "Bearer tok-123", "Accept": "application/json"}


def test_graph_get_json_calls_urlopen_with_expected_request_and_timeout(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        captured["timeout"] = timeout
        return _FakeResponse({"hello": "world"})

    monkeypatch.setattr(microsoft365.urllib.request, "urlopen", fake_urlopen)

    result = microsoft365.graph_get_json("https://graph.microsoft.com/v1.0/sites/x", "tok-abc")

    assert result == {"hello": "world"}
    assert captured["url"] == "https://graph.microsoft.com/v1.0/sites/x"
    assert captured["headers"] == {"Authorization": "Bearer tok-abc", "Accept": "application/json"}
    assert captured["timeout"] == microsoft365.DEFAULT_TIMEOUT


def test_graph_get_json_respects_explicit_timeout(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["timeout"] = timeout
        return _FakeResponse({})

    monkeypatch.setattr(microsoft365.urllib.request, "urlopen", fake_urlopen)
    microsoft365.graph_get_json("https://graph.microsoft.com/v1.0/x", "tok", timeout=30)
    assert captured["timeout"] == 30


def test_graph_get_json_propagates_urllib_errors_without_normalizing(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(microsoft365.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(urllib.error.URLError):
        microsoft365.graph_get_json("https://graph.microsoft.com/v1.0/x", "tok")


def test_graph_get_paged_values_follows_next_link_and_aggregates(monkeypatch):
    pages = [
        {"value": [{"id": 1}, {"id": 2}], "@odata.nextLink": "https://graph.microsoft.com/v1.0/x?page=2"},
        {"value": [{"id": 3}]},
    ]
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return _FakeResponse(pages[len(calls) - 1])

    monkeypatch.setattr(microsoft365.urllib.request, "urlopen", fake_urlopen)

    items = microsoft365.graph_get_paged_values("https://graph.microsoft.com/v1.0/x", "tok")

    assert items == [{"id": 1}, {"id": 2}, {"id": 3}]
    assert calls == [
        "https://graph.microsoft.com/v1.0/x",
        "https://graph.microsoft.com/v1.0/x?page=2",
    ]


def test_graph_get_paged_values_stops_when_no_next_link(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request.full_url)
        return _FakeResponse({"value": [{"id": 1}]})

    monkeypatch.setattr(microsoft365.urllib.request, "urlopen", fake_urlopen)

    items = microsoft365.graph_get_paged_values("https://graph.microsoft.com/v1.0/x", "tok")

    assert items == [{"id": 1}]
    assert len(calls) == 1


def test_catalog_service_delegates_to_shared_microsoft365(monkeypatch):
    from tools.catalog import catalog_service

    assert catalog_service._graph_headers("tok-xyz") == microsoft365.graph_headers("tok-xyz")

    def fake_urlopen(request, timeout=None):
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(microsoft365.urllib.request, "urlopen", fake_urlopen)
    assert catalog_service._graph_get_json("https://graph.microsoft.com/v1.0/x", "tok") == {"ok": True}
