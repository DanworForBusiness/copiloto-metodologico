"""guides_service.py — How-tos del copiloto (guías paso a paso).

Cada how-to es CONTENIDO del pack, no código: vive en
``content_packs/<pack>/guides/<slug>.yaml`` y se renderiza como slides estilo
Danwor Academy. El copiloto solo LEE. Misma filosofía que el resto del contenido
(módulos .md, novedades .yaml, academy .html): editable sin tocar el webapp,
pack-scoped y escalable a N guías (dominios, arquitectura, seguridad…) agregando
archivos, sin engordar main.py ni forkear el template.

`load(pack, slug)` devuelve el how-to listo para el template: la bajada con {n}
resuelto y cada captura con su URL de /pack-assets (o "" si el archivo falta, para
mostrar el placeholder). Ver el schema en el propio guides/<slug>.yaml.
"""

from __future__ import annotations

import re

import yaml

from config import CONTENT_PACKS_DIR

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_TAG_RE = re.compile(r"<[^>]+>")
# Acciones ejecutables que un paso puede exponer como botón (allowlist: el template
# mapea cada una a su handler; nunca se ejecuta texto arbitrario del YAML).
_ACTIONS = {"regenerate"}


def _safe_slug(slug: str) -> str:
    """Evita traversal: solo kebab/snake simple. Vacío si no valida."""
    s = (slug or "").strip().lower()
    return s if _SLUG_RE.match(s) else ""


def _link(raw) -> dict | None:
    """CTA opcional de un paso. Acepta `link: "https://…"` o
    `link: {url, label}`. Solo http(s) (se abre en pestaña nueva). None si no aplica."""
    if isinstance(raw, str):
        raw = {"url": raw}
    if not isinstance(raw, dict):
        return None
    url = str(raw.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    return {"url": url, "label": str(raw.get("label") or "").strip() or "Abrir enlace"}


def load(pack: str, slug: str) -> dict | None:
    """Carga la guía `slug` del `pack`. None si no existe / no valida.

    Devuelve: {eyebrow, title, accent, lead, steps:[{n, title, body, substeps,
    shots:[{fname, caption, img}]}]}. `img` es la URL de /pack-assets o "" si el
    archivo no está (el template muestra placeholder)."""
    safe = _safe_slug(slug)
    if not safe:
        return None
    path = CONTENT_PACKS_DIR / pack / "guides" / f"{safe}.yaml"
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict) or not data.get("title"):
        return None

    assets_dir = str(data.get("assets_dir") or f"guias/{safe}").strip("/ ")
    base = CONTENT_PACKS_DIR / pack / "assets" / assets_dir

    def _note(raw) -> dict | None:
        """Bloque de cierre opcional de un paso: separador + texto + botón a un
        template. `template` es un archivo en assets_dir (se sirve por /pack-assets,
        fuera del alcance del scanner de catálogo); alternativamente `url` externa."""
        if not isinstance(raw, dict):
            return None
        text = str(raw.get("text") or "").strip()
        label = str(raw.get("label") or "").strip() or "Abrir template"
        tmpl = str(raw.get("template") or "").strip()
        url = str(raw.get("url") or "").strip()
        href = ""
        if tmpl:
            try:
                href = f"/pack-assets/{pack}/{assets_dir}/{tmpl}" if (base / tmpl).exists() else ""
            except Exception:  # noqa: BLE001
                href = ""
        elif url.startswith(("http://", "https://")):
            href = url
        if not text and not href:
            return None
        return {"text": text, "url": href, "label": label}

    def _shot(raw: dict) -> dict:
        fname = str(raw.get("img") or raw.get("fname") or "").strip()
        caption = str(raw.get("caption") or "").strip()
        exists = False
        if fname:
            try:
                exists = (base / fname).exists()
            except Exception:  # noqa: BLE001
                exists = False
        return {"fname": fname, "caption": caption,
                "img": f"/pack-assets/{pack}/{assets_dir}/{fname}" if exists else ""}

    steps: list[dict] = []
    for i, st in enumerate(data.get("steps") or [], start=1):
        if not isinstance(st, dict) or not st.get("title"):
            continue
        # `title` puede traer <em>…</em> para resaltar en dorado (lo pinta
        # dw-academy: .topic-title em). `title_plain` (sin tags) se usa donde no va
        # markup (nav, alt). `short` = etiqueta corta opcional para la botonera del
        # landing; si no está, cae al título sin tags.
        title = str(st.get("title") or "").strip()
        title_plain = _TAG_RE.sub("", title).strip()
        steps.append({
            "n": i,
            "title": title,
            "title_plain": title_plain,
            "short": str(st.get("short") or "").strip() or title_plain,
            "body": str(st.get("body") or "").strip(),
            "substeps": [str(x).strip() for x in (st.get("substeps") or []) if str(x).strip()],
            "shots": [_shot(s) for s in (st.get("shots") or []) if isinstance(s, dict)],
            "link": _link(st.get("link")),
            "action": str(st.get("action") or "").strip().lower() if str(st.get("action") or "").strip().lower() in _ACTIONS else "",
            "note": _note(st.get("note")),
        })
    if not steps:
        return None

    lead = str(data.get("lead") or "").strip().replace("{n}", str(len(steps)))
    return {
        "eyebrow": str(data.get("eyebrow") or "").strip(),
        "title": str(data.get("title") or "").strip(),
        "accent": str(data.get("accent") or "").strip(),
        "lead": lead,
        "steps": steps,
    }
