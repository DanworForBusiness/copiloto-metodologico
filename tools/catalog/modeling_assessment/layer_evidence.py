"""layer_evidence — definiciones PURAS de capa/contrato que las señales leen, tomadas de la fuente única compartida con el Juicio.

``signals.py`` sólo lee hechos y no puede importar el bridge ni el código legacy (guardas AST); este módulo intra-paquete expone, sin lógica propia, las definiciones neutras de
``tools/catalog/layer_semantics.py`` (a las que sólo se llega por ``legacy_bridge``, invariante R-I3): una vista de paso, un contrato de origen gobernado y las bases de capa con evidencia.
"""
from __future__ import annotations

from . import legacy_bridge as _bridge

GOVERNED_CONTRACT_CLASSES = _bridge.layer_semantics.GOVERNED_CONTRACT_CLASSES
LAYER_EVIDENCE_BASES = _bridge.layer_semantics.LAYER_EVIDENCE_BASES
is_pass_through_view = _bridge.layer_semantics.is_pass_through_view
