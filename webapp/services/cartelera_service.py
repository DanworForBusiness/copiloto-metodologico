"""cartelera_service.py — reglas de negocio de CARTELERA VIRTUAL (Capital Humano): ediciones mensuales, secciones y contenidos, proyectos del
mes, programación y publicación, cumpleaños y contenido institucional.

Decisiones ratificadas que gobiernan este módulo (ver `_handoffs/CARTELERA_VIRTUAL_DECISIONES_20261125.md`, sección R):
  - F1  administra quien está admitido a Capital Humano (MISMA frontera que Staffing); sin allowlists ni roles nuevos.
  - F3  publicación inmediata o programada; la VISIBILIDAD se resuelve SIEMPRE acá (servidor), con la hora corporativa de Buenos Aires y
        persistencia en UTC.
  - F6  la publicación EFECTIVA (inmediata o al vencer la programación) dispara el aviso por correo del servicio existente del Hub, con clave
        idempotente; el disparo no depende de que alguien visite la cartelera (lo ejecuta el programador del proceso, ver `run_due`).
  - F8  el proyecto del mes se identifica desde las fuentes gobernadas de Operaciones (id + nombre de proyecto y cliente); el texto editorial
        es libre. No se guardan datos comerciales, contractuales ni financieros.
  - F4  cumpleaños propios de la Cartelera: día y mes (nunca el año), autorización de publicación y ocultamiento.
  - F5  recursos documentales (imágenes, flyers, galería, organigrama): SharePoint es la fuente de verdad. Hasta verificar el acceso a la carpeta
        NO se simula esa integración: los campos de recursos se muestran como pendientes.
"""
from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone

from webapp.services import cartelera_repository as repo
from webapp.services.busqueda_repository import BUSINESS_TZ

MONTHS = ("Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre")

# Secciones de cada edición (orden por defecto). `kind`: general (ítems con título, texto y enlace) · projects (proyectos del mes) ·
# birthdays (calendario automático, sin ítems) · links (ítems + accesos directos globales, siempre al final).
SECTIONS = (
    {"key": "comunicados", "title": "Comunicados", "kind": "general"},
    {"key": "proyectos", "title": "Novedades de proyectos", "kind": "projects"},
    {"key": "bienestar", "title": "Bienestar y calidad de vida", "kind": "general"},
    {"key": "aprendizaje", "title": "Aprendizaje e innovación", "kind": "general"},
    {"key": "cumpleanos", "title": "Cumpleaños del mes", "kind": "birthdays"},
    {"key": "otros", "title": "Otros contenidos de interés", "kind": "general"},
    {"key": "beneficios", "title": "Beneficios e información útil", "kind": "links"},
)
SECTION_BY_KEY = {s["key"]: s for s in SECTIONS}

STATE_LABELS = {"draft": "Borrador", "scheduled": "Programada", "published": "Publicada", "withdrawn": "Retirada"}
GENERAL_FIELDS = ("title", "body", "link", "link_label")
PROJECT_FIELDS = ("project_id", "project_name", "client_name", "kind", "service", "tech", "body", "team", "thanks", "link")
_MONTH = re.compile(r"^(20\d{2})-(0[1-9]|1[0-2])$")
_URL = re.compile(r"^https://[^\s<>\"]+$")
MAX_TEXT = 4000


class CarteleraValidationError(ValueError):
    """Dato inválido: el mensaje es apto para mostrar."""


# ── tiempo ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def local_input_to_utc(value: str) -> datetime:
    """`datetime-local` del formulario (hora de Buenos Aires) → instante UTC."""
    try:
        naive = datetime.fromisoformat(str(value).strip())
    except ValueError:
        raise CarteleraValidationError("Ingresá una fecha y hora de publicación válidas.") from None
    return naive.replace(tzinfo=BUSINESS_TZ).astimezone(timezone.utc)


def fmt_local(value: str | datetime | None, *, with_time: bool = True) -> str:
    dt = parse_utc(value) if isinstance(value, str) or value is None else value
    if dt is None:
        return ""
    local = dt.astimezone(BUSINESS_TZ)
    return local.strftime("%d/%m/%Y %H:%M" if with_time else "%d/%m/%Y")


def to_local_input(value: str | None) -> str:
    dt = parse_utc(value)
    return dt.astimezone(BUSINESS_TZ).strftime("%Y-%m-%dT%H:%M") if dt else ""


def month_key(d: date | None = None) -> str:
    d = d or now_utc().astimezone(BUSINESS_TZ).date()
    return f"{d.year}-{d.month:02d}"


def month_label(key: str) -> str:
    m = _MONTH.match(key or "")
    return f"{MONTHS[int(m.group(2)) - 1]} {m.group(1)}" if m else key


def shift_month(key: str, delta: int) -> str:
    y, m = int(key[:4]), int(key[5:7])
    total = y * 12 + (m - 1) + delta
    return f"{total // 12}-{total % 12 + 1:02d}"


# ── validación de texto ───────────────────────────────────────────────────────────────────────────────────────────────────────────
def _text(value, *, limit: int = MAX_TEXT) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if len(text) > limit:
        raise CarteleraValidationError(f"Un texto supera el máximo de {limit} caracteres.")
    return text


