"""cv_history_drafts.py — BORRADORES por excepción de «Históricos sin validación» (Entrega B): el owner revisa y completa de a poco los datos de un CV histórico y guarda su trabajo SIN incorporarlo al Banco.

Un borrador NO es un perfil: no crea, vincula ni modifica perfiles ni CVs, no toca el staging, el manifiesto (`run.json`), el libro de ejecución ni SharePoint, y no aparece en búsquedas, entrevistas, selectores ni indicadores (vive en
`human-capital/import-history/<run_id>/drafts/<content_key>.json`, separado de `human-capital/profiles/`; esos listados leen solo los perfiles y las postulaciones). Se persiste con el mecanismo del lote (`cv_history_manifest`:
escritura atómica + write-through a Spaces).

Qué guarda: nombre, apellido, email, teléfono y observaciones; `revision` (control optimista), autor y fechas de creación y de última modificación, e HISTORIAL de cambios (por revisión: quién, cuándo, qué campos y su valor anterior;
acotado a las últimas `MAX_HISTORY`). Contiene datos personales: nada de esto se escribe en logs (los avisos llevan solo códigos).

Concurrencia: `save` compara la revisión que el formulario cargó con la vigente DENTRO del mismo candado que la escritura —el del hilo y uno entre procesos del mismo disco (`cv_history_manifest._interprocess_lock`)—; si no coinciden
devuelve el conflicto (con lo guardado) y no escribe nada: una revisión obsoleta nunca pisa a una más nueva, ni entre hilos ni entre workers. Si otro proceso retiene el candado más de 10 s, no se guarda nada y se pide reintentar. Errores parciales de persistencia
(Spaces no confirma): se restaura lo anterior y se informa que NO se guardó, así que reintentar con la misma revisión funciona y el formulario conserva lo escrito.

Ciclo de vida y retención: sin borrado automático y sin conservación indefinida por diseño — la política está pendiente de definición del owner. Mientras exista un borrador de una excepción SIN RESOLVER no se puede eliminar el área temporal
(`cv_history_import_exec.active_drafts` + `cv_history_pilot.discard_staging`): sin ese staging se perdería el contexto (evidencia, «Ver CV original») del que depende. Resuelta o descartada la excepción el borrador deja de ser
«activo»: el descarte ya no se bloquea y el borrador se CONSERVA intacto (nada lo borra). Pasado el descarte no se aceptan borradores nuevos."""
from __future__ import annotations

import logging
import re

from webapp.services import cv_history_import_exec as ex, cv_history_manifest as manifest

_log = logging.getLogger(__name__)

FIELDS = ("first_name", "last_name", "email", "phone", "notes")
LABELS = {"first_name": "nombre", "last_name": "apellido", "email": "email", "phone": "teléfono", "notes": "observaciones"}
LIMITS = {"first_name": 60, "last_name": 60, "email": 254, "phone": 40, "notes": 2000}
MAX_HISTORY = 50
EDITABLE_STATUSES = ex.UNRESOLVED_STATUSES                                              # un borrador solo existe mientras la excepción sigue sin resolver
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE = re.compile(r"^[\d+()\-.\s]+$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class DraftError(ValueError):
    """Datos inválidos o acción no aplicable; el mensaje es para el owner (sin datos personales)."""


class DraftConflict(Exception):
    """Otro guardado se adelantó: `current` es el borrador vigente (None si no existe)."""

    def __init__(self, current: dict | None):
        super().__init__("conflicto de revisión")
        self.current = current


def clean(raw: dict) -> dict[str, str]:
    """Normaliza lo escrito: sin espacios sobrantes; los campos de una línea colapsan espacios; las observaciones conservan sus saltos de línea."""
    out: dict[str, str] = {}
    for name in FIELDS:
        value = str(raw.get(name) or "")
        if name == "notes":
            value = "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n")).strip()
        else:
            value = " ".join(value.split())
        out[name] = value
    return out


