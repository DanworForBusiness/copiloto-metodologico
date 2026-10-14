"""Consultas INDIVIDUALES sobre un modelo (paso 4B.3): qué se pregunta (``Intent``) y cómo se redacta la respuesta (``render``).

Reconocimiento y redacción puros: no leen el Catálogo, no identifican modelos (``assistant_entry_resolver``) ni recuperan datos (``assistant_entry_facts``). El vocabulario sale de los contratos
(``assistant_query.parse``: indicadores, niveles y estados DERIVADOS del registro); acá solo se suman las marcas de pregunta («qué sabés de», «por qué»). Todo determinista y sin LLM: los textos de
fundamentos son los que ya presenta el producto, y cada respuesta muestra SIEMPRE la identidad del modelo para que el usuario corrija si se entendió otro.

Textos: nunca terminan una cláusula con «número + punto» (el chat lo interpreta como lista numerada y se come el número).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.utils import norm_text
from webapp.services import assistant_capabilities as caps
from webapp.services import assistant_entry_facts as facts
from webapp.services import assistant_indicators as indicators
from webapp.services import assistant_query as query

_WHY = re.compile(r"\b(?:por que|porque|fundament\w*|motivos?|razon(?:es)?|justific\w*|en que se basa|que (?:lo|la) (?:hace|vuelve))\b")
_STRENGTHS = re.compile(r"\b(?:fortalezas?|evidencias? favorables?|puntos? fuertes?)\b")
_LIMITATIONS = re.compile(r"\b(?:limitaciones?|evidencia limitada)\b")
_FICHA = re.compile(r"\b(?:que (?:sabes|sabe|sabemos|se sabe)|contame|contanos|decime|dime|resumime|resumen|ficha|informacion|info|como esta|como anda|que tal|situacion)\b")
_SET_WORDS = re.compile(r"\b(?:modelos|entradas|flujos|activos|dominios|clientes|assessments|evaluaciones|lineas de negocio)\b")
_CONCEPTUAL = ("que es", "que significa", "defin", "cual es la diferencia", "explica", "para que", "como se ", "como funciona")


@dataclass(frozen=True)
class Intent:
    attributes: tuple[str, ...] = ()           # ids de indicador pedidos (orden del registro)
    state: bool = False                        # pidió la vigencia / el estado del Assessment
    ficha: bool = False                        # «qué sabés de X»: resumen breve de todo
    why: bool = False                          # «por qué»: fundamentos del Juicio (razones y fortalezas)
    strengths: bool = False
    limitations: bool = False
    levels: tuple[tuple[str, str], ...] = ()   # (indicador, nivel) nombrados: «¿por qué tiene Buena?»
    nd: bool = False                           # nombró «no determinable» (condición del Juicio): «¿por qué es no determinable?»

    @property
    def fundamentals(self) -> bool:
        return self.why or self.strengths or self.limitations


def parse_intent(question: str) -> Intent | None:
    """Qué pregunta sobre UN modelo hace el usuario; None si no pide nada de lo individual (atributo, vigencia, fundamentos o resumen) o si es una consulta de conjunto / conceptual."""
    q = norm_text(question).strip().lstrip("¿¡")
    frame = query.parse(question)
    if frame.group_by or frame.ops or _SET_WORDS.search(q) or q.startswith(_CONCEPTUAL):
        return None                                                          # conjuntos, agregaciones, comparaciones y conceptos NO son una consulta individual (los resuelve 4A / el contenido)
    named = list(frame.indicators)
    for owner, _level in frame.levels:                                       # «¿por qué tiene Buena?»: el nivel pertenece a UN indicador
        if owner not in named:
            named.append(owner)
    state = frame.dimension_state or any(not query.STATE_BY_ID[s].judgment for s in frame.states)
    if any(query.STATE_BY_ID[s].judgment for s in frame.states) and "adoption" not in named:
        named.append("adoption")                                             # «no determinable» es una condición del Juicio de adopción
    why, strengths, limitations = bool(_WHY.search(q)), bool(_STRENGTHS.search(q)), bool(_LIMITATIONS.search(q))
    if (why or strengths or limitations) and not named:
        named.append("adoption")                                             # los fundamentos son los del Juicio de adopción
    ficha = bool(_FICHA.search(q)) and not (named or state or why or strengths or limitations)
    if not (named or state or why or strengths or limitations or ficha):
        return None
    return Intent(attributes=tuple(i.id for i in indicators.INDICATORS if i.id in named), state=state, ficha=ficha, why=why, strengths=strengths, limitations=limitations, levels=frame.levels,
                  nd=any(query.STATE_BY_ID[s].judgment for s in frame.states))


@dataclass(frozen=True)
class Rendered:
    message: str
    footer: str
    badge: str
    note: str


def _date(value: str | None) -> str:
    return f" (informe del {value})" if value else ""


def _evidence_note(v: caps.AttributeValue) -> str:
    e = v.evidence
    return " · ".join(x for x in ((e.source if e else None), (e.ruleset if e else None), (e.generated_at if e else None)) if x)


def _value_text(v: caps.AttributeValue, state: caps.StateInfo) -> str:
    if v.historical:
        return f"**{v.display}** — último valor conocido{_date(state.generated_at)}; **no es el vigente**: el Assessment está desactualizado"
    return f"**{v.display}**"


def _state_line(state: caps.StateInfo) -> str:
    head = f"**Estado del Assessment**: {state.label}" + (f" (generado el {state.generated_at})" if state.generated_at and state.state in ("CURRENT", "STALE") else "")
    extra = " ".join(x for x in (state.note, state.body) if x)
    return head + (f". {extra}" if extra else "")


def _items(header: str, rows: list[str], total: int, shown: int) -> list[str]:
    """Una sección con su rótulo y una línea «• …» por ítem (saltos duros, sin listas Markdown: el chat aplana las listas anidadas y pega al último ítem lo que sigue)."""
    out = [header] + [f"› {r}" for r in rows]
    if total > shown:
        out.append(f"› y {total - shown} más en el informe")
    return out


def _fundamentals_lines(f: caps.Fundamentals, intent: Intent) -> list[str]:
    lines: list[str] = []
    both = not (intent.strengths or intent.limitations) or intent.why
    prefix = "Último informe conocido (Assessment desactualizado, **no vigente**): " if f.historical else ""
    if not intent.limitations or intent.why or intent.strengths:
        lines.append(f"{prefix}**Adopción metodológica: {f.level_label}**. {f.headline}")
    if not f.determined:
        if f.nd_reason:
            lines.append(f"Motivo: {f.nd_reason}.")
        if f.nd_missing:
            lines.append("Sin evidencia afirmativa suficiente en: " + ", ".join(f.nd_missing) + ".")
    if f.contracts and (both or intent.strengths) and not intent.limitations:
        lines.append("Los cuatro contratos: " + " · ".join(f"{dim} ({state})" for dim, state in f.contracts))
    if (both or intent.strengths) and f.strengths:
        lines += _items(f"**Evidencias favorables** ({f.strengths_total}):", [f"{dim}: {text}" for dim, text in f.strengths], f.strengths_total, len(f.strengths))
    if (intent.why or not (intent.strengths or intent.limitations)) and f.reasons:
        lines += _items(f"**Puntos de atención** ({f.reasons_total}):", [f"{dim} · {kind}: {text}" for dim, kind, text in f.reasons], f.reasons_total, len(f.reasons))
    if (intent.limitations or intent.why) and f.limitations:
        lines += _items(f"**Limitaciones de la evidencia** ({f.limitations_total}):",
                        [f"{title} ({area}): {state}" + (f" — {reason}" if reason else "") for title, area, state, reason in f.limitations], f.limitations_total, len(f.limitations))
    if intent.strengths and not intent.why and not f.strengths:
        lines.append("El Juicio vigente no registra evidencias favorables para este modelo.")
    if (intent.limitations or intent.why) and f.limitations_total == facts.UNREADABLE:
        lines.append("No pude leer las limitaciones de la evidencia en este momento; están en el informe de Assessment.")
    elif intent.limitations and not f.limitations:
        lines.append("El informe no registra limitaciones de evidencia para este modelo.")
    return lines


def _ficha_bullets(lines, by_id, held, state) -> list[str]:
    """Una viñeta por medida; las medidas que se retienen por el MISMO motivo (p. ej. «sin assessment») se agrupan en una sola para no repetir la misma frase."""
    out, grouped = [], {}
    for label, iid in lines:
        if iid in by_id:
            v = by_id[iid]
            out.append(f"- **{label}**: **{v.display}**" + (" (histórico)" if v.historical else ""))
        else:
            grouped.setdefault(held[iid].text, []).append(label)
    for text, labels in grouped.items():
        out.append(f"- **{', '.join(labels)}**: {text}")
    return out


_SHORT = {facts.ABSENT: "Sin assessment", facts.NOT_APPLICABLE: "No aplica", facts.UNAVAILABLE: "No disponible", facts.INCOMPATIBLE: "No compatible", facts.STALE: "Desactualizado",
          facts.NOT_DETERMINABLE: "No determinable", facts.NO_VALUE: "Sin valor", facts.LEGACY_RULES: "Reglas anteriores"}


def render(answer: caps.EntryAnswer, intent: Intent) -> Rendered:
    """Redacta la respuesta individual desde el ``EntryAnswer`` (la identidad siempre primero). No agrega hechos que no estén en la estructura.
    Formato: líneas con salto duro («  \\n»: el chat colapsa los párrafos) y, a lo sumo, UNA lista al final (lo que sigue a una lista se pega a su último ítem)."""
    item, state = answer.entry, answer.state
    ident = f"**{item.title}** ({item.subtitle})" if item.subtitle else f"**{item.title}**"
    by_id, held = {v.indicator: v for v in answer.attributes}, {w.indicator: w for w in answer.withheld}
    ids = facts.ATTRIBUTE_ORDER if intent.ficha else tuple(i for i in intent.attributes if not (intent.fundamentals and i == "adoption"))
    measures = [(indicators.BY_ID[i].label, i) for i in ids if i in by_id or i in held]
    text_of = lambda iid: _value_text(by_id[iid], state) if iid in by_id else held[iid].text           # noqa: E731
    lines: list[str] = []
    bullets: list[str] = []
    footer: list[str] = []
    badge, note = state.label, (f"generado el {state.generated_at}" if state.generated_at and state.current else "")

    if intent.ficha:
        lines += [ident, _state_line(state)]
        if any(v.historical for v in by_id.values()):
            lines.append("Los valores marcados como «histórico» son del último informe" + (f" ({state.generated_at})" if state.generated_at else "") + " y **no son los vigentes**.")
        bullets = _ficha_bullets(measures, by_id, held, state)
        footer.append("Cada medida tiene su propia fuente y advertencia: no se combinan en un puntaje.")
    else:
        if intent.fundamentals:
            f = answer.fundamentals
            adoption = held.get("adoption")
            lines += [ident] + (_fundamentals_lines(f, intent) if f is not None else [adoption.text if adoption else "No hay un Juicio metodológico para este modelo."])
            if f is not None:
                badge, note = f.level_label + (" · histórico, no vigente" if f.historical else ""), ("Juicio metodológico" + (f" · {state.generated_at}" if state.generated_at else ""))
            footer.append("Los fundamentos son los del informe de Assessment; abrilo para ver el detalle completo.")
        if measures:
            if len(measures) == 1 and not intent.fundamentals:
                label, iid = measures[0]
                lines.append(f"{ident} · {label}: {text_of(iid)}")
                if iid in by_id:
                    v = by_id[iid]
                    badge, note = v.display + (" · histórico, no vigente" if v.historical else ""), _evidence_note(v)
                    footer.append(v.caveat)
                else:
                    badge, note = _SHORT.get(held[iid].code, state.label), ""
            else:
                lines += [] if lines else [ident]
                bullets = [f"- **{label}**: {text_of(i)}" for label, i in measures]
                footer += [by_id[i].caveat for _l, i in measures if i in by_id][:2]
        for owner in dict.fromkeys(o for o, _lv in intent.levels):                           # «¿tiene adopción Buena?»: se contesta el valor y si coincide con lo consultado
            v = by_id.get(owner)
            if v is not None and not v.historical and not intent.fundamentals:
                asked = tuple(lv for o, lv in intent.levels if o == owner)
                lines.append(f"Consultaste el nivel «{indicators.level_text(indicators.BY_ID[owner], asked)}»: " + ("coincide" if v.value in asked else "no coincide") + " con el valor vigente.")
        if intent.state:
            lines += [] if lines else [ident]
            lines.append(_state_line(state))
        if not lines:
            lines.append(ident)
    message = "  \n".join(lines) + (("\n" if lines else "") + "\n".join(bullets) if bullets else "")
    return Rendered(message, " ".join(dict.fromkeys(x for x in footer if x)), badge, note)
