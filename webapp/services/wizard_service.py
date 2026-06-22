from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from uuid import uuid4
import yaml

from webapp.services.case_library import CaseLibraryRepository

from core.content_registry import iter_pack_files


ROUTING_ONLY_TREE_IDS = frozenset({"m03_clean_core_guardrails"})


@dataclass
class WizardRun:
    id: str
    tree: dict
    current: str
    history: list[tuple[str, str]] = field(default_factory=list)
    error: str = ""
    feedback_rating: str = ""
    feedback_outcome: str = ""
    feedback_context: str = ""
    feedback_detail: str = ""
    feedback_lesson: str = ""
    case_id: str = ""
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def node(self): return self.tree.get("nodes", {}).get(self.current)
    @property
    def leaf(self): return self.tree.get("leaves", {}).get(self.current)
    @property
    def title(self): return str(self.tree.get("title") or "Asistente de decisión")
    @property
    def progress(self): return 100 if self.leaf else min(90, 18 + len(self.history) * 18)


class WizardStore:
    """Thread-safe beta store; replaceable by Redis behind the same contract."""
    def __init__(self, case_repository: CaseLibraryRepository | None = None):
        self._runs: dict[str, WizardRun] = {}
        self._lock = RLock()
        self._case_repository = case_repository

    @staticmethod
    def trees_for_module(
        module_stem: str,
        pack_name: str = "datasphere_mvp",
        *,
        include_routing_only: bool = False,
    ) -> tuple[dict, ...]:
        trees = []
        for path in iter_pack_files("trees", "*.yaml", pack_name):
            tree = yaml.safe_load(path.read_text(encoding="utf-8", errors="ignore")) or {}
            linked = str((tree.get("links") or {}).get("module") or "")
            # Match EXACTO por stem. El fallback por prefijo numérico hacía que un
            # módulo BDC (01_Estrategia…) heredara el árbol Datasphere (01_Arquitectura…).
            # Todos los árboles enlazan por stem completo, así que el exacto alcanza.
            if linked == module_stem:
                if not include_routing_only and tree.get("id") in ROUTING_ONLY_TREE_IDS:
                    continue
                trees.append(tree)
        return tuple(trees)

    def start(self, tree: dict) -> WizardRun:
        run = WizardRun(uuid4().hex, tree, str(tree["start"]))
        with self._lock: self._runs[run.id] = run
        return run

    def get(self, run_id: str) -> WizardRun:
        if run_id not in self._runs: raise KeyError("La sesión del árbol expiró.")
        return self._runs[run_id]

    def answer(self, run_id: str, answer: str) -> WizardRun:
        with self._lock:
            run, run_error = self.get(run_id), ""
            run.error = run_error
            node = run.node
            options = [str(option) for option in (node or {}).get("options") or ()]
            if answer not in options: run.error = "Seleccioná una de las opciones disponibles."; return run
            target = (node.get("next") or {}).get(answer)
            if not target: run.error = "El YAML no define una transición para esa respuesta."; return run
            if target == run.current: run.error = "El YAML contiene un ciclo sobre el mismo nodo; la transición fue bloqueada."; return run
            run.history.append((run.current, answer)); run.current = str(target)
            if run.leaf is not None and self._case_repository is not None:
                self._case_repository.create_for_run(run)
            return run

    def back(self, run_id: str) -> WizardRun:
        with self._lock:
            run = self.get(run_id); run.error = ""
            if run.history: run.current = run.history.pop()[0]
            return run

    def restart(self, run_id: str) -> WizardRun:
        with self._lock:
            run = self.get(run_id); run.current = str(run.tree["start"]); run.history.clear(); run.error = ""
            run.feedback_rating = run.feedback_outcome = run.feedback_context = run.feedback_detail = run.feedback_lesson = ""
            run.case_id = ""
            run.started_at = datetime.now(timezone.utc).isoformat()
            return run

    def submit_feedback(self, run_id: str, *, rating: str, outcome: str = "",
                        context: str = "", detail: str = "", lesson: str = "") -> WizardRun:
        """Registra el feedback de una decisión. El 👎 exige lección clave (como el
        formulario del Streamlit original). En la beta persiste en memoria;
        reemplazable por case_library/DB sin cambiar las rutas."""
        if rating not in {"up", "down"}:
            raise ValueError("Valor de feedback inválido")
        with self._lock:
            run = self.get(run_id)
            if run.leaf is None:
                raise ValueError("El árbol todavía no finalizó")
            if rating == "down" and not lesson.strip():
                raise ValueError("La lección clave es obligatoria para el feedback negativo.")
            if self._case_repository is not None:
                self._case_repository.update_feedback(
                    run,
                    rating=rating,
                    outcome=outcome,
                    context=context,
                    detail=detail,
                    lesson=lesson,
                )
            run.feedback_rating = rating
            run.feedback_outcome = outcome.strip()
            run.feedback_context = context.strip()
            run.feedback_detail = detail.strip()
            run.feedback_lesson = lesson.strip()
            return run

    def rate(self, run_id: str, rating: str) -> WizardRun:
        """Compatibilidad con la API breve de la primera vertical."""
        return self.submit_feedback(run_id, rating=rating)
