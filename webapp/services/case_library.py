from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import TYPE_CHECKING

import yaml

import config
from core import remote_store

if TYPE_CHECKING:
    from webapp.services.wizard_service import WizardRun


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_outcome(leaf: dict) -> str:
    mode = ((leaf.get("post_actions") or {}).get("mode") or "none").strip()
    return {
        "resolved": "anduvo",
        "llm_value_add": "en curso",
        "needs_human_review": "inconcluso",
        "none": "anduvo",
    }.get(mode, "anduvo")


class CaseLibraryRepository:
    """Persistencia YAML compatible con la case_library heredada."""

    def __init__(self, root: Path | None = None) -> None:
        # `root=None` → se resuelve `config.CASE_LIBRARY_DIR` AL USAR (no al importar): el singleton de `copilot_runtime` ya no captura la ruta real antes de que
        # un entorno (p. ej. los tests) la redirija. Un `root` explícito se respeta tal cual.
        self._root = Path(root) if root is not None else None
        self._lock = RLock()

    @property
    def root(self) -> Path:
        return self._root if self._root is not None else Path(config.CASE_LIBRARY_DIR)

    @root.setter
    def root(self, value) -> None:
        self._root = Path(value)

    def _path(self, case_id: str) -> Path:
        return self.root / f"{case_id}.yaml"

    def _write(self, path: Path, data: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        remote_store.write_through(path, yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False))   # atómico + persistencia explícita al terminar el evento

    def create_for_run(self, run: WizardRun) -> str:
        if run.leaf is None:
            raise ValueError("El árbol todavía no finalizó")
        if run.case_id:
            return run.case_id

        leaf, tree = run.leaf, run.tree
        case_id = f"case_{datetime.now():%Y%m%d_%H%M%S}_{run.id[:8]}"
        nodes = tree.get("nodes") or {}
        route, alternatives = [], []
        for node_id, answer in run.history:
            node = nodes.get(node_id) or {}
            route.append({"node": node_id, "question": node.get("question", ""), "answer": answer})
            alternatives.extend(
                {"node": node_id, "option": option, "reason": None}
                for option in node.get("options") or []
                if str(option) != str(answer)
            )

        post_actions = leaf.get("post_actions") or None
        if post_actions:
            post_actions = {
                "mode": post_actions.get("mode"),
                "next_steps": post_actions.get("next_steps") or [],
                "reason": post_actions.get("reason"),
                "questions": post_actions.get("questions") or [],
            }
        case = {
            "case_id": case_id,
            "version": "1.0",
            "source": {
                "tree_id": tree.get("id"),
                "tree_title": tree.get("title", ""),
                "module": (tree.get("links") or {}).get("module_stem") or (tree.get("links") or {}).get("module", ""),
                "run_id": run.id,
                "timestamp": run.started_at,
            },
            "route": route,
            "leaf_id": run.current,
            "decision": leaf.get("decision"),
            "post_actions": post_actions,
            "leaf_notes": leaf.get("notes") or [],
            "uncharted": False,
            "gap_node": None,
            "context_description": "",
            "decision_taken": None,
            "outcome": _default_outcome(leaf),
            "lessons": [],
            "alternatives_discarded": alternatives,
            "confidence": None,
            "reviewed": False,
        }
        with self._lock:
            self._write(self._path(case_id), case)
        run.case_id = case_id
        return case_id

    def update_feedback(self, run: WizardRun, *, rating: str, outcome: str = "", context: str = "", detail: str = "", lesson: str = "") -> str:
        case_id = self.create_for_run(run)
        path = self._path(case_id)
        with self._lock:
            case = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            case["rating"] = rating
            if outcome.strip(): case["outcome"] = outcome.strip()
            if context.strip(): case["context_description"] = context.strip()
            if detail.strip(): case["decision_taken"] = detail.strip()
            if lesson.strip(): case["lessons"] = [lesson.strip()]
            case["reviewed"] = True
            case["feedback_at"] = _utc_now_iso()
            self._write(path, case)
        return case_id