def _url(value) -> str:
    url = str(value or "").strip()
    if url and not _URL.match(url):
        raise CarteleraValidationError("Los enlaces tienen que empezar con https://")
    return url


# ── ediciones ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def effective_state(edition: dict, now: datetime | None = None) -> str:
    """Estado tal como lo ve el lector: una programada vencida YA está publicada (aunque el programador todavía no la haya consolidado)."""
    state = edition.get("state", "draft")
    if state == "scheduled":
        due = parse_utc(edition.get("publish_at"))
        if due and due <= (now or now_utc()):
            return "published"
    return state


def is_visible(edition: dict, now: datetime | None = None) -> bool:
    return effective_state(edition, now) == "published"


def _blank_sections() -> list[dict]:
    return [{"key": s["key"], "items": []} for s in SECTIONS]


def normalize_sections(sections) -> list[dict]:
    """Orden guardado + secciones nuevas al final de las generales; «links» siempre última; claves desconocidas descartadas."""
    seen, out = set(), []
    for sec in sections or []:
        key = (sec or {}).get("key")
        if key in SECTION_BY_KEY and key not in seen:
            seen.add(key)
            out.append({"key": key, "items": list(sec.get("items") or []), "wide": bool(sec.get("wide"))})
    for s in SECTIONS:
        if s["key"] not in seen:
            out.append({"key": s["key"], "items": [], "wide": False})
    out.sort(key=lambda s: SECTION_BY_KEY[s["key"]]["kind"] == "links")
    return out


def list_editions() -> list[dict]:
    return sorted(repo.list_all("editions"), key=lambda e: e.get("id", ""), reverse=True)


def get_edition(month: str) -> dict | None:
    edition = repo.get("editions", month)
    if edition:
        edition["sections"] = normalize_sections(edition.get("sections"))
    return edition


EDIT_ACTION = "Editó el contenido"
_EDIT_COALESCE_SECONDS = 30 * 60


def _history(edition: dict | None, actor: str, action: str) -> list[dict]:
    """Historial de la edición. Los guardados de contenido sucesivos de la MISMA persona dentro de 30 minutos se agrupan en una sola entrada
    (se actualiza su hora y se cuenta cuántos fueron): el historial muestra hitos legibles, no cada clic en «Guardar»."""
    history = list((edition or {}).get("history") or [])
    now = repo.now_iso()
    if action == EDIT_ACTION and history:
        last = history[-1]
        last_at = parse_utc(last.get("at"))
        if (last.get("action") == EDIT_ACTION and last.get("actor") == actor and last_at
                and (now_utc() - last_at).total_seconds() <= _EDIT_COALESCE_SECONDS):
            history[-1] = {**last, "at": now, "count": int(last.get("count") or 1) + 1}
            return history[-100:]
    return [*history, {"at": now, "actor": actor, "action": action}][-100:]


def create_edition(month: str, actor: str, *, copy_previous: bool) -> dict:
    if not _MONTH.match(month or ""):
        raise CarteleraValidationError("Elegí un mes válido.")
    if repo.get("editions", month):
        raise CarteleraValidationError(f"Ya existe la edición de {month_label(month)}.")
    sections = _blank_sections()
    source = None
    if copy_previous:
        previous = [e for e in list_editions() if e["id"] < month]
        if previous:
            source = previous[0]
            sections = [{"key": s["key"], "wide": s.get("wide", False),
                         "items": [{**{k: it.get(k, "") for k in GENERAL_FIELDS}, "id": uuid.uuid4().hex[:12]}
                                   for it in s.get("items") or []] if SECTION_BY_KEY[s["key"]]["kind"] in ("general", "links") else []}
                        for s in normalize_sections(source.get("sections"))]
    fields = {"month": month, "title": "", "subtitle": "", "state": "draft", "publish_at": None, "published_at": None,
              "sections": sections, "history": _history(None, actor, "Creó la edición" + (f" a partir de {month_label(source['id'])}" if source else ""))}
    return repo.save("editions", month, fields, expected_revision=None)


def copy_edition(source_month: str, month: str, actor: str) -> dict:
    """Copia una edición como BORRADOR nuevo del mes `month`: conserva portada y contenidos (ítems y proyectos con ids nuevos, orden y ancho),
    nunca el estado de publicación, la programación, el aviso enviado ni el historial de la original."""
    if not _MONTH.match(month or ""):
        raise CarteleraValidationError("Elegí un mes válido.")
    source = get_edition(source_month)
    if source is None:
        raise repo.CarteleraNotFoundError(source_month)
    if repo.get("editions", month):
        raise CarteleraValidationError(f"Ya existe la edición de {month_label(month)}.")
    sections = [{"key": s["key"], "wide": bool(s.get("wide")),
                 "items": [{**{k: v for k, v in it.items() if k != "id"}, "id": uuid.uuid4().hex[:12]} for it in s.get("items") or []]}
                for s in source["sections"]]
    fields = {"month": month, "title": source.get("title", ""), "subtitle": source.get("subtitle", ""), "state": "draft",
              "publish_at": None, "published_at": None, "sections": sections, "copied_from": source_month,
              "history": _history(None, actor, f"Creó la edición como copia de {month_label(source_month)}")}
    return repo.save("editions", month, fields, expected_revision=None)


