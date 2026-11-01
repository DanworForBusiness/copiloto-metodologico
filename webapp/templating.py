"""
templating.py — entorno Jinja2 compartido (capacidad de presentación de plataforma).

Construye la ÚNICA instancia de ``Jinja2Templates`` de la webapp y registra los
globals de PLATAFORMA: `static_version` (cache-busting de estáticos),
`verbose_telemetry` (flag de observabilidad) y `config` (módulo de
configuración raíz, de solo lectura — permite a templates compartidas como
`partials/platform_topbar.html` leer identidad de app config-driven, p. ej.
`config.APP_TITLE`/`config.OPERATIONS_APP_TITLE`, sin que cada router tenga
que reinyectarla en su contexto). Se extrajo de ``webapp/main.py`` (Fase 2C)
para que los routers puedan renderizar sin importar ``webapp.main``; ``main``
re-exporta ESTA MISMA instancia (identidad única, una sola env).

Alcance PLATFORM-pure en RESPONSABILIDAD: acá NO se registran filtros específicos
de workspace (`diagnostic_title`, `catalog_verdict_*`). Esos los sigue registrando
``webapp/main.py`` sobre esta misma ``templates.env``.

⚠️ Deuda conocida: por lo anterior, la environment resultante todavía recibe
mutaciones Copilot/Catalog desde main — NO está workspace-pure. Se tratará cuando
exista presión real por un segundo workspace (p. ej. Portal de Operaciones).

Este módulo NO importa ``webapp.main`` y NO contiene rutas, servicios, config ni
lógica de negocio.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.templating import Jinja2Templates

import config
from webapp.services.activity_service import is_verbose

WEB_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=WEB_DIR / "templates")


class _StaticVersion:
    """Cache-busting de estáticos: huella del CONTENIDO de /static (no de su mtime).

    Antes era el máx. mtime: en producción el build normaliza los mtimes (``last-modified: 1980-01-01``), así que ``?v=`` no cambiaba entre
    deploys y los navegadores seguían con ``app.js``/``tools.css`` viejos (frescura heurística). La huella cambia si y solo si cambia algún
    archivo de primer nivel. Se recalcula en cada render, pero cada archivo se vuelve a leer solo si cambió su (mtime, tamaño): un refresh
    local tras editar un .css/.js sigue trayendo la versión nueva sin reiniciar el server, y el costo por render es un ``stat`` por archivo."""

    def __init__(self) -> None:
        self._digests: dict[str, tuple[tuple[int, int], str]] = {}

    def __str__(self) -> str:
        try:
            overall = hashlib.sha1()
            for path in sorted(p for p in (WEB_DIR / "static").iterdir() if p.is_file()):
                st = path.stat()
                key = (st.st_mtime_ns, st.st_size)
                cached = self._digests.get(path.name)
                if cached is None or cached[0] != key:
                    cached = (key, hashlib.sha1(path.read_bytes()).hexdigest())
                    self._digests[path.name] = cached
                overall.update(path.name.encode("utf-8"))
                overall.update(cached[1].encode("ascii"))
            return overall.hexdigest()[:12]
        except OSError:
            return "0"


templates.env.globals["static_version"] = _StaticVersion()
templates.env.globals["verbose_telemetry"] = is_verbose()
templates.env.globals["config"] = config
# URL ÚNICA de las fuentes institucionales del Hub (Inter + JetBrains Mono): la consumen base.html y los documentos propios del Hub que no extienden base (Presentaciones, R1).
templates.env.globals["hub_fonts_url"] = "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800;900&family=JetBrains+Mono:wght@500&display=swap"


def _dsa_tool(request, stem: str) -> dict:
    """Contexto de la herramienta "Assessment de tu modelo" para el panel de diagnóstico (ver datasphere_assessment_service)."""
    from webapp.services import datasphere_assessment_service as _dsa
    return _dsa.tool_context(request, stem)


templates.env.globals["dsa_tool"] = _dsa_tool


def _dsp_pub_tool(request, stem: str) -> dict:
    """Contexto de la herramienta "Publicar modelo" para el grupo "Herramientas de decisión" (ver datasphere_publication_service)."""
    from webapp.services import datasphere_publication_service as _pub
    return _pub.tool_context(request, stem)


templates.env.globals["dsp_pub_tool"] = _dsp_pub_tool


def _dsc_tool(request, stem: str) -> dict:
    """Contexto de la extracción ad-hoc de conexiones del módulo 01 (ver connectivity_inventory_service)."""
    from webapp.services import connectivity_inventory_service as _dsc
    return _dsc.tool_context(request, stem)


templates.env.globals["dsc_tool"] = _dsc_tool
