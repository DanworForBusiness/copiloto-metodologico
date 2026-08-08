"""
presentation — helpers de PRESENTACIÓN (no-HTTP) del Copiloto.

Módulos que construyen/transforman HTML de features del Copiloto y que hoy son
consumidos por más de un handler. Viven acá (y no dentro de un router) para que
un router NO tenga que importar a otro cuando comparten una pieza de presentación.

Ningún módulo de este paquete importa ``webapp.main``, define rutas, contiene
auth/session policy ni se declara PLATFORM/SHARED. "Shared" acá significa
"compartido hoy por features del Copiloto", no reuso cross-workspace (NOT YET
DECIDED).
"""
