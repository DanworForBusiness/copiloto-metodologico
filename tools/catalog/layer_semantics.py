"""layer_semantics.py — definiciones PURAS compartidas por el Assessment (señales) y el Juicio metodológico.

El Juicio y el Assessment son paquetes hermanos que NO se importan entre sí (ver ``tests/test_methodological_judgment_guards.py``). Las pocas definiciones que ambos necesitan —y que no pueden
divergir— viven acá, en un módulo neutro sin dependencias (mismo criterio que ``leaf_materialization.py``):

  * ``GOVERNED_CONTRACT_CLASSES``: clases de contrato de fuente que cuentan como contrato de origen GOBERNADO (CDS, ODP/SAPI, vista estándar sobre tabla física, puente BW).
  * ``is_pass_through_view``: una VISTA DE PASO es una proyección que no hace nada: 1 proveedor, 1 nodo SELECT, sin joins, uniones, columnas derivadas ni filtros.

  * ``LAYER_EVIDENCE_BASES``: bases de la capa inferida (``InferredLayerBasis``) que constituyen EVIDENCIA de capa: nomenclatura explícita de capas (§28) o rol declarado por el export.
    ``NAME_PREFIX`` (último recurso: el nombre empieza con ``V…``/``VR``/``AM``…), ``UNKNOWN`` y ``REMOTE_ENTITY`` (sólo describe una hoja) NO lo son.

Sin I/O, sin reloj, sin nombres de cliente ni de modelo.
"""
from __future__ import annotations

from typing import Any, Mapping

LAYER_EVIDENCE_BASES = frozenset({"SECTION_28_NAME", "DECLARED_ROLE"})
GOVERNED_CONTRACT_CLASSES = frozenset({"GOVERNED_SAP_CDS_CONTRACT", "GOVERNED_SAP_ODP_SAPI_CONTRACT", "SAP_STANDARD_VIEW_ON_PHYSICAL_TABLE", "SAP_BW_BRIDGE_VIRTUALIZATION"})


def is_pass_through_view(view: Mapping[str, Any] | None) -> bool:
    """``view``: registro de complejidad de la vista (``providers`` + ``metrics``). Vista de paso: 1 proveedor, 1 nodo SELECT, sin joins, uniones, columnas derivadas ni filtros.
    ``None``/vacío (no se conoce la vista) NO es de paso: nunca se asume transparencia sin evidencia."""
    if not view:
        return False
    m = view.get("metrics") or {}
    f = m.get("filters") or {}
    return (view.get("providers") == 1 and m.get("select_nodes") == 1 and not m.get("joins") and not m.get("unions") and not m.get("derived_columns")
            and not f.get("where_nodes") and not f.get("having_nodes"))
