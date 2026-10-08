"""Referencias SEMÁNTICAS a secciones de módulos del contenido (jamás por número ordinal).

El número de una sección (§29, §30…) cambia en cuanto se inserta otra sección antes: NO es una clave estable. La identidad de una referencia es
``(module_key, section_key)``, donde:

    · ``module_key``  = el ``module:`` del front-matter del módulo (p. ej. "04");
    · ``section_key`` = el ``id`` de ``key_topics`` del MISMO front-matter (p. ej. "naming"), que apunta a su ``heading``.

Ese registro ya existe en el contenido (``content_packs/<pack>/content/NN_*.md``); acá solo se RESUELVE, en tiempo de presentación, contra la
estructura ACTUAL del módulo (``content_service.load_module``): número vigente, rótulo y ancla. Si no se puede resolver, se muestra solo el rótulo
semántico: nunca un número que no se pudo verificar.

Compatibilidad: los Assessments ya persistidos traen textos con el ordinal viejo («sección 28», «§28», «(§29: …)»). ``dynamic_text`` los traduce al
vuelo a la referencia semántica (sin reescribir archivos históricos); ``strip_ordinals`` quita ordinales que no se pueden mapear.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from webapp.services import content_service

log = logging.getLogger(__name__)

DEFAULT_PACK = "datasphere_core"
# Referencia semántica de la convención de nombres: módulo 04 · key_topic «naming» (ya existente en el front-matter del módulo).
NAMING_CONVENTION = {"module_key": "04", "section_key": "naming", "label": "Convención de nombres"}
# Adaptador de COMPATIBILIDAD (solo textos HISTÓRICOS persistidos): el ordinal «28» del texto viejo apuntaba a la convención de nombres.
_LEGACY_ORDINAL_TO_REF = {"28": NAMING_CONVENTION}


def _key(text: str) -> str:
    return " ".join(content_service._norm(str(text or "")).split())


def resolve(module_key: str, section_key: str, pack: str = DEFAULT_PACK, label: str | None = None) -> dict:
    """{module_key, section_key, label, number|None, slug|None, text}. ``text`` = «Módulo 04 · §N · Rótulo» con el N VIGENTE, o «Módulo 04 · Rótulo» si
    el registro no puede resolverlo (sin número ordinal inventado)."""
    out = {"module_key": module_key, "section_key": section_key, "label": label or section_key, "number": None, "slug": None}
    try:
        summary = next((m for m in content_service.list_modules(pack) if m.number == str(module_key).zfill(2)), None)
        if summary is not None:
            meta, _body = content_service.split_front_matter(summary.path.read_text(encoding="utf-8", errors="ignore"))
            topic = next((t for t in meta.get("key_topics") or [] if isinstance(t, dict) and t.get("id") == section_key), None)
            if topic is not None:
                heading = str(topic.get("heading") or "")
                doc = content_service.load_module(summary.stem, pack)
                sec = next((s for s in doc.sections if _key(s.title) == _key(heading)), None)
                if sec is not None:
                    out.update(label=sec.title, number=sec.number, slug=sec.slug)
    except Exception:                                   # noqa: BLE001 — la referencia nunca rompe el informe; degrada al rótulo semántico
        log.warning("module_reference: no se pudo resolver %s/%s", module_key, section_key, exc_info=True)
    mod = f"Módulo {str(module_key).zfill(2)}"
    out["text"] = f"{mod} · §{out['number']} · {out['label']}" if out["number"] is not None else f"{mod} · {out['label']}"
    return out


def resolve_ref(ref: dict, pack: str = DEFAULT_PACK) -> dict:
    return resolve(ref["module_key"], ref["section_key"], pack, ref.get("label"))


_LEGACY_SECTION = re.compile(r"(?i)\bsecci[oó]n (\d{1,3})\b")
_LEGACY_SYMBOL = re.compile(r"§(\d{1,3})")
_PAREN_ORDINAL = re.compile(r"\(§\d{1,3}\s*:\s*")


def strip_ordinals(text: str) -> str:
    """Quita ordinales literales (§N) de un texto cuyo destino no se puede mapear: «(§29: regla)» → «(regla)». No inventa ningún número."""
    t = _PAREN_ORDINAL.sub("(", str(text or ""))
    t = re.sub(r"\s*§\d{1,3}\s*:?\s*", " ", t)
    return re.sub(r"\s{2,}", " ", t).strip()


def dynamic_text(text: str, pack: str = DEFAULT_PACK) -> str:
    """Traduce ordinales históricos a la referencia semántica VIGENTE: «sección 28» → «sección {N actual de la convención de nombres}» (o
    «convención de nombres» si no resuelve); ordinales sin mapa se quitan. Idempotente y sin efecto sobre textos sin ordinales."""
    if not text or ("ecci" not in text and "§" not in text):
        return text

    def _sec(m: re.Match) -> str:
        ref = _LEGACY_ORDINAL_TO_REF.get(m.group(1))
        if ref is None:
            return m.group(0)
        r = resolve_ref(ref, pack)
        return f"sección {r['number']}" if r["number"] is not None else "convención de nombres"

    out = _PAREN_ORDINAL.sub("(", _LEGACY_SECTION.sub(_sec, text))

    def _sym(m: re.Match) -> str:                      # «§N» mapeado → ordinal VIGENTE; sin mapa → se quita (no se inventa ningún número)
        ref = _LEGACY_ORDINAL_TO_REF.get(m.group(1))
        r = resolve_ref(ref, pack) if ref else None
        return f"§{r['number']}" if r and r["number"] is not None else ""

    out = re.sub(r"[ 	]{2,}", " ", _LEGACY_SYMBOL.sub(_sym, out)).strip()
    return out


def walk(obj: Any, pack: str = DEFAULT_PACK) -> Any:
    """``dynamic_text`` sobre todos los strings de una estructura de presentación (dict/list/tuple), sin tocar claves."""
    if isinstance(obj, str):
        return dynamic_text(obj, pack)
    if isinstance(obj, dict):
        return {k: walk(v, pack) for k, v in obj.items()}
    if isinstance(obj, list):
        return [walk(v, pack) for v in obj]
    if isinstance(obj, tuple):
        return tuple(walk(v, pack) for v in obj)
    return obj
