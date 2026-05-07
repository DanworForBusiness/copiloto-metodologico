"""
core/assessment_engine.py
=========================
Motor del simulador de certificación.
Responsabilidades:
  - Cargar y parsear exam YAMLs (bdc_exam.yaml y futuros)
  - Keyword matching (any / all / min) — mismo algoritmo que el .tsx original
  - Scoring automático y ponderado por categoría
  - Evaluación LLM de respuesta abierta (opcional, solo en API Real)
  - Estado de una sesión de examen (sin dependencia de Streamlit)
"""

from __future__ import annotations

import random
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


# =========================================================
# Paths
# =========================================================
BASE_DIR      = Path(__file__).parent.parent
ASSESSMENT_DIR = BASE_DIR / "assessment"


# =========================================================
# Data classes
# =========================================================

@dataclass
class ChecklistItem:
    id:        str
    label_en:  str
    label_es:  str
    keywords:  list[str]
    match_mode: str = "any"   # any | all | min
    min_hits:  int  = 1


@dataclass
class Question:
    id:         str
    category:   str
    prompt_en:  str
    prompt_es:  str
    reference_en: str
    reference_es: str
    checklist:  list[ChecklistItem] = field(default_factory=list)


@dataclass
class ExamMeta:
    title_en:    str
    title_es:    str
    subtitle_en: str
    subtitle_es: str
    pass_mark:   float
    borderline_low: float
    timer_default_sec: int
    checklist_delay_sec: int
    persona_en:  str
    persona_es:  str
    weights:     dict[str, float]  # category → weight


@dataclass
class QuestionScore:
    concept:      int = 0
    architecture: int = 0
    procedure:    int = 0
    manual:       bool = False   # True si el usuario sobreescribió el auto-score

    @property
    def avg(self) -> float:
        return (self.concept + self.architecture + self.procedure) / 3


@dataclass
class QuestionState:
    answer:       str = ""
    timed_out:    bool = False
    show_ref:     bool = False
    score:        QuestionScore = field(default_factory=QuestionScore)
    llm_feedback: str | None = None   # feedback LLM si está disponible


# =========================================================
# Loader
# =========================================================

def load_exam(filename: str = "bdc_exam.yaml") -> tuple[ExamMeta, list[Question]]:
    path = ASSESSMENT_DIR / filename
    raw  = yaml.safe_load(path.read_text(encoding="utf-8"))

    m = raw["meta"]
    meta = ExamMeta(
        title_en    = m["title"]["en"],
        title_es    = m["title"]["es"],
        subtitle_en = m["subtitle"]["en"],
        subtitle_es = m["subtitle"]["es"],
        pass_mark   = float(m.get("pass_mark", 0.60)),
        borderline_low = float(m.get("borderline_low", 0.55)),
        timer_default_sec = int(m.get("timer_default_sec", 90)),
        checklist_delay_sec = int(m.get("checklist_delay_sec", 30)),
        persona_en  = m.get("persona", {}).get("en", ""),
        persona_es  = m.get("persona", {}).get("es", ""),
        weights     = {k: float(v) for k, v in raw.get("weights", {}).items()},
    )

    questions = []
    for q in raw["questions"]:
        checklist = []
        for it in q.get("checklist", []):
            checklist.append(ChecklistItem(
                id         = it["id"],
                label_en   = it["label"]["en"],
                label_es   = it["label"]["es"],
                keywords   = [kw.lower() for kw in it["keywords"]],
                match_mode = it.get("matchMode", "any"),
                min_hits   = int(it.get("minHits", 1)),
            ))
        questions.append(Question(
            id           = q["id"],
            category     = q["category"],
            prompt_en    = q["prompt"]["en"],
            prompt_es    = q["prompt"]["es"],
            reference_en = q["reference"]["en"],
            reference_es = q["reference"]["es"],
            checklist    = checklist,
        ))

    return meta, questions


# =========================================================
# Keyword matching
# =========================================================