def next_free_month() -> str:
    """Próximo período sin edición a partir del mes en curso (propuesta por defecto para crear o copiar; cualquier otro mes libre es válido)."""
    existing = {e["id"] for e in list_editions()}
    month = month_key()
    while month in existing:
        month = shift_month(month, 1)
    return month


def _items_from_form(kind: str, rows: list[dict]) -> list[dict]:
    items = []
    for row in rows:
        if kind == "projects":
            item = {k: _text(row.get(k), limit=MAX_TEXT if k == "body" else 300) for k in PROJECT_FIELDS if k != "link"}
            item["link"] = _url(row.get("link"))
            item["kind"] = "fin" if item.get("kind") == "fin" else "inicio"
            if not (item["project_id"] and item["project_name"]):
                raise CarteleraValidationError("Cada proyecto del mes tiene que elegirse desde Operaciones.")
        else:
            item = {"title": _text(row.get("title"), limit=200), "body": _text(row.get("body")),
                    "link": _url(row.get("link")), "link_label": _text(row.get("link_label"), limit=120)}
            if not (item["title"] or item["body"]):
                continue                                                  # fila vacía: se descarta, no es un error
        item["id"] = str(row.get("id") or uuid.uuid4().hex[:12])[:24]
        items.append(item)
    return items


def save_content(month: str, revision: int, *, title: str, subtitle: str, sections: list[dict], actor: str) -> dict:
    """Guarda portada y secciones (orden, ancho e ítems). No cambia el estado de publicación."""
    edition = get_edition(month)
    if edition is None:
        raise repo.CarteleraNotFoundError(month)
    fields = {**_clean_content(title, subtitle, sections), "history": _history(edition, actor, EDIT_ACTION)}
    return repo.save("editions", month, fields, expected_revision=revision)


def _clean_content(title: str, subtitle: str, sections: list[dict]) -> dict:
    clean = []
    for sec in sections:
        meta = SECTION_BY_KEY.get(sec.get("key"))
        if not meta:
            continue
        items = [] if meta["kind"] == "birthdays" else _items_from_form(meta["kind"], sec.get("items") or [])
        clean.append({"key": meta["key"], "wide": bool(sec.get("wide")) and meta["kind"] == "general", "items": items})
    return {"title": _text(title, limit=160), "subtitle": _text(subtitle, limit=300), "sections": normalize_sections(clean)}


def draft_edition(month: str, *, title: str, subtitle: str, sections: list[dict]) -> dict:
    """Edición con el borrador del lienzo SIN validar (para volver a dibujarlo en modo edición, p. ej. al recuperar cambios locales): los
    textos se recortan y se escapan al renderizar; los enlaces inválidos se conservan para que el editor los marque. Nunca se persiste."""
    edition = get_edition(month)
    if edition is None:
        raise repo.CarteleraNotFoundError(month)
    clip = lambda v, n=MAX_TEXT: str(v or "")[:n]                                          # noqa: E731
    clean = []
    for sec in sections or []:
        meta = SECTION_BY_KEY.get((sec or {}).get("key"))
        if not meta:
            continue
        fields = PROJECT_FIELDS if meta["kind"] == "projects" else GENERAL_FIELDS
        items = [] if meta["kind"] == "birthdays" else [
            {**{k: clip(row.get(k)) for k in fields}, "id": clip(row.get("id"), 24) or uuid.uuid4().hex[:12]}
            for row in (sec.get("items") or []) if isinstance(row, dict)]
        clean.append({"key": meta["key"], "wide": bool(sec.get("wide")) and meta["kind"] == "general", "items": items})
    return {**edition, "title": clip(title, 160), "subtitle": clip(subtitle, 300), "sections": normalize_sections(clean)}


def preview_edition(month: str, *, title: str, subtitle: str, sections: list[dict]) -> dict:
    """Edición tal como quedaría con el contenido EN PANTALLA del editor (sin guardar): MISMA validación que `save_content`, nada se persiste.
    Conserva estado y fechas de la guardada: la vista previa nunca cambia qué ven los colaboradores."""
    edition = get_edition(month)
    if edition is None:
        raise repo.CarteleraNotFoundError(month)
    return {**edition, **_clean_content(title, subtitle, sections)}


def publish_now(month: str, revision: int, actor: str) -> dict:
    edition = get_edition(month)
    if edition is None:
        raise repo.CarteleraNotFoundError(month)
    if not (edition.get("title") or "").strip():
        raise CarteleraValidationError("Completá el título de portada antes de publicar.")
    published_at = repo.now_iso()
    saved = repo.save("editions", month, {"state": "published", "publish_at": published_at, "published_at": published_at,
                                          "history": _history(edition, actor, "Publicó la edición")}, expected_revision=revision)
    notify_published(saved)
    return saved


