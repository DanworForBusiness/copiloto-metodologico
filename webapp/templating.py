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

from pathlib import Path

from fastapi.templating import Jinja2Templates

import config
from webapp.services.activity_service import is_verbose

WEB_DIR = Path(__file__).resolve().parent

templates = Jinja2Templates(directory=WEB_DIR / "templates")


class _StaticVersion:
    """Cache-busting de estáticos: se recalcula en cada render (mtime actual de
    /static). Así, tras editar un .css/.js, un simple refresh trae la versión
    nueva sin reiniciar el server (antes se fijaba al importar el módulo → los
    cambios locales quedaban cacheados con la URL vieja)."""

    def __str__(self) -> str:
        try:
            return str(max(
                path.stat().st_mtime_ns
                for path in (WEB_DIR / "static").iterdir() if path.is_file()
            ))
        except ValueError:
            return "0"


templates.env.globals["static_version"] = _StaticVersion()
templates.env.globals["verbose_telemetry"] = is_verbose()
templates.env.globals["config"] = config


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
