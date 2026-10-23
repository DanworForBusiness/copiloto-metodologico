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


# AGRUPACIÓN de los 17 ítems en contenedores colapsables: es SOLO presentación. No cambia los ítems, sus claves ni su orden canónico (`CHECKLIST_ITEMS`), el estado `{clave: bool}`, `progress()`, `completado` ni los endpoints. Cada ítem
# pertenece a exactamente UN grupo (lo garantiza un test). El orden dentro de cada grupo es el de la propuesta aprobada por Capital Humano.
CHECKLIST_GROUPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("documentacion_personal", "Documentación personal", ("dni", "foto", "ddjj_domicilio", "titulos_certificados")),
    ("contratacion_alta_legal", "Contratación y alta legal", ("oferta_empleo", "nda", "nota_convenio_colectivo", "preocupacional", "alta_arca")),
    ("perfil_verificacion", "Perfil y verificación", ("cv_formato_danwor", "requerimiento_perfil", "verificacion_referencias")),
    ("induccion_comunicacion", "Inducción y comunicación", ("nota_herramientas", "sensibilizacion_iso", "comunidad_wp")),
    ("altas_sistemas_internos", "Altas en sistemas internos", ("alta_legajo_diamo", "alta_timenotes")),
)


def groups_view(checklist: dict | None) -> list[dict]:
    """Los grupos con sus ítems (mismo estado tildado de `items_view`), su progreso `hechos/total`, un estado visual (`completo` | `en_curso` | `sin_empezar`) y `abierto_por_defecto`: se abren los grupos con tareas
    pendientes y quedan colapsados los completos (la preferencia manual de la persona, guardada en el navegador, prevalece sobre esto)."""
    by_key = {it["key"]: it for it in items_view(checklist)}
    out = []
    for key, label, item_keys in CHECKLIST_GROUPS:
        items = [by_key[k] for k in item_keys]
        hechos = sum(1 for it in items if it["checked"])
        total = len(items)
        out.append({"key": key, "label": label, "tareas": items, "hechos": hechos, "total": total,
                    "estado": "completo" if hechos == total else ("sin_empezar" if hechos == 0 else "en_curso"), "abierto_por_defecto": hechos < total})
    return out


def items_view(checklist: dict | None) -> list[dict]:
    state = checklist or {}
    return [{"key": k, "label": label, "checked": state.get(k) is True} for k, label in CHECKLIST_ITEMS]
