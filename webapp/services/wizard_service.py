from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from threading import RLock
from uuid import uuid4
import yaml

from webapp.services.case_library import CaseLibraryRepository

from core.content_registry import iter_pack_files

# yaml.safe_load() usa siempre el SafeLoader puro-Python, incluso con libyaml
# instalado. CSafeLoader es igual de seguro y notablemente más rápido en
# árboles grandes — ver mismo patrón en tools/catalog/catalog_service.py.
try:
    _YAML_LOADER = yaml.CSafeLoader
except AttributeError:  # libyaml no instalado en este entorno
    _YAML_LOADER = yaml.SafeLoader


def _yaml_load(text: str):
    return yaml.load(text, Loader=_YAML_LOADER)


ROUTING_ONLY_TREE_IDS = frozenset({"m03_clean_core_guardrails", "framework_assessment"})


@lru_cache(maxsize=8)
def _all_trees(pack_name: str) -> tuple[dict, ...]:
    """Todos los árboles de trees/*.yaml de un pack, parseados una sola vez.

    Cacheado: `trees_for_module`/`tree_by_id` releían y reparseaban TODO el
    directorio en cada llamada — una por cada módulo abierto — aunque ese
    módulo solo matcheara 0-2 árboles. Mismo criterio que load_module: el
    contenido es estático mientras el proceso corre."""
    return tuple(
        _yaml_load(path.read_text(encoding="utf-8", errors="ignore")) or {}
        for path in iter_pack_files("trees", "*.yaml", pack_name)
    )


def _format_score(value: float):
    return int(value) if float(value).is_integer() else round(value, 1)


def _range_matches(value: float, expression: str) -> bool:
    expr = str(expression or "").strip().replace(" ", "")
    if not expr:
        return False
    try:
        if expr.startswith("<"):
            return value < float(expr[1:])
        if expr.startswith(">="):
            return value >= float(expr[2:])
        if expr.startswith(">"):
            return value > float(expr[1:])
        if "-" in expr:
            low, high = expr.split("-", 1)
            return float(low) <= value <= float(high)
        return value == float(expr)
    except ValueError:
        return False


def _assessment_maturity(tree: dict, normalized_score: float) -> str:
    scoring = tree.get("scoring") or {}
    for item in scoring.get("interpretation") or ():
        if _range_matches(normalized_score, str(item.get("range") or "")):
            return str(item.get("maturity_level") or "")
    return "Resultado calculado"


def _tree_id_aliases(value: str) -> set[str]:
    raw = str(value or "").strip()
    aliases = {raw} if raw else set()
    if len(raw) > 4 and raw[0] == "m" and raw[1:3].isdigit() and raw[3] == "_":
        aliases.add(raw[1:])
    if len(raw) > 3 and raw[:2].isdigit() and raw[2] == "_":
        aliases.add(f"m{raw}")
    return aliases


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
    pack_name: str = "datasphere_core"
    orchestration_sequence: list[str] = field(default_factory=list)
    orchestration_index: int = 0
    orchestration_scores: list[dict] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    @property
    def node(self): return self.tree.get("nodes", {}).get(self.current)
    @property
    def leaf(self): return self.tree.get("leaves", {}).get(self.current)
    @property
    def title(self): return str(self.tree.get("title") or "Asistente de decisión")
    @property
    def display_title(self):
        title = self.title
        if self.is_assessment:
            for prefix in ("Diagn\u00f3stico \u00b7 ", "Diagn\u00f3stico del ", "Diagn\u00f3stico de "):
                if title.startswith(prefix):
                    return title[len(prefix):]
        return title

    @property
    def module_stem(self) -> str:
        links = self.tree.get("links") or {}
        linked = str(links.get("module") or "").strip()
        if linked:
            return linked
        module = str(self.tree.get("module") or "").strip()
        if module[:2].isdigit() and " " in module:
            return module.rsplit(" ", 1)[-1].strip()
        return module

    @property
    def progress(self): return 100 if self.leaf else min(90, 18 + len(self.history) * 18)
    @property
    def is_assessment(self) -> bool:
        return str(self.tree.get("kind") or "") == "assessment"

    @property
    def is_orchestrated(self) -> bool:
        return bool(self.orchestration_sequence)

    @property
    def orchestration_complete(self) -> bool:
        return self.is_orchestrated and self.leaf is not None and self.orchestration_index >= len(self.orchestration_sequence) - 1

    @property
    def assessment_score(self) -> dict | None:
        if not self.is_assessment or self.leaf is None:
            return None
        nodes = self.tree.get("nodes") or {}
        total_score = 0.0
        max_score = 0.0
        scored_answers = 0
        skipped_answers = 0
        details = []
        for node_id, answer in self.history:
            node = nodes.get(node_id) or {}
            scores = node.get("scores") or {}
            if answer not in scores:
                continue
            numeric_values = [value for value in scores.values() if isinstance(value, (int, float))]
            node_max = float(max(numeric_values)) if numeric_values else 0.0
            value = scores.get(answer)
            details.append({
                "question": str(node.get("question") or node_id),
                "dimension": str(node.get("dimension") or ""),
                "answer": answer,
                "score": value,
                "max_score": node_max,
            })
            if isinstance(value, (int, float)):
                total_score += float(value)
                max_score += node_max
                scored_answers += 1
            else:
                skipped_answers += 1
        if max_score <= 0:
            return {
                "score": 0,
                "max_score": 0,
                "percent": None,
                "normalized": None,
                "maturity_level": "Sin scoring suficiente",
                "scored_answers": scored_answers,
                "skipped_answers": skipped_answers,
                "total_answers": len(self.history),
                "details": details,
            }
        normalized = round((total_score / max_score) * 10, 1)
        return {
            "score": _format_score(total_score),
            "max_score": _format_score(max_score),
            "percent": round((total_score / max_score) * 100),
            "normalized": normalized,
            "maturity_level": _assessment_maturity(self.tree, normalized),
            "scored_answers": scored_answers,
            "skipped_answers": skipped_answers,
            "total_answers": len(self.history),
            "details": details,
        }

    @property
    def orchestration_summary(self) -> dict | None:
        if not self.orchestration_complete:
            return None
        current = self.assessment_score
        items = list(self.orchestration_scores)
        if current:
            item = dict(current)
            item["tree_id"] = str(self.tree.get("id") or "")
            item["title"] = self.display_title
            items.append(item)
        if not items:
            return None
        total_score = sum(float(item.get("score") or 0) for item in items)
        max_score = sum(float(item.get("max_score") or 0) for item in items)
        scored_answers = sum(int(item.get("scored_answers") or 0) for item in items)
        total_answers = sum(int(item.get("total_answers") or 0) for item in items)
        if max_score <= 0:
            percent = None
            normalized = None
        else:
            percent = round((total_score / max_score) * 100)
            normalized = round((total_score / max_score) * 10, 1)
        return {
            "score": _format_score(total_score),
            "max_score": _format_score(max_score),
            "percent": percent,
            "normalized": normalized,
            "scored_answers": scored_answers,
            "total_answers": total_answers,
            "modules": items,
            "module_count": len(items),
        }


