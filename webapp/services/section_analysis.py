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
from core.prompts import (
    ALL_ACTIONS,
    MODULE_ACTIONS,
    QUICK_ACTIONS,
    build_system_prompt,
    build_topic_prompt,
    topic_instruction,
)

# Slug especial: el target no es una sección sino la cabecera del módulo.
MODULE_TARGET_SLUG = "__module__"
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
_INLINE_NUMBERED_ITEM_RE = re.compile(r"(?<![\w/])([1-9]\d*)[\.)]\s+(?=\S)")
_BOLD_HEADING_BEFORE_NUMBER_RE = re.compile(r"(\*\*[^*\n]{3,120}\*\*)\s*:?\s+(?=[1-9]\d*[\.)]\s+)")
_PLAIN_ACTION_HEADING_BEFORE_NUMBER_RE = re.compile(
    r"(?<!\S)("
    # Tolerante a acentos y a texto que haya llegado con mojibake desde alguna capa.
    r"Gu\S?a operativa del m\S?dulo|Salida operativa|"
    r"Checklist de implementaci\S?n|Checklist ejecutable|"
    r"Errores frecuentes|"
    r"Ejemplos pr\S?cticos|"
    r"Decisiones si/entonces|Mapa de decisiones|"
    r"Anti-patterns y riesgos|"
    r"Mirada Danwor"
    r")\s*:?\s+(?=[1-9]\d*[\.)]\s+)",
    re.I,
)
_PLAIN_ACTION_HEADING_PREFIX_LINE_RE = re.compile(
    r"^\s*(Gu\S?a operativa del m\S?dulo|Salida operativa|Checklist de implementaci\S?n|Checklist ejecutable|"
    r"Errores frecuentes|Ejemplos pr\S?cticos|Decisiones si/entonces|Mapa de decisiones|"
    r"Anti-patterns y riesgos|Mirada Danwor)\b(?:\s+[—-].+)?\s*:?\s*$",
    re.I,
)
_PLAIN_ACTION_HEADING_LINE_RE = re.compile(
    r"^\s*(Gu\S?a operativa del m\S?dulo|Salida operativa|Checklist de implementaci\S?n|Checklist ejecutable|"
    r"Errores frecuentes|Ejemplos pr\S?cticos|Decisiones si/entonces|Mapa de decisiones|"
    r"Anti-patterns y riesgos|Mirada Danwor)\s*:?\s*$",
    re.I,
)
_TRAILING_ACTION_HEADING_RE = re.compile(
    r"^(.+?)\s*:?[ 	]*(?:\*\*)?(Gu\S?a operativa del m\S?dulo|Salida operativa|Checklist de implementaci\S?n|Checklist ejecutable|"
    r"Errores frecuentes|Ejemplos pr\S?cticos|Decisiones si/entonces|Mapa de decisiones|"
    r"Anti-patterns y riesgos|Mirada Danwor)(?:\*\*)?\s*:?\s*$",
    re.I,
)
# Heading numerado en negrita: "1. **ODBC:**" → se trata como heading propio
# (no como item de <ol>, que renumeraría y absorbería los bullets siguientes).
_NUMBERED_HEADING_LINE_RE = re.compile(r"^\s*(\d+)\.\s+\*\*(.+?)\*\*\s*:?\s*$")
_DANGLING_BOLD_BEFORE_ACTION_TITLE_RE = re.compile(
    r"[ \t]*\*\*[ \t]*(?:\n[ \t]*)+(?=\*\*(?:Gu\S?a operativa del m\S?dulo|Salida operativa|Checklist de implementaci\S?n|Checklist ejecutable|"
    r"Errores frecuentes|Ejemplos pr\S?cticos|Decisiones si/entonces|Mapa de decisiones|"
    r"Anti-patterns y riesgos|Mirada Danwor)\*\*)",
    re.I,
)
_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")
_CHECKLIST_TITLE_LINE_RE = re.compile(r"^\s*(Checklist[^:\n]{8,160})\s*:?\s*$", re.I)
_CHECKLIST_FIELD_LINE_RE = re.compile(
    r"^\s*(?:\?[-–—−•▪◦*+]\s+)?(?:\*\*)?(Qu\S? validar|Con qui\S?n|"
    r"Qu\S? pasa si no est\S? resuelto|Justificaci\S?n|Por qu\S? est\S? mal|Riesgo concreto)(?:\*\*)?\s*:\s*(?:\*\*)?(.+?)\s*$",
    re.I,
)


