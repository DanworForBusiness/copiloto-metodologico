"""catalog_retirement.py — D-LC-01 · LC-2: el generador del Catálogo respeta los tiles RETIRADOS CON BORRADO DE ARCHIVOS. Solo lectura de ``<catalog>/.retired``.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Un tile retirado con «eliminar también sus archivos» (``catalog_governance.purge_files``) dejaba de existir en disco y en Spaces, pero la próxima
# publicación de CUALQUIER modelo del cliente regeneraba su carpeta desde el pool (el borrado no era durable). Ahora el flujo PROPIO de ese modelo
# (un cubo = un AM terminal) no se vuelve a escribir. Alcance acotado: no se borra nada (ni del pool ni del Catálogo: una carpeta que ya exista queda
# como está), los flujos de dominio legacy (2 niveles, varios modelos) y los objetos compartidos siguen igual, y un retiro SIN borrado no cambia nada
# (su tile se oculta como siempre). La REACTIVACIÓN explícita —volver a publicar ese mismo modelo— lo regenera (``reactivate``).
# Identidad: el AM anclado en el registro de retiro (``terminal_am``) o, en registros anteriores, el ``entry_id`` exacto que este generador produciría.
RETIRED_DIR = ".retired"


@dataclass(frozen=True)
class RetiredSuppression:
    by_id: dict                                            # {entry_id: AM terminal anclado | None (registro anterior)}
    models: frozenset                                      # AM terminales anclados

    def applies(self, client_dir: str, folder: str, cube_name: str, reactivate: frozenset = frozenset()) -> bool:
        if cube_name in reactivate:
            return False
        if cube_name in self.models:
            return True
        eid = _entry_id_for(client_dir, folder)
        return eid in self.by_id and self.by_id[eid] is None       # registro sin AM anclado: identidad por entry_id; si ancla OTRO AM, no es este modelo


def _entry_id_for(client_dir: str, folder: str) -> str:
    """El ``entry_id`` que ``catalog_service`` le asigna al flujo ``<cliente>/<carpeta>/flujo_<carpeta>.md``."""
    try:
        from catalog_service import _slug
    except Exception:  # pragma: no cover
        from tools.catalog.catalog_service import _slug
    return _slug(f"{client_dir}/{folder}/flujo_{folder}")


def purged_retirements(out_root) -> dict[str, RetiredSuppression]:
    """``{cliente normalizado: supresión}`` de los tiles retirados CON borrado de archivos (``files_deleted``). Solo lectura; registros ilegibles se ignoran."""
    base = Path(out_root) / RETIRED_DIR
    acc: dict[str, tuple[dict, set]] = {}
    try:
        files = sorted(base.glob("*.json")) if base.is_dir() else []
    except OSError:
        return {}
    for p in files:
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(rec, dict) or not rec.get("files_deleted") or rec.get("entry_id") != p.stem:
            continue
        by_id, models = acc.setdefault(str(rec.get("cliente") or "").strip().replace(" ", "_").casefold(), ({}, set()))
        am = str(rec["terminal_am"]) if rec.get("terminal_am") else None
        by_id[p.stem] = am
        if am:
            models.add(am)
    return {k: RetiredSuppression(by_id, frozenset(models)) for k, (by_id, models) in acc.items()}
