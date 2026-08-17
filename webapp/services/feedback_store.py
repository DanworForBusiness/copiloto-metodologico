"""feedback_store.py — seam de escritura COMPARTIDO de la familia de feedback.

Extrae el ÚNICO mecanismo genérico que las 4 familias de feedback de Copilot
(`webapp/routers/feedback.py`) tenían deliberadamente duplicado: mkdir del
`FEEDBACK_LOG_DIR` + serializar el payload a YAML + write-tmp + `replace`
atómico + best-effort (el feedback NUNCA rompe la navegación). Reporting reusa
ESTE seam (mismo store, mismo formato, misma analítica) en vez de crear un
almacenamiento propio — "Shared Hub Feedback" (ver ticket CLOSE REPORTING §A2).

Deliberadamente NO conoce la taxonomía de `type`/campos: el caller arma el
payload (incluido `type`, `user`, `ts`) y elige el `prefix` del archivo. Así el
lector/analítica (`analyze_feedback`) sigue agrupando por el campo `type` del
YAML, agnóstico de quién lo escribió. Los writers históricos de Copilot quedan
intactos a propósito (no desestabilizar Copilot); pueden adoptar este helper
más adelante sin cambio de contrato en disco."""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import yaml

from config import FEEDBACK_LOG_DIR


def write_feedback_record(payload: dict[str, Any], *, prefix: str, stamp: str) -> None:
    """Persiste `payload` como un YAML en `FEEDBACK_LOG_DIR`. `prefix` +
    `stamp` (timestamp ya formateado por el caller, p. ej. `%Y%m%d_%H%M%S`)
    nombran el archivo; un `uuid4` corto evita colisiones dentro del segundo.
    Best-effort: cualquier fallo de IO se traga (el feedback nunca rompe la
    experiencia). El `ts` ISO y demás campos viven DENTRO de `payload`."""
    try:
        FEEDBACK_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = FEEDBACK_LOG_DIR / f"{prefix}_{stamp}_{uuid4().hex[:8]}.yaml"
        temporary = path.with_suffix(".yaml.tmp")
        temporary.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
        temporary.replace(path)
    except Exception:  # noqa: BLE001 — el feedback nunca rompe la navegación
        pass