def _split_inline_decision_rules(line: str) -> list[str]:
    """Split compact SI/ENTONCES decision maps into readable list items."""
    if "ENTONCES" not in line.upper():
        return [line]
    pattern = re.compile(r"(?=(?:\*\*)?SI(?:\*\*)?\s+)", re.I)
    parts = [part.strip() for part in pattern.split(line) if part.strip()]
    rules = [part for part in parts if re.match(r"^(?:\*\*)?SI(?:\*\*)?\s+", part, re.I)]
    prefix_parts = [part for part in parts if part not in rules]
    if len(rules) < 2:
        return [line]
    output: list[str] = []
    prefix = " ".join(prefix_parts).strip()
    if prefix:
        output.extend([prefix, ""])
    for idx, rule in enumerate(rules, start=1):
        output.append(f"{idx}. {rule}")
    return output

def _split_inline_numbered_items(line: str) -> list[str]:
    """Turn compact ordered lists into Markdown list lines.

    Models often return `Intro **Heading** 1. First 2. Second` as a
    single paragraph. Markdown cannot recover hierarchy from that, so we
    split only explicit integer markers followed by whitespace. Decimals
    such as `7.56` and case numbers with slashes are ignored by the regex.
    """
    line = _BOLD_HEADING_BEFORE_NUMBER_RE.sub(r"\n\n\1\n\n", line)
    line = _PLAIN_ACTION_HEADING_BEFORE_NUMBER_RE.sub(r"\n\n**\1**\n\n", line)
    matches = list(_INLINE_NUMBERED_ITEM_RE.finditer(line))
    if not matches:
        return [line]

    prefix = line[:matches[0].start()].rstrip()
    items: list[str] = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        body = line[match.end():end].strip()
        if body:
            items.append(f"{match.group(1)}. {body}")
    if not items:
        return [line]
    if not prefix:
        return items
    prefix_lines = prefix.splitlines()
    while prefix_lines and not prefix_lines[-1].strip():
        prefix_lines.pop()
    return [*prefix_lines, "", *items]


def _normalize_bullets_on_line(line: str) -> list[str]:
    matches = list(_INLINE_BULLET_RE.finditer(line))
    if not matches:
        return [line]
    prefix = line[:matches[0].start()].rstrip()
    plain_prefix = re.sub(r"[*_`]", "", prefix).rstrip()
    if len(matches) < 2 and not plain_prefix.endswith(":") and not _LEADING_BULLET_RE.match(line):
        return [line]

    is_numbered_prefix = bool(re.match(r"^\s*[1-9]\d*\.\s+", prefix))
    items = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
        item = line[match.end():end].strip()
        if item:
            indent = "    " if is_numbered_prefix else ""
            items.append(f"{indent}- {item}")
    if not items:
        return [line]
    return ([prefix, *items] if is_numbered_prefix else [prefix, "", *items]) if prefix else items


