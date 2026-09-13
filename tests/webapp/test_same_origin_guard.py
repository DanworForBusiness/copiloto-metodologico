"""test_same_origin_guard.py — cobertura unitaria de `webapp.same_origin_guard`
(protección anti-CSRF mínima de Estrategia, ver storage gate/pre-PR
correction). Prueba el predicado PURO, sin pasar por la app completa (ver
tests/webapp/test_commercial_strategy.py para la integración end-to-end)."""
from __future__ import annotations

from fastapi import Request

from webapp.same_origin_guard import is_same_origin_write


def _request(*, url: str = "http://testserver/comercial/estrategia", origin: str = "", referer: str = "") -> Request:
    headers = []
    if origin:
        headers.append((b"origin", origin.encode()))
    if referer:
        headers.append((b"referer", referer.encode()))
    scope = {
        "type": "http", "method": "POST", "path": "/comercial/estrategia",
        "headers": headers, "query_string": b"", "server": ("testserver", 80),
        "scheme": "http",
    }
    return Request(scope)


def test_matching_origin_passes():
    assert is_same_origin_write(_request(origin="http://testserver")) is True


def test_mismatched_origin_fails():
    assert is_same_origin_write(_request(origin="https://evil.example")) is False


def test_falls_back_to_referer_when_origin_absent():
    assert is_same_origin_write(_request(referer="http://testserver/comercial/estrategia")) is True


def test_mismatched_referer_fails():
    assert is_same_origin_write(_request(referer="https://evil.example/steal")) is False


def test_no_origin_and_no_referer_fails_closed():
    assert is_same_origin_write(_request()) is False


def test_port_mismatch_fails():
    # MISMO host, puerto distinto — sigue siendo un origen distinto.
    assert is_same_origin_write(_request(origin="http://testserver:9999")) is False
