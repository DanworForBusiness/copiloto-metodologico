"""republication_stage.py — el assessment como ETAPA de la unidad de publicación (P1).

La publicación segura aplica pool, manifiestos y Catálogo bajo el candado de escritor, con undo y detrás de la compuerta de lectores. Esta etapa agrega los assessments gobernados del
cliente a la MISMA unidad, con el criterio del dueño:

  AFECTADO (contiene el modelo publicado, o cambió algo de lo que su assessment lee) → regeneración obligatoria ANTES de activar: el motor corre en memoria sobre el pool ya aplicado, se
      valida, y recién si todo valida se escribe en local. Si cualquiera falla, la unidad entera se deshace y queda la última versión válida.
  NO afectado, independencia demostrada (firma de dependencias igual antes y después) → conserva su vigencia; si su sobre no traía la firma, se le agrega (sin regenerar).
  Dependencias INCIERTAS → no se afirma independencia: queda STALE explícito (``UNCERTAIN``) y trazable en la bitácora.
  Sin AM en el pool (modelo retirado) → su sobre gobernado se retira: no quedan diagnósticos obsoletos en la versión activa.

Implementa el protocolo de ``tools/catalog/publication_txn.StageHooks``. Solo LOCAL: la confirmación durable en Spaces es de la unidad.
"""
from __future__ import annotations

import copy
import logging
import os
from pathlib import Path
from typing import Any

import config
from webapp.services import assessment_service as assess

log = logging.getLogger("danwor.republication_stage")

KEPT, REBASED, REGENERATED, UNCERTAIN, LEFT, REMOVED = "kept", "rebased", "regenerated", "uncertain", "left_as_is", "removed"


class StageError(RuntimeError):
    """La etapa no pudo prepararse: la unidad se deshace. ``code`` estable; ``failures`` con el detalle por AM (sin trazas)."""

    def __init__(self, code: str, failures: list[dict] | None = None):
        super().__init__(code)
        self.code, self.failures = code, list(failures or [])


