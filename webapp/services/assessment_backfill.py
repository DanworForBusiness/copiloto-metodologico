"""assessment_backfill.py — ALTA ÚNICA (offline) de assessments para entradas HISTÓRICAS del Catálogo.

Existe solo porque el Assessment se introdujo DESPUÉS de que ya hubiera entradas en el Catálogo: esas entradas no pasaron por el puente
Publicar → Assessment y figuran "Sin assessment". Es una acción de migración/bootstrap, NO comportamiento de request: ningún GET
(detalle del flujo, Portfolio) la invoca ni genera assessments.

Garantías (cubiertas por tests):
  * usa SOLO la evidencia local ya presente (el pool CSN del Catálogo): cero conexión a Datasphere, cero credenciales, cero extracción
    de linaje. Genera con ``assessment_service.generate`` (la misma API canónica del POST del informe);
  * omite lo que no está en ``NOT_YET_ASSESSED`` (ya vigente, desactualizado, no aplica, etc.: no se pisa ni se regenera);
  * una falla individual no corta el lote; se informa ``generated / skipped / failed`` con el motivo de cada uno.

Uso (con el MISMO ``DANWOR_CATALOG_DIR`` / ``DANWOR_ASSESSMENT_CACHE_DIR`` del servidor que se quiere poblar; la caché de assessments es
operativa y NO se espeja):
    python -m webapp.services.assessment_backfill [--client "Biogenesis"] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Callable, Iterable

from webapp.services import assessment_service as svc
from webapp.services import catalog_reads


def backfill(entries: Iterable[Any], *, dry_run: bool = False,
             status_for: Callable[[Any], svc.AssessmentStatus] = svc.status_for_entry,
             generate: Callable[[Any], svc.AssessmentStatus] = svc.generate) -> dict:
    out: dict = {"generated": [], "skipped": [], "failed": [], "dry_run": dry_run}
    for entry in entries:
        ref = {"entry_id": getattr(entry, "entry_id", ""), "client": str(getattr(entry, "cliente", "")),
               "domain": str(getattr(entry, "dominio", ""))}
        try:
            status = status_for(entry)
            if status.state != svc.NOT_YET_ASSESSED:
                out["skipped"].append({**ref, "state": status.state, "reason": status.reason})
                continue
            if dry_run:
                out["skipped"].append({**ref, "state": status.state, "reason": "dry-run"})
                continue
            result = generate(entry)
        except Exception as exc:                                        # noqa: BLE001  una entrada rota no corta el lote
            out["failed"].append({**ref, "state": svc.FAILED, "reason": f"{type(exc).__name__}: {exc}"})
            continue
        row = {**ref, "state": result.state, "reason": result.reason}
        bucket = "generated" if result.state == svc.CURRENT else "skipped" if result.state == svc.NOT_APPLICABLE else "failed"
        out[bucket].append(row)
    out["counts"] = {k: len(out[k]) for k in ("generated", "skipped", "failed")}
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--client", default="", help="limitar a un cliente (por defecto: todas las entradas del Catálogo)")
    ap.add_argument("--dry-run", action="store_true", help="solo informa qué se generaría")
    args = ap.parse_args(argv)
    _mod, entries = catalog_reads._catalog_entries()
    wanted = args.client.strip().lower()
    entries = [e for e in (entries or []) if not wanted or str(getattr(e, "cliente", "")).strip().lower() == wanted]
    report = backfill(entries, dry_run=args.dry_run)
    sys.stdout.buffer.write((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))   # UTF-8 aun con stdout redirigido (cp1252)
    return 1 if report["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
