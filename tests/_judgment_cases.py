"""Casos de calibracion (benchmark humano CONGELADO) como fixtures SEUDONIMIZADOS: solo las secciones del perfil que lee el motor, nombres de artefactos reemplazados.

Origen: perfiles de Assessment gobernados generados por el motor de ``origin/main`` (evidencia canonica v2). No hay logica por cliente/raiz: los casos solo se cargan por alias."""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "methodological_judgment"

# Niveles humanos CONGELADOS (la unica verdad de calibracion). Ningun nivel humano se toca para que el motor coincida.
FROZEN_LABELS = {"illolay_pedidos_entregas": "PARCIAL", "biogenesis_base_stocks": "PARCIAL", "biogenesis_descuentos_comerciales": "ADECUADA", "kensing_gl_line_item_raw": "BUENA",
                 "biogenesis_prefacturacion": "ADECUADA", "biogenesis_lista_precios": "PARCIAL", "biogenesis_reserva_material": "PARCIAL", "biogenesis_sellout_gp": "PARCIAL"}
# Casos cuyo nivel humano congelado la evidencia ACTUAL no AFIRMA (brecha DOCUMENTADA, no un workaround): alias -> brecha.
# SELLOUT_GP: el motor NO afirma el nivel; lo marca NOT_DETERMINABLE decision-critico (la clase de la fuente externa decide leak vs reuso).
KNOWN_EVIDENCE_GAPS = {"biogenesis_sellout_gp": "el vocabulario tecnico llega al publicado VERBATIM desde fuentes SQL Server de clase EXTERNAL_CONTRACT_UNDETERMINED; las tablas ABAP crudas "
                                                "A004/A307 no llegan al consumidor con su vocabulario. La clase de esas hojas externas es decision-critica (si cruda, debilidad de contrato publicado; "
                                                "si gobernada, reuso): sin resolverla, el motor emite NOT_DETERMINABLE (no ADECUADA ni PARCIAL falsas). La etiqueta humana congelada sigue PARCIAL."}

# alias -> (nivel que el motor emite CON LA EVIDENCIA ACTUAL, regla/s o razon decisiva, clase de la razon decisiva, dimension, cierre esperado).
# Para NOT_DETERMINABLE: nivel = None, "decisive" = (codigo de not_determinable_reason,), clase = "NOT_DETERMINABLE", dimension = None.
BENCHMARK = {
    "illolay_pedidos_entregas": ("PARCIAL", ("LEK-MAT-02",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
    "biogenesis_base_stocks": ("PARCIAL", ("PRO-MAT-01",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
    "biogenesis_descuentos_comerciales": ("ADECUADA", ("HAR-REL-01",), "RELEVANT_WEAKNESS", "HARMONIZATION", "PROVISIONAL"),
    "kensing_gl_line_item_raw": ("BUENA", ("SRC-STR-01", "HAR-STR-01", "PRO-STR-01", "PUB-STR-01"), "POSITIVE_EVIDENCE", None, "FINAL"),
    "biogenesis_prefacturacion": ("ADECUADA", ("HAR-REL-02",), "RELEVANT_WEAKNESS", "HARMONIZATION", "PROVISIONAL"),
    "biogenesis_lista_precios": ("PARCIAL", ("PRO-MAT-01",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
    "biogenesis_reserva_material": ("PARCIAL", ("PRO-MAT-01",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
    "biogenesis_sellout_gp": (None, ("EXTERNAL_SOURCE_CONTRACT_DECISION_CRITICAL",), "NOT_DETERMINABLE", None, "PROVISIONAL"),
}


def load_case(alias: str) -> dict:
    return json.loads(gzip.open(FIXTURES / f"{alias}.json.gz").read().decode("utf-8"))


def fresh(alias: str) -> dict:
    return copy.deepcopy(load_case(alias))