def validate(fields: dict[str, str]) -> None:
    for name in FIELDS:
        value = fields[name]
        if _CONTROL.search(value):
            raise DraftError(f"El campo «{LABELS[name]}» tiene caracteres no válidos.")
        if len(value) > LIMITS[name]:
            raise DraftError(f"El campo «{LABELS[name]}» supera los {LIMITS[name]} caracteres.")
    if fields["email"] and not _EMAIL.match(fields["email"]):
        raise DraftError("El email no parece válido (por ejemplo nombre@dominio.com). Podés dejarlo vacío y completarlo después.")
    if fields["phone"] and not (_PHONE.match(fields["phone"]) and sum(ch.isdigit() for ch in fields["phone"]) >= 6):
        raise DraftError("El teléfono debe tener al menos 6 dígitos (se aceptan +, espacios, guiones y paréntesis). Podés dejarlo vacío y completarlo después.")


def _parse_revision(value) -> int:
    text = str(value if value is not None else "").strip() or "0"
    if not text.isdigit() or len(text) > 9:
        raise DraftError("La revisión del formulario no es válida: recargá la página y volvé a intentar.")
    return int(text)


def get(run_id: str, key: str) -> dict | None:
    return manifest.get_draft(run_id, key)


def count(run_id: str) -> int:
    return manifest.count_drafts(run_id)


def save(run_id: str, key: str, raw: dict, *, actor: str, expected_revision) -> dict:
    """Guarda (crea o actualiza) el borrador de UNA excepción. Devuelve `{doc, created, changed}`. Lanza `DraftError` (datos o acción inválidos), `DraftConflict` (la revisión cargada ya no es la vigente) o `ManifestError`
    (persistencia no confirmada; no se guardó nada)."""
    run = manifest.get_run(run_id)                                                        # lote inexistente: ManifestNotFoundError (la ruta lo traduce a 404)
    if (run.get("staging") or {}).get("state") != "PRESENT":
        raise DraftError("Los datos temporales del lote ya no están disponibles (o se están eliminando): no se pueden guardar borradores nuevos.")
    row = ex.exception_row(run_id, key)
    if row is None or row["status"] not in EDITABLE_STATUSES:
        raise DraftError("Este contenido ya no es una excepción por resolver: no se puede guardar un borrador.")
    if ex._accepted_committed(manifest.get_ledger(run_id, key)):                           # el estado «Aceptado» lo dice el libro: ese borrador queda inmutable
        raise DraftError("Este borrador ya fue aceptado (la aceptación está en curso o terminada): no se puede modificar.")
    fields = clean(raw)
    validate(fields)
    expected = _parse_revision(expected_revision)
    outcome = {"created": False, "changed": True}

    def build(previous: dict | None) -> dict | None:
        current = int((previous or {}).get("revision") or 0)
        if expected != current:
            raise DraftConflict(previous)
        before = (previous or {}).get("fields") or {}
        changed = [name for name in FIELDS if fields[name] != (before.get(name) or "")]
        if previous is None and not any(fields.values()):
            raise DraftError("No hay nada para guardar: completá al menos un dato.")
        if previous is not None and not changed:
            outcome["changed"] = False
            return None
        outcome["created"] = previous is None
        now = manifest.now_iso()
        history = list((previous or {}).get("history") or [])
        history.append({"revision": current + 1, "at": now, "by": actor, "changed": changed, "before": {name: before.get(name) or "" for name in changed}})
        dropped = int((previous or {}).get("history_dropped") or 0) + max(len(history) - MAX_HISTORY, 0)
        return {"revision": current + 1, "created_at": (previous or {}).get("created_at") or now, "created_by": (previous or {}).get("created_by") or actor, "updated_at": now, "updated_by": actor,
                "fields": fields, "history": history[-MAX_HISTORY:], "history_dropped": dropped}

    try:
        doc = manifest.update_draft(run_id, key, build)
    except manifest.ManifestBusyError:
        raise DraftError("Otro proceso está guardando este borrador en este momento. No se guardó nada: reintentá en unos segundos.") from None
    except manifest.ManifestError as exc:
        if not isinstance(exc, manifest.ManifestNotFoundError):
            _log.warning("cv_history_drafts: guardado no confirmado run=%s key=%s causa=%s", run_id, key, type(exc).__name__)
        raise
    _log.info("cv_history_drafts: run=%s key=%s revision=%s creado=%s cambios=%s", run_id, key, (doc or {}).get("revision"), outcome["created"], outcome["changed"])
    return {"doc": doc, "created": outcome["created"], "changed": outcome["changed"]}


