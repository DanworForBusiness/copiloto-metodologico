"""commercial_presentations.py — Gestión Comercial → Presentaciones (V1): template gobernado, seed, instancias, validación y autorización.

ESTRATEGIA (aprobada): extracción mínima. El template Jinja (`webapp/templates/presentations/<template_id>.html`) conserva TODO el markup, CSS y JS de la presentación original; solo
`TemplateSpec.fields` (texto plano) pasa a ser contenido de la instancia. Sin renderer universal, sin bloques genéricos, sin store transversal.

TRES COSAS DISTINTAS (no se mezclan):
  * SEED (`commercial_presentations_data/<template_id>.yaml`): presentación institucional de SOLO LECTURA; solo cambia por código/deploy. Nadie la edita desde la UI/API.
  * INSTANCIA (`commercial_strategy_runtime/presentations/<uuid4>.json`): copia independiente — contenido, identidad, `owner_id` y `revision` propios; `copied_from` = procedencia.
  * TEMPLATE (`TEMPLATES`): lenguaje visual + campos permitidos + versión. Las instancias lo COMPARTEN (CSS/JS/assets externos; la instancia no los duplica).

AUTORIZACIÓN (contrato ratificado):
  * LEER / PRESENTAR por URL: `can_read_shared_user` = sesión + `auth_service.is_authorized` (ALLOWED_USERS ∪ ACADEMY_USERS). NO exige Comercial.
  * Catálogo, copia, edición: Comercial (`commercial_access.is_commercial_authorized_user`).
  * COPIAR no requiere nada más que Comercial (cualquier presentación, sin importar quién la creó). EDITAR es otra cosa: `can_edit_presentation_user` = Comercial Y no-seed Y (responsable
    de la instancia O `CONTENT_OWNER` O `APP_OWNER`). ELIMINAR: `can_delete_presentation_user` = Comercial Y no-seed Y (responsable O `APP_OWNER`). `created_by_*` (procedencia histórica) y `owner_id` (responsabilidad editable) están separados aunque inicialmente coincidan.
  * Sin variables, roles ni allowlists nuevos: se reusan `commercial_access`, `auth_service.is_authorized` y `auth_service.is_content_owner_user`.

PERSISTENCIA: el MISMO contrato que `profile_repository` (un JSON por entidad, escritura atómica, push síncrono, compensación). Vive bajo `commercial_strategy_runtime/` — ya registrado en
`config.PERSIST_PATHS` (restore/mirror_up/push_path cubren los subdirectorios) — para no tocar la infraestructura de persistencia. Es una decisión de infraestructura, no de pertenencia a Estrategia.
  * `read-only`: rechaza ANTES de tocar el disco. `local`: escritura local atómica, sin push. `remote`: push obligatorio; si no se confirma, se compensa.
  * Réplica única (ver docs/PUBLICATION_SAFE_RUNBOOK.md): el `_write_lock` es por proceso; si el número de instancias sube, revisar este módulo ANTES.

SEGURIDAD DE SALIDA: todo campo editable es texto plano, se valida (tipo, largo, sin caracteres de control) y se emite SOLO como texto de elemento con autoescape de Jinja. No hay markup, enlaces,
atributos ni URLs editables en V1; un campo en otro contexto exigiría un tipo de contexto nuevo con su propio validador y su propio test."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable
from uuid import uuid4

import yaml

import config
from core import remote_store
from core.content_registry import DEFAULT_PACK, resolve_pack_file
from webapp import access, commercial_access
from webapp.services import auth_service

_logger = logging.getLogger("danwor.commercial_presentations")

SCHEMA_VERSION = 1
_SEED_DIR = Path(__file__).parent / "commercial_presentations_data"
_PERSIST_SUBDIR = "presentations"
_RESTORE_WAIT_TIMEOUT_S = 15.0
# Logo institucional Danwor: el MISMO asset que Danwor Academy muestra a pantalla completa (variante de wordmark BLANCO, para fondo oscuro). Se referencia por `/pack-assets`; no se copia.
_INSTITUTIONAL_LOGO = "academy/assets/img/danwor-logo.png"
MAX_INSTANCES = 200                                   # tope global (V1): evita crecimiento sin control del directorio
MAX_TITLE = 120

_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
# Caracteres de control, separadores de línea/párrafo y controles bidi: ningún campo editable los admite (no son texto de negocio; los bidi permiten disfrazar el orden visual).
_FORBIDDEN_CHARS = re.compile("[\x00-\x1f\x7f  ‪-‮⁦-⁩]")


# ── Errores ────────────────────────────────────────────────────────────────────────────────────────────────────────────

class PresentationNotFound(Exception):
    """El id no existe (o no tiene el formato de una instancia / un seed registrado)."""


class PresentationUnavailable(Exception):
    """La presentación existe pero no se puede mostrar. `reason`: `unknown_template` (el template ya no está registrado), `newer_version` (la instancia usa una versión de template mayor que la
    soportada: no se adivina) o `seed` (seed ilegible o sin la forma del template)."""

    def __init__(self, message: str, reason: str = "unavailable"):
        super().__init__(message)
        self.reason = reason


class PresentationValidationError(Exception):
    """Payload rechazado — mensaje SEGURO para mostrar al usuario (nunca refleja el valor recibido)."""


class SeedIsReadOnly(Exception):
    """Los seeds institucionales no se editan desde la UI/API."""


class StaleRevision(Exception):
    def __init__(self, current_revision: int):
        super().__init__(f"Revision desactualizada: actual es {current_revision}")
        self.current_revision = current_revision


class QuotaExceeded(Exception):
    """Se alcanzó el máximo de instancias (V1)."""


class PresentationStorageError(Exception):
    """Fallo de persistencia (disco o Spaces) — nunca silenciado."""


class PresentationConfigIncomplete(PresentationStorageError):
    """El entorno no puede persistir de forma durable (modo `read-only`, o `remote` sin credenciales): determinístico, no se reintenta."""


class DurablePushFailed(PresentationStorageError):
    """El push a Spaces no se confirmó; el local ya fue compensado a la versión anterior."""


class RemoteOutcomeUnknown(PresentationStorageError):
    """Alta cuyo resultado remoto no se pudo confirmar: el archivo local ya se retiró."""


# ── Template (registro) ─────────────────────────────────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FieldSpec:
    key: str
    section: str
    label: str
    max_len: int
    multiline: bool = False       # solo gobierna si el editor usa <textarea> (la salida sigue siendo texto plano de una línea)


@dataclass(frozen=True)
class TemplateSpec:
    template_id: str
    version: int
    label: str
    jinja: str                    # relativo a webapp/templates
    assets_dir: str               # relativo a assets/ del content pack
    fields: tuple[FieldSpec, ...]
    css: str = "vanture.css"       # archivos COMPARTIDOS de assets_dir (la instancia no los duplica)
    js: str = "vanture.js"
    upgrade: Callable[[dict, int], dict] = lambda content, from_version: content   # cambios ROTOS: sube `version` y transforma el contenido (en memoria)

    @property
    def field_keys(self) -> tuple[str, ...]:
        return tuple(f.key for f in self.fields)

    def field(self, key: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.key == key), None)


_HERO, _S2, _CTA, _ALLIANCE = "Portada", "Dato de contexto", "Llamado a la acción", "Alianza"

VANTURE_FIELDS = (
    FieldSpec("hero_title_prefix", _HERO, "Título — inicio", 120),
    FieldSpec("hero_title_accent", _HERO, "Título — palabra destacada", 40),
    FieldSpec("hero_lead", _HERO, "Bajada", 400, True),
    FieldSpec("s2_stat_value", _S2, "Dato — valor", 20),
    FieldSpec("s2_stat_label", _S2, "Dato — descripción", 200, True),
    FieldSpec("cta_title_prefix", _CTA, "Título — inicio", 120),
    FieldSpec("cta_title_accent", _CTA, "Título — frase destacada", 60),
    FieldSpec("cta_lead", _CTA, "Bajada", 300, True),
    FieldSpec("cta_qr_caption", _CTA, "Texto junto al QR", 120),
    *(f for i in range(1, 5) for f in (
        FieldSpec(f"alliance_{i}_title", _ALLIANCE, f"Propuesta {i} — título", 80),
        FieldSpec(f"alliance_{i}_body", _ALLIANCE, f"Propuesta {i} — texto", 300, True),
    )),
    FieldSpec("recognition_intro", _ALLIANCE, "Reconocimientos — inicio", 300, True),
    FieldSpec("recognition_highlight", _ALLIANCE, "Reconocimientos — frase destacada", 80),
    FieldSpec("recognition_tail", _ALLIANCE, "Reconocimientos — continuación", 400, True),
)

TEMPLATES: dict[str, TemplateSpec] = {
    "vanture-bdc": TemplateSpec(
        template_id="vanture-bdc", version=1, label="Vanture · La empresa autónoma comienza con los datos",
        jinja="presentations/vanture-bdc.html", assets_dir="presentations/vanture-bdc", fields=VANTURE_FIELDS,
    ),
}


def is_seed_id(presentation_id: str) -> bool:
    return presentation_id in TEMPLATES


def is_instance_id(presentation_id: str) -> bool:
    return bool(_UUID_RE.match(presentation_id or ""))


def template_spec(template_id: str) -> TemplateSpec | None:
    return TEMPLATES.get(template_id)


# ── Seed ───────────────────────────────────────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _seed_raw(template_id: str) -> dict:
    spec = TEMPLATES[template_id]
    path = _SEED_DIR / f"{template_id}.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise PresentationUnavailable(f"Seed ilegible: {template_id}", "seed") from exc
    content = data.get("content") if isinstance(data, dict) else None
    if not isinstance(content, dict) or set(content) != set(spec.field_keys) or not all(isinstance(v, str) and v.strip() for v in content.values()):
        raise PresentationUnavailable(f"Seed sin la forma del template: {template_id}", "seed")
    return data


def _seed_view(template_id: str) -> dict:
    spec, data = TEMPLATES[template_id], _seed_raw(template_id)
    return {
        "presentation_id": template_id, "template_id": template_id, "template_version": spec.version,
        "title": str(data.get("title") or spec.label), "revision": 1, "is_seed": True,
        "created_by_id": None, "created_by_label": "Danwor (institucional)", "owner_id": None, "copied_from": None,
        "created_at": None, "updated_at": None, "updated_by_id": None, "updated_by_label": None,
        "content": copy.deepcopy(data["content"]),
    }


# ── Validación ─────────────────────────────────────────────────────────────────────────────────────────────────────────

def _text(value, *, max_len: int, label: str) -> str:
    if not isinstance(value, str):
        raise PresentationValidationError(f"{label}: se esperaba texto")
    cleaned = value.strip()
    if not cleaned:
        raise PresentationValidationError(f"{label}: no puede estar vacío")
    if len(cleaned) > max_len:
        raise PresentationValidationError(f"{label}: excede el máximo de {max_len} caracteres")
    if _FORBIDDEN_CHARS.search(cleaned):
        raise PresentationValidationError(f"{label}: contiene caracteres no permitidos")
    return cleaned


def validate_title(value) -> str:
    return _text(value, max_len=MAX_TITLE, label="Título")


def validate_fields(template_id: str, fields) -> dict:
    """Valida un SUBCONJUNTO de campos editables. Rechazo atómico: ante cualquier problema no se devuelve nada. Una clave que no es de la spec se rechaza (no se descarta en silencio) y su
    nombre no se refleja en el mensaje."""
    spec = TEMPLATES.get(template_id)
    if spec is None:
        raise PresentationUnavailable("Template no disponible", "unknown_template")
    if not isinstance(fields, dict) or not fields:
        raise PresentationValidationError("No hay campos para guardar")
    out: dict[str, str] = {}
    for key, value in fields.items():
        field = spec.field(key) if isinstance(key, str) else None
        if field is None:
            raise PresentationValidationError("Se envió un campo que no es editable")
        out[key] = _text(value, max_len=field.max_len, label=field.label)
    return out


def full_content(template_id: str, content: dict | None) -> dict:
    """Contenido COMPLETO para renderizar: solo claves de la spec; las que falten (campo agregado después) caen al valor del seed. Nunca claves ajenas."""
    spec = TEMPLATES[template_id]
    seed = _seed_raw(template_id)["content"]
    content = content or {}
    return {k: (content[k] if isinstance(content.get(k), str) and content[k].strip() else seed[k]) for k in spec.field_keys}


# ── Persistencia (contrato de profile_repository) ─────────────────────────────────────────────────────────────────────────

_write_lock = threading.Lock()


def _dir() -> Path:
    return config.COMMERCIAL_STRATEGY_RUNTIME_DIR / _PERSIST_SUBDIR


def _path(presentation_id: str) -> Path:
    return _dir() / f"{presentation_id}.json"


def _persistence_mode() -> str:
    return str(getattr(config, "PERSISTENCE_MODE", "local"))


def _local_only() -> bool:
    return _persistence_mode() == "local"


def _require_writable_mode() -> None:
    """`read-only` no habilita escrituras de negocio: se rechaza ANTES de tocar el disco."""
    if _persistence_mode() == "read-only":
        raise PresentationConfigIncomplete("Las presentaciones están en modo de solo lectura en este entorno: no se pueden guardar cambios.")


def _ensure_restored() -> None:
    if not remote_store.enabled():
        return
    remote_store.await_restore(timeout=_RESTORE_WAIT_TIMEOUT_S)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError as exc:
        raise PresentationStorageError(f"No se pudo escribir la presentación en disco: {exc}") from exc
    finally:
        if tmp_name is not None:
            try:
                os.remove(tmp_name)
            except OSError:
                pass


def _cleanup_failed_write(path: Path) -> None:
    """Retira un alta cuya durabilidad remota nunca se confirmó (que ni `list()` ni un `mirror_up()` posterior la completen a escondidas). Best-effort: nunca enmascara el error real."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        _logger.warning("commercial_presentations.cleanup_failed reason=%s", type(exc).__name__)
    try:
        remote_store.delete_path(path)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("commercial_presentations.remote_cleanup_failed reason=%s", type(exc).__name__)