def normalize_llm_markdown(answer: str) -> str:
    """Repair compact headings and bullet chains commonly returned by chat models."""
    text = (answer or "").replace("\r\n", "\n").translate(
        {ord("\u00a0"): " ", ord("\u202f"): " ", ord("\u2007"): " ", ord("\u200b"): ""}
    )
    output = []
    in_fence = False
    in_table = False
    pending_ordered_children = False
    last_ordered_context = False
    for original_line in text.splitlines():
        if original_line.lstrip().startswith("```"):
            in_fence = not in_fence
            output.append(original_line)
            continue
        if in_fence:
            output.append(original_line)
            continue

        # No apliques el splitter genérico de headings sobre bullets: en
        # checklist rompería líneas como `- **Qué pasa...` antes de que
        # podamos reconocerlas como campos hijos del ítem numerado.
        structured_source = original_line
        if not _LEADING_BULLET_RE.match(original_line):
            structured_source = _STRUCTURED_HEADING_RE.sub("\n\n", original_line)
        structured_lines = structured_source.splitlines()
        for line in structured_lines:
            # Algunos modelos insertan un guion solo entre el ítem numerado y
            # sus campos ("Qué validar", "Con quién", etc.). No es contenido:
            # si lo dejamos pasar rompe la jerarquía del checklist.
            if re.match(r"^\s*(?:\\?[-–—−•▪◦*+])\s*$", line):
                continue

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

            trailing_action_heading = _TRAILING_ACTION_HEADING_RE.match(line)
            if trailing_action_heading and not _PLAIN_ACTION_HEADING_LINE_RE.match(line):
                prefix = trailing_action_heading.group(1).strip()
                if prefix.endswith(":"):
                    prefix = prefix[:-1].rstrip()
                if prefix:
                    output.append(prefix)
                if output and output[-1].strip():
                    output.append("")
                output.extend([f"**{trailing_action_heading.group(2).strip()}**", ""])
                pending_ordered_children = False
                continue

            checklist_title = _CHECKLIST_TITLE_LINE_RE.match(line)
            if checklist_title:
                if output and output[-1].strip():
                    output.append("")
                output.extend([f"**{checklist_title.group(1).strip()}**", ""])
                pending_ordered_children = False
                last_ordered_context = False
                continue

            checklist_field_source = _LEADING_BULLET_RE.sub("", line).strip()
            checklist_field = _CHECKLIST_FIELD_LINE_RE.match(checklist_field_source)
            if checklist_field and last_ordered_context:
                label = checklist_field.group(1).strip()
                value = checklist_field.group(2).strip()
                output.append(f"    - **{label}:** {value}")
                pending_ordered_children = True
                continue

            numbered = _NUMBERED_HEADING_LINE_RE.match(line)
            if numbered:
                if output and output[-1].strip():
                    output.append("")
                # Conserva el número como texto literal dentro del negrita para que
                # NO arranque un <ol> (que renumeraría y absorbería los bullets).
                output.extend([f"**{numbered.group(1)}. {numbered.group(2)}**", ""])
                continue

            action_heading = _PLAIN_ACTION_HEADING_LINE_RE.match(line)
            action_heading_prefix = _PLAIN_ACTION_HEADING_PREFIX_LINE_RE.match(line)
            if action_heading or action_heading_prefix:
                if output and output[-1].strip():
                    output.append("")
                title = (action_heading.group(1).strip() if action_heading else line.strip().rstrip(":"))
                output.extend([f"**{title}**", ""])
                pending_ordered_children = False
                continue

            normalized_lines: list[str] = []
            for decision_line in _split_inline_decision_rules(line):
                for numbered_line in _split_inline_numbered_items(decision_line):
                    normalized_lines.extend(_normalize_bullets_on_line(numbered_line))

            first_content = next((item for item in normalized_lines if item.strip()), "")
            is_bullet = bool(first_content and _LEADING_BULLET_RE.match(first_content))
            is_indented_bullet = bool(first_content and re.match(r"^\s+(?:\\?[-–—−•▪◦*+])\s+", first_content))
            is_ordered = bool(first_content and re.match(r"^\s*[1-9]\d*\.\s+", first_content))
            if (pending_ordered_children or last_ordered_context) and is_bullet:
                normalized_lines = [
                    ("    " + item.lstrip() if _LEADING_BULLET_RE.match(item) else item)
                    for item in normalized_lines
                ]
                first_content = next((item for item in normalized_lines if item.strip()), "")
                is_bullet = bool(first_content and _LEADING_BULLET_RE.match(first_content))
                is_indented_bullet = bool(first_content and re.match(r"^\s+(?:\\?[-–—−•▪◦*+])\s+", first_content))
            if (is_bullet or is_ordered) and output and output[-1].strip():
                previous_content = next((item for item in reversed(output) if item.strip()), "")
                previous_is_list = bool(
                    _LEADING_BULLET_RE.match(previous_content)
                    or re.match(r"^\s*[1-9]\d*\.\s+", previous_content)
                )
                # Evita que Markdown convierta cada item en <p> separado; mantenemos
                # un solo bloque de lista y solo abrimos espacio antes del primer item.
                if not previous_is_list and not is_indented_bullet:
                    output.append("")
            output.extend(normalized_lines)
            last_content = next((item for item in reversed(normalized_lines) if item.strip()), "")
            if is_ordered:
                pending_ordered_children = last_content.rstrip().endswith(":")
                last_ordered_context = True
            elif is_bullet:
                # Mantiene bullets consecutivos como hijos del ?ltimo punto numerado.
                pending_ordered_children = pending_ordered_children
            elif last_content:
                pending_ordered_children = False
                last_ordered_context = False
    normalized = re.sub(r"\n{3,}", "\n\n", "\n".join(output)).strip()
    normalized = _DANGLING_BOLD_BEFORE_ACTION_TITLE_RE.sub("\n\n", normalized)
    normalized = re.sub(
        r"\s*\*\*\s+(?=\*\*(?:Gu\S?a operativa del m\S?dulo|Salida operativa|Checklist de implementaci\S?n|Checklist ejecutable|"
        r"Errores frecuentes|Ejemplos pr\S?cticos|Decisiones si/entonces|Mapa de decisiones|"
        r"Anti-patterns y riesgos|Mirada Danwor)\*\*)",
        "\n\n",
        normalized,
        flags=re.I,
    )
    return normalized.strip()


_ESCAPED_ENTITY_RE = re.compile(r"&amp;((?:lt|gt|amp|quot|apos|#\d+|#x[0-9a-fA-F]+));")
_PLACEHOLDER_SEPARATOR_RE = re.compile(r"((?:&lt;[^&\n]+?&gt;|<[^<>\n]+?>))_(?=(?:&lt;|<))")
_PLACEHOLDER_SEPARATOR_TOKEN = "DWTEMPLATEUNDERSCORE"