# ═══════════════════ vista para la pantalla ═══════════════════
def view(doc: dict | None) -> dict | None:
    """Borrador listo para la plantilla: valores, revisión, autoría e historial (lo más nuevo primero, con los campos en palabras)."""
    if not doc:
        return None
    fields = {name: (doc.get("fields") or {}).get(name) or "" for name in FIELDS}
    history = [{"revision": h.get("revision"), "at": _stamp(h.get("at")), "by": h.get("by") or "—", "changed": [LABELS.get(c, c) for c in h.get("changed") or []],
                "before": [(LABELS.get(c, c), (h.get("before") or {}).get(c) or "") for c in h.get("changed") or []]} for h in reversed(doc.get("history") or [])]
    return {"revision": int(doc.get("revision") or 0), "fields": fields, "created_by": doc.get("created_by") or "—", "created_at": _stamp(doc.get("created_at")), "updated_by": doc.get("updated_by") or "—",
            "updated_at": _stamp(doc.get("updated_at")), "name": f"{fields['first_name']} {fields['last_name']}".strip(), "history": history, "history_dropped": int(doc.get("history_dropped") or 0)}


def _stamp(value) -> str:
    return f"{str(value or '')[:16].replace('T', ' ')} UTC".strip() if value else "—"


def conflict_view(current: dict | None, submitted: dict[str, str]) -> dict:
    """Lo que se le muestra al owner cuando otro guardado se adelantó: la revisión vigente, quién y cuándo, y los campos que difieren de lo que él escribió."""
    saved = view(current)
    values = (saved or {}).get("fields") or {name: "" for name in FIELDS}
    return {"revision": (saved or {}).get("revision", 0), "by": (saved or {}).get("updated_by", "—"), "at": (saved or {}).get("updated_at", "—"),
            "differs": [(LABELS[name], values[name]) for name in FIELDS if values[name] != submitted.get(name, "")], "deleted": saved is None}


def accepted_drafts(run_id: str) -> list[dict]:
    """SOLO LECTURA y sin datos personales: los borradores cuya aceptación TERMINÓ según el libro (única fuente de verdad), con la revisión aceptada, la actual y a qué perfil quedó asociado el CV. Es la base de una futura política de
    retención y purga de borradores aceptados; no borra ni modifica nada."""
    ledgers = manifest.list_ledgers(run_id)
    out = []
    for key in manifest.draft_keys(run_id):
        ledger = ledgers.get(key) or {}
        accepted = ledger.get("accepted")
        if accepted and ledger.get("state") == ex.DONE:
            draft = manifest.get_draft(run_id, key) or {}
            out.append({"content_key": key, "op": accepted.get("op"), "profile_id": accepted.get("profile_id"), "accepted_by": accepted.get("by"), "accepted_at": accepted.get("at"), "accepted_revision": accepted.get("draft_revision"),
                        "draft_revision": draft.get("revision"), "unchanged_since_acceptance": draft.get("revision") == accepted.get("draft_revision")})
    return out


def attach(run_id: str, rows: list[dict | None]) -> list[dict | None]:
    """Agrega `draft` (vista o None) a cada fila de la página. Un borrador ilegible no rompe la pantalla (queda como sin borrador y se registra solo un código)."""
    for r in rows:
        if not r:
            continue
        try:
            r["draft"] = view(manifest.get_draft(run_id, r["content_key"]))
        except manifest.ManifestError:
            _log.warning("cv_history_drafts: borrador ilegible run=%s key=%s", run_id, r.get("content_key"))
            r["draft"] = None
    return rows
