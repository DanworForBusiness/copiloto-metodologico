"""model_lifecycle.py — RETIRO LÓGICO GOBERNADO de modelos y sus Assessments (D-LC-01 · LC-1). SOLO LECTURA: no escribe, no borra, no regenera.

PROBLEMA (auditoría D-LC-01, 2026-11-27): retirar un tile del Catálogo (``catalog_governance.retire``) solo lo ocultaba de ``visible_entries``; el pool, la
etapa de Assessment de la publicación y el padrón de los rulesets no conocían el retiro. ``REMOVED`` (borrado físico del sobre) exige que el AM salga del pool,
cosa que ningún flujo soportado hace. Resultado: un Assessment de un modelo retirado seguía «activo» (AM_TEST_VENTA_2).

CONTRATO (opción C de la auditoría): el estado «retirado del Catálogo» se DERIVA del registro de retiro existente (``<catalog>/.retired/<entry_id>.json``), sin
tocar el sobre, el pool ni el Juicio:

  ACTIVO     el modelo tiene tile visible (o no tiene registro de retiro).
  RETIRADO   hay un registro de retiro vigente que resuelve INEQUÍVOCAMENTE a ese AM terminal del cliente. Su sobre, su Juicio histórico, las decisiones que lo
             nombran y los respaldos de migración se CONSERVAN intactos (evidencia); deja de contar como Assessment activo. Es distinto de:
               · STALE / UNCERTAIN: vigencia de un Assessment ACTIVO (su entrada cambió o no se puede afirmar independencia);
               · REMOVED: borrado físico del sobre de un AM que salió del pool (``republication_stage._obsolete``); un modelo RETIRADO nunca se borra así.
  Reactivación: volver a publicar el propio modelo levanta el retiro (``catalog_governance.lift_retirement``) dejando constancia en ``.retired_history/``.

IDENTIDAD (sin depender solo del nombre): (cliente del registro, ``entry_id`` del tile) → AM terminal. Registros nuevos: ``terminal_am`` anclado al retirar con el
resolvedor canónico del Assessment (``assessment_service.resolve_entries``). Registros anteriores (sin ese campo): se resuelve el tile por su ``entry_id`` en el
Catálogo COMPLETO del cliente (incluye retirados) con el MISMO resolvedor. Lo que no resuelve con certeza queda ``unresolved`` (se informa; no se excluye nada).
El cliente se compara normalizado (espacios = «_», sin distinguir mayúsculas): un homónimo en OTRO cliente jamás se ve afectado.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from webapp.services import catalog_governance as gov

log = logging.getLogger("danwor.model_lifecycle")

ACTIVE, RETIRED = "ACTIVE", "RETIRED"
CAUSE_RETIRED = "RETIRED_FROM_CATALOG"                       # causa registrada de exclusión del padrón / del inventario activo
SOURCE_RECORD, SOURCE_CATALOG = "retirement_record", "catalog_entry"


@dataclass(frozen=True)
class RetiredModel:
    client: str
    terminal_am: str
    entry_id: str
    retired_at: str
    retired_by: str
    files_deleted: bool
    identity_source: str                                     # SOURCE_RECORD | SOURCE_CATALOG

    def as_dict(self) -> dict:
        return {"client": self.client, "terminal_am": self.terminal_am, "entry_id": self.entry_id, "retired_at": self.retired_at, "retired_by": self.retired_by,
                "files_deleted": self.files_deleted, "identity_source": self.identity_source, "cause": CAUSE_RETIRED}


@dataclass
class ClientRetirements:
    models: dict[str, RetiredModel] = field(default_factory=dict)       # {AM terminal: retiro}
    unresolved: list[dict] = field(default_factory=list)              # registros que no resuelven a un AM con certeza (se informan, no excluyen)

    def is_retired(self, am: str | None) -> bool:
        return bool(am) and am in self.models


def norm_client(value: Any) -> str:
    return str(value or "").strip().replace(" ", "_").casefold()


def _records(root: Path) -> list[dict]:
    out = []
    try:
        files = sorted((root / gov.RETIRED_DIR).glob("*.json"))
    except OSError:
        return out
    for p in files:
        rec = gov._read(p)
        if rec and gov.valid_entry_id(str(rec.get("entry_id") or "")) and rec.get("entry_id") == p.stem:
            out.append(rec)
    return out


def _resolve_by_catalog(root: Path, client_dir: str, entry_ids: set[str]) -> dict[str, str | None]:
    """``{entry_id: AM terminal | None}`` con el resolvedor canónico, sobre el Catálogo COMPLETO (incluye retirados). Se carga desde la RAÍZ del Catálogo —como el
    Hub—, porque el ``entry_id`` deriva de la ruta relativa a la raíz cargada (``<cliente>/<carpeta>/flujo_…``)."""
    if not entry_ids or not (root / client_dir).is_dir():
        return {}
    from tools.catalog import catalog_service
    from webapp.services import assessment_service as assess
    want = norm_client(client_dir)
    entries = [e for e in catalog_service.load_catalog(str(root)) if e.entry_id in entry_ids and norm_client(e.cliente) == want]
    try:
        res = assess.resolve_entries(entries)
    except Exception:                                        # noqa: BLE001 — sin resolución no se afirma identidad
        log.warning("model_lifecycle: no se pudo resolver la identidad de tiles retirados de %s", client_dir, exc_info=True)
        return {}
    return {e.entry_id: (res[e.entry_id].terminal_am if res.get(e.entry_id) is not None and res[e.entry_id].kind == "OK" else None) for e in entries}


def retirements(client: str, *, root: str | Path | None = None) -> ClientRetirements:
    """Modelos RETIRADOS del cliente (por AM terminal). SOLO LECTURA."""
    base = Path(root) if root else gov.catalog_root()
    want = norm_client(client)
    mine = [r for r in _records(base) if norm_client(r.get("cliente")) == want]
    out = ClientRetirements()
    if not mine:
        return out
    legacy = {str(r["entry_id"]) for r in mine if not r.get("terminal_am")}
    client_dir = str(mine[0].get("cliente") or client).strip().replace(" ", "_")
    by_catalog = _resolve_by_catalog(base, client_dir, legacy)
    for r in mine:
        eid = str(r["entry_id"])
        am, source = (str(r["terminal_am"]), SOURCE_RECORD) if r.get("terminal_am") else (by_catalog.get(eid), SOURCE_CATALOG)
        if not am:
            out.unresolved.append({"entry_id": eid, "client": r.get("cliente"), "reason": "TILE_NOT_RESOLVABLE"})
            continue
        out.models.setdefault(am, RetiredModel(str(r.get("cliente") or client), am, eid, str(r.get("retired_at") or ""), str(r.get("retired_by") or ""),
                                               bool(r.get("files_deleted")), source))
    return out


def lifecycle_state(client: str, am: str, *, root: str | Path | None = None) -> str:
    return RETIRED if retirements(client, root=root).is_retired(am) else ACTIVE


def split_consumers(client: str, consumers: Iterable[str], *, root: str | Path | None = None) -> tuple[list[str], list[str]]:
    """(activos, retirados) de una lista de AM consumidores: la trazabilidad se conserva completa, el conteo activo excluye los retirados."""
    ret = retirements(client, root=root)
    items = sorted(set(consumers or ()))
    return [c for c in items if not ret.is_retired(c)], [c for c in items if ret.is_retired(c)]


def padron_preview(*, client: str = "", root: str | Path | None = None) -> dict:
    """VISTA PREVIA (solo lectura) del padrón de una activación FUTURA con el retiro lógico aplicado: qué sobres entrarían y cuáles quedarían EXCLUIDOS con su
    causa registrada. NO modifica ``assessment_backfill`` ni el padrón real (eso requiere un GO aparte: ver el alcance en el handoff D-LC-01)."""
    from webapp.services import assessment_service as assess
    wanted = norm_client(client)
    cache: dict[str, ClientRetirements] = {}
    included, excluded = [], []
    for env, _path in assess.iter_governed_envelopes():
        c, am = str(env.get("client") or ""), str(env.get("terminal_am") or "")
        if wanted and norm_client(c) != wanted:
            continue
        ret = cache.setdefault(norm_client(c), retirements(c, root=root))
        j = env.get("judgment") or {}
        row = {"client": c, "terminal_am": am, "ruleset_version": j.get("ruleset_version"), "level": j.get("level"), "level_rule": j.get("level_rule")}
        if ret.is_retired(am):
            excluded.append({**row, **ret.models[am].as_dict()})
        else:
            included.append(row)
    unresolved = [u for r in cache.values() for u in r.unresolved]
    return {"included": included, "excluded": excluded, "unresolved": unresolved,
            "counts": {"envelopes": len(included) + len(excluded), "included": len(included), "excluded": len(excluded), "unresolved": len(unresolved)}}
