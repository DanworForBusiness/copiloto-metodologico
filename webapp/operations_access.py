"""
operations_access.py — Access policy MÍNIMA y PROVISORIA de Operations
(Batch 3 de aceleración post-Fase 2).

Regla única y deliberadamente pequeña: solo un usuario con sesión válida y
tier "full" (``auth_service.user_tier``) puede ver Operations/Staffing —
excluye tier "academy" (solo lectura del Copiloto) y a los no autenticados.
NO es RBAC, NO es un workspace registry, NO es un modelo de permisos
general — se revisita cuando exista un login/permission model real de
Operations (ver HANDOFF, Batch 3).

Distinto de ``webapp/access.py``: ese módulo es política ACTUAL del
Copiloto (``is_readonly == tier academy`` es semántica de negocio del
Copiloto, explícitamente no-generalizada). Acá se reutiliza el mismo
primitivo de identidad (``auth_service.user_tier``, capacidad PLATFORM /
SHARED) con la regla mínima que Operations necesita — sin importar
``webapp.access`` ni ``webapp.main``.
"""
from __future__ import annotations

from fastapi import Request

from webapp.services import auth_service


def is_staffing_authorized(request: Request) -> bool:
    """True si hay sesión válida y el tier del usuario es "full". Regla
    mínima y provisoria — no asumir que cubre el modelo final de permisos
    de Operations."""
    user = auth_service.session_user(request)
    if not user:
        return False
    return auth_service.user_tier(str(user.get("sub") or "")) == "full"
