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
- `display_order` es metadata EXPLÍCITA e independiente — nunca se calcula a
  partir de los dígitos de `module_id`. Este registro captura fielmente el
  orden ACTUAL de navegación (Copiloto: orden de archivo, que hoy coincide
  con el número visible para los módulos principales pero NO para los
  apéndices — ver "known appendix display-number inconsistency" en HANDOFF;
  esta fase no reordena nada, solo lo documenta).
- Los IDs son OPACOS para la lógica de aplicación: ningún código de este
  archivo (ni de ningún consumidor futuro) debe hacer `.startswith` sobre el
  prefijo de dos letras, ni slicing, para decidir comportamiento — toda
  decisión de negocio debe leer los campos explícitos del descriptor
  (`workspace`/`area`/`kind`), nunca el string del ID.
- `resolve()` es el ÚNICO resolver canónico — acepta indistintamente una key
  legacy (stem, `"assignments"`) o un `module_id` canónico y devuelve el
  MISMO descriptor. Una key desconocida devuelve `None` explícito — nunca se
  fabrica un descriptor para algo no registrado.

Namespace reservado, NO implementado en esta fase (sin entradas en el
registro, sin mapeo): `CPACxx` (Academy de Copiloto — su espacio de
identidad ya difiere del nav normal, ver HANDOFF; requiere su propio
discovery), `CMxx` (Comercial), `CHxx` (Capital Humano) — ninguno de los
tres tiene todavía workspace/área/contenido real en el repo.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from webapp.services import content_service
from webapp.services.activity_context import MODULE_ASSIGNMENTS, module_label


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
    # `module_id`. Reusa la fuente existente (content_service `title` /
    # activity_context `module_label`) en vez de duplicar el texto humano.
    visible_label: str
    # Orden de navegación EXPLÍCITO — nunca calculado a partir de
    # `module_id`. Ver principios congelados arriba.
    display_order: int
    # Contrato de compatibilidad explícito: cada key legacy históricamente
    # usada por el código (stem de Copiloto, "assignments" de Operations)
    # que este `module_id` reemplaza. NUNCA incluye labels visibles.
    legacy_aliases: tuple[str, ...]


# Asignación canónica de IDs — Copiloto principal. El orden de este dict
# importa: define `display_order` vía el orden de iteración de
# `content_service.list_modules()` (que ya ordena por archivo, el mismo
# orden que hoy renderiza el nav) filtrado por esta asignación.
_COPILOT_MODULE_IDS: dict[str, str] = {
    "00_Intro": "CP00",
    "01_Arquitectura_y_Conectividad": "CP01",
    "02_Seguridad_y_Gobernanza": "CP02",
    "03_Integracion_y_Estrategia_de_Datos": "CP03",
    "04_Modelado_y_Desarrollo": "CP04",
    "05_Reporting_y_Consumo": "CP05",
}

# Apéndices — el orden de nav actual es por ARCHIVO (A01..A06), NO por el
# número de frontmatter (`module:`, que hoy va "10","08","09","07","06","A06"
# — ver HANDOFF, inconsistencia conocida, NO corregida en esta fase). Fase 1
# es behavior-preserving: `display_order` refleja el nav real de HOY (orden
# de archivo), no el número de frontmatter.
_COPILOT_APPENDIX_IDS: dict[str, str] = {
    "A01_Arquitectura_y_Modelado": "CPA01",
    "A02_Modernizacion_BW_Greenfield": "CPA02",
    "A03_Hub_de_Integracion_AWS": "CPA03",
    "A04_Biblioteca_de_Referencias": "CPA04",
    "A05_Troubleshooting_y_Performance": "CPA05",
    "A06_SAP_Analytics_Cloud": "CPA06",
}


def _build_entries() -> tuple[ModuleDescriptor, ...]:
    entries: list[ModuleDescriptor] = []
    order = 0
    for m in content_service.list_modules():
        module_id = _COPILOT_MODULE_IDS.get(m.stem)
        if module_id is None:
            # Contenido sin ID canónico asignado todavía — Fase 1 NO inventa
            # uno; el módulo simplemente no aparece en este registro (sigue
            # 100% funcional por su stem legacy, sin cambios).
            continue
        entries.append(ModuleDescriptor(
            module_id=module_id, workspace="copilot", area="methodology",
            kind="module", content_key=m.stem, visible_label=m.title,
            display_order=order, legacy_aliases=(m.stem,),
        ))
        order += 1
    for a in content_service.list_appendices():
        module_id = _COPILOT_APPENDIX_IDS.get(a.stem)
        if module_id is None:
            continue
        entries.append(ModuleDescriptor(
            module_id=module_id, workspace="copilot", area="methodology",
            kind="appendix", content_key=a.stem, visible_label=a.title,
            display_order=order, legacy_aliases=(a.stem,),
        ))
        order += 1
    # Operations/Asignaciones — sin Markdown backing (`content_key=None`).
    # `kind="module"`: no hay evidencia de un consumidor actual que necesite
    # distinguir Asignaciones de un módulo de Copiloto por `kind` — ambos son
    # "un módulo navegable", la diferencia real ya la expresa `workspace`.
    entries.append(ModuleDescriptor(
        module_id="OP01", workspace="operations", area="staffing",
        kind="module", content_key=None,
        visible_label=module_label(MODULE_ASSIGNMENTS),
        display_order=order, legacy_aliases=(MODULE_ASSIGNMENTS,),
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
