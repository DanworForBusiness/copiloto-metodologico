"""onboarding_checklist.py — DEFINICION del checklist de Onboarding (los 17 ítems canónicos).

Fuente: app de referencia (app-seleccion), `lib/types.ts::ITEMS_ONBOARDING` (columnas de `onboarding` en `supabase/schema.sql` + migraciones 2026-07-23/27 y fase 3).
El orden de esta tupla es el orden de presentación.

Esto es la DEFINICION (qué se checklistea), separada del ESTADO de cada Onboarding (qué está tildado, que vive en `onboarding_repository` como
``{clave: bool}``). NO es una taxonomía de negocio de un dropdown → no vive en `tblCatalogosCCHH`. En la referencia es configuración de aplicación
(`configuraciones_app`, categoría `onboarding`, editable desde Administración): su administración dinámica queda DIFERIDA; este core usa los 17 canónicos.
"""
from __future__ import annotations

CHECKLIST_ITEMS: tuple[tuple[str, str], ...] = (
    ("ddjj_domicilio", "DDJJ de Domicilio"),
    ("cv_formato_danwor", "CV en formato Danwor"),
    ("nota_convenio_colectivo", "Nota de convenio Colectivo"),
    ("oferta_empleo", "Oferta de empleo / Carta oferta de servicios"),
    ("nda", "NDA"),
    ("dni", "DNI"),
    ("foto", "Foto"),
    ("titulos_certificados", "Títulos o Certificados"),
    ("preocupacional", "Preocupacional"),
    ("alta_arca", "Alta de ARCA"),
    ("nota_herramientas", "Nota de herramientas"),
    ("sensibilizacion_iso", "Sensibilización ISO"),
    ("requerimiento_perfil", "Requerimiento de perfil"),
    ("comunidad_wp", "Comunidad de WP"),
    ("alta_legajo_diamo", "Alta en legajo DIAMO"),
    ("alta_timenotes", "Alta en Timenotes"),
    ("verificacion_referencias", "Verificación de referencias laborales"),
)

ITEM_KEYS: tuple[str, ...] = tuple(k for k, _ in CHECKLIST_ITEMS)


def is_item_key(key: str) -> bool:
    return key in ITEM_KEYS


def progress(checklist: dict | None) -> dict:
    """Progreso derivado del estado ``{clave: bool}``: ``hechos / total · porcentaje`` y ``completo`` (MISMA fórmula que `calcularProgresoOnboarding` de la referencia).
    Una clave ausente cuenta como NO tildada (sobre legacy / sin checklist)."""
    state = checklist or {}
    total = len(CHECKLIST_ITEMS)
    hechos = sum(1 for k in ITEM_KEYS if state.get(k) is True)
    return {"hechos": hechos, "total": total, "porcentaje": round(hechos * 100 / total) if total else 100, "completo": hechos == total}


def items_view(checklist: dict | None) -> list[dict]:
    state = checklist or {}
    return [{"key": k, "label": label, "checked": state.get(k) is True} for k, label in CHECKLIST_ITEMS]
