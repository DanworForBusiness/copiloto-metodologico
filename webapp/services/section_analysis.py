from __future__ import annotations

from dataclasses import dataclass
import html
import os
import re
import tomllib
from typing import Callable

import markdown
from openai import OpenAI

from config import BASE_DIR, DEFAULT_EXECUTION_MODE, MODEL, ia_enabled_for
from core.prompts import ALL_ACTIONS, build_system_prompt, build_topic_prompt, topic_instruction
from webapp.services.content_service import ModuleDocument


ACTION_DESCRIPTIONS = {
    "Salida completa (operativa)": "Síntesis ejecutiva orientada a decisiones para compartir con el cliente.",
    "Checklist ejecutable": "Lista de verificación accionable para usar durante el relevamiento técnico.",
    "Decisiones si/entonces": "Reglas condicionales claras extraídas del contenido metodológico.",
    "Anti-patterns y riesgos": "Prácticas que deben evitarse, sus consecuencias y riesgos concretos.",
    "Mirada Danwor": "Revisión de una decisión o entregable contra el estándar metodológico Danwor.",
}


class SectionAnalysisError(RuntimeError):
    pass


@dataclass(frozen=True)
class SectionAnalysisResult:
    section_title: str
    action: str
    answer: str
    html: str
    prompt: str


_STRUCTURED_HEADING_RE = re.compile(
    r"\s+(?=\*\*(?:¿?qué|lo que|decisiones|riesgos|checklist|mapa|anti-patterns|"
    r"postura|evidencia|señales|veredicto|recomendación)[^*\n]{0,100}\*\*)",
    re.I,
)
_STRUCTURED_HEADING_LINE_RE = re.compile(
    r"^\s*\*\*(?:¿?qué|lo que|decisiones|riesgos|checklist|mapa|anti-patterns|"
    r"postura|evidencia|señales|veredicto|recomendación)[^*\n]{0,100}\*\*\s*$",
    re.I,
)
_INLINE_BULLET_RE = re.compile(
    r"[ \t\u00a0]+(?:\\?[-–—−•▪◦*+])(?:[ \t\u00a0]+|(?=[A-ZÁÉÍÓÚÑ¿]))"
)
_LEADING_BULLET_RE = re.compile(r"^\s*(?:\\?[-–—−•▪◦*+])\s+")
# Heading numerado en negrita: "1. **ODBC:**" → se trata como heading propio
# (no como item de <ol>, que renumeraría y absorbería los bullets siguientes).
_NUMBERED_HEADING_LINE_RE = re.compile(r"^\s*(\d+)\.\s+\*\*(.+?)\*\*\s*:?\s*$")
_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")


def _normalize_bullets_on_line(line: str) -> list[str]:
    matches = list(_INLINE_BULLET_RE.finditer(line))
    if not matches:
        return [line]
    prefix = line[:matches[0].start()].rstrip()
    plain_prefix = re.sub(r"[*_`]", "", prefix).rstrip()
    if len(matches) < 2 and not plain_prefix.endswith(":") and not _LEADING_BULLET_RE.match(line):
        return [line]

    items = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        item = line[match.end():end].strip()
        if item:
            items.append(f"- {item}")
    if not items:
        return [line]
    return [prefix, "", *items] if prefix else items


