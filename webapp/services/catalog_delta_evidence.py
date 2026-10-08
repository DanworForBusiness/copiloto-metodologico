"""
catalog_delta_evidence.py — evidencia CANÓNICA de captura delta de una tabla local (Catálogo → detalle técnico y colapso de linaje).

UN solo hecho gobierna tres lecturas:
    · colapso del par «ingesta local X + su compañera técnica X_Delta» en un único objeto lógico del linaje;
    · la propiedad «Delta capture» del detalle técnico del objeto lógico;
    · la «Tabla técnica de cambios» (trazabilidad) cuando la compañera es conocida.

La fuente es la definición AUTORITATIVA del pool CSN (`definitions.<X>["@DataWarehouse.delta"]`, la anotación nativa de una tabla local
delta-capable que ya usa el Assessment: ``readers.technical_delta_shadow_entities``), nunca la ausencia de un nodo `_Delta` en el Catálogo:

    ON            la definición declara la captura delta (`deltaFromEntities` no vacío o `type: ACTIVE`);
    OFF           la definición se evaluó, es una tabla local y NO trae ninguna configuración delta;
    UNDETERMINED  no hay definición autoritativa en el pool (sin pool / sin JSON / no es tabla local) o la anotación no alcanza para afirmar ON ni OFF.

Falta de evidencia NUNCA se convierte en OFF. Solo lectura; el índice en memoria guarda solo (nombre → anotación) por huella del pool.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from webapp.services import assessment_service as asvc
from webapp.services import catalog_view_source as cvs

log = logging.getLogger("danwor.catalog_delta_evidence")

ON, OFF, UNDETERMINED = "ON", "OFF", "UNDETERMINED"
_DELTA = "@DataWarehouse.delta"
_LOCAL_TABLE = "DWC_LOCAL_TABLE"
_lock = threading.Lock()
_memo: dict[tuple[str, str], tuple[tuple, dict]] = {}


def _tag(value: Any) -> str:
    return str(value.get("#") if isinstance(value, dict) else value or "")


def classify_definition(defn: Any) -> dict:
    """Estado de captura delta de UNA definición CSN: {state, companions}. Puro (testeable sin pool)."""
    if not isinstance(defn, dict):
        return {"state": UNDETERMINED, "companions": []}
    if _tag(defn.get("@DataWarehouse.repository.technicalType")) != _LOCAL_TABLE:
        return {"state": UNDETERMINED, "companions": []}            # no es una tabla local: la propiedad no aplica / no hay base para afirmar
    if _DELTA not in defn:
        return {"state": OFF, "companions": []}                       # definición autoritativa evaluada, sin configuración delta
    delta = defn.get(_DELTA)
    if not isinstance(delta, dict):
        return {"state": UNDETERMINED, "companions": []}
    companions = [str(c) for c in (delta.get("deltaFromEntities") or []) if isinstance(c, str) and c]
    if companions or _tag(delta.get("type")).upper() == "ACTIVE":
        return {"state": ON, "companions": companions}
    return {"state": UNDETERMINED, "companions": []}                  # anotación presente pero ambigua: no se afirma ni ON ni OFF


def _index(client: str, domain: str) -> dict:
    found = asvc.pool_json_files(client, domain)
    if found is None:
        return {}
    root, files = found
    fp = tuple(sorted((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files))
    key = (str(root), client.lower())
    with _lock:
        cached = _memo.get(key)
        if cached is not None and cached[0] == fp:
            return cached[1]
    index: dict[str, dict] = {}
    for path in sorted(files):
        data = cvs.load_json(path)
        for name, defn in ((data or {}).get("definitions") or {}).items():
            if name not in index and isinstance(defn, dict):
                index[name] = classify_definition(defn)
    with _lock:
        _memo[key] = (fp, index)
    return index


def delta_capture(client: str, domain: str, name: str) -> dict:
    """{state: ON|OFF|UNDETERMINED, companions: [X_Delta…]} de la tabla local ``name``. Jamás lanza: ante cualquier falla, UNDETERMINED."""
    try:
        return _index(client, domain).get(name) or {"state": UNDETERMINED, "companions": []}
    except Exception:                                               # noqa: BLE001
        log.exception("catalog_delta_evidence: no se pudo leer la evidencia de %s/%s", client, name)
        return {"state": UNDETERMINED, "companions": []}
