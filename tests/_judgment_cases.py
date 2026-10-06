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
# Casos congelados que la evidencia ACTUAL no sustenta causalmente (brecha de evidencia DOCUMENTADA, sin parche): alias -> brecha
KNOWN_EVIDENCE_GAPS = {"biogenesis_sellout_gp": "el vocabulario tecnico llega al publicado desde fuentes SQL Server (EXTERNAL_CONTRACT_UNDETERMINED); las tablas ABAP crudas A004/A307 no llegan "
                                                "al consumidor con su vocabulario. Con la clase de esas hojas externas establecida como tecnica/cruda el caso se sustenta (sensibilidad de politica externa)."}

# alias -> (nivel que el motor emite CON LA EVIDENCIA ACTUAL, regla/s decisiva/s, clase de la razon decisiva, dimension, cierre esperado)
BENCHMARK = {
    "illolay_pedidos_entregas": ("PARCIAL", ("LEK-MAT-02",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
    "biogenesis_base_stocks": ("PARCIAL", ("PRO-MAT-01",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
    "biogenesis_descuentos_comerciales": ("ADECUADA", ("HAR-REL-01",), "RELEVANT_WEAKNESS", "HARMONIZATION", "PROVISIONAL"),
    "kensing_gl_line_item_raw": ("BUENA", ("SRC-STR-01", "HAR-STR-01", "PRO-STR-01", "PUB-STR-01"), "POSITIVE_EVIDENCE", None, "FINAL"),
    "biogenesis_prefacturacion": ("ADECUADA", ("HAR-REL-02",), "RELEVANT_WEAKNESS", "HARMONIZATION", "PROVISIONAL"),
    "biogenesis_lista_precios": ("PARCIAL", ("PRO-MAT-01",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
    "biogenesis_reserva_material": ("PARCIAL", ("PRO-MAT-01",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
    "biogenesis_sellout_gp": ("ADECUADA", ("HAR-REL-02",), "RELEVANT_WEAKNESS", "HARMONIZATION", "PROVISIONAL"),
}


def load_case(alias: str) -> dict:
    return json.loads(gzip.open(FIXTURES / f"{alias}.json.gz").read().decode("utf-8"))


def fresh(alias: str) -> dict:
    return copy.deepcopy(load_case(alias))
