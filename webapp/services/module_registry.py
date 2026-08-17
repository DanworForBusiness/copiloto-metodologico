"""
module_registry.py — Canonical Hub module identity registry.

MODULE IDENTIFIER CONVENTION V2 — Phase 1 (registry foundation ONLY, ver
HANDOFF): construye una capa de identidad estable (`module_id`, p. ej. `CP01`)
por ENCIMA de las keys legacy que ya existen (stems de contenido de Copiloto,
`assignments` de Operations) — puramente ADITIVA. NINGÚN productor/consumidor
actual (rutas, Activity, Feedback, nav Jinja, assistant_store, YAML de
árboles, ramas hardcodeadas `stem.startswith(...)`) se migra en esta fase —
todos siguen usando sus keys legacy exactamente como hoy. Ese trabajo es
Phase 2, explícitamente NO autorizado todavía.

Principios congelados (discovery previo — ver HANDOFF):
- `module_id` es un código de identidad ESTABLE asignado en la creación,
  nunca derivado de la posición de nav ni del título visible — no cambia si
  el módulo se reordena o se renombra.
- `display_order` es metadata EXPLÍCITA, declarada literalmente en el
  registro (NUNCA calculada — ni de los dígitos de `module_id`, ni del
  filename/`content_key`, ni de la posición de enumeración al construir el
  registro). Significa "orden DENTRO de la colección navegable actual del
  propio descriptor" — Copiloto principal, apéndices de Copiloto, y
  Operations son tres colecciones SEPARADAS hoy (no existe una secuencia
  Hub-wide única todavía), así que `CP01`, `CPA01` y `OP01` comparten
  legítimamente `display_order=1` sin colisionar — `module_id`/`workspace`/
  `kind` ya los distinguen. Los valores declarados preservan el orden de
  nav ACTUAL (Copiloto: orden de archivo, que hoy coincide con el número
  visible para los módulos principales pero NO para los apéndices — ver
  "known appendix display-number inconsistency" en HANDOFF; esta fase no
  reordena nada, solo lo documenta).
- Los IDs son OPACOS para la lógica de aplicación: ningún código de este
  archivo (ni de ningún consumidor futuro) debe hacer `.startswith` sobre el
  prefijo de dos letras, ni slicing, para decidir comportamiento — toda
  decisión de negocio debe leer los campos explícitos del descriptor
  (`workspace`/`area`/`kind`), nunca el string del ID.
- `resolve()` es el ÚNICO resolver canónico — acepta indistintamente una key
  legacy (stem, `"assignments"`) o un `module_id` canónico y devuelve el
  MISMO descriptor. Una key desconocida devuelve `None` explícito — nunca se
  fabrica un descriptor para algo no registrado.
- **Dirección de dependencia:** este módulo es la capa de identidad de MÁS
  BAJO nivel del Hub — NO importa `webapp.services.activity_context` (ni
  ningún otro futuro consumidor). Donde eso implicaría duplicar un literal
  que hoy también vive en `activity_context` (p. ej. la key/label de OP01),
  la duplicación temporal es preferible a invertir la dependencia — ver
  `_OP01_LEGACY_KEY`/`_OP01_VISIBLE_LABEL` más abajo.

Namespace reservado, NO implementado en esta fase (sin entradas en el
registro, sin mapeo): `CPACxx` (Academy de Copiloto — su espacio de
identidad ya difiere del nav normal, ver HANDOFF; requiere su propio
discovery), `CHxx` (Capital Humano) — ninguno de los dos tiene todavía
workspace/área/contenido real en el repo. `CMxx` (Comercial) SÍ se activa
en esta fase (`CM01`, ver más abajo) — Commercial Hub Workspace
Foundation, Batch 0.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from webapp.services import content_service


@dataclass(frozen=True)
class ModuleDescriptor:
    module_id: str
    workspace: str
    area: str
    # Distinción de agrupamiento más chica que soporta evidencia actual —
    # SOLO "module" | "appendix" (Academy NO tiene entrada activa todavía,
    # ver docstring del módulo; no se inventa un tercer valor sin consumidor).
    kind: str
    # Key de lookup de contenido físico (stem) para módulos con Markdown
    # backing. `None` para módulos sin archivo de contenido (p. ej.
    # Operations/Asignaciones, que es un dashboard, no un documento).
    content_key: str | None
    # Label de producto, independiente del ID — nunca se deriva del
    # `module_id`. Para Copiloto reusa `content_service.title` (fuente
    # existente, sin duplicar texto humano); para OP01 es un literal propio
    # del registro (ver `_OP01_VISIBLE_LABEL` — dirección de dependencia,
    # NO se importa de `activity_context`).
    visible_label: str
    # Orden de navegación EXPLÍCITO — nunca calculado a partir de
    # `module_id`. Ver principios congelados arriba.
    display_order: int
    # Contrato de compatibilidad explícito: cada key legacy históricamente
    # usada por el código (stem de Copiloto, "assignments" de Operations)
    # que este `module_id` reemplaza. NUNCA incluye labels visibles.
    legacy_aliases: tuple[str, ...]


# Asignación canónica de IDs — Copiloto principal. `display_order` es el
# SEGUNDO elemento de la tupla, declarado literalmente acá — NUNCA se deriva
# de enumerar este dict ni de ningún otro cálculo (final display-order
# semantics corrective). Preserva el orden de nav ACTUAL (que hoy coincide
# con los dígitos del stem para los módulos principales, por disciplina de
# contenido, no por una regla que el código enforce).
_COPILOT_MODULE_IDS: dict[str, tuple[str, int]] = {
    "00_Intro": ("CP00", 0),
    "01_Arquitectura_y_Conectividad": ("CP01", 1),
    "02_Seguridad_y_Gobernanza": ("CP02", 2),
    "03_Integracion_y_Estrategia_de_Datos": ("CP03", 3),
    "04_Modelado_y_Desarrollo": ("CP04", 4),
    "05_Reporting_y_Consumo": ("CP05", 5),
}

# Apéndices — el orden de nav actual es por ARCHIVO (A01..A06), NO por el
# número de frontmatter (`module:`, que hoy va "10","08","09","07","06","A06"
# — ver HANDOFF, inconsistencia conocida, NO corregida en esta fase). Los
# `display_order` de abajo son una colección PROPIA (empieza en 1, separada
# de Copiloto principal) — preservan ese orden de archivo declarándolo
# literalmente, nunca parseando el número de frontmatter ni el nombre del
# archivo.
_COPILOT_APPENDIX_IDS: dict[str, tuple[str, int]] = {
    "A01_Arquitectura_y_Modelado": ("CPA01", 1),
    "A02_Modernizacion_BW_Greenfield": ("CPA02", 2),
    "A03_Hub_de_Integracion_AWS": ("CPA03", 3),
    "A04_Biblioteca_de_Referencias": ("CPA04", 4),
    "A05_Troubleshooting_y_Performance": ("CPA05", 5),
    "A06_SAP_Analytics_Cloud": ("CPA06", 6),
}

# OP01 — Operations/Asignaciones. Literales propios del registro, NUNCA
# importados de `activity_context` (ver comentario junto a la entrada OP01
# en `_build_entries`, más abajo, para la razón de dirección de dependencia).
# Colección propia (Operations), separada de las dos de arriba — único
# módulo hoy, `display_order=1` dentro de SU colección.
#
# `OP01_MODULE_ID` es PÚBLICO (Fase 2 — Canonical Module Adoption): los
# productores de Activity de Staffing (`webapp/routers/staffing.py`) lo
# importan directo para persistir el ID canónico en vez de reproducir el
# mapeo `assignments → OP01` fuera del registro (ver "Registry Ownership"
# en HANDOFF — toda resolución de identidad canónica pasa por acá).
OP01_MODULE_ID = "OP01"
_OP01_LEGACY_KEY = "assignments"
_OP01_VISIBLE_LABEL = "Asignaciones"
_OP01_DISPLAY_ORDER = 1

# CM01 — Comercial/CRM (Commercial Hub Workspace Foundation, Batch 0: solo
# plataforma/shell, sin Odoo/dashboard real todavía). MISMO criterio que
# OP01 arriba — literales propios del registro, `content_key=None` (sin
# Markdown backing), única entrada de SU colección (`display_order=1`).
CM01_MODULE_ID = "CM01"
_CM01_LEGACY_KEY = "crm"
_CM01_VISIBLE_LABEL = "CRM"
_CM01_DISPLAY_ORDER = 1


def _build_entries() -> tuple[ModuleDescriptor, ...]:
    # `display_order` viene SIEMPRE del segundo elemento de la tupla
    # declarada en _COPILOT_MODULE_IDS/_COPILOT_APPENDIX_IDS/
    # _OP01_DISPLAY_ORDER — nunca de `enumerate`, de una posición de
    # iteración, ni de parsear `module_id`/`content_key` (final
    # display-order semantics corrective). Iterar
    # `content_service.list_modules()`/`list_appendices()` acá SOLO decide
    # qué stems existen y les provee `title` (label) — el orden de esa
    # iteración es irrelevante para `display_order`, que ya viene fijo en
    # el mapeo.
    entries: list[ModuleDescriptor] = []
    for m in content_service.list_modules():
        mapped = _COPILOT_MODULE_IDS.get(m.stem)
        if mapped is None:
            # Contenido sin ID canónico asignado todavía — Fase 1 NO inventa
            # uno; el módulo simplemente no aparece en este registro (sigue
            # 100% funcional por su stem legacy, sin cambios).
            continue
        module_id, display_order = mapped
        entries.append(ModuleDescriptor(
            module_id=module_id, workspace="copilot", area="methodology",
            kind="module", content_key=m.stem, visible_label=m.title,
            display_order=display_order, legacy_aliases=(m.stem,),
        ))
    for a in content_service.list_appendices():
        mapped = _COPILOT_APPENDIX_IDS.get(a.stem)
        if mapped is None:
            continue
        module_id, display_order = mapped
        entries.append(ModuleDescriptor(
            module_id=module_id, workspace="copilot", area="methodology",
            kind="appendix", content_key=a.stem, visible_label=a.title,
            display_order=display_order, legacy_aliases=(a.stem,),
        ))
    # Operations/Asignaciones — sin Markdown backing (`content_key=None`).
    # `kind="module"`: no hay evidencia de un consumidor actual que necesite
    # distinguir Asignaciones de un módulo de Copiloto por `kind` — ambos son
    # "un módulo navegable", la diferencia real ya la expresa `workspace`.
    #
    # `_OP01_LEGACY_KEY`/`_OP01_VISIBLE_LABEL` (dirección de dependencia,
    # final foundation dependency check): este registro es la capa de
    # identidad de MÁS BAJO nivel — no debe depender de ninguno de sus
    # futuros consumidores. `activity_context.MODULE_ASSIGNMENTS`/
    # `module_label()` tienen el MISMO valor hoy — es una duplicación
    # temporal deliberada, preferible a que module_registry importe
    # activity_context. Fase 2 migrará la resolución de labels de Activity
    # para leer de ESTE registro y podrá eliminar la entrada redundante en
    # `activity_context.MODULE_LABELS` donde sea seguro — NO se toca acá.
    entries.append(ModuleDescriptor(
        module_id=OP01_MODULE_ID, workspace="operations", area="staffing",
        kind="module", content_key=None,
        visible_label=_OP01_VISIBLE_LABEL,
        display_order=_OP01_DISPLAY_ORDER, legacy_aliases=(_OP01_LEGACY_KEY,),
    ))
    # Comercial/CRM — sin Markdown backing (`content_key=None`), MISMO
    # criterio que Operations/Asignaciones arriba (`kind="module"`, sin
    # evidencia de un consumidor que necesite distinguir "appendix").
    entries.append(ModuleDescriptor(
        module_id=CM01_MODULE_ID, workspace="commercial", area="crm",
        kind="module", content_key=None,
        visible_label=_CM01_VISIBLE_LABEL,
        display_order=_CM01_DISPLAY_ORDER, legacy_aliases=(_CM01_LEGACY_KEY,),
    ))
    return tuple(entries)


@lru_cache(maxsize=1)
def _registry() -> tuple[ModuleDescriptor, ...]:
    return _build_entries()


def all_entries() -> tuple[ModuleDescriptor, ...]:
    """Inventario completo — uso principal: tests de consistencia (sin
    module_id/alias duplicados, cobertura completa de stems conocidos)."""
    return _registry()


def resolve(key: str) -> ModuleDescriptor | None:
    """Resolver canónico ÚNICO: acepta una key legacy (stem de contenido,
    `"assignments"`) o un `module_id` canónico (`"CP01"`) indistintamente y
    devuelve el MISMO descriptor. `None` si la key no está registrada — nunca
    fabrica un descriptor para algo desconocido. Comparación por igualdad
    exacta de string únicamente — sin parsear prefijos ni normalizar
    mayúsculas/espacios más allá de lo que el código actual ya hace."""
    key = (key or "").strip()
    if not key:
        return None
    for entry in _registry():
        if key == entry.module_id or key in entry.legacy_aliases:
            return entry
    return None
