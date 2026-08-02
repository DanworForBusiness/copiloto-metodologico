"""portability_telemetry.py — Telemetría de la dimensión Portabilidad BDC.

Escanea el catálogo generado (dominios + data products), agrega qué reglas disparan con
más frecuencia y el score/estado por activo, y escribe reports/portability_bdc.yaml. Es
READ-ONLY sobre el catálogo (nunca lo muta); sigue el patrón de analyze_reconciliation.

Permite que la matriz evolucione con evidencia: ver qué reglas se disparan más, cuáles
activos concentran riesgos, y (a futuro) descartar falsos positivos vía acknowledgements.
No incluye contenido de clientes más allá del nombre técnico del activo/artefacto.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


def _read_portability(md_path: Path) -> tuple[str, dict | None]:
    try:
        text = md_path.read_text(encoding="utf-8", errors="ignore")
        m = re.match(r"^---\n(.*?)\n---", text, re.S)
        if not m or yaml is None:
            return "", None
        auto = (yaml.safe_load(m.group(1)) or {}).get("auto") or {}
        return str(auto.get("tipo") or ""), auto.get("portabilidad_bdc")
    except Exception:
        return "", None


def aggregate(cat_dir: str | Path) -> dict:
    cat = Path(cat_dir)
    activos: list[dict] = []
    reglas = Counter()
    for md in sorted(cat.glob("**/flujo_*.md")) + sorted((cat / "data_products").glob("*.md")):
        tipo, port = _read_portability(md)
        if not isinstance(port, dict) or not port:
            continue
        activos.append({
            "activo": md.stem,
            "tipo": tipo or "?",
            "score": port.get("score"),
            "clasificacion": port.get("clasificacion"),
            "confianza": port.get("confianza"),
            "riesgos": port.get("n_riesgos", 0),
            "adaptaciones": port.get("n_adaptaciones", 0),
            "no_evaluables": port.get("campos_no_evaluables", 0),
        })
        for f in port.get("findings") or []:
            reglas[str(f.get("regla"))] += 1
    return {
        "total_activos": len(activos),
        "reglas_disparadas": dict(reglas.most_common()),
        "activos": sorted(activos, key=lambda a: (a.get("score") if a.get("score") is not None else 999)),
    }


def run(cat_dir: str | Path, report_dir: str | Path | None = None) -> tuple[dict, str]:
    """Agrega y persiste reports/portability_bdc.yaml. Devuelve (report, línea_de_estado)."""
    report = aggregate(cat_dir)
    rep_dir = Path(report_dir) if report_dir else (Path(cat_dir).parent / "reports")
    if yaml is not None:
        try:
            rep_dir.mkdir(parents=True, exist_ok=True)
            (rep_dir / "portability_bdc.yaml").write_text(
                yaml.safe_dump(report, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception:
            pass
    top = ", ".join(f"{k}×{v}" for k, v in list(report["reglas_disparadas"].items())[:4]) or "—"
    line = f"[portabilidad BDC] {report['total_activos']} activo(s) evaluado(s); reglas top: {top}"
    return report, line