def _protect_placeholder_separators(text: str) -> str:
    """Avoid Markdown emphasis inside naming templates such as <A>_<B>."""
    return _PLACEHOLDER_SEPARATOR_RE.sub(rf"\1{_PLACEHOLDER_SEPARATOR_TOKEN}", text)


def _restore_literal_entities(rendered: str) -> str:
    """Collapse double-escaped entities while keeping literal angle brackets safe."""
    restored = _ESCAPED_ENTITY_RE.sub(r"&\1;", rendered)
    return restored.replace(_PLACEHOLDER_SEPARATOR_TOKEN, "_")


_ACTION_TITLE_HTML_RE = re.compile(
    r"<p><strong>((?:Gu\S?a operativa del m\S?dulo|Salida operativa|Checklist de implementaci\S?n|Checklist ejecutable|"
    r"Errores frecuentes|Ejemplos pr\S?cticos|Decisiones si/entonces|Mapa de decisiones|"
    r"Anti-patterns y riesgos|Mirada Danwor)(?:\s+[—-][^<]+)?)</strong></p>",
    re.I,
)
_CHECKLIST_TITLE_HTML_RE = re.compile(
    r"<p><strong>(Checklist[^<]{8,180})</strong></p>",
    re.I,
)


def _mark_action_titles(rendered: str) -> str:
    rendered = _ACTION_TITLE_HTML_RE.sub(
        r'<p class="section-analysis-title"><strong>\1</strong></p>', rendered
    )
    return _CHECKLIST_TITLE_HTML_RE.sub(
        r'<p class="section-analysis-title"><strong>\1</strong></p>', rendered
    )


_SINGLE_CHILD_LIST_RE = re.compile(
    r"<li>(?P<head>(?:(?!<ul>).)+?)<ul>\s*<li>(?P<child>(?:(?!</li>).)+?)</li>\s*</ul>\s*</li>",
    re.S,
)
_CHECKLIST_FIELD_HTML_RE = re.compile(
    r"^\s*<strong>(Qué validar|Con quién|Qué pasa si no está resuelto|Justificación|Por qué está mal|Riesgo concreto):</strong>",
    re.I,
)


def _collapse_single_child_lists(rendered: str) -> str:
    """Compacta listas hijas de un solo item cuando no aportan jerarquía real.

    La guía operativa suele leerse mejor como `1. Decisión: detalle` si hay
    un único detalle. En cambio, los campos del checklist sí deben quedar como
    bullets hijos aunque haya uno solo, porque representan estructura fija.
    """
    def repl(match: re.Match) -> str:
        head = match.group("head").strip()
        child = match.group("child").strip()
        if _CHECKLIST_FIELD_HTML_RE.match(child):
            return match.group(0)
        return f"<li>{head} {child}</li>"

    previous = None
    current = rendered
    while previous != current:
        previous = current
        current = _SINGLE_CHILD_LIST_RE.sub(repl, current)
    return current


def render_llm_html(answer: str) -> str:
    normalized = _protect_placeholder_separators(normalize_llm_markdown(answer))
    rendered = markdown.markdown(
        html.escape(normalized),
        extensions=["tables", "fenced_code", "sane_lists"],
    )
    rendered = _mark_action_titles(rendered)
    rendered = _collapse_single_child_lists(rendered)
    return _restore_literal_entities(rendered)


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
        is_module = section_slug == MODULE_TARGET_SLUG
        valid_actions = MODULE_ACTIONS if is_module else ALL_ACTIONS
        if action not in valid_actions:
            raise SectionAnalysisError("La acción seleccionada no es válida.")
        if not ia_enabled_for(document.summary.stem):
            raise SectionAnalysisError("El análisis con IA no está habilitado para este módulo.")

        if is_module:
            # Target = cabecera del módulo: el fragmento es el módulo completo y la
            # instrucción es la quick action (QUICK_ACTIONS), no la de sección.
            target_title = document.summary.title
            fragment = document.source
            instruction = QUICK_ACTIONS.get(action)
        else:
            section = next((item for item in document.sections if item.slug == section_slug), None)
            if section is None:
                raise SectionAnalysisError("No se encontró la sección seleccionada.")
            target_title = section.title
            fragment = section.markdown
            instruction = topic_instruction(action)

        prompt = build_topic_prompt(
            document.source,
            target_title,
            fragment,
            action,
            instruction,
            module=document.summary.stem,
            pack_name=pack_name,
        )
        if self.execution_mode.lower().startswith("simul"):
            answer = (
                "🧪 **Simulación activa** — no se consumieron créditos.\n\n"
                f"La acción **{action}** quedó preparada para **{target_title}**."
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
        return SectionAnalysisResult(target_title, action, answer, rendered, prompt)