def _normalize(text: str) -> str:
    """Lowercase + strip accents para matching robusto."""
    nfkd = unicodedata.normalize("NFKD", text.lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def check_item(item: ChecklistItem, answer: str) -> bool:
    """
    Evalúa si un ChecklistItem se cumple en la respuesta del usuario.
    Replica exactamente el matchMode del .tsx original.
    """
    norm = _normalize(answer)
    hits = sum(1 for kw in item.keywords if kw in norm)

    if item.match_mode == "any":
        return hits >= 1
    if item.match_mode == "all":
        return hits == len(item.keywords)
    if item.match_mode == "min":
        return hits >= item.min_hits
    return False


def auto_checklist(question: Question, answer: str) -> dict[str, bool]:
    """Devuelve {item_id: bool} para todos los items del checklist."""
    return {item.id: check_item(item, answer) for item in question.checklist}


def coverage(question: Question, answer: str) -> tuple[int, int, float]:
    """Devuelve (matched, total, ratio)."""
    results = auto_checklist(question, answer)
    matched = sum(1 for v in results.values() if v)
    total   = len(results)
    ratio   = matched / total if total else 0.0
    return matched, total, ratio


def suggested_score(question: Question, answer: str) -> int:
    """Score sugerido 0-5 basado en cobertura del checklist."""
    _, _, ratio = coverage(question, answer)
    return round(ratio * 5)


# =========================================================
# Session scoring
# =========================================================

def weighted_score(
    questions: list[Question],
    states:    dict[str, QuestionState],
    weights:   dict[str, float],
) -> dict[str, Any]:
    """
    Calcula el score ponderado final de una sesión.
    Devuelve dict con score total, por categoría, y veredicto.
    """
    # Agrupar scores por categoría
    cat_scores: dict[str, list[float]] = {}
    for q in questions:
        state = states.get(q.id)
        if not state or not state.answer.strip():
            continue
        avg = state.score.avg
        cat_scores.setdefault(q.category, []).append(avg / 5.0)  # normalizar a 0-1

    # Promediar por categoría
    cat_avgs: dict[str, float] = {
        cat: sum(scores) / len(scores)
        for cat, scores in cat_scores.items()
        if scores
    }

    # Ponderar con weights del YAML
    total_weight = 0.0
    weighted_sum = 0.0
    for cat, avg in cat_avgs.items():
        w = weights.get(cat, 0.0)
        weighted_sum += avg * w
        total_weight  += w

    final = weighted_sum / total_weight if total_weight > 0 else 0.0

    return {
        "final":       round(final, 3),
        "final_pct":   round(final * 100, 1),
        "by_category": {k: round(v * 100, 1) for k, v in cat_avgs.items()},
        "answered":    len([q for q in questions if states.get(q.id, QuestionState()).answer.strip()]),
        "total":       len(questions),
        "timed_out":   len([q for q in questions if states.get(q.id, QuestionState()).timed_out]),
    }


# =========================================================
# Shuffle determinístico (mismo algoritmo que el .tsx)
# =========================================================

def shuffle_questions(questions: list[Question], seed: int) -> list[Question]:
    rng = random.Random(seed)
    result = list(questions)
    rng.shuffle(result)
    return result


# =========================================================
# LLM evaluation (solo en API Real)
# =========================================================

def evaluate_with_llm(
    question: Question,
    answer:   str,
    lang:     str = "es",
    model:    str = "gpt-4o-mini",
) -> str | None:
    """
    Evalúa la respuesta del usuario con LLM y devuelve feedback textual.
    Retorna None si no hay API key o si hay error.
    """
    import os
    try:
        from openai import OpenAI
    except ImportError:
        return None

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        try:
            import streamlit as st
            api_key = st.secrets.get("OPENAI_API_KEY")
        except Exception:
            pass
    if not api_key:
        return None

    prompt_text = question.prompt_es if lang == "es" else question.prompt_en
    reference   = question.reference_es if lang == "es" else question.reference_en

    if lang == "es":
        system = (
            "Sos un evaluador técnico de certificaciones SAP. "
            "Tu tarea es dar feedback breve y constructivo (máximo 4 oraciones) sobre la respuesta del candidato. "
            "Señalá qué estuvo bien, qué faltó y cómo mejorarla. Sé directo y técnico. No repitas la pregunta."
        )
        user = (
            f"Pregunta: {prompt_text}\n\n"
            f"Respuesta del candidato: {answer}\n\n"
            f"Respuesta de referencia: {reference}\n\n"
            "Feedback:"
        )
    else:
        system = (
            "You are a technical SAP certification evaluator. "
            "Give brief, constructive feedback (max 4 sentences) on the candidate's answer. "
            "Note what was good, what was missing, and how to improve. Be direct and technical. Don't repeat the question."
        )
        user = (
            f"Question: {prompt_text}\n\n"
            f"Candidate answer: {answer}\n\n"
            f"Reference answer: {reference}\n\n"
            "Feedback:"
        )

    try:
        client   = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user},
            ],
            max_tokens=200,
            temperature=0.3,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return None