def _restore_previous(path: Path, previous_bytes: bytes) -> None:
    try:
        path.write_bytes(previous_bytes)
    except OSError as exc:
        _logger.error("commercial_presentations.rollback_failed reason=%s", type(exc).__name__)


def _instance_view(raw: dict) -> dict:
    """Instancia persistida → vista uniforme (misma forma que el seed). El contenido se normaliza contra la spec VIGENTE; una versión de template mayor que la soportada no se adivina."""
    template_id = str(raw.get("template_id") or "")
    spec = TEMPLATES.get(template_id)
    if spec is None:
        raise PresentationUnavailable("Template no disponible", "unknown_template")
    stored_version = int(raw.get("template_version") or 1)
    if stored_version > spec.version:
        raise PresentationUnavailable("La presentación usa una versión de template más nueva que la soportada", "newer_version")
    content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
    if stored_version < spec.version:
        content = spec.upgrade(dict(content), stored_version)                   # en memoria; se persiste recién en el próximo guardado
    return {
        "presentation_id": raw["presentation_id"], "template_id": template_id, "template_version": spec.version,
        "title": str(raw.get("title") or spec.label), "revision": int(raw.get("revision") or 1), "is_seed": False,
        "created_by_id": raw.get("created_by_id"), "created_by_label": raw.get("created_by_label"),
        "owner_id": raw.get("owner_id"), "copied_from": raw.get("copied_from"),
        "created_at": raw.get("created_at"), "updated_at": raw.get("updated_at"),
        "updated_by_id": raw.get("updated_by_id"), "updated_by_label": raw.get("updated_by_label"),
        "content": full_content(template_id, content),
    }


