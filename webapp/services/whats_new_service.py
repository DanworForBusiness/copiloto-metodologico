"""whats_new_service.py — "Novedades" del copiloto (What's New).

El marco estático es difícil de seguir ("no me voy a poner a leer el marco todos los
días para ver qué cambió"). Acá armamos un resumen de NOVEDADES con la MISMA estructura
de contenido del copiloto (id en dorado, header = descripción, pie = resumen), desde
DOS fuentes que el copiloto solo LEE y fusiona (nunca sobrescribe → los dos tranquilos):

  • auto   → commits con trailer `Novedad:` en su mensaje (opt-in; los fix/refactors y
             las acciones runtime NO aparecen). id = hash de commit.
  • manual → `whats_new.yaml` curado en el pack (append-only, hand-owned). Sirve para
             backfill (features previas a la convención) o anuncios sin commit. id = `ref`.

Estado "desde tu último ingreso": una cookie (`dw-whatsnew-seen`) guarda el id (ref) del
anuncio más reciente que el usuario ya vio; si aparece uno más nuevo, el banner vuelve.
"""

from __future__ import annotations

import subprocess
import time

import yaml

from config import BASE_DIR, CONTENT_PACKS_DIR

TRAILER = "Novedad:"
COOKIE_NAME = "dw-whatsnew-seen"
DEFAULT_PACK = "datasphere_core"
MANUAL_FILE = "whats_new.yaml"
_FS = "\x1f"
_RS = "\x1e"
_CACHE_TTL = 30.0
_cache: dict = {}   # pack -> (monotonic_t, entries)


# ── Fuente AUTO: commits con trailer `Novedad:` ─────────────────────────────
def _git_log_raw() -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(BASE_DIR), "log", "--extended-regexp",
             "--grep", "^Novedad:", f"--format=%h{_FS}%aI{_FS}%s{_FS}%b{_RS}"],
            capture_output=True, text=True, encoding="utf-8", timeout=8,
        )
        return proc.stdout if proc.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def _extract_named(text: str, key: str) -> str:
    """Valor de un trailer `Key:` (primera aparición) en el mensaje. "" si no está."""
    pref = key.lower() + ":"
    for line in (text or "").splitlines():
        s = line.strip()
        if s.lower().startswith(pref):
            return s.split(":", 1)[1].strip()
    return ""


_MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _date_label(iso: str) -> str:
    """'2026-07-25' → '25 jul 2026' (para el encabezado del colapsable)."""
    try:
        y, m, d = (iso or "").split("-")[:3]
        return f"{int(d)} {_MESES[int(m) - 1]} {y}"
    except Exception:  # noqa: BLE001
        return iso or ""


def _split_title_detail(text: str) -> tuple[str, str]:
    """`título — detalle` (o `-`) → (título, detalle). Sin separador, todo es título."""
    for sep in (" — ", " - "):
        if sep in text:
            a, b = text.split(sep, 1)
            return a.strip(), b.strip()
    return text.strip(), ""


def _git_entries() -> list[dict]:
    out: list[dict] = []
    for rec in _git_log_raw().split(_RS):
        rec = rec.strip("\n ")
        if not rec:
            continue
        parts = rec.split(_FS)
        if len(parts) < 4:
            continue
        short, iso, subject, body = parts[0].strip(), parts[1].strip(), parts[2].strip(), parts[3]
        full = subject + "\n" + body
        nov = _extract_named(full, "Novedad")
        if not nov:
            continue
        titulo, detalle = _split_title_detail(nov)
        cat = _extract_named(full, "Categoria") or _extract_named(full, "Categoría") or "General"
        out.append({"ref": short, "date": (iso[:10] if iso else ""), "ts": iso or "",
                    "title": titulo, "detail": detalle, "categoria": cat,
                    "link": _extract_named(full, "Link"), "source": "git"})
    return out


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
                    "link": str(it.get("link") or "").strip(),
                    "source": "manual"})
    return out


# ── Fusión ──────────────────────────────────────────────────────────────────
def entries(pack: str = DEFAULT_PACK) -> list[dict]:
    """Novedades fusionadas (git + manual), más nuevas primero, dedup por `ref`.
    Cacheadas por pack con TTL corto."""
    now = time.monotonic()
    hit = _cache.get(pack)
    if hit and now - hit[0] <= _CACHE_TTL:
        return hit[1]
    merged, seen = [], set()
    # Orden global descendente por timestamp (fino): el commit/entrada más reciente
    # arriba. Al agrupar por fecha en la vista, el orden dentro de cada día se conserva.
    for e in sorted(_git_entries() + _manual_entries(pack),
                    key=lambda x: (x.get("ts") or x.get("date") or ""), reverse=True):
        if e["ref"] in seen:
            continue
        seen.add(e["ref"])
        e["label"] = _date_label(e.get("date", ""))
        merged.append(e)
    _cache[pack] = (now, merged)
    return merged


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