def schedule(month: str, revision: int, when_local: str, actor: str) -> dict:
    edition = get_edition(month)
    if edition is None:
        raise repo.CarteleraNotFoundError(month)
    if not (edition.get("title") or "").strip():
        raise CarteleraValidationError("Completá el título de portada antes de programar.")
    when = local_input_to_utc(when_local)
    if when <= now_utc():
        raise CarteleraValidationError("La fecha programada ya pasó: usá «Publicar ahora».")
    return repo.save("editions", month, {"state": "scheduled", "publish_at": when.isoformat(), "published_at": None,
                                         "history": _history(edition, actor, f"Programó la publicación para el {fmt_local(when)}")},
                     expected_revision=revision)


def to_draft(month: str, revision: int, actor: str, *, action: str) -> dict:
    edition = get_edition(month)
    if edition is None:
        raise repo.CarteleraNotFoundError(month)
    state = "withdrawn" if action == "withdraw" else "draft"
    label = "Retiró la edición" if action == "withdraw" else "Canceló la programación"
    return repo.save("editions", month, {"state": state, "publish_at": None, "history": _history(edition, actor, label)},
                     expected_revision=revision)


def delete_edition(month: str, actor: str) -> None:
    edition = get_edition(month)
    if edition is None:
        raise repo.CarteleraNotFoundError(month)
    if effective_state(edition) == "published":
        raise CarteleraValidationError("Una edición publicada no se elimina: primero retirala.")
    repo.delete("editions", month)


def run_due(now: datetime | None = None) -> int:
    """Programador: consolida como publicadas las programadas vencidas y dispara su aviso. Idempotente (la clave del aviso es estable) y seguro
    ante reinicios: si el proceso cae entre publicar y avisar, la siguiente pasada ve la edición publicada sin aviso registrado y lo emite."""
    now = now or now_utc()
    done = 0
    for edition in list_editions():
        if edition.get("state") == "scheduled":
            due = parse_utc(edition.get("publish_at"))
            if due and due <= now:
                try:
                    edition = repo.save("editions", edition["id"], {"state": "published", "published_at": edition["publish_at"],
                                                                    "history": _history(edition, "programador", "Publicación programada efectiva")},
                                        expected_revision=edition.get("revision", 1))
                except repo.CarteleraConcurrentModificationError:
                    continue                                              # alguien la editó en paralelo: la próxima pasada la reevalúa
                done += 1
        if edition.get("state") == "published" and edition.get("notified_key") != f"{edition['id']}:{edition.get('published_at') or ''}":
            notify_published(edition)
    return done


def notify_published(edition: dict) -> str:
    """Aviso por correo con el servicio existente (`notifications.emit`): clave «<mes>:<published_at>», nunca dos correos por la misma
    publicación efectiva. Registra en la edición la clave notificada para que el programador no vuelva a intentarlo."""
    from webapp.services import notifications
    key = f"{edition['id']}:{edition.get('published_at') or ''}"
    result = notifications.emit("human_capital.cartelera.published", key, {
        "label": month_label(edition["id"]), "headline": edition.get("title") or "",
        "url": notifications.hub_link(f"/cartelera/novedades/{edition['id']}"),
    })
    try:
        repo.save("editions", edition["id"], {"notified_key": key, "notification_result": result}, expected_revision=edition.get("revision", 1))
    except Exception:                                                     # noqa: BLE001 — accesorio: la idempotencia la garantiza la clave
        pass
    return result


def current_edition(now: datetime | None = None) -> dict | None:
    """La edición visible del mes en curso; si no hay, la última visible anterior; si no, ninguna."""
    now = now or now_utc()
    visible = [e for e in list_editions() if is_visible(e, now)]
    current = month_key(now.astimezone(BUSINESS_TZ).date())
    past = [e for e in visible if e["id"] <= current]
    chosen = past[0] if past else (visible[-1] if visible else None)
    if chosen:
        chosen["sections"] = normalize_sections(chosen.get("sections"))
    return chosen


def visible_editions(now: datetime | None = None) -> list[dict]:
    return [e for e in list_editions() if is_visible(e, now)]


# ── cumpleaños ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
# Modelo de cumpleaños: nombre, día, mes y AUTORIZACIÓN DE PUBLICACIÓN (único atributo de publicación). Autorizado ⇒ publicable; sin
# autorización ⇒ nunca aparece en la Cartelera. Cada cambio de autorización queda registrado (`authorization` + `authorization_log`).
CONSOLIDATION_ACTOR = "Consolidación del modelo (estaba oculto)"


def _consolidate(b: dict) -> dict:
    """Registros del modelo anterior (autorización + ocultamiento). Un cumpleaños que estaba OCULTO pasa a SIN AUTORIZACIÓN (nunca se
    vuelve visible por la simplificación), dejando la traza; el campo de ocultamiento desaparece. Se persiste en el primer uso y, si la
    escritura fallara, igual se devuelve consolidado: lo oculto jamás se publica."""
    if "hidden" not in b and "hidden_change" not in b:
        return b
    fields = {}
    if b.get("hidden") and b.get("authorized"):
        entry = {"value": False, "by": CONSOLIDATION_ACTOR, "at": repo.now_iso()}
        fields = {"authorized": False, "authorization": entry, "authorization_log": [*(b.get("authorization_log") or []), entry][-50:]}
    clean = {k: v for k, v in b.items() if k not in ("hidden", "hidden_change")}
    try:
        return repo.save("birthdays", b["id"], fields, expected_revision=b.get("revision"), drop=("hidden", "hidden_change"))
    except Exception:                                                     # noqa: BLE001 — la lectura nunca publica lo que estaba oculto
        return {**clean, **fields}