def _read_raw(presentation_id: str) -> dict:
    _ensure_restored()
    try:
        raw = json.loads(_path(presentation_id).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise PresentationNotFound(presentation_id) from exc
    except ValueError as exc:                                                    # archivo corrupto: se trata como ausente (fail-soft, mismo criterio que profile_repository)
        raise PresentationNotFound(presentation_id) from exc
    except OSError as exc:
        raise PresentationStorageError(f"No se pudo leer la presentación: {exc}") from exc
    if not isinstance(raw, dict) or raw.get("presentation_id") != presentation_id:
        raise PresentationNotFound(presentation_id)
    return raw


def get(presentation_id: str) -> dict:
    """Vista de un seed o de una instancia. `PresentationNotFound` si el id no tiene un formato válido (no se toca el filesystem con ids arbitrarios)."""
    if is_seed_id(presentation_id):
        return _seed_view(presentation_id)
    if not is_instance_id(presentation_id):
        raise PresentationNotFound(presentation_id)
    return _instance_view(_read_raw(presentation_id))


def _instance_ids() -> list[str]:
    _ensure_restored()
    directory = _dir()
    if not directory.exists():
        return []
    try:
        return sorted(p.stem for p in directory.iterdir() if p.is_file() and p.suffix == ".json" and is_instance_id(p.stem))
    except OSError as exc:
        raise PresentationStorageError(f"No se pudo listar las presentaciones: {exc}") from exc


def list_presentations() -> list[dict]:
    """Seeds primero (orden del registro) y luego instancias por edición descendente. Una instancia ilegible o con template no disponible no rompe el listado: aparece marcada `available=False`."""
    items = []
    for template_id in TEMPLATES:
        try:
            view = _seed_view(template_id)
            items.append({**view, "available": True})
        except PresentationUnavailable:
            _logger.error("commercial_presentations.seed_unavailable template=%s", template_id)
    instances = []
    for pid in _instance_ids():
        try:
            instances.append({**_instance_view(_read_raw(pid)), "available": True})
        except PresentationUnavailable:
            try:
                raw = _read_raw(pid)
                instances.append({
                    "presentation_id": pid, "template_id": str(raw.get("template_id") or ""), "template_version": raw.get("template_version"),
                    "title": str(raw.get("title") or pid), "revision": int(raw.get("revision") or 1), "is_seed": False,
                    "created_by_label": raw.get("created_by_label"), "owner_id": raw.get("owner_id"), "copied_from": raw.get("copied_from"),
                    "updated_at": raw.get("updated_at"), "available": False, "content": {},
                })
            except (PresentationNotFound, PresentationStorageError):
                continue
        except PresentationNotFound:
            continue
    instances.sort(key=lambda v: str(v.get("updated_at") or ""), reverse=True)
    return items + instances


def copy_presentation(source_id: str, *, title, owner_id: str, owner_label: str) -> dict:
    """Copia INDEPENDIENTE de un seed o de una instancia: uuid4 nuevo, creador y responsable = quien copia, `copied_from` = procedencia, contenido snapshot (las claves de la spec, sin
    referencias al origen). Cualquier usuario de Comercial puede copiar cualquier presentación: la autorización la aplica el router; acá no se decide quién."""
    clean_title = validate_title(title)
    source = get(source_id)                                                      # NotFound / Unavailable propagan
    spec = TEMPLATES[source["template_id"]]
    owner_id = str(owner_id or "").strip().lower()
    if not owner_id:
        raise PresentationValidationError("No se pudo identificar al usuario")
    now = _now_iso()
    new_id = str(uuid4())
    payload = {
        "schema_version": SCHEMA_VERSION, "presentation_id": new_id, "template_id": spec.template_id, "template_version": spec.version,
        "title": clean_title, "revision": 1,
        "created_by_id": owner_id, "created_by_label": str(owner_label or owner_id), "owner_id": owner_id,
        "copied_from": {"presentation_id": source["presentation_id"], "revision": source["revision"], "template_version": source["template_version"], "at": now},
        "created_at": now, "updated_at": now, "updated_by_id": owner_id, "updated_by_label": str(owner_label or owner_id),
        "content": {k: source["content"][k] for k in spec.field_keys},
    }
    _require_writable_mode()
    path = _path(new_id)
    with _write_lock:
        if len(_instance_ids()) >= MAX_INSTANCES:
            raise QuotaExceeded()
        _write_atomic(path, payload)
        if _local_only():
            return _instance_view(payload)
        if remote_store.push_path(path):
            return _instance_view(payload)
        if not remote_store.enabled():                                           # determinístico: ni este intento ni un reintento pueden confirmar en este entorno
            _cleanup_failed_write(path)
            raise PresentationConfigIncomplete("Las presentaciones todavía no están configuradas para este entorno.")
        if remote_store.push_path(path):
            return _instance_view(payload)
        _cleanup_failed_write(path)
        raise RemoteOutcomeUnknown("No pudimos confirmar si la copia se guardó. Antes de reintentar, revisá el catálogo — si ya aparece, no la vuelvas a crear.")


def authorization_view(presentation_id: str) -> dict:
    """Lo mínimo que hace falta para AUTORIZAR sobre una instancia (`is_seed`, `owner_id`), sin exigir que su template esté disponible: una instancia de un template desconocido o más nuevo
    igual se puede eliminar. NotFound si no existe."""
    if is_seed_id(presentation_id):
        return {"is_seed": True, "owner_id": ""}
    if not is_instance_id(presentation_id):
        raise PresentationNotFound(presentation_id)
    return {"is_seed": False, "owner_id": str(_read_raw(presentation_id).get("owner_id") or "")}


def delete_presentation(presentation_id: str) -> None:
    """ELIMINA una instancia (borrado real, no recuperable desde el Hub). El seed no se elimina nunca. Quién puede hacerlo lo decide el router (`can_delete_presentation_user`). En `remote` el borrado
    en Spaces es obligatorio: si no se confirma tras un reintento, el archivo local se restaura (503) — nunca queda borrada en un lado y viva en el otro."""
    if is_seed_id(presentation_id):
        raise SeedIsReadOnly()
    if not is_instance_id(presentation_id):
        raise PresentationNotFound(presentation_id)
    _require_writable_mode()
    with _write_lock:
        _read_raw(presentation_id)                                               # NotFound si no existe
        path = _path(presentation_id)
        previous_bytes = path.read_bytes()
        try:
            path.unlink()
        except OSError as exc:
            raise PresentationStorageError(f"No se pudo eliminar la presentación del disco: {exc}") from exc
        if _local_only():
            return
        if not remote_store.enabled():                                           # determinístico: ni este intento ni un reintento pueden confirmar en este entorno
            _restore_previous(path, previous_bytes)
            raise PresentationConfigIncomplete("Las presentaciones todavía no están configuradas para este entorno.")
        if remote_store.delete_path(path) or remote_store.delete_path(path):
            return
        _restore_previous(path, previous_bytes)
        _logger.error("commercial_presentations.delete: el borrado en Spaces no se confirmó — local restaurado (id=%s)", presentation_id)
        raise DurablePushFailed()


def save(presentation_id: str, fields, *, expected_revision: int, updated_by_id: str, updated_by_label: str) -> dict:
    """Guarda un SUBCONJUNTO de campos de UNA instancia bajo revisión optimista. El seed no se edita nunca. Si el push a Spaces no se confirma, el local vuelve a la versión anterior (503)."""
    if is_seed_id(presentation_id):
        raise SeedIsReadOnly()
    if not is_instance_id(presentation_id):
        raise PresentationNotFound(presentation_id)
    _require_writable_mode()
    with _write_lock:
        raw = _read_raw(presentation_id)
        view = _instance_view(raw)                                               # Unavailable si el template no existe o es de una versión mayor
        if view["revision"] != expected_revision:
            raise StaleRevision(view["revision"])
        clean = validate_fields(view["template_id"], fields)
        candidate = {**raw, "schema_version": SCHEMA_VERSION, "template_version": view["template_version"],
                     "content": {**view["content"], **clean}, "revision": expected_revision + 1,
                     "updated_at": _now_iso(), "updated_by_id": str(updated_by_id or ""), "updated_by_label": str(updated_by_label or updated_by_id or "")}
        path = _path(presentation_id)
        previous_bytes = path.read_bytes()
        _write_atomic(path, candidate)
        if _local_only():
            return _instance_view(candidate)
        if remote_store.push_path(path):
            return _instance_view(candidate)
        _restore_previous(path, previous_bytes)
        if not remote_store.enabled():
            raise PresentationConfigIncomplete("Las presentaciones todavía no están configuradas para este entorno.")
        _logger.error("commercial_presentations.save: push síncrono a Spaces falló — local revertido (id=%s)", presentation_id)
        raise DurablePushFailed()


# ── Autorización ───────────────────────────────────────────────────────────────────────────────────────────────────────

def _email(user: dict | None) -> str:
    return str((user or {}).get("sub") or "").strip().lower()


def can_read_shared_user(user: dict | None) -> bool:
    """LEER / PRESENTAR por URL: cualquier usuario admitido al Hub (ALLOWED_USERS ∪ ACADEMY_USERS). NO exige Comercial. Se reevalúa en cada request (la cookie solo prueba que el login fue válido)."""
    email = _email(user)
    return bool(email) and auth_service.is_authorized(email)


def can_edit_presentation_user(user: dict | None, presentation: dict) -> bool:
    """EDITAR: Comercial Y no-seed Y (responsable de la instancia O `CONTENT_OWNER` O `APP_OWNER`). Copiar NO implica poder editar."""
    if presentation.get("is_seed"):
        return False
    if not commercial_access.is_commercial_authorized_user(user):
        return False
    email = _email(user)
    owner = str(presentation.get("owner_id") or "").strip().lower()
    return bool(email) and ((bool(owner) and email == owner) or auth_service.is_content_owner_user(email) or access.is_owner_user(user))


def can_delete_presentation_user(user: dict | None, presentation: dict) -> bool:
    """ELIMINAR: Comercial Y no-seed Y (responsable de la instancia O `APP_OWNER`). Más estricto que editar a propósito: `CONTENT_OWNER` edita, pero no elimina lo ajeno."""
    if presentation.get("is_seed"):
        return False
    if not commercial_access.is_commercial_authorized_user(user):
        return False
    email = _email(user)
    owner = str(presentation.get("owner_id") or "").strip().lower()
    return bool(email) and ((bool(owner) and email == owner) or access.is_owner_user(user))


# ── Render ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def _asset_hash(template_id: str, name: str) -> str:
    path = resolve_pack_file("assets", f"{TEMPLATES[template_id].assets_dir}/{name}", DEFAULT_PACK)
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


def render_context(presentation: dict, *, embed: bool = False) -> dict:
    """Contexto del documento inmersivo: `c` = contenido completo (solo claves de la spec), URLs de assets COMPARTIDAS (idénticas para toda instancia del template) con cache-busting por contenido."""
    template_id = presentation["template_id"]
    spec = TEMPLATES[template_id]
    asset_base = f"/pack-assets/{DEFAULT_PACK}/{spec.assets_dir}"
    return {
        "c": full_content(template_id, presentation.get("content")),
        "asset_base": asset_base,
        "institutional_logo_url": f"/pack-assets/{DEFAULT_PACK}/{_INSTITUTIONAL_LOGO}",
        "css_url": f"{asset_base}/{spec.css}?v={_asset_hash(template_id, spec.css)}",
        "js_url": f"{asset_base}/{spec.js}?v={_asset_hash(template_id, spec.js)}",
        "embed": bool(embed),
    }
