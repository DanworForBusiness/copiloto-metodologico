"""commercial_strategy_markup.py — Renderer del mini-markup constreñido de
Estrategia Comercial (owner decision: "no arbitrary HTML, no WYSIWYG/CMS").

El documento persistido (`commercial_strategy_repository.load_strategy`)
NUNCA contiene HTML — solo texto llano + un mini-markup propio producido por
`scripts/import_commercial_strategy.py`: `**negrita**`, `*cursiva*`,
`[label](url)` (solo http/https), `\\n` para salto de línea. Este módulo es
el ÚNICO lugar que lo convierte a HTML, y lo hace con la MISMA disciplina que
ya usa el resto del Hub para contenido (`content_service.py`/
`guides_service.py`): escapar el texto CRUDO primero, recién después
sustituir los 4 patrones conocidos por tags fijos — nunca al revés, y nunca
un tag/atributo que no esté en esta lista corta. Nada de lo que entra a
`render()` puede convertirse en un tag/atributo arbitrario ni en un
`javascript:`/`data:` URL, sin importar qué haya en el texto de origen.
"""
from __future__ import annotations

import html
import re

_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
_BOLD_RE = re.compile(r"\*\*([^*\n]+)\*\*")
_ITALIC_RE = re.compile(r"\*([^*\n]+)\*")


def render(raw: str | None) -> str:
    """Texto crudo (mini-markup) → HTML seguro. Vacío/None → cadena vacía."""
    if not raw:
        return ""
    escaped = html.escape(str(raw), quote=True)

    def _link(match: re.Match[str]) -> str:
        label, url = match.group(1), match.group(2)
        return f'<a href="{url}" target="_blank" rel="noopener">{label}</a>'

    escaped = _LINK_RE.sub(_link, escaped)
    escaped = _BOLD_RE.sub(r"<strong>\1</strong>", escaped)
    escaped = _ITALIC_RE.sub(r"<em>\1</em>", escaped)
    return escaped.replace("\n", "<br>")


def status_badge_class(raw: str | None) -> str:
    """Texto de estado (columna "Estado" de una tabla de contactos, p. ej.
    "Hecho"/"En construcción"/"Stand-by, no prioridad") → modificador de
    `.status-badge` (tools.css, ya theme-aware) — REUSA el mismo primitivo
    que el resto del Hub usa para frescura de dato, en vez de un badge propio.
    Clasificación por palabra clave (el texto de origen es prosa libre, no un
    enum): nunca falla, cae a `--otro` (gris neutro) si no matchea nada."""
    text = (raw or "").strip().lower()
    if "hecho" in text:
        return "status-badge--activo"
    if "construcción" in text or "proceso" in text:
        return "status-badge--tentativo"
    if "standby" in text or "stand-by" in text or "sin abordaje" in text:
        return "status-badge--inactivo"
    return "status-badge--otro"
