"""Cartelera Virtual · servicio multimedia COMPARTIDO (Novedades, Galería y Comunidad).

Un solo camino para validar, procesar, persistir, autorizar y servir imágenes; cambia solo la POLÍTICA de persistencia del original:
  - Novedades y Galería (contenido corporativo, gestionado por Capital Humano): el ORIGINAL subido se guarda tal cual en SharePoint
    (fuente de verdad: «Comunicaciones / CCHH HUB / Cartelera virtual / Novedades|Galería / …»), con el token DELEGADO de escritura ya
    consentido (`dwCarteleraWriteToken`, el mismo de Cumpleaños). Spaces guarda solo las representaciones optimizadas (regenerables).
  - Comunidad (contenido nativo del Hub, de cualquier colaborador admitido): el ORIGINAL se guarda en el almacenamiento privado del Hub
    (Spaces vía `remote_store`), gobernado por la publicación a la que pertenece. Sin colas ni sincronizaciones con SharePoint: los
    colaboradores no tienen (ni deben tener) escritura en la carpeta de Capital Humano y el Hub no tiene identidad de aplicación con SharePoint.

El SERVIDOR es la capa de seguridad: Pillow decodifica de verdad (formato real, no extensión), rechaza animaciones, bombas de
descompresión y dimensiones fuera de rango, corrige la orientación EXIF y genera las representaciones de consumo (WebP sin metadatos:
EXIF/GPS/ICC se descartan) — `display` (≤ 1920 px) y `thumb` (≤ 480 px), siempre con la proporción original. El navegador solo previsualiza.

Identidad = sha256 del ORIGINAL (deduplica: la misma imagen en dos ediciones/publicaciones es UN recurso con referencias compartidas).
Archivos: `human-capital/cartelera/media/<id>/{meta.json, display.webp, thumb.webp[, original.<ext>]}` (persistidos como el resto de la
Cartelera; objetos PRIVADOS: nunca se exponen URLs de Spaces). La visualización pasa SIEMPRE por la ruta autorizada del Hub.
Nada se borra automáticamente en esta etapa (ni originales de SharePoint ni copias): quitar una imagen solo quita la referencia.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

import config
from core import microsoft365, remote_store
from webapp.services import cartelera_birthdays_source as bd_source

MAX_UPLOAD_BYTES = 10 * 1024 * 1024        # original recibido
MAX_PIXELS = 40_000_000                    # ancho × alto del original (también corta bombas de descompresión)
MIN_SIDE = 32
DISPLAY_MAX, THUMB_MAX = 1920, 480
FORMATS = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"), "WEBP": ("webp", "image/webp")}
SCOPES = ("novedades", "galeria", "comunidad")
SP_FOLDERS = {"novedades": "Novedades", "galeria": "Galería"}   # Comunidad no va a SharePoint (contenido nativo del Hub)
_ID = re.compile(r"^[0-9a-f]{32}$")
_lock = threading.Lock()

Image.MAX_IMAGE_PIXELS = MAX_PIXELS
MEDIA_REL = "human-capital/cartelera/media"
remote_store.register_lazy_dir(MEDIA_REL)      # el arranque no baja el histórico multimedia: cada archivo se recupera al pedirlo


class MediaError(ValueError):
    """Error presentable al usuario (validación o almacenamiento)."""


def valid_id(media_id: str) -> bool:
    return bool(_ID.match(media_id or ""))


def _dir(media_id: str) -> Path:
    return config.BASE_DIR / MEDIA_REL / media_id


def meta(media_id: str) -> dict | None:
    if not valid_id(media_id):
        return None
    p = _dir(media_id) / "meta.json"
    if not p.exists() and not remote_store.fetch_lazy(p):                         # bajo demanda desde Spaces (sin resubir)
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def variant_bytes(media_id: str, variant: str) -> tuple[bytes, str] | None:
    """Bytes de una representación de consumo (`display`/`thumb`, WebP). Nunca el original (no se distribuye)."""
    if variant not in ("display", "thumb") or not valid_id(media_id):
        return None
    p = _dir(media_id) / f"{variant}.webp"
    if not p.exists() and not remote_store.fetch_lazy(p):
        return None
    return p.read_bytes(), "image/webp"


# ── procesamiento (Pillow) ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def _webp(img: Image.Image, max_side: int, quality: int) -> tuple[bytes, int, int]:
    out = img.copy()
    out.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)                 # conserva la proporción; nunca agranda
    buf = io.BytesIO()
    out.save(buf, format="WEBP", quality=quality, method=4)                       # sin exif/icc: metadatos descartados
    return buf.getvalue(), out.width, out.height


def process(data: bytes) -> dict:
    """Valida y procesa un original. Devuelve formato real, dimensiones y las representaciones de consumo."""
    if not data:
        raise MediaError("El archivo está vacío.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise MediaError(f"La imagen supera {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = probe.format
            probe.verify()                                                        # estructura íntegra (sin decodificar todo)
        img = Image.open(io.BytesIO(data))
        if fmt not in FORMATS:
            raise MediaError("Formato no admitido: usá JPG, PNG o WebP.")
        if getattr(img, "is_animated", False) and getattr(img, "n_frames", 1) > 1:
            raise MediaError("Las imágenes animadas no están admitidas.")
        w, h = img.size
        if w * h > MAX_PIXELS:
            raise MediaError("La imagen es demasiado grande (más de 40 megapíxeles).")
        if min(w, h) < MIN_SIDE:
            raise MediaError(f"La imagen es demasiado chica (mínimo {MIN_SIDE} px por lado).")
        img = ImageOps.exif_transpose(img)                                        # orientación real; el EXIF no se copia
        img.load()
        has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
        img = img.convert("RGBA" if has_alpha else "RGB")
    except MediaError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise MediaError("La imagen es demasiado grande (más de 40 megapíxeles).") from None
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise MediaError("El archivo no es una imagen válida (JPG, PNG o WebP).") from None
    display, dw, dh = _webp(img, DISPLAY_MAX, 82)
    thumb, tw, th = _webp(img, THUMB_MAX, 76)
    ext, mime = FORMATS[fmt]
    return {"id": hashlib.sha256(data).hexdigest()[:32], "format": fmt, "ext": ext, "mime": mime, "width": img.width, "height": img.height,
            "bytes": len(data), "display": display, "display_size": [dw, dh], "thumb": thumb, "thumb_size": [tw, th]}


# ── SharePoint (originales de Novedades y Galería) ─────────────────────────────────────────────────────────────────────────────────
def _folder_id(token: str) -> str:
    """Carpeta «Cartelera virtual»: el padre de la carpeta del Excel de Cumpleaños (misma ubicación ya acreditada; sin variables nuevas)."""
    base = f"{bd_source.GRAPH}/drives/{bd_source.DRIVE_ID}/items"
    excel = bd_source._call(microsoft365.graph_get_json, f"{base}/{bd_source.ITEM_ID}?$select=parentReference", token)
    bday_folder = (excel.get("parentReference") or {}).get("id")
    folder = bd_source._call(microsoft365.graph_get_json, f"{base}/{bday_folder}?$select=parentReference", token)
    return (folder.get("parentReference") or {}).get("id")


def _child_folder(token: str, parent_id: str, name: str) -> str:
    base = f"{bd_source.GRAPH}/drives/{bd_source.DRIVE_ID}/items"
    try:
        found = microsoft365.graph_get_json(f"{base}/{parent_id}:/{urllib.parse.quote(name)}?$select=id,folder", token)
        if found.get("folder") is not None:
            return found["id"]
    except Exception as exc:                                                      # noqa: BLE001 — 404 ⇒ se crea
        if getattr(exc, "code", None) != 404:
            raise
    created = bd_source._call(microsoft365.graph_post_json, f"{base}/{parent_id}/children", token,
                              {"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}, writing=True)
    return created["id"]


def _upload_original(token: str, scope: str, context: str, media_id: str, data: bytes, ext: str, mime: str) -> dict:
    if not token:
        raise MediaError("Para guardar la imagen en SharePoint hace falta autorizar con Microsoft.")
    parent = _folder_id(token)
    parent = _child_folder(token, parent, SP_FOLDERS[scope])
    if context:
        parent = _child_folder(token, parent, context)
    name = f"{media_id}.{ext}"
    url = f"{bd_source.GRAPH}/drives/{bd_source.DRIVE_ID}/items/{parent}:/{urllib.parse.quote(name)}:/content?@microsoft.graph.conflictBehavior=replace"
    item = bd_source._call(microsoft365.graph_put_bytes, url, token, data, content_type=mime, writing=True)
    return {"drive_id": bd_source.DRIVE_ID, "item_id": item.get("id"), "name": name, "folder": "/".join(p for p in (SP_FOLDERS[scope], context) if p),
            "web_url": item.get("webUrl", "")}


def _context_folder(scope: str, context: str) -> str:
    """Subcarpeta legible y segura: período «AAAA-MM» (Novedades) o álbum (Galería)."""
    text = re.sub(r"[^\w\- ]+", "", str(context or ""), flags=re.UNICODE).strip()[:60]
    return text


# ── alta (punto ÚNICO para los tres escenarios) ────────────────────────────────────────────────────────────────────────────────────
def ingest(data: bytes, *, scope: str, actor: str, owner: str = "", context: str = "", token: str = "", filename: str = "") -> dict:
    """Valida, procesa y persiste. Si algo falla, NO queda nada confirmado (los archivos se escriben al final, tras el original)."""
    if scope not in SCOPES:
        raise MediaError("Destino de imagen desconocido.")
    p = process(data)
    with _lock:
        existing = meta(p["id"])
        if existing and existing.get("display_size"):
            return existing                                                      # misma imagen ya procesada: referencia compartida
        record = {"id": p["id"], "scope": scope, "format": p["format"], "mime": p["mime"], "width": p["width"], "height": p["height"],
                  "bytes": p["bytes"], "display_size": p["display_size"], "thumb_size": p["thumb_size"], "uploaded_by": actor,
                  "owner": owner, "filename": str(filename or "")[:160], "created_at": datetime.now(timezone.utc).isoformat()}
        folder = _dir(p["id"])
        if scope in SP_FOLDERS and bd_source.ENABLED:
            record["original"] = {"store": "sharepoint", **_upload_original(token, scope, _context_folder(scope, context), p["id"], data, p["ext"], p["mime"])}
        else:
            # Comunidad (o un entorno sin fuente SharePoint, p. ej. el arnés DEMO): el original queda en el almacenamiento privado del Hub.
            remote_store.write_through(folder / f"original.{p['ext']}", data)
            record["original"] = {"store": "hub", "name": f"original.{p['ext']}"}
        remote_store.write_through(folder / "display.webp", p["display"])
        remote_store.write_through(folder / "thumb.webp", p["thumb"])
        remote_store.write_through(folder / "meta.json", json.dumps(record, ensure_ascii=False, indent=1))
        return record


def public_view(record: dict) -> dict:
    """Lo que el navegador necesita (sin rutas internas)."""
    mid = record["id"]
    return {"id": mid, "width": record.get("width"), "height": record.get("height"), "display": f"/cartelera/media/{mid}/display",
            "thumb": f"/cartelera/media/{mid}/thumb", "display_size": record.get("display_size"), "thumb_size": record.get("thumb_size")}