def get_birthday(birthday_id: str) -> dict | None:
    b = repo.get("birthdays", birthday_id)
    return _consolidate(b) if b else None


def list_birthdays() -> list[dict]:
    rows = [_consolidate(b) for b in repo.list_all("birthdays")]
    return sorted(rows, key=lambda b: (int(b.get("month", 0)), int(b.get("day", 0)), b.get("name", "").lower()))


def is_publishable(b: dict | None) -> bool:
    return bool(b and b.get("authorized"))


def _valid_day_month(day, month) -> tuple[int, int]:
    try:
        d, m = int(day), int(month)
        date(2024, m, d)                                                  # año bisiesto: admite 29/02
    except (TypeError, ValueError):
        raise CarteleraValidationError("Ingresá un día y un mes válidos.") from None
    return d, m


def save_birthday(birthday_id: str | None, revision: int | None, *, name: str, day, month, authorized: bool, actor: str) -> dict:
    name = _text(name, limit=120)
    if not name:
        raise CarteleraValidationError("Completá el nombre.")
    d, m = _valid_day_month(day, month)
    current = get_birthday(birthday_id) if birthday_id else None
    fields = {"name": name, "day": d, "month": m, "authorized": bool(authorized)}
    if not current or bool(current.get("authorized")) != bool(authorized):
        entry = {"value": bool(authorized), "by": actor, "at": repo.now_iso()}
        fields["authorization"] = entry
        fields["authorization_log"] = [*((current or {}).get("authorization_log") or []), entry][-50:]
    return repo.save("birthdays", birthday_id or uuid.uuid4().hex, fields, expected_revision=revision if current else None)


def import_birthdays(text: str, actor: str, *, authorized: bool) -> tuple[int, list[str]]:
    """Carga masiva: una persona por línea, «Nombre Apellido;DD/MM» (también coma o tabulación). Devuelve (creados, líneas rechazadas)."""
    created, rejected = 0, []
    existing = {(b.get("name", "").lower(), b.get("day"), b.get("month")) for b in list_birthdays()}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = re.match(r"^(.+?)[;,\t]\s*(\d{1,2})[/\-.](\d{1,2})\s*$", line)
        if not m:
            rejected.append(line)
            continue
        try:
            d, mo = _valid_day_month(m.group(2), m.group(3))
        except CarteleraValidationError:
            rejected.append(line)
            continue
        name = m.group(1).strip()
        if (name.lower(), d, mo) in existing:
            continue
        save_birthday(None, None, name=name, day=d, month=mo, authorized=authorized, actor=actor)
        existing.add((name.lower(), d, mo))
        created += 1
    return created, rejected


AUTH_FILTERS = {"si": "Autorizado", "no": "Sin autorización"}


def filter_birthdays(*, q: str = "", month: str = "", authorized: str = "") -> list[dict]:
    q = (q or "").strip().lower()
    out = []
    for b in list_birthdays():
        if q and q not in b.get("name", "").lower():
            continue
        if month and str(b.get("month")) != str(month):
            continue
        if authorized and is_publishable(b) != (authorized == "si"):
            continue
        out.append(b)
    return out


def preview_birthday_import(text: str) -> list[dict]:
    """Vista previa de la carga masiva (mismo flujo que la carga masiva de CVs: previsualizar, elegir, confirmar). Una fila por línea con
    estado `valid` · `duplicate` (ya cargado) · `invalid` y su mensaje. Nunca escribe."""
    existing = {(b.get("name", "").lower(), int(b.get("day", 0)), int(b.get("month", 0))) for b in list_birthdays()}
    seen, rows = set(), []
    for n, line in enumerate((text or "").splitlines(), start=1):
        raw = line.strip()
        if not raw:
            continue
        m = re.match(r"^(.+?)[;,\t]\s*(\d{1,2})[/\-.](\d{1,2})\s*$", raw)
        if not m:
            rows.append({"line": n, "raw": raw, "status": "invalid", "message": "Formato esperado: Nombre Apellido;DD/MM"})
            continue
        name = m.group(1).strip()[:120]
        try:
            d, mo = _valid_day_month(m.group(2), m.group(3))
        except CarteleraValidationError:
            rows.append({"line": n, "raw": raw, "name": name, "status": "invalid", "message": "Día o mes inválido"})
            continue
        key = (name.lower(), d, mo)
        status, message = ("duplicate", "Ya está cargado") if key in existing or key in seen else ("valid", "Se incorpora")
        seen.add(key)
        rows.append({"line": n, "raw": raw, "name": name, "day": d, "month": mo, "status": status, "message": message})
    return rows


def public_birthdays() -> list[dict]:
    """Solo los que tienen autorización de publicación."""
    return [b for b in list_birthdays() if is_publishable(b)]


