"""Casos de calibracion (benchmark humano CONGELADO) como fixtures SEUDONIMIZADOS: solo las secciones del perfil que lee el motor, nombres de artefactos reemplazados.

Origen: perfiles de Assessment gobernados generados por el motor de ``origin/main`` (evidencia canonica v2). No hay logica por cliente/raiz: los casos solo se cargan por alias."""
from __future__ import annotations

import copy
import gzip
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "methodological_judgment"

# alias -> (nivel humano congelado, regla/s decisiva/s, clase de la razon decisiva, dimension, cierre esperado)
BENCHMARK = {
    "illolay_pedidos_entregas": ("PARCIAL", ("LEK-MAT-02",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
    "biogenesis_base_stocks": ("PARCIAL", ("PRO-MAT-01",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
    "biogenesis_descuentos_comerciales": ("ADECUADA", ("HAR-REL-01",), "RELEVANT_WEAKNESS", "HARMONIZATION", "PROVISIONAL"),
    "kensing_gl_line_item_raw": ("BUENA", ("SRC-STR-01", "HAR-STR-01", "PRO-STR-01", "PUB-STR-01"), "POSITIVE_EVIDENCE", None, "FINAL"),
    "biogenesis_prefacturacion": ("ADECUADA", ("HAR-REL-02",), "RELEVANT_WEAKNESS", "HARMONIZATION", "PROVISIONAL"),
    "biogenesis_lista_precios": ("PARCIAL", ("PRO-MAT-01",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
    "biogenesis_reserva_material": ("PARCIAL", ("PRO-MAT-01",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
    "biogenesis_sellout_gp": ("PARCIAL", ("LEK-MAT-02",), "MATERIAL_DEVIATION", "PROPAGATION", "PROVISIONAL"),
}


def load_case(alias: str) -> dict:
    return json.loads(gzip.open(FIXTURES / f"{alias}.json.gz").read().decode("utf-8"))


def fresh(alias: str) -> dict:
    return copy.deepcopy(load_case(alias))
