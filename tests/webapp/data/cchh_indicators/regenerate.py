"""Regenera `dataset.json` y `golden.json` del oráculo diferencial de 06 Indicadores. NO lo ejecutan los tests (solo leen los JSON versionados).

Uso (requiere Node >= 22.6 y el código fuente de la app de referencia):
    python tests/webapp/data/cchh_indicators/regenerate.py "E:\\Joe\\Metodologia\\Refactorizacion\\cchh\\app-seleccion"

Se copian `lib/metricas.ts`, `lib/types.ts` y `lib/publicaciones.ts` a un directorio temporal con los alias `@/lib/...` reescritos a rutas relativas (únicos cambios; el cálculo es el original) y se
ejecutan con TZ=UTC. El golden registra su procedencia (hash SHA-256 de los tres archivos y versión de Node). Regenerar solo si cambia `build_raw_dataset` o el original.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3]))                       # raíz del repo
from tests.webapp.human_capital_indicators_fixtures import build_raw_dataset, to_original_inputs  # noqa: E402


def main(original_root: str) -> None:
    lib = Path(original_root) / "lib"
    raw = build_raw_dataset()
    (HERE / "dataset.json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    hashes = {}
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("metricas.ts", "types.ts", "publicaciones.ts"):
            text = (lib / name).read_bytes()
            hashes[name] = hashlib.sha256(text).hexdigest()
            (Path(tmp) / name).write_text(text.decode("utf-8").replace('"@/lib/types"', '"./types.ts"').replace('"@/lib/publicaciones"', '"./publicaciones.ts"'), encoding="utf-8")
        result = subprocess.run(
            ["node", "--experimental-strip-types", "--no-warnings", str(HERE / "oracle_run.mjs"), tmp],
            input=json.dumps(to_original_inputs(raw)), capture_output=True, text=True, env={**os.environ, "TZ": "UTC"}, check=True)
        node_version = subprocess.run(["node", "--version"], capture_output=True, text=True, check=True).stdout.strip()
    golden = {"provenance": {"source": "app-seleccion lib/metricas.ts (funciones originales, sin cambios de cálculo)", "sha256": hashes, "node": node_version, "tz": "UTC",
                              "dataset_seed": 20261020, "excluded": ["busquedasEstancadas (depende del reloj)"]}, "results": json.loads(result.stdout)}
    (HERE / "golden.json").write_text(json.dumps(golden, ensure_ascii=False, indent=1), encoding="utf-8")
    print("dataset.json y golden.json regenerados;", json.dumps(hashes))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    main(sys.argv[1])
