"""evidence_observability.py — cobertura de OBSERVACIÓN de la evidencia por categoría (``evidence-observability/v1``). Solo evidencia: no es juicio.

Distingue «observado, sin artefactos» de «no observable», que ``evidence_scope.effective_features`` no puede expresar (``[]`` o ``None`` nunca
prueban ausencia):

  OBSERVED      la extracción leyó la categoría (con o sin artefactos); ``artifacts`` = objetos de esa categoría en el pool de ESTA corrida.
  NOT_OBSERVED  la extracción NO la leyó (excluida o no efectiva en esta corrida).
  UNAVAILABLE   el canal no permite saberlo: pool histórico / del Catálogo sin metadato de extracción y SIN objetos de la categoría (no se distingue
                «exportado y vacío» de «no exportado»). Con objetos presentes, la categoría se OBSERVÓ.

Compatible con pools históricos: sin metadato de extracción se deriva del CONTENIDO del pool, sin exigir republicaciones ni migraciones.
"""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Mapping

EVIDENCE_OBSERVABILITY_VERSION = "evidence-observability/v1"
OBSERVED, NOT_OBSERVED, UNAVAILABLE = "OBSERVED", "NOT_OBSERVED", "UNAVAILABLE"
SOURCE_EXTRACTOR, SOURCE_POOL = "EXTRACTOR", "POOL"
CATEGORIES = ("analytic-models", "views", "tables", "data-flows", "replication-flows", "transformation-flows", "task-chains")
_DIR_CATEGORY = {"analytic-models": "analytic-models", "views": "views", "remote-tables": "tables", "local-tables": "tables",
                 "data-flows": "data-flows", "replication-flows": "replication-flows", "transformation-flows": "transformation-flows",
                 "task-chains": "task-chains"}


def build_observability(evidence_scope: Mapping | None, files: Iterable[Path]) -> dict:
    counts = {c: 0 for c in CATEGORIES}
    for f in files:
        cat = _DIR_CATEGORY.get(Path(f).parent.name)
        if cat:
            counts[cat] += 1
    effective = set((evidence_scope or {}).get("effective_features") or ()) if evidence_scope else None
    cats = {}
    for c in CATEGORIES:
        if effective is not None:
            state = OBSERVED if c in effective else NOT_OBSERVED
        else:
            state = OBSERVED if counts[c] > 0 else UNAVAILABLE
        cats[c] = {"state": state, "artifacts": counts[c]}
    return {"version": EVIDENCE_OBSERVABILITY_VERSION, "source": SOURCE_EXTRACTOR if effective is not None else SOURCE_POOL, "categories": cats}
