"""
system.py — rutas de infraestructura (health/liveness + favicon).

Primer router extraído de ``webapp/main.py`` (Fase 1 del refactor
arquitectónico). Frontera coherente "system/infra": endpoints que NO dependen de
sesión, templates ni servicios de dominio, así que se extraen sin arrastrar
singletons ni el runtime compartido.

Regla de dependencia: este módulo NO importa ``webapp.main`` (evita el ciclo de
imports; es ``main.py`` quien incluye este router). El path del asset se resuelve
localmente igual que ``WEB_DIR`` en ``main.py`` (``webapp/``), sin importarlo.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse

# Mismo directorio que resuelve WEB_DIR en main.py (webapp/), calculado acá para
# no importar main.py: routers/system.py -> parent (routers) -> parent (webapp).
_WEB_DIR = Path(__file__).resolve().parent.parent

router = APIRouter()


@router.get("/favicon.ico", include_in_schema=False)
def favicon():
    return FileResponse(_WEB_DIR / "static" / "favicon.ico")


@router.get("/health", include_in_schema=False)
def health():
    return {"status": "ok", "architecture": "fastapi-ssr-htmx"}