def normalize_llm_markdown(answer: str) -> str:
    """Repair compact headings and bullet chains commonly returned by chat models."""
    text = (answer or "").replace("\r\n", "\n").translate(
        {ord("\u00a0"): " ", ord("\u202f"): " ", ord("\u2007"): " ", ord("\u200b"): ""}
    )
    output = []
    in_fence = False
    in_table = False
    for original_line in text.splitlines():
        if original_line.lstrip().startswith("```"):
            in_fence = not in_fence
            output.append(original_line)
            continue
        if in_fence:
            output.append(original_line)
            continue

        structured_lines = _STRUCTURED_HEADING_RE.sub("\n\n", original_line).splitlines()
        for line in structured_lines:
            if _TABLE_ROW_RE.match(line):
                if not in_table and output and output[-1].strip():
                    output.append("")
                output.append(line.strip())
                in_table = True
                continue
            if in_table:
                if line.strip() and output and output[-1].strip():
                    output.append("")
                in_table = False

            if _STRUCTURED_HEADING_LINE_RE.match(line):
                if output and output[-1].strip():
                    output.append("")
                output.extend([line.strip(), ""])
                continue

            numbered = _NUMBERED_HEADING_LINE_RE.match(line)
            if numbered:
                if output and output[-1].strip():
                    output.append("")
                # Conserva el número como texto literal dentro del negrita para que
                # NO arranque un <ol> (que renumeraría y absorbería los bullets).
                output.extend([f"**{numbered.group(1)}. {numbered.group(2)}**", ""])
                continue

            normalized = _normalize_bullets_on_line(line)
            is_bullet = bool(normalized and _LEADING_BULLET_RE.match(normalized[0]))
            if is_bullet and output and output[-1].strip() and not _LEADING_BULLET_RE.match(output[-1]):
                output.append("")
            output.extend(normalized)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()


def render_llm_html(answer: str) -> str:
    normalized = normalize_llm_markdown(answer)
    return markdown.markdown(
        html.escape(normalized),
        extensions=["tables", "fenced_code", "sane_lists"],
    )


def _legacy_api_key() -> str:
    path = BASE_DIR / ".streamlit" / "secrets.toml"
    if not path.exists():
        return ""
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    return str(data.get("OPENAI_API_KEY") or "").strip()


class SectionAnalysisService:
    def __init__(
        self,
        client_factory: Callable[..., OpenAI] = OpenAI,
        execution_mode: str | None = None,
    ) -> None:
        self.client_factory = client_factory
        self.execution_mode = execution_mode or DEFAULT_EXECUTION_MODE

    def analyze(
        self,
        document: ModuleDocument,
        section_slug: str,
        action: str,
        pack_name: str,
    ) -> SectionAnalysisResult:
        if action not in ALL_ACTIONS:
            raise SectionAnalysisError("La acción seleccionada no es válida.")
        if not ia_enabled_for(document.summary.stem):
            raise SectionAnalysisError("El análisis con IA no está habilitado para este módulo.")
        section = next((item for item in document.sections if item.slug == section_slug), None)
        if section is None:
            raise SectionAnalysisError("No se encontró la sección seleccionada.")

        prompt = build_topic_prompt(
            document.source,
            section.title,
            section.markdown,
            action,
            topic_instruction(action),
            module=document.summary.stem,
            pack_name=pack_name,
        )
        if self.execution_mode.lower().startswith("simul"):
            answer = (
                "🧪 **Simulación activa** — no se consumieron créditos.\n\n"
                f"La acción **{action}** quedó preparada para **{section.title}**."
            )
        else:
            api_key = (os.getenv("OPENAI_API_KEY") or "").strip() or _legacy_api_key()
            if not api_key:
                raise SectionAnalysisError(
                    "Falta configurar OPENAI_API_KEY para ejecutar este análisis."
                )
            system_prompt = build_system_prompt(
                document.summary.stem,
                pack_name=pack_name,
                user_context={},
            )
            messages = [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": (
                        "CONTEXTO (Marco Danwor)\n"
                        f"{'─' * 40}\n{document.source}\n\n"
                        "SOLICITUD\n"
                        f"{'─' * 40}\n{prompt}"
                    ),
                },
            ]
            try:
                client = self.client_factory(api_key=api_key)
                response = client.chat.completions.create(
                    model=MODEL,
                    messages=messages,
                    temperature=0.1,
                    max_tokens=1200,
                )
                answer = str(response.choices[0].message.content or "").strip()
            except Exception as exc:
                raise SectionAnalysisError(
                    "No pudimos completar el análisis con el modelo. Intentá nuevamente."
                ) from exc
            if not answer:
                raise SectionAnalysisError("El modelo no devolvió contenido para esta acción.")

        rendered = render_llm_html(answer)
        return SectionAnalysisResult(section.title, action, answer, rendered, prompt)