def birthdays_of_month(month: int) -> list[dict]:
    return [b for b in public_birthdays() if int(b.get("month", 0)) == month]


def upcoming_birthdays(days: int = 7, today: date | None = None) -> list[dict]:
    today = today or now_utc().astimezone(BUSINESS_TZ).date()
    out = []
    for b in public_birthdays():
        for offset in range(days + 1):
            day = date.fromordinal(today.toordinal() + offset)
            if day.day == int(b["day"]) and day.month == int(b["month"]):
                out.append({**b, "offset": offset})
                break
    return sorted(out, key=lambda b: b["offset"])


# ── saludos de cumpleaños («Preparar saludo») ───────────────────────────────────────────────────────────────────────────────────────
# En la app original, «Preparar saludo» abría el editor de una publicación de Comunidad con el título y el mensaje ya armados, para adjuntar
# el flyer y publicar (o programar); el saludo quedaba vinculado al cumpleaños de ESE año y el calendario y el Inicio llevaban a él.
# Acá el saludo es un activo propio de la Cartelera (Comunidad todavía no existe): una tarjeta con la identidad del portal, editable sobre su
# representación, con publicación inmediata o programada. Mismas reglas que las ediciones: la visibilidad la resuelve SIEMPRE el servidor
# (`effective_state` + persona autorizada y visible). No envía correos: la original tampoco lo hacía (el mail personal era externo).
GREETING_FIELDS = ("title", "body", "link", "link_label")


def greeting_id(birthday_id: str, year: int) -> str:
    return f"{birthday_id}-{int(year)}"


def birthday_date(b: dict, year: int) -> date:
    d, m = int(b["day"]), int(b["month"])
    try:
        return date(year, m, d)
    except ValueError:                                                    # 29/02 en un año no bisiesto: se saluda el 28/02
        return date(year, m, 28)


def next_birthday(b: dict, today: date | None = None) -> date:
    """Próxima fecha de cumpleaños (hoy incluido)."""
    today = today or now_utc().astimezone(BUSINESS_TZ).date()
    this_year = birthday_date(b, today.year)
    return this_year if this_year >= today else birthday_date(b, today.year + 1)


def greeting_defaults(b: dict) -> dict:
    name = b.get("name") or ""
    first = name.split(" ")[0]
    return {"title": f"¡Feliz cumpleaños, {first}!", "link": "", "link_label": "",
            "body": f"¡Feliz cumpleaños, {name}! Que tengas un gran día. Todo el equipo de Danwor te saluda."}


def get_greeting(gid: str) -> dict | None:
    return repo.get("greetings", gid)


def greeting_visible(g: dict | None, now: datetime | None = None) -> bool:
    """Lo ven los colaboradores: publicado (o programado vencido) Y la persona sigue autorizada."""
    if not g or not is_visible(g, now):
        return False
    return is_publishable(get_birthday(g.get("birthday_id", "")))


def greeting_status(b: dict, today: date | None = None) -> dict:
    """Estado del saludo para el PRÓXIMO cumpleaños de la persona (lo que muestra la gestión)."""
    when = next_birthday(b, today)
    g = get_greeting(greeting_id(b["id"], when.year))
    state = effective_state(g) if g else "none"
    return {"id": greeting_id(b["id"], when.year), "year": when.year, "date": when, "state": state, "greeting": g,
            "can_prepare": is_publishable(b)}


def live_greetings(year: int) -> set:
    """Ids de saludos visibles para colaboradores de un año (para enlazarlos desde el calendario y el Inicio)."""
    return {g["id"] for g in repo.list_all("greetings") if g.get("year") == int(year) and greeting_visible(g)}


def greeting_birthday(birthday_id: str) -> dict:
    """Cumpleaños para preparar o publicar su saludo: tiene que existir y estar autorizado y visible."""
    b = get_birthday(birthday_id)
    if b is None:
        raise repo.CarteleraNotFoundError(birthday_id)
    if not is_publishable(b):
        raise CarteleraValidationError("Para preparar el saludo, " + (b.get("name") or "la persona") + " tiene que tener la autorización de publicación.")
    return b


def _clean_greeting(data: dict) -> dict:
    fields = {"title": _text(data.get("title"), limit=160), "body": _text(data.get("body"), limit=1500),
              "link": _url(data.get("link")), "link_label": _text(data.get("link_label"), limit=120)}
    if not fields["link"]:
        fields["link_label"] = ""
    return fields


def draft_greeting(b: dict, year: int, data: dict | None = None) -> dict:
    """Saludo tal como se dibuja: el guardado o, si todavía no existe, el texto propuesto (sin persistir). Con `data`, el borrador en
    pantalla (sin validar: se escapa al renderizar; nunca se persiste por esta vía)."""
    g = get_greeting(greeting_id(b["id"], year))
    base = g or {"id": greeting_id(b["id"], year), "birthday_id": b["id"], "year": int(year), "state": "draft", **greeting_defaults(b)}
    if data is not None:
        base = {**base, **{k: str(data.get(k) or "")[:1500] for k in GREETING_FIELDS}}
    return base


