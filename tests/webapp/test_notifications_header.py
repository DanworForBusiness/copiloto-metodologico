"""Consistencia del ENCABEZADO de los correos del Hub (notificaciones).

Criterio de aceptación: todos los eventos deben producir EXACTAMENTE el mismo HTML/estilos de cabecera cuando reciben los mismos
metadatos de vertical (no basta con que se parezcan). La cabecera vive SOLO en la base compartida `notifications/_layout.html`
(fuera de `{% block content %}`); los templates de evento solo overridean el contenido. Estas pruebas lo fijan estructuralmente,
renderizando por el MISMO camino (`notifications.build_message`).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from webapp import workspaces
from webapp.services import notifications as n

REPO = Path(__file__).resolve().parents[2]
NOTIF_TPL = REPO / "webapp" / "templates" / "notifications"

# Datos genéricos (claves de los tres eventos); las que no apliquen a un evento se renderizan vacías y no afectan la cabecera.
_DATA = {"id": "1", "title": "X", "project": "P", "priority": "Alta", "status": "Solicitado", "requester": "u@x",
         "created": "hoy", "description": "d", "url": "http://x/y", "puesto": "Dev", "area": "TI", "modalidad": "Remoto",
         "area_consultora": "Cliente"}


def _render(event: str) -> str:
    return n.build_message(event, _DATA, ["a@example.com"], cc=["b@example.com"])["message"]["body"]["content"]


def _header_cell(html: str) -> str:
    """Celda `<td>` del encabezado (balanceando `<td>`/`</td>`, hay tablas anidadas)."""
    anchor = html.index("padding:16px 24px;background:")
    start = html.rfind("<td", 0, anchor)
    i, depth = start, 0
    while i < len(html):
        if re.match(r"<td\b", html[i:]):
            depth += 1; i += 3; continue
        if html.startswith("</td>", i):
            depth -= 1; i += 5
            if depth == 0:
                return html[start:i]
            continue
        i += 1
    raise AssertionError("no se pudo cerrar la celda del encabezado")


def test_every_event_header_is_byte_identical():
    """La cabecera renderizada es IDÉNTICA entre todos los eventos (mismos metadatos de vertical de Operaciones)."""
    headers = {event: _header_cell(_render(event)) for event in n.EVENTS}
    reference = next(iter(headers.values()))
    for event, cell in headers.items():
        assert cell == reference, f"el encabezado de {event} difiere del de referencia"


def test_header_has_the_approved_structure_and_styles():
    """Invariantes de la cabecera aprobada (fallan si alguien altera el layout compartido)."""
    cell = _header_cell(_render(next(iter(n.EVENTS))))
    brand = n.INSTITUTIONAL
    vertical = workspaces.brand_identity(n.EVENTS[next(iter(n.EVENTS))].workspace)
    assert brand["hub_name"] in cell                       # «Danwor Hub»
    assert vertical["title"] in cell and vertical["accent"] in cell
    assert "border-radius:10px 10px 0 0" in cell           # celda oscura del encabezado
    assert "font-size:21px" in cell and "font-size:18px" in cell   # marca 21px / vertical 18px
    assert cell.count("font-weight:800") >= 2              # marca + vertical en 800
    assert "letter-spacing:-0.63px" in cell and "letter-spacing:-0.36px" in cell


def test_event_templates_do_not_redefine_the_header():
    """Ningún template de evento define la cabecera: así no puede divergir (solo overridea `{% block content %}`)."""
    for tpl in NOTIF_TPL.glob("architecture_request_*.html"):
        text = tpl.read_text(encoding="utf-8")
        assert "border-radius:10px 10px 0 0" not in text, f"{tpl.name} define markup de cabecera"
        assert "{% extends \"notifications/_layout.html\" %}" in text or "{% extends 'notifications/_layout.html' %}" in text
        assert "{% block content %}" in text
