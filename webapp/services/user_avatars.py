"""Avatares de usuario del Hub (capacidad TRANSVERSAL, no ligada a ninguna vertical).

Prioridad de la imagen efectiva: (1) foto PERSONALIZADA cargada en el Hub · (2) foto CORPORATIVA de Microsoft 365 · (3) iniciales (la
resuelve quien dibuja, sin imagen). La personalizada nunca modifica la cuenta de Microsoft 365; al quitarla vuelve la corporativa o las iniciales.

Identidad estable = el correo corporativo de la sesión de Entra ID (`sub`), normalizado; en disco se usa su hash (sin correos en rutas).
Foto corporativa SIN permisos nuevos: el token delegado del Hub ya incluye `User.Read`, que alcanza para `/me/photo` (la foto PROPIA). Cada
colaborador sincroniza la suya al usar el Hub (como mucho una vez por día); los demás ven esa copia, sin una llamada a Graph por publicación
o comentario. (Leer la foto de OTROS exigiría `User.ReadBasic.All`, que no está consentido: no se usa.)

Procesamiento: la MISMA validación del servicio multimedia (Pillow: formato real, límites, orientación, sin metadatos) + recorte cuadrado
256 px en WebP. Persistencia: `notifications/hub-profiles/<hash>/{meta.json, custom.webp, corporate.webp}` con `remote_store` (Spaces
PRIVADO; nunca URLs públicas) y recuperación BAJO DEMANDA (el arranque no baja las fotos). Cada colaborador administra solo la suya.
Ubicación = decisión de INFRAESTRUCTURA, no de pertenencia funcional (mismo criterio que Presentaciones bajo `commercial_strategy_runtime/`):
`notifications/` ya es un directorio PERSISTIDO de plataforma (default de código y valor documentado de `SPACES_PERSIST_PATHS`; el registro de
correos del Hub depende de él), así que no se agrega ninguna ruta de persistencia ni variable de entorno.
"""
from __future__ import annotations

import hashlib
import io
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageOps

import config
from core import microsoft365, remote_store
from webapp.services import cartelera_media as media     # procesamiento multimedia compartido (Pillow); no se duplica

REL = "notifications/hub-profiles"
SIZE = 256
CORPORATE_REFRESH = timedelta(hours=24)
remote_store.register_lazy_dir(REL)
_lock = threading.Lock()


def key(email: str) -> str:
    return hashlib.sha256(str(email or "").strip().lower().encode("utf-8")).hexdigest()[:32]


def _dir(email: str) -> Path:
    return config.BASE_DIR / REL / key(email)


_missing: dict[str, float] = {}            # clave → instante en que se confirmó que no hay perfil (evita consultar Spaces en cada render)
MISSING_TTL = 600


def _meta(email: str) -> dict:
    p = _dir(email) / "meta.json"
    if not p.exists():
        k, now = key(email), datetime.now(timezone.utc).timestamp()
        if now - _missing.get(k, 0) < MISSING_TTL:
            return {}
        if not remote_store.fetch_lazy(p):
            _missing[k] = now
            return {}
        _missing.pop(k, None)
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_meta(email: str, meta: dict) -> None:
    remote_store.write_through(_dir(email) / "meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
    _missing.pop(key(email), None)


def _square(data: bytes) -> bytes:
    media.process(data)                                                           # validación real compartida (lanza MediaError)
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    img = img.convert("RGBA" if img.mode in ("RGBA", "LA", "P") else "RGB")
    img = ImageOps.fit(img, (SIZE, SIZE), Image.Resampling.LANCZOS)              # avatar: recorte cuadrado centrado
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=84, method=4)                            # sin metadatos
    return buf.getvalue()


def effective(email: str) -> dict:
    """{'source': 'custom'|'corporate'|None, 'version': str} — la imagen que corresponde mostrar."""
    meta = _meta(email)
    for src in ("custom", "corporate"):
        if meta.get(src):
            return {"source": src, "version": str(meta[src])}
    return {"source": None, "version": ""}


def avatar_url(email: str) -> str:
    """URL autorizada de la imagen efectiva ('' ⇒ iniciales). Versionada: cambia cuando cambia la foto (caché del navegador segura)."""
    if not email:
        return ""
    eff = effective(email)
    return f"/perfil/avatar/{key(email)}?v={eff['version']}" if eff["source"] else ""


def image_bytes(avatar_key: str) -> bytes | None:
    """Bytes de la imagen efectiva por clave (para la ruta pública dentro del Hub)."""
    folder = config.BASE_DIR / REL / avatar_key
    meta_p = folder / "meta.json"
    if not meta_p.exists() and not remote_store.fetch_lazy(meta_p):
        return None
    try:
        meta = json.loads(meta_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for src in ("custom", "corporate"):
        if meta.get(src):
            p = folder / f"{src}.webp"
            if p.exists() or remote_store.fetch_lazy(p):
                return p.read_bytes()
    return None


def set_custom(email: str, data: bytes) -> dict:
    out = _square(data)
    with _lock:
        meta = _meta(email)
        remote_store.write_through(_dir(email) / "custom.webp", out)
        meta["custom"] = hashlib.sha256(out).hexdigest()[:12]
        _save_meta(email, meta)
    return effective(email)


def remove_custom(email: str) -> dict:
    """Quita la personalizada (deja de usarse; vuelve la corporativa o las iniciales). El archivo no se borra en esta etapa."""
    with _lock:
        meta = _meta(email)
        if meta.pop("custom", None) is not None:
            _save_meta(email, meta)
    return effective(email)


def sync_corporate(email: str, token: str, *, force: bool = False) -> dict:
    """Foto corporativa PROPIA desde Microsoft 365 (`/me/photo`, permiso `User.Read` ya consentido). Como mucho una vez por día."""
    meta = _meta(email)
    checked = meta.get("corporate_checked_at")
    if not force and checked:
        try:
            if datetime.now(timezone.utc) - datetime.fromisoformat(checked) < CORPORATE_REFRESH:
                return effective(email)
        except ValueError:
            pass
    if not token:
        return effective(email)
    try:
        data = microsoft365.graph_get_bytes("https://graph.microsoft.com/v1.0/me/photos/360x360/$value", token)
    except Exception as exc:                                                      # noqa: BLE001
        code = getattr(exc, "code", None)
        if code != 404:                                                           # sin red/token: se reintenta en la próxima visita
            return effective(email)
        data = None                                                               # la cuenta no tiene foto corporativa
    with _lock:
        meta = _meta(email)
        meta["corporate_checked_at"] = datetime.now(timezone.utc).isoformat()
        if data:
            try:
                out = _square(data)
                remote_store.write_through(_dir(email) / "corporate.webp", out)
                meta["corporate"] = hashlib.sha256(out).hexdigest()[:12]
            except media.MediaError:
                meta.pop("corporate", None)
        else:
            meta.pop("corporate", None)
        _save_meta(email, meta)
    return effective(email)