class AssessmentStage:
    def __init__(self) -> None:
        self._pre: dict[str, dict] = {}
        self._pre_envelopes: dict[str, dict] = {}
        self._prepared: list[assess.PreparedAssessment] = []
        self._removed: list[str] = []
        self.summary: dict[str, list[str]] = {}
        self._entries_by_am: dict[str, Any] = {}

    # ── protocolo de la unidad ──
    def persisted_names(self) -> tuple[str, ...]:
        from core import remote_store
        return (config.GOVERNED_ASSESSMENTS_NAME,) if config.GOVERNED_ASSESSMENTS_NAME in {n for n, _ in remote_store._persist_dirs()} else ()

    def governed_client_dir(self, ctx) -> Path | None:
        return assess.governed_client_dir(ctx.client_dir)

    def durable_store(self, ctx) -> tuple[str, Path | None]:
        """El almacén gobernado de assessments DEBE ser durable: la publicación segura no admite un assessment solo en disco efímero (B2)."""
        return config.GOVERNED_ASSESSMENTS_NAME, self.governed_client_dir(ctx)

    def undo_paths(self, ctx) -> list[Path]:
        d = self.governed_client_dir(ctx)
        return [d] if d is not None else []

    def _entries(self, ctx) -> list[Any]:
        from tools.catalog import catalog_service
        folder = Path(ctx.catalog_root) / ctx.client_dir
        return catalog_service.load_catalog(folder) if folder.is_dir() else []

    def _guard_pool(self, ctx) -> None:
        """El assessment lee el pool por ``DANWOR_CATALOG_CSN_CACHE`` o ``<catalog>/.csn_cache``: debe ser EXACTAMENTE el pool que se está publicando (aunque todavía no exista)."""
        expected = Path(os.getenv("DANWOR_CATALOG_CSN_CACHE", "").strip() or str(assess.catalog_dir() / ".csn_cache"))
        if expected.resolve() != Path(ctx.csn_cache_root).resolve() or assess.catalog_dir().resolve() != Path(ctx.catalog_root).resolve():
            raise StageError("POOL_ROOT_MISMATCH")          # el assessment leería un pool distinto del que se está publicando

    def needs_work(self, ctx) -> bool:
        """Sin cambios en el pool: ¿algún AM que contiene el modelo publicado no está vigente? (Si no, la republicación es un NO_CHANGE.)"""
        self._guard_pool(ctx)
        entries = [e for e in self._entries(ctx) if ctx.root_object in {str(a) for a in (e.artefactos or [])}]
        if not entries:
            return False
        for st in assess.statuses_for_entries(entries).values():
            if st.state not in (assess.CURRENT, assess.NOT_APPLICABLE):
                return True
        return False

    def before_apply(self, ctx) -> None:
        """Estado PREVIO (antes de tocar nada): vigencia y firma de dependencias de cada AM del cliente contra el pool VIEJO."""
        self._guard_pool(ctx)
        entries = self._entries(ctx)
        resolutions = assess.resolve_entries(entries) if entries else {}
        versions = assess.current_assessment_versions()
        for e in entries:
            res = resolutions[e.entry_id]
            if res.kind != "OK" or res.terminal_am in self._pre:
                continue
            env = assess._load_envelope(res.client, res.terminal_am)
            state = assess._status_from(e, res, versions).state if env is not None else assess.NOT_YET_ASSESSED
            sig = None
            if state == assess.CURRENT:
                sig = (env or {}).get("input_closure_signature") if (env or {}).get("dependency_rule_version") == assess.ma_dependencies.DEPENDENCY_RULE_VERSION else None
                sig = sig or assess.closure_signature(res)
            self._pre[res.terminal_am] = {"state": state, "closure": sig, "has_env": env is not None, "has_signature": bool((env or {}).get("input_closure_signature"))}
        self._pre_envelopes = {am: copy.deepcopy(env) for am, env in assess.governed_envelopes_of(ctx.client_dir).items()}

    def after_catalog(self, ctx) -> None:
        """PREPARA (memoria, sin escribir): clasifica cada AM y corre el motor sobre los afectados. Falla ⇒ ``StageError`` y la unidad se deshace."""
        self._guard_pool(ctx)
        entries = self._entries(ctx)
        resolutions = assess.resolve_entries(entries)
        summary: dict[str, list[str]] = {k: [] for k in (REGENERATED, REBASED, KEPT, UNCERTAIN, LEFT, REMOVED)}
        prepared: list[assess.PreparedAssessment] = []
        failures: list[dict] = []
        unhealthy = [r for r in resolutions.values() if r.kind in ("POOL_LOAD_ERROR", "POOL_AMBIGUOUS_DOMAIN", "POOL_UNAVAILABLE")]
        by_am: dict[str, tuple[Any, assess.Resolution]] = {}
        for e in entries:
            res = resolutions[e.entry_id]
            if res.kind == "OK":
                by_am.setdefault(res.terminal_am, (e, res))
        if unhealthy and any(ctx.root_object in {str(a) for a in (e.artefactos or [])} for e in entries):
            kinds = sorted({r.kind for r in unhealthy})
            raise StageError("ASSESSMENT_FAILED", [{"kind": "assessment", "object_type": "", "technical_name": ctx.root_object, "code": kinds[0]}])
        for am, (entry, res) in sorted(by_am.items()):
            contains_root = ctx.root_object in {str(a) for a in (entry.artefactos or [])}
            pre = self._pre.get(am)
            new_sig = assess.closure_signature(res)
            if contains_root:
                action = REGENERATED
            elif pre is None or not pre["has_env"]:
                action = LEFT                                            # nunca evaluado y ajeno al modelo publicado: no se inventa un assessment
            elif pre["state"] != assess.CURRENT:
                action = LEFT                                            # ya no estaba vigente antes de esta publicación: no se regresa ni se pisa
            elif pre["closure"] is None or new_sig is None:
                action = UNCERTAIN                                       # no se puede afirmar independencia: STALE explícito
            elif pre["closure"] != new_sig:
                action = REGENERATED                                     # cambió algo que este AM lee (una dependencia compartida)
            else:
                action = KEPT if pre["has_signature"] else REBASED       # independencia demostrada
            summary[action].append(am)
            try:
                if action == REGENERATED:
                    prep = assess.prepare_regeneration(entry, res)
                    if prep is not None:
                        prepared.append(prep)
                elif action == REBASED:
                    prepared.append(assess.prepare_rebase(res.client, am, copy.deepcopy(self._pre_envelopes.get(am) or assess._load_envelope(res.client, am)), new_sig))
            except assess.PrepareError as exc:
                failures.append({"kind": "assessment", "object_type": "", "technical_name": exc.am or am, "code": exc.code})
        if failures:
            raise StageError("ASSESSMENT_FAILED", failures)
        removed = self._obsolete(ctx, entries, resolutions)
        summary[REMOVED] = removed
        self._prepared, self._removed, self.summary = prepared, removed, summary
        self._entries_by_am = {am: pair[0] for am, pair in by_am.items()}

    def _obsolete(self, ctx, entries, resolutions) -> list[str]:
        """AM con sobre gobernado que YA NO existen como terminales en el pool del cliente. Solo si el pool se resolvió sano e inequívoco (nunca por una falla de carga)."""
        if not self._pre_envelopes:
            return []
        root = assess.pool_root()
        client = next((e.cliente for e in entries), ctx.client_dir)
        tag, payload = assess._select(root, client, "") if root is not None else ("POOL_UNAVAILABLE", None)
        if tag != "OK":
            return []
        idx = assess._index_for(root, payload[0], payload[1])
        if idx.error:
            return []
        live = set(idx.terminal_ams)
        return sorted(am for am in self._pre_envelopes if am not in live)

    def commit(self, ctx) -> dict:
        """Escribe en LOCAL lo ya preparado y validado (sobres nuevos, firmas agregadas, sobres obsoletos retirados)."""
        written: list[str] = []
        for prep in self._prepared:
            if assess.commit_prepared_local(prep) == assess.DURABLE_MEMORY:
                raise StageError("ASSESSMENT_WRITE_FAILED", [{"kind": "assessment", "object_type": "", "technical_name": prep.terminal_am, "code": "WRITE_FAILED"}])
            written.append(prep.terminal_am)
        for am in self._removed:
            assess.remove_governed_local(self._pre_envelopes[am].get("client") or ctx.client_dir, am)
        return {"assessment": {k: list(v) for k, v in self.summary.items()}}

    def verify(self, ctx) -> list[str]:
        problems: list[str] = []
        for prep in self._prepared:
            entry = self._entries_by_am.get(prep.terminal_am)
            if entry is None:
                continue
            st = assess.status_for_entry(entry)
            if st.state != assess.CURRENT:
                problems.append(f"assessment {prep.terminal_am}: {st.state}")
        for am in self._removed:
            path = assess.governed_path(self._pre_envelopes[am].get("client") or ctx.client_dir, am)
            if path is not None and path.exists():
                problems.append(f"assessment obsoleto {am}")
        return problems

    def after_rollback(self, ctx) -> None:
        """El disco volvió al estado previo: la memoria del servicio no puede conservar sobres de la unidad deshecha."""
        assess.reset_memory_cache()
        assess.invalidate_pool_index()

    def finished(self, ctx) -> None:
        """Versión activa y confirmada: se descartan las cachés para que los lectores (que la compuerta soltará a continuación) vean SOLO la nueva."""
        assess.reset_memory_cache()
        assess.invalidate_pool_index()
        try:
            from webapp import main as hub_main
            hub_main._invalidate_catalog_cache()
        except Exception:                               # noqa: BLE001 — la caché del Hub no es parte de la unidad
            log.warning("republication_stage: no se pudo invalidar la caché del Catálogo")