class WizardStore:
    """Thread-safe beta store; replaceable by Redis behind the same contract."""
    def __init__(self, case_repository: CaseLibraryRepository | None = None):
        self._runs: dict[str, WizardRun] = {}
        self._lock = RLock()
        self._case_repository = case_repository

    @staticmethod
    def trees_for_module(
        module_stem: str,
        pack_name: str = "datasphere_core",
        *,
        include_routing_only: bool = False,
        include_assessment: bool = False,
    ) -> tuple[dict, ...]:
        trees = []
        for tree in _all_trees(pack_name):
            links = tree.get("links") or {}
            linked = str(links.get("module") or "")
            linked_modules = {str(item) for item in (links.get("modules") or [])}
            module_value = str(tree.get("module") or "").strip()
            module_field_stem = module_value.rsplit(" ", 1)[-1].strip() if module_value[:2].isdigit() and " " in module_value else module_value
            # Match EXACTO por stem. `links.modules` permite exponer un árbol
            # transversal (por ejemplo un assessment orquestador) en varios módulos
            # sin volver al fallback por prefijo numérico que mezclaba content packs.
            if linked == module_stem or module_stem in linked_modules or module_field_stem == module_stem or module_value.endswith(module_stem):
                if not include_routing_only and tree.get("id") in ROUTING_ONLY_TREE_IDS:
                    continue
                # Los árboles de diagnóstico (kind=assessment) son una feature
                # aparte (panel "Herramientas de diagnóstico"); por defecto este
                # listado devuelve solo árboles de DECISIÓN. Los consumidores que
                # arman el panel de diagnóstico opt-in con include_assessment.
                if not include_assessment and str(tree.get("kind") or "") == "assessment":
                    continue
                trees.append(tree)
        return tuple(trees)

    @staticmethod
    def tree_by_id(tree_id: str, pack_name: str = "datasphere_core", *, include_routing_only: bool = False) -> dict | None:
        for tree in _all_trees(pack_name):
            if not (_tree_id_aliases(str(tree.get("id") or "")) & _tree_id_aliases(str(tree_id or ""))):
                continue
            if not include_routing_only and tree.get("id") in ROUTING_ONLY_TREE_IDS:
                return None
            return tree
        return None

    def start(
        self,
        tree: dict,
        pack_name: str = "datasphere_core",
        *,
        orchestration_sequence: list[str] | None = None,
        orchestration_index: int = 0,
        orchestration_scores: list[dict] | None = None,
    ) -> WizardRun:
        run = WizardRun(
            uuid4().hex,
            tree,
            str(tree["start"]),
            pack_name=pack_name,
            orchestration_sequence=list(orchestration_sequence or []),
            orchestration_index=orchestration_index,
            orchestration_scores=list(orchestration_scores or []),
        )
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