def save_greeting(birthday_id: str, year: int, revision, data: dict, actor: str) -> dict:
    b = greeting_birthday(birthday_id)
    gid = greeting_id(birthday_id, year)
    current = get_greeting(gid)
    fields = {**_clean_greeting(data), "birthday_id": birthday_id, "year": int(year)}
    if not fields["title"]:
        raise CarteleraValidationError("El saludo necesita un título.")
    if current is None:
        fields.update({"state": "draft", "publish_at": None, "published_at": None,
                       "history": _history(None, actor, "Preparó el saludo de " + (b.get("name") or ""))})
        return repo.save("greetings", gid, fields, expected_revision=None)
    fields["history"] = _history(current, actor, EDIT_ACTION)
    return repo.save("greetings", gid, fields, expected_revision=revision)


def greeting_state(birthday_id: str, year: int, revision: int, action: str, when_local: str, actor: str):
    """publish · schedule · unschedule · withdraw · delete — mismas reglas que las ediciones."""
    gid = greeting_id(birthday_id, year)
    g = get_greeting(gid)
    if g is None:
        raise repo.CarteleraNotFoundError(gid)
    if action == "delete":
        if effective_state(g) == "published":
            raise CarteleraValidationError("Un saludo publicado no se elimina: primero retiralo.")
        repo.delete("greetings", gid)
        return None
    if action in ("publish", "schedule"):
        greeting_birthday(birthday_id)
    if action == "publish":
        at = repo.now_iso()
        return repo.save("greetings", gid, {"state": "published", "publish_at": at, "published_at": at,
                                            "history": _history(g, actor, "Publicó el saludo")}, expected_revision=revision)
    if action == "schedule":
        when = local_input_to_utc(when_local)
        if when <= now_utc():
            raise CarteleraValidationError("La fecha programada ya pasó: usá «Publicar ahora».")
        return repo.save("greetings", gid, {"state": "scheduled", "publish_at": when.isoformat(), "published_at": None,
                                            "history": _history(g, actor, "Programó el saludo para el " + fmt_local(when))},
                         expected_revision=revision)
    if action in ("unschedule", "withdraw"):
        state, label = ("withdrawn", "Retiró el saludo") if action == "withdraw" else ("draft", "Canceló la programación del saludo")
        return repo.save("greetings", gid, {"state": state, "publish_at": None, "history": _history(g, actor, label)},
                         expected_revision=revision)
    raise CarteleraValidationError("Acción desconocida.")


def delete_birthday(birthday_id: str) -> None:
    """Elimina el cumpleaños y sus saludos (no quedan saludos huérfanos)."""
    repo.delete("birthdays", birthday_id)
    for g in repo.list_all("greetings"):
        if g.get("birthday_id") == birthday_id:
            repo.delete("greetings", g["id"])


# ── contenido institucional y accesos ─────────────────────────────────────────────────────────────────────────────────────────────
# PISO = contenido institucional de la aplicación original (app-cartelera, vista «Nosotros»): mientras Capital Humano no lo modifique, el portal
# muestra exactamente esos textos, valores y enlaces. Cualquier campo guardado por Capital Humano tiene prioridad sobre el piso.
ORIGINAL_VALUES = [
    {"title": 'Integridad', "text": 'En Danwor, la integridad es el núcleo de todas nuestras acciones y decisiones. Creemos firmemente en la importancia de actuar con transparencia, haciendo de ella la guía de nuestro compromiso con la excelencia. Esto nos permite fomentar un entorno de confianza y respeto mutuo, esencial para nuestras relaciones internas y externas.'},
    {"title": 'Empatía', "text": 'La empatía es fundamental para nuestra cultura y nuestras prácticas diarias. Entender y valorar las perspectivas y sentimientos de los demás es esencial para construir relaciones sólidas y colaborativas. Elegimos ser empáticos porque nos permite crear un entorno de trabajo inclusivo y respetuoso, donde cada individuo se siente escuchado y valorado.'},
    {"title": 'Compromiso', "text": 'Nos esforzamos por cumplir con nuestras responsabilidades y mantenernos fieles a nuestras promesas, asegurando que cada proyecto y tarea se realice con la máxima dedicación y calidad. Este firme compromiso se extiende a nuestra relación con los clientes, socios y empleados, garantizando que todos trabajamos juntos hacia objetivos comunes y resultados sobresalientes.'},
    {"title": 'Humildad', "text": 'En Danwor, la humildad es un valor esencial que guía nuestra forma de trabajar y de relacionarnos. Reconocemos que siempre hay algo nuevo que aprender y que cada persona puede aportar una perspectiva valiosa. La humildad nos permite colaborar de manera efectiva, valorando las contribuciones de todos y fomentando un ambiente de respeto y crecimiento mutuo.'},
    {"title": 'Trabajo en equipo', "text": 'El verdadero potencial se alcanza cuando trabajamos juntos como un equipo unificado. Valoramos la diversidad de habilidades y experiencias de cada miembro, reconociendo que cada contribución es esencial para lograr nuestros objetivos comunes. Promovemos un ambiente de confianza y apoyo mutuo, donde cada persona se siente empoderada para compartir ideas, resolver problemas y colaborar en proyectos.'},
]
DEFAULT_INSTITUTIONAL = {
    "intro": ("Creemos en el poder transformador de la tecnología para mejorar procesos, optimizar resultados y crear nuevas oportunidades. Este "
              "compromiso es la base sobre la cual construimos nuestra misión y visión, guiándonos hacia un futuro de continuo progreso y crecimiento."),
    "mission": "Satisfacer las necesidades analíticas existentes en el mercado nacional e internacional generando un valor adicional para los clientes.",
    "vision": ("Ser reconocidos en el mercado como una compañía enfocada en el bienestar de sus colaboradores y a partir de eso entregar servicios de "
               "máxima calidad a sus clientes."),
    "values_intro": ("Fomentar la cultura de la empresa es un desafío y un componente vital para lograr nuestra misión. Nuestro objetivo de desarrollar "
                     "una cultura inclusiva y diversa no solo fortalece nuestro equipo, sino que también impulsa a nuestros empleados y clientes a "
                     "alcanzar logros extraordinarios."),
    "values": ORIGINAL_VALUES,
    "links": [{"label": "Sitio web", "url": "https://www.danwor.com/"}, {"label": "LinkedIn", "url": "https://www.linkedin.com/company/danwor/"}],
    "albums": [], "org_chart_note": "",
}
_HTTPS = re.compile(r"^https://[^\s<>\"]+$")


