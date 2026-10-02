"""Modeling Self-Assessment v2 — FASE 1 (shadow, solo perfil, SIN score).

Paquete NUEVO y paralelo al evaluador legacy (`generate_catalog.evaluate_compliance`),
que permanece intacto y sigue sirviendo a sus consumidores. Reglas de frontera:

  * dependencia en UN solo sentido: este paquete -> helpers legacy; el legacy NUNCA
    importa este paquete;
  * TODO acceso al legacy pasa por `legacy_bridge.py`;
  * no hay hook en `generate_all` ni se modifica `generate_catalog.py` (su sha256
    completo participa de la identidad de generacion del catalogo);
  * la salida shadow es LOCAL-ONLY y NO PERSISTIDA: destino explicito y configurable, sin
    default dentro de `catalog_local` (que `mirror_up()` espeja a Spaces); ver `paths.py`;
  * el perfil NO contiene scores, bandas, pesos, umbrales, penalidades ni veredictos.

Importar este paquete es liviano: NO carga el legacy (solo `legacy_bridge` lo hace).
"""
from .model import RULESET_VERSION, SCHEMA_VERSION

__all__ = ["SCHEMA_VERSION", "RULESET_VERSION"]
