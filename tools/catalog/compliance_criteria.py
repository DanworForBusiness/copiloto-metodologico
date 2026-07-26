"""compliance_criteria.py — FUENTE ÚNICA del criterio de cumplimiento del catálogo.

Antes el criterio estaba DUPLICADO: `classify_extraction` vivía en `generate_catalog`
y una copia en `analyze_reconciliation`; las frases de `apto` en `evaluate_compliance`
y en `catalog_service._APTO_ESTANDAR`. Eso podía desincronizarse en silencio.

Este módulo (sin imports, solo constantes + una función pura) es el único lugar del
criterio compartido. Lo importan `generate_catalog` (automatización), `catalog_service`
(lectura/exhibición) y `analyze_reconciliation` (reconciliación). Así el criterio queda
espejado TRANSVERSALMENTE por construcción, no por chequeo posterior. Un test de
invariantes (tests/test_compliance_rubric.py) verifica que nadie lo reintroduzca por copia.
"""

# Enum cerrado de veredictos (mirada Danwor). No es texto libre.
VERDICTS = ("plena", "buena", "parcial")

# Etiqueta canónica de `apto` por veredicto — la que se muestra cuando el senior
# CONFIRMA un veredicto (override). El generador construye sus frases automáticas a
# partir de estas bases (más un sufijo descriptivo), así la parte canónica nunca diverge.
APTO_CANONICAL = {
    "plena": "Referencia Danwor — reutilizable",
    "buena": "Reutilizable con nota de especificidad",
    "parcial": "Inspiración conceptual con reservas — no copiar tal cual",
}


def classify_extraction(remote_entity: str) -> str:
    """Clasifica la CALIDAD del desacople del origen (eje primario del semáforo).

    ideal          → CDS view corporativa (ABAP_CDS.*), desacople correcto.
    acceptable_view→ vista estándar SAP (ABAPTABLES.NSDM_V_* / _V_*), no tabla cruda.
    poor_raw       → tabla ABAP cruda replicada directa (viola el desacople).
    acceptable_bw  → BW Bridge (virtualización), válido pero específico.
    unknown / ""   → otro origen (p. ej. SAPI.*) / sin origen.
    """
    r = (remote_entity or "").upper()
    if r.startswith("ABAP_CDS."):
        return "ideal"
    if r.startswith("ABAPTABLES.") and ("NSDM" in r or "_V_" in r):
        return "acceptable_view"
    if r.startswith("ABAPTABLES."):
        return "poor_raw"
    if "BWBRIDGE" in r:
        return "acceptable_bw"
    return "unknown" if r else ""