def institutional() -> dict:
    return {**DEFAULT_INSTITUTIONAL, **(repo.get("settings", "institutional") or {})}


def save_institutional(revision: int | None, *, mission: str, vision: str, values: list[dict], albums: list[dict], actor: str,
                       intro: str | None = None, values_intro: str | None = None, links: list[dict] | None = None) -> dict:
    clean_values = [{"title": _text(v.get("title"), limit=80), "text": _text(v.get("text"), limit=1200)}
                    for v in values if (v.get("title") or "").strip() or (v.get("text") or "").strip()]
    clean_albums = []
    for a in albums:
        title = _text(a.get("title"), limit=120)
        if not title:
            continue
        clean_albums.append({"id": str(a.get("id") or uuid.uuid4().hex[:12])[:24], "title": title,
                             "date": _text(a.get("date"), limit=10), "description": _text(a.get("description"), limit=600)})
    current = repo.get("settings", "institutional")
    fields = {"mission": _text(mission, limit=1500), "vision": _text(vision, limit=1500), "values": clean_values, "albums": clean_albums,
              "updated_by": actor}
    if intro is not None:
        fields["intro"] = _text(intro, limit=1500)
    if values_intro is not None:
        fields["values_intro"] = _text(values_intro, limit=1500)
    if links is not None:                                                              # enlaces institucionales del encabezado (Sitio web, LinkedIn…)
        clean_links = []
        for link in links[:4]:
            label, url = _text(link.get("label"), limit=40), str(link.get("url") or "").strip()
            if not (label or url):
                continue
            if not (label and _HTTPS.match(url)):
                raise CarteleraValidationError("Cada enlace institucional necesita un nombre y una dirección https://")
            clean_links.append({"label": label, "url": url})
        fields["links"] = clean_links
    return repo.save("settings", "institutional", fields, expected_revision=revision if current else None)


def site_links() -> list[dict]:
    return list((repo.get("settings", "site") or {}).get("links") or [])


def site_revision() -> int | None:
    doc = repo.get("settings", "site")
    return int(doc["revision"]) if doc else None


def save_links(revision: int | None, links: list[dict], actor: str) -> dict:
    clean = []
    for link in links:
        title = _text(link.get("title"), limit=120)
        url = _url(link.get("url"))
        if not (title or url):
            continue
        if not (title and url):
            raise CarteleraValidationError("Cada acceso directo necesita nombre y enlace.")
        clean.append({"title": title, "group": _text(link.get("group"), limit=60), "url": url})
    current = repo.get("settings", "site")
    return repo.save("settings", "site", {"links": clean, "updated_by": actor}, expected_revision=revision if current else None)


# ── proyectos del mes (fuente gobernada de Operaciones) ──────────────────────────────────────────────────────────────────────────────
class ProjectSourceError(Exception):
    """La fuente de proyectos de Operaciones no pudo leerse con la autorización del editor (mensaje apto para mostrar)."""


def project_options(token: str) -> list[dict]:
    """Proyectos de Operaciones (lista Proyectos + Cliente, vía `StaffingRepository.get_projects`) leídos con el token DELEGADO del editor:
    solo id y nombre de proyecto y de cliente. El lector de la cartelera nunca consulta Operaciones: la edición guarda esa referencia."""
    if not token:
        raise ProjectSourceError("Autorizá Microsoft para consultar los proyectos de Operaciones.")
    from webapp.services.staffing_repository import StaffingRepository
    try:
        rows = StaffingRepository().get_projects(token)
    except Exception as exc:                                              # noqa: BLE001 — config incompleta, permisos o Graph
        raise ProjectSourceError("No se pudieron leer los proyectos de Operaciones con tu autorización actual.") from exc
    return sorted(({"id": pid, "name": name, "client": client or ""} for pid, name, _cid, client in rows),
                  key=lambda p: (p["client"].lower(), p["name"].lower()))
