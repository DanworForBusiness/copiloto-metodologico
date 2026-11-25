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


def _history(edition: dict | None, actor: str, action: str) -> list[dict]:
    return [*((edition or {}).get("history") or []), {"at": repo.now_iso(), "actor": actor, "action": action}][-100:]


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
    clean = []
    for sec in sections:
        meta = SECTION_BY_KEY.get(sec.get("key"))
        if not meta:
            continue
        items = [] if meta["kind"] == "birthdays" else _items_from_form(meta["kind"], sec.get("items") or [])
        clean.append({"key": meta["key"], "wide": bool(sec.get("wide")), "items": items})
    fields = {"title": _text(title, limit=160), "subtitle": _text(subtitle, limit=300), "sections": normalize_sections(clean),
              "history": _history(edition, actor, "Editó el contenido")}
    return repo.save("editions", month, fields, expected_revision=revision)


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
def list_birthdays() -> list[dict]:
    return sorted(repo.list_all("birthdays"), key=lambda b: (int(b.get("month", 0)), int(b.get("day", 0)), b.get("name", "").lower()))


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
    current = repo.get("birthdays", birthday_id) if birthday_id else None
    fields = {"name": name, "day": d, "month": m, "authorized": bool(authorized),
              "hidden": bool((current or {}).get("hidden", False))}
    if not current or bool(current.get("authorized")) != bool(authorized):
        fields["authorization"] = {"value": bool(authorized), "by": actor, "at": repo.now_iso()}
    return repo.save("birthdays", birthday_id or uuid.uuid4().hex, fields, expected_revision=revision if current else None)


def set_birthday_hidden(birthday_id: str, revision: int, hidden: bool, actor: str) -> dict:
    return repo.save("birthdays", birthday_id, {"hidden": bool(hidden), "hidden_change": {"value": bool(hidden), "by": actor, "at": repo.now_iso()}},
                     expected_revision=revision)


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


def public_birthdays() -> list[dict]:
    """Solo los que tienen autorización de publicación y no están ocultos."""
    return [b for b in list_birthdays() if b.get("authorized") and not b.get("hidden")]


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


# ── contenido institucional y accesos ─────────────────────────────────────────────────────────────────────────────────────────────
DEFAULT_INSTITUTIONAL = {"mission": "", "vision": "", "values": [], "albums": [], "org_chart_note": ""}


def institutional() -> dict:
    return {**DEFAULT_INSTITUTIONAL, **(repo.get("settings", "institutional") or {})}


def save_institutional(revision: int | None, *, mission: str, vision: str, values: list[dict], albums: list[dict], actor: str) -> dict:
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
    return repo.save("settings", "institutional", {"mission": _text(mission, limit=1500), "vision": _text(vision, limit=1500),
                                                   "values": clean_values, "albums": clean_albums, "updated_by": actor},
                     expected_revision=revision if current else None)


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
