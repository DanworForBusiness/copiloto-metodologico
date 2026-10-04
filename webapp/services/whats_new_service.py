"""whats_new_service.py — "Novedades" del Copiloto (What's New).

CONTRATO DE PROPIEDAD (owner corrective, editorial ownership). Esta superficie
es COMUNICACION DE PRODUCTO DEL COPILOTO, curada, y su unica fuente es:

    content_packs/<pack>/whats_new.yaml

Append-only, escrito a mano, pack-scoped por construccion. El copiloto SOLO lo
lee — nunca lo sobrescribe.

CAMBIO DESPLEGADO != NOVEDAD PUBLICADA. Antes habia una segunda fuente: los
commits con trailer `Novedad:`, leidos con `git log` sobre TODO el repositorio.
Ese diseno era correcto cuando el repo ERA el Copiloto; el monolito despues
sumo Comercial, Reporting, Staffing y plataforma, y la equivalencia se rompio
en silencio. El sintoma: un fix de autenticacion de Graph (Hub/Reporting)
aparecio anunciado como novedad del Copiloto.

El trailer declaraba "esto es anunciable", nunca "esto es del Copiloto" — y el
repositorio no tiene forma de saber la diferencia. Que un commit exista, este
en main, o sea tecnicamente importante NO lo vuelve elegible. Solo una entrada
explicita del pack publica una novedad.

HANDOFF.md y el historial de git siguen siendo mecanismos de CONTINUIDAD
TECNICA, no superficies de publicacion.

Estado "desde tu ultimo ingreso": una cookie (`dw-whatsnew-seen`) guarda el ref
del anuncio mas reciente que el usuario ya vio; si aparece uno mas nuevo, el
banner vuelve.
"""

from __future__ import annotations

import time

import yaml

from config import CONTENT_PACKS_DIR

COOKIE_NAME = "dw-whatsnew-seen"
DEFAULT_PACK = "datasphere_core"
MANUAL_FILE = "whats_new.yaml"
_CACHE_TTL = 30.0
_cache: dict = {}   # pack -> (monotonic_t, entries)


# ── Etiqueta de fecha (la usa la vista, no la publicacion) ────────────────
_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _date_label(iso: str) -> str:
    """'2026-07-25' → '25 jul 2026' (para el encabezado del colapsable)."""
    try:
        y, m, d = (iso or "").split("-")[:3]
        return f"{int(d)} {_MESES[int(m) - 1]} {y}"
    except Exception:  # noqa: BLE001
        return iso or ""

# ── Fuente MANUAL: whats_new.yaml curado ────────────────────────────────────
def _manual_entries(pack: str) -> list[dict]:
    path = CONTENT_PACKS_DIR / pack / MANUAL_FILE
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for it in data if isinstance(data, list) else []:
        if not isinstance(it, dict):
            continue
        ref = str(it.get("ref") or "").strip()
        titulo = str(it.get("titulo") or it.get("title") or "").strip()
        if not (ref and titulo):
            continue
        _d = str(it.get("fecha") or it.get("date") or "").strip()[:10]
        out.append({"ref": ref, "date": _d, "ts": (_d + "T00:00:00") if _d else "",
                    "title": titulo, "detail": str(it.get("detalle") or it.get("detail") or "").strip(),
                    "categoria": str(it.get("categoria") or it.get("category") or "General").strip() or "General",
                    "link": str(it.get("link") or "").strip(), "cta": str(it.get("cta") or "").strip(),
                    "source": "manual"})
    return out


# ── Publicación ───────────────────────────────────────────────────────────
def entries(pack: str = DEFAULT_PACK) -> list[dict]:
    """Novedades PUBLICADAS del pack, más nuevas primero.

    UNA sola fuente, y es la que el pack posee. No hay fusión con nada
    derivado del repositorio: la elegibilidad es editorial, no técnica.

    El dedup por `ref` se conserva porque el archivo es append-only y escrito
    a mano — dos entradas con el mismo ref serían un error de edición, y gana
    la más nueva en vez de duplicar la fila.
    """
    now = time.monotonic()
    hit = _cache.get(pack)
    if hit and now - hit[0] <= _CACHE_TTL:
        return hit[1]
    publicadas, vistos = [], set()
    for e in sorted(_manual_entries(pack),
                    key=lambda x: (x.get("ts") or x.get("date") or ""), reverse=True):
        if e["ref"] in vistos:
            continue
        vistos.add(e["ref"])
        e["label"] = _date_label(e.get("date", ""))
        publicadas.append(e)
    _cache[pack] = (now, publicadas)
    return publicadas

def latest_ref(pack: str = DEFAULT_PACK) -> str:
    e = entries(pack)
    return e[0]["ref"] if e else ""


def is_unseen(request, pack: str = DEFAULT_PACK) -> bool:
    """¿Hay una novedad más reciente que la última vista por este usuario (cookie)?"""
    latest = latest_ref(pack)
    return bool(latest) and latest != (request.cookies.get(COOKIE_NAME) or "")


def build_markdown(items: list[dict]) -> str:
    """Markdown con la estructura de contenido del copiloto: header
    `{dw-gold}<ref> · <fecha> · {dw-black}<título>` + subtítulo `> <detalle>`."""
    parts: list[str] = []
    for e in items:
        meta = f"`{e['ref']}`" + (f" · {e['date']}" if e.get("date") else "")
        parts.append(f"## {{dw-gold}}{meta} · {{dw-black}}{e['title']}")
        if e.get("detail"):
            parts.append(f"> {e['detail']}")
    return "\n\n".join(parts)
