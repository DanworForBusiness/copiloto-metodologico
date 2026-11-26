"""cartelera_social.py — COMUNIDAD y MOTOR DE INTERACCIÓN de Cartelera Virtual (B3 + B4).

Un solo motor para las dos experiencias:
  - Cartelera (contenido institucional de Capital Humano): contenidos de una edición (`item:<id>`) y saludos de cumpleaños (`greeting:<id>`).
  - Comunidad (publicaciones de colaboradores y de Capital Humano): `post:<id>`.
Cada contenido se identifica con un TARGET estable («tipo:id»). Sobre un target el motor ofrece reacciones (una por persona, cambiable o
retirable), comentarios con respuestas de un nivel y reportes; las encuestas y votos aplican SOLO a publicaciones de Comunidad (como en la
app original). Lo que NO es target no recibe interacciones (portada, calendario, accesos, Nosotros).

Reglas tomadas de la app original, con las correcciones de seguridad del discovery:
  - Cualquier persona admitida al Hub participa (F11): publica en las categorías habilitadas para colaboradores, reacciona, comenta,
    responde, menciona, vota y reporta lo ajeno. Capital Humano (autorización EXISTENTE de la vertical) publica comunicaciones oficiales y
    las programa. La MODERACIÓN es una autorización aparte y ortogonal: `COMMUNITY_OWNER` (allowlist explícita, patrón de CONTENT_OWNER)
    aprueba, rechaza, retira, elimina y fija lo ajeno y resuelve reportes. Capital Humano NO modera por serlo; moderar NO da Capital Humano.
  - Categorías con aprobación previa: lo publicado por un colaborador queda PENDIENTE. Corrección: CUALQUIER edición posterior de un
    colaborador en una categoría con aprobación vuelve a dejarla pendiente (en la original solo el cambio de categoría lo hacía, y editar
    el texto de algo aprobado eludía la moderación).
  - Pendiente, programado y retirado no se exponen a quien no corresponde: la visibilidad se resuelve SIEMPRE acá (servidor).
  - Propiedad: SOLO la autoría edita lo propio (nadie reescribe contenido ajeno, tampoco la moderación); la autoría elimina lo propio y la
    moderación retira o elimina lo ajeno, con registro.
  - Integridad: al eliminar una publicación, un contenido de una edición o un saludo, se eliminan sus interacciones; al copiar una edición
    los contenidos reciben ids nuevos (no se copian interacciones).
  - Notificaciones INTERNAS de Comunidad (menciones, respuestas, comentarios en lo propio, comunicaciones fijadas), calculadas y con
    preferencias por persona. No envían correo: el correo del Hub queda solo para la publicación de una cartelera (F6).
  - Imágenes y adjuntos: SharePoint + Spaces (F5); hasta verificar el acceso, no se simula esa integración.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from markupsafe import Markup, escape

from webapp import human_capital_access
from webapp.services import auth_service
from webapp.services import cartelera_repository as repo
from webapp.services import cartelera_service as svc

REACTIONS = (("like", "Me gusta", "like"), ("celebrate", "Celebrar", "party"), ("support", "Apoyo", "heart"), ("thanks", "Gracias", "hands"))
REACTION_IDS = {r[0] for r in REACTIONS}
CATEGORIES = (
    {"id": "oficial", "name": "Comunicaciones oficiales", "icon": "megaphone", "collab": False, "approval": False},
    {"id": "celebraciones", "name": "Cumpleaños y celebraciones", "icon": "cake", "collab": False, "approval": False},
    {"id": "reconocimientos", "name": "Felicitaciones y reconocimientos", "icon": "star", "collab": True, "approval": False},
    {"id": "personales", "name": "Novedades personales", "icon": "heart", "collab": True, "approval": True},
    {"id": "proyectos", "name": "Proyectos, equipos e innovación", "icon": "rocket", "collab": True, "approval": False},
    {"id": "comunidad", "name": "Bienvenidas y comunidad", "icon": "users", "collab": True, "approval": False},
    {"id": "ideas", "name": "Ideas y participación", "icon": "bulb", "collab": True, "approval": False},
)
CATEGORY_BY_ID = {c["id"]: c for c in CATEGORIES}
RULES = ("Publicá con respeto y buena fe. Pedí permiso antes de compartir información o fotos de otras personas. No compartas información "
         "confidencial de clientes ni proyectos. La moderación de Comunidad puede retirar contenido que no cumpla estas pautas.")
REPORT_REASONS = ("Contenido ofensivo o inapropiado", "Información confidencial", "Información personal sin consentimiento",
                  "Spam o fuera de tema", "Otro")
POLL_RESULTS = {"after_vote": "Después de votar", "always": "Siempre", "after_close": "Al cierre"}
WALL_TYPES = (("all", "Todo tipo"), ("oficial", "Oficiales (CH)"), ("colab", "De colaboradores"), ("encuesta", "Encuestas"),
              ("mias", "Mías y menciones"))
DEFAULT_NOTIF = {"mentions": True, "replies": True, "official": True}
_TARGET = re.compile(r"^(post|item|greeting):([A-Za-z0-9_-]{1,55})$")
_MENTION = re.compile(r"@\{u:([^}\s]{1,120})\}")
_URL = re.compile(r"^https://[^\s<>\"]+$")


class SocialError(ValueError):
    """Dato inválido o regla funcional: el mensaje es apto para mostrar."""


class SocialForbidden(Exception):
    """La persona no puede hacer esto (propiedad o permiso) → 403."""


class SocialNotFound(Exception):
    """El contenido no existe o no es visible para esta persona → 404 (nunca se revela que existe)."""


# ── identidad ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def uid(user: dict) -> str:
    return str((user or {}).get("sub") or "").strip().lower()


def uname(user: dict) -> str:
    return str((user or {}).get("name") or uid(user)).strip()


def is_moderator(user: dict | None) -> bool:
    """Moderación de Comunidad: admisión al Hub + allowlist EXPLÍCITA `COMMUNITY_OWNER` (fail-closed, sin bypass de APP_OWNER). Independiente
    de Capital Humano en los dos sentidos."""
    email = uid(user)
    return bool(email) and auth_service.is_authorized(email) and auth_service.is_community_owner_user(email)


def can_publish_official(user: dict | None) -> bool:
    """Comunicación INSTITUCIONAL (marca oficial «Capital Humano», categorías no habilitadas para colaboradores y programación): EXCLUSIVA de
    Capital Humano, con el predicado existente de la vertical (`HUMAN_CAPITAL_ALLOWED_USERS`). Moderar Comunidad (COMMUNITY_OWNER) NO la
    habilita: moderación y voz institucional son independientes. La autoría real queda siempre en `author`/`author_name` (auditoría)."""
    return bool(user) and human_capital_access.is_human_capital_authorized_user(user)


def _hid(value: str) -> str:
    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def _now() -> datetime:
    return svc.now_utc()


def _iso() -> str:
    return repo.now_iso()


def _text(value, limit: int) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    if len(text) > limit:
        raise SocialError(f"Un texto supera el máximo de {limit} caracteres.")
    return text


def _mutate(collection: str, doc_id: str, fn, base: dict) -> dict:
    """Leer-modificar-escribir atómico (mismo lock del repositorio) para documentos compartidos (reacciones, votos)."""
    with repo._write_lock:                                                             # noqa: SLF001 — mismo módulo de almacenamiento
        current = repo.get(collection, doc_id)
        doc = fn(dict(current) if current else dict(base))
        fields = {k: v for k, v in doc.items() if k not in ("id", "schema_version", "revision", "created_at", "updated_at")}
        return repo.save(collection, doc_id, fields, expected_revision=current.get("revision") if current else None)


# ── personas (directorio interno para menciones) ─────────────────────────────────────────────────────────────────────────────────
def touch_person(user: dict | None) -> None:
    """Registra a quien participa (correo + nombre de Entra ID), como el directorio interno de la app original. Sin perfiles propios."""
    if not user or not uid(user):
        return
    pid, today = _hid(uid(user)), _now().date().isoformat()
    current = repo.get("people", pid)
    if current and current.get("name") == uname(user) and current.get("seen") == today:
        return
    try:
        repo.save("people", pid, {"email": uid(user), "name": uname(user), "seen": today}, expected_revision=current.get("revision") if current else None)
    except (repo.CarteleraConcurrentModificationError, repo.CarteleraStorageError):
        pass


def people() -> dict[str, str]:
    return {p["email"]: p.get("name") or p["email"] for p in repo.list_all("people") if p.get("email")}


def search_people(q: str, limit: int = 8) -> list[dict]:
    fold = lambda s: svc_fold(s)                                                       # noqa: E731
    q = fold(q)
    rows = sorted(people().items(), key=lambda kv: kv[1].lower())
    return [{"id": e, "name": n} for e, n in rows if not q or q in fold(n) or q in e][:limit]


def svc_fold(value: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFD", str(value or ""))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).lower().split())


def mention_ids(body: str) -> list[str]:
    return list(dict.fromkeys(m.lower() for m in _MENTION.findall(body or "")))


def render_text(body: str, names: dict[str, str] | None = None) -> Markup:
    """Texto plano escapado; las menciones `@{u:correo}` se muestran como nombre resaltado (clase `.mention` de la app original)."""
    names = names if names is not None else people()
    out, last = [], 0
    for m in _MENTION.finditer(body or ""):
        out.append(escape(body[last:m.start()]))
        who = names.get(m.group(1).lower(), "Colaborador/a")
        out.append(Markup('<span class="mention">@') + escape(who) + Markup("</span>"))
        last = m.end()
    out.append(escape((body or "")[last:]))
    return Markup("").join(out)


def editable_text(body: str, names: dict[str, str] | None = None) -> str:
    names = names if names is not None else people()
    return _MENTION.sub(lambda m: "@" + names.get(m.group(1).lower(), "Colaborador/a"), body or "")


# ── publicaciones de Comunidad ────────────────────────────────────────────────────────────────────────────────────────────────────
def list_posts() -> list[dict]:
    return repo.list_all("posts")


def get_post(post_id: str) -> dict | None:
    return repo.get("posts", post_id) if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", post_id or "") else None


def post_when(p: dict) -> str:
    return p.get("publish_at") or p.get("created_at") or ""


def post_state(p: dict, now: datetime | None = None) -> str:
    if p.get("status") == "removed":
        return "removed"
    if p.get("status") == "pending":
        return "pending"
    due = svc.parse_utc(p.get("publish_at"))
    if due and due > (now or _now()):
        return "scheduled"
    return "published"


def post_visible(p: dict | None, user: dict | None, mod: bool) -> bool:
    if not p:
        return False
    state = post_state(p)
    if state == "published" or mod:
        return True
    return state in ("pending", "scheduled") and p.get("author") == uid(user)          # la autoría ve lo suyo aún no publicado; nadie más


def visible_posts(user: dict | None, mod: bool) -> list[dict]:
    rows = [p for p in list_posts() if post_visible(p, user, mod)]
    return sorted(rows, key=lambda p: (bool(p.get("pinned")) and post_state(p) == "published", post_when(p)), reverse=True)


def can_edit_post(p: dict, user: dict) -> bool:
    """SOLO la autoría edita (también lo oficial): la moderación retira o elimina lo ajeno, nunca lo reescribe."""
    return p.get("status") != "removed" and bool(uid(user)) and p.get("author") == uid(user)


def can_delete_post(p: dict, user: dict, mod: bool) -> bool:
    return p.get("author") == uid(user) or mod


def _clean_poll(raw) -> dict | None:
    if not raw:
        return None
    options = [_text(o, 120) for o in (raw.get("options") or []) if str(o or "").strip()][:8]
    if len(options) < 2:
        raise SocialError("La encuesta necesita al menos dos opciones.")
    closes = str(raw.get("closes_at") or "").strip()
    if closes and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", closes):
        raise SocialError("Fecha de cierre de la encuesta inválida.")
    results = raw.get("results") if raw.get("results") in POLL_RESULTS else "after_vote"
    return {"options": options, "closes_at": closes or None, "results": results}


def save_post(user: dict, data: dict, post_id: str | None = None) -> dict:
    """Roles resueltos ACÁ (servidor): oficial/programación, solo Capital Humano; fijar (lo oficial) y publicar sin aprobación, solo
    moderación. Editar, solo la autoría. La autoría real se conserva aunque se presente como Capital Humano."""
    mod, official_ok = is_moderator(user), can_publish_official(user)
    cat = CATEGORY_BY_ID.get(str(data.get("category") or ""))
    if not cat:
        raise SocialError("Elegí una categoría.")
    if not cat["collab"] and not official_ok:
        raise SocialForbidden("Esa categoría es solo para comunicaciones oficiales de Capital Humano.")
    if data.get("official") and not official_ok:                                       # crear o convertir en oficial: solo Capital Humano
        raise SocialForbidden("Solo Capital Humano publica comunicaciones oficiales.")
    title, body = _text(data.get("title"), 140), _text(data.get("body"), 4000)
    if not (title or body):
        raise SocialError("Escribí un título o un mensaje.")
    link = str(data.get("link") or "").strip()
    if link and not _URL.match(link):
        raise SocialError("El enlace tiene que empezar con https://")
    if cat["id"] == "personales" and not data.get("consent"):
        raise SocialError("Confirmá que las personas involucradas están de acuerdo con que se comparta esta novedad.")
    poll = _clean_poll(data.get("poll"))
    fields = {"category": cat["id"], "title": title, "body": body, "mentions": mention_ids(body), "link": link or None,
              "link_label": _text(data.get("link_label"), 120) if link else "", "poll": poll,
              "consent": True if cat["id"] == "personales" else None}
    if official_ok:                                                                    # opciones oficiales: solo Capital Humano
        fields["official"] = bool(data.get("official"))
        when = str(data.get("publish_at") or "").strip()
        fields["publish_at"] = svc.local_input_to_utc(when).isoformat() if when else None
    current = get_post(post_id) if post_id else None
    if current:                                                                        # fijar NO se decide editando: solo desde la moderación
        fields["pinned"] = bool(current.get("pinned")) and bool(fields.get("official", current.get("official")))
    if post_id:
        if not current or not post_visible(current, user, mod):
            raise SocialNotFound(post_id)
        if not can_edit_post(current, user):
            raise SocialForbidden("Solo podés editar tus publicaciones.")
        if not official_ok:                                                            # nunca se escalan privilegios editando
            fields.update({"official": False, "pinned": False, "publish_at": current.get("publish_at")})
        # Corrección del discovery: un colaborador que edita en una categoría con aprobación vuelve a la cola de moderación.
        if not mod and cat["approval"]:
            fields["status"] = "pending"
        elif current.get("status") == "pending" and not cat["approval"] and not mod:
            fields["status"] = "published"
        fields["edited_at"] = _iso()
        if (current.get("poll") or {}).get("options") != (poll or {}).get("options"):
            _purge_votes(post_id)                                                       # cambiaron las opciones: los votos ya no aplican
        saved = repo.save("posts", post_id, fields, expected_revision=current["revision"])
        if fields.get("status") == "pending" and current.get("status") != "pending":
            _modlog_system("Volvió a moderación por edición", title or body[:60])
        return saved
    fields.update({"author": uid(user), "author_name": uname(user),
                   "status": "pending" if cat["approval"] and not mod else "published"})
    fields.setdefault("official", False)
    fields.setdefault("pinned", False)
    fields.setdefault("publish_at", None)
    return repo.save("posts", uuid.uuid4().hex[:16], fields, expected_revision=None)


def delete_post(user: dict, post_id: str) -> None:
    """La autoría elimina lo propio; la moderación elimina lo ajeno (queda en el registro)."""
    mod = is_moderator(user)
    p = get_post(post_id)
    if not p or not post_visible(p, user, mod):
        raise SocialNotFound(post_id)
    if not can_delete_post(p, user, mod):
        raise SocialForbidden("Solo podés eliminar tus publicaciones.")
    repo.delete("posts", post_id)
    purge_targets([f"post:{post_id}"])
    if p.get("author") != uid(user):
        log(user, "Eliminó publicación", p.get("title") or p.get("body", "")[:60])


MODERATION_REASONS = REPORT_REASONS


def moderate_post(user: dict, post_id: str, action: str, reason: str = "") -> dict:
    """Solo moderación (COMMUNITY_OWNER): aprobar · rechazar · retirar (con motivo, que se le informa a la autoría) · fijar/desfijar (lo
    oficial). Lo propio no se retira ni se rechaza: se edita o se elimina."""
    if not is_moderator(user):
        raise SocialForbidden("Solo la moderación de Comunidad puede hacer esto.")
    p = get_post(post_id)
    if not p:
        raise SocialNotFound(post_id)
    if action in ("rechazar", "retirar") and p.get("author") == uid(user):
        raise SocialError("Lo propio no se retira: editalo o eliminalo.")
    label = p.get("title") or (p.get("body") or "")[:60]
    if action == "aprobar" and p.get("status") == "pending":
        saved = repo.save("posts", post_id, {"status": "published", "approved_at": _iso(), "approved_by": uname(user)}, expected_revision=p["revision"])
        log(user, "Aprobó publicación", label)
    elif action in ("rechazar", "retirar") and p.get("status") != "removed":
        kind = "rechazada" if p.get("status") == "pending" else "retirada"
        reason = _text(reason, 300)
        saved = repo.save("posts", post_id, {"status": "removed", "removed_at": _iso(), "removed_by": uname(user), "removed_kind": kind,
                                             "removed_reason": reason, "pinned": False}, expected_revision=p["revision"])
        log(user, ("Rechazó publicación" if kind == "rechazada" else "Retiró publicación") + (f" · {reason}" if reason else ""), label)
    elif action == "fijar" and p.get("official") and p.get("status") != "removed":
        saved = repo.save("posts", post_id, {"pinned": not p.get("pinned")}, expected_revision=p["revision"])
        log(user, "Desfijó" if p.get("pinned") else "Fijó", label)
    else:
        raise SocialError("Esa acción no aplica a esta publicación.")
    return saved


# ── targets ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def parse_target(target: str) -> tuple[str, str]:
    m = _TARGET.match(target or "")
    if not m:
        raise SocialNotFound(target)
    return m.group(1), m.group(2)


def _doc_key(target: str) -> str:
    kind, ident = parse_target(target)
    return f"{kind}-{ident}"


def _item_edition(item_id: str) -> dict | None:
    for e in svc.visible_editions():
        for sec in svc.normalize_sections(e.get("sections")):
            if any(it.get("id") == item_id for it in sec.get("items") or []):
                return e
    return None


def target_visible(target: str, user: dict | None, mod: bool) -> bool:
    try:
        kind, ident = parse_target(target)
    except SocialNotFound:
        return False
    if kind == "post":
        p = get_post(ident)
        return post_visible(p, user, mod) and post_state(p) == "published"           # se interactúa solo con lo publicado
    if kind == "item":
        return _item_edition(ident) is not None
    return svc.greeting_visible(svc.get_greeting(ident))


def target_label(target: str) -> tuple[str, str, str]:
    """(tipo legible, título, URL) para los listados de moderación."""
    kind, ident = parse_target(target)
    if kind == "post":
        p = get_post(ident) or {}
        return "Publicación", p.get("title") or (p.get("body") or "")[:70] or "(eliminada)", f"/cartelera/comunidad#post-{ident}"
    if kind == "item":
        e = _item_edition(ident)
        for sec in svc.normalize_sections((e or {}).get("sections")):
            for it in sec.get("items") or []:
                if it.get("id") == ident:
                    return "Novedades", it.get("title") or it.get("project_name") or "Contenido", f"/cartelera/novedades/{e['id']}#it-{ident}"
        return "Novedades", "(contenido no visible)", ""
    g = svc.get_greeting(ident) or {}
    return "Saludo", g.get("title") or "Saludo", f"/cartelera/saludos/{ident}"


def _require_target(target: str, user: dict, mod: bool) -> None:
    if not target_visible(target, user, mod):
        raise SocialNotFound(target)


# ── reacciones ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def react(user: dict, target: str, kind: str) -> str | None:
    """Agrega, cambia o retira (si es la misma) la reacción de la persona. Devuelve la reacción vigente."""
    if kind not in REACTION_IDS:
        raise SocialError("Reacción inválida.")
    _require_target(target, user, is_moderator(user))
    me, result = uid(user), {}

    def apply(doc):
        by = dict(doc.get("by") or {})
        if (by.get(me) or {}).get("t") == kind:
            by.pop(me, None)
            result["now"] = None
        else:
            by[me] = {"t": kind, "at": _iso()}
            result["now"] = kind
        return {**doc, "target": target, "by": by}
    _mutate("reactions", _doc_key(target), apply, {"target": target, "by": {}})
    return result["now"]


# ── comentarios ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def list_comments(target: str | None = None) -> list[dict]:
    rows = repo.list_all("comments")
    return sorted([c for c in rows if target is None or c.get("target") == target], key=lambda c: c.get("created_at", ""))


def add_comment(user: dict, target: str, body: str, parent_id: str = "") -> dict:
    mod = is_moderator(user)
    _require_target(target, user, mod)
    body = _text(body, 2000)
    if not body:
        raise SocialError("Escribí un comentario.")
    parent = None
    if parent_id:
        parent = repo.get("comments", parent_id) if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", parent_id) else None
        if not parent or parent.get("target") != target or parent.get("status") == "removed":
            raise SocialError("El comentario que querés responder ya no está disponible.")
        parent_id = parent.get("parent_id") or parent["id"]                             # respuestas de un nivel (como la original)
    return repo.save("comments", uuid.uuid4().hex[:16], {"target": target, "parent_id": parent_id or None, "body": body,
                     "mentions": mention_ids(body), "author": uid(user), "author_name": uname(user), "status": "visible"}, expected_revision=None)


def _own_comment(user: dict, comment_id: str) -> dict:
    c = repo.get("comments", comment_id) if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", comment_id or "") else None
    if not c or c.get("status") == "removed":
        raise SocialNotFound(comment_id)
    if c.get("author") != uid(user):
        raise SocialForbidden("Solo podés modificar tus comentarios.")
    return c


def edit_comment(user: dict, comment_id: str, body: str) -> dict:
    c = _own_comment(user, comment_id)
    body = _text(body, 2000)
    if not body:
        raise SocialError("El comentario no puede quedar vacío.")
    return repo.save("comments", comment_id, {"body": body, "mentions": mention_ids(body), "edited_at": _iso()}, expected_revision=c["revision"])


def delete_comment(user: dict, comment_id: str) -> None:
    c = _own_comment(user, comment_id)
    for child in list_comments(c["target"]):
        if child.get("parent_id") == comment_id:
            repo.delete("comments", child["id"])
    repo.delete("comments", comment_id)
    _purge_reports(f"comment:{comment_id}")


def remove_comment(user: dict, comment_id: str, reason: str = "") -> dict:
    if not is_moderator(user):
        raise SocialForbidden("Solo la moderación de Comunidad puede hacer esto.")
    c = repo.get("comments", comment_id) if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", comment_id or "") else None
    if not c:
        raise SocialNotFound(comment_id)
    saved = repo.save("comments", comment_id, {"status": "removed", "removed_at": _iso(), "removed_by": uname(user),
                                               "removed_reason": _text(reason, 300)}, expected_revision=c["revision"])
    log(user, "Retiró comentario", (c.get("body") or "")[:60])
    return saved


# ── encuestas (solo publicaciones de Comunidad) ─────────────────────────────────────────────────────────────────────────────────
def poll_closed(p: dict) -> bool:
    closes = (p.get("poll") or {}).get("closes_at")
    return bool(closes) and closes < _now().astimezone(svc.BUSINESS_TZ).date().isoformat()


def vote(user: dict, post_id: str, option: int) -> None:
    p = get_post(post_id)
    if not p or not target_visible(f"post:{post_id}", user, is_moderator(user)) or not p.get("poll"):
        raise SocialNotFound(post_id)
    if poll_closed(p):
        raise SocialError("La encuesta está cerrada.")
    if not (0 <= int(option) < len(p["poll"]["options"])):
        raise SocialError("Opción inválida.")
    me = uid(user)
    _mutate("votes", f"post-{post_id}", lambda doc: {**doc, "post": post_id, "by": {**(doc.get("by") or {}), me: {"i": int(option), "at": _iso()}}},
            {"post": post_id, "by": {}})


def poll_state(p: dict, user: dict | None, mod: bool) -> dict | None:
    poll = p.get("poll")
    if not poll:
        return None
    by = (repo.get("votes", f"post-{p['id']}") or {}).get("by") or {}
    n = [0] * len(poll["options"])
    for v in by.values():
        if 0 <= int(v.get("i", -1)) < len(n):
            n[int(v["i"])] += 1
    mine = (by.get(uid(user)) or {}).get("i")
    closed, total = poll_closed(p), sum(n)
    show = closed or poll.get("results") == "always" or (poll.get("results") != "after_close" and mine is not None) or mod
    return {"n": n, "total": total, "mine": mine, "closed": closed, "show": show,
            "pct": [round(x / total * 100) if total else 0 for x in n]}


# ── reportes ──────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def report(user: dict, kind: str, ident: str, reason: str, note: str) -> dict:
    mod = is_moderator(user)
    if kind == "post":
        p = get_post(ident)
        if not p or not target_visible(f"post:{ident}", user, mod):
            raise SocialNotFound(ident)
        if p.get("author") == uid(user):
            raise SocialError("No podés reportar tu propia publicación.")
        target, owner = f"post:{ident}", p.get("author")
    elif kind == "comment":
        c = repo.get("comments", ident) if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", ident or "") else None
        if not c or c.get("status") == "removed" or not target_visible(c.get("target", ""), user, mod):
            raise SocialNotFound(ident)
        if c.get("author") == uid(user):
            raise SocialError("No podés reportar tu propio comentario.")
        target, owner = f"comment:{ident}", c.get("author")
    else:
        raise SocialError("Tipo de reporte inválido.")
    reason = reason if reason in REPORT_REASONS else "Otro"
    for r in repo.list_all("reports"):
        if r.get("target") == target and r.get("reporter") == uid(user) and r.get("status") == "open":
            return r                                                                    # un reporte abierto por persona y contenido
    return repo.save("reports", uuid.uuid4().hex[:16], {"target": target, "kind": kind, "owner": owner, "reason": reason,
                     "note": _text(note, 600), "reporter": uid(user), "reporter_name": uname(user), "status": "open"}, expected_revision=None)


def open_reports() -> list[dict]:
    return sorted([r for r in repo.list_all("reports") if r.get("status") == "open"], key=lambda r: r.get("created_at", ""), reverse=True)


def resolve_report(user: dict, report_id: str) -> None:
    if not is_moderator(user):
        raise SocialForbidden("Solo la moderación de Comunidad puede hacer esto.")
    r = repo.get("reports", report_id) if re.fullmatch(r"[A-Za-z0-9_-]{1,40}", report_id or "") else None
    if not r:
        raise SocialNotFound(report_id)
    repo.save("reports", report_id, {"status": "resolved", "resolved_at": _iso(), "resolved_by": uname(user)}, expected_revision=r["revision"])
    log(user, "Resolvió reporte", r.get("reason", ""))


def report_target(r: dict) -> dict:
    """Contenido reportado (publicación o comentario) con su texto y dónde verlo."""
    kind, ident = r["target"].split(":", 1)
    if kind == "post":
        p = get_post(ident)
        return {"exists": bool(p), "text": (p or {}).get("title") or ((p or {}).get("body") or "")[:90], "removed": (p or {}).get("status") == "removed",
                "url": f"/cartelera/comunidad#post-{ident}", "kind": "post", "id": ident}
    c = repo.get("comments", ident)
    url = target_label(c["target"])[2] if c else ""
    return {"exists": bool(c), "text": editable_text((c or {}).get("body") or "")[:90], "removed": (c or {}).get("status") == "removed",
            "url": url, "kind": "comment", "id": ident}


# ── registro de moderación ────────────────────────────────────────────────────────────────────────────────────────────────────────
def log(user: dict, action: str, detail: str) -> None:
    repo.save("modlog", uuid.uuid4().hex[:16], {"action": action, "detail": (detail or "")[:120], "by": uname(user)}, expected_revision=None)


def _modlog_system(action: str, detail: str) -> None:
    repo.save("modlog", uuid.uuid4().hex[:16], {"action": action, "detail": (detail or "")[:120], "by": "Regla de moderación"}, expected_revision=None)


def modlog(limit: int = 30) -> list[dict]:
    return sorted(repo.list_all("modlog"), key=lambda x: x.get("created_at", ""), reverse=True)[:limit]


# ── integridad ────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _purge_votes(post_id: str) -> None:
    if repo.get("votes", f"post-{post_id}"):
        repo.delete("votes", f"post-{post_id}")


def _purge_reports(target: str) -> None:
    for r in repo.list_all("reports"):
        if r.get("target") == target:
            repo.delete("reports", r["id"])


def purge_targets(targets: list[str]) -> None:
    """Elimina las interacciones de contenidos que dejaron de existir (reacciones, comentarios, votos y reportes)."""
    wanted = set(targets)
    if not wanted:
        return
    for t in wanted:
        try:
            key = _doc_key(t)
        except SocialNotFound:
            continue
        if repo.get("reactions", key):
            repo.delete("reactions", key)
        if t.startswith("post:"):
            _purge_votes(t.split(":", 1)[1])
        _purge_reports(t)
    for c in repo.list_all("comments"):
        if c.get("target") in wanted:
            _purge_reports(f"comment:{c['id']}")
            repo.delete("comments", c["id"])


def item_targets(edition: dict | None) -> set[str]:
    return {f"item:{it['id']}" for sec in svc.normalize_sections((edition or {}).get("sections")) for it in sec.get("items") or [] if it.get("id")}


# ── vista: lo que necesita la plantilla para un conjunto de targets ─────────────────────────────────────────────────────────────
def snapshot(targets: list[str], user: dict | None, mod: bool, names: dict[str, str] | None = None) -> dict[str, dict]:
    names = names if names is not None else people()
    me = uid(user)
    by_target: dict[str, list] = {}
    for c in list_comments():
        if c.get("target") in targets and (c.get("status") != "removed" or mod):
            by_target.setdefault(c["target"], []).append(c)
    out = {}
    for t in targets:
        doc = repo.get("reactions", _doc_key(t)) or {}
        by = doc.get("by") or {}
        counts = {r[0]: 0 for r in REACTIONS}
        who = {r[0]: [] for r in REACTIONS}
        for person, r in by.items():
            if r.get("t") in counts:
                counts[r["t"]] += 1
                who[r["t"]].append(names.get(person, "Colaborador/a"))
        cmts = by_target.get(t, [])
        rows = [{**c, "html": render_text(c.get("body", ""), names), "raw": editable_text(c.get("body", ""), names),
                 "mine": c.get("author") == me, "name": names.get(c.get("author", ""), c.get("author_name") or "Colaborador/a")} for c in cmts]
        roots = [c for c in rows if not c.get("parent_id")]
        out[t] = {"counts": counts, "who": who, "mine": (by.get(me) or {}).get("t"), "total": sum(counts.values()),
                  "comments": [{**r, "replies": [x for x in rows if x.get("parent_id") == r["id"]]} for r in roots],
                  "ncomments": len([c for c in rows if c.get("status") != "removed"])}
    return out


# ── notificaciones internas ───────────────────────────────────────────────────────────────────────────────────────────────────────
def prefs(user: dict) -> dict:
    doc = repo.get("prefs", _hid(uid(user))) or {}
    return {"notif": {**DEFAULT_NOTIF, **(doc.get("notif") or {})}, "seen_at": doc.get("seen_at") or "", "revision": doc.get("revision")}


def save_prefs(user: dict, *, notif: dict | None = None, seen: bool = False) -> None:
    current = repo.get("prefs", _hid(uid(user)))
    fields = {"user": uid(user)}
    if notif is not None:
        fields["notif"] = {k: bool(notif.get(k)) for k in DEFAULT_NOTIF}
    if seen:
        fields["seen_at"] = _iso()
    repo.save("prefs", _hid(uid(user)), fields, expected_revision=current.get("revision") if current else None)


def notifications(user: dict | None, names: dict[str, str] | None = None) -> list[dict]:
    """Calculadas, como en la original: menciones, respuestas, comentarios en lo propio y comunicaciones fijadas por Capital Humano."""
    if not user or not uid(user):
        return []
    names = names if names is not None else people()
    me, pr, mod = uid(user), prefs(user)["notif"], is_moderator(user)
    posts = [p for p in list_posts() if post_state(p) == "published"]
    mine_posts = {f"post:{p['id']}" for p in posts if p.get("author") == me}
    out = []
    for p in posts:
        who = "Capital Humano" if p.get("official") else names.get(p.get("author", ""), p.get("author_name") or "Colaborador/a")
        if pr["mentions"] and me in (p.get("mentions") or []) and p.get("author") != me:
            out.append({"at": post_when(p), "url": f"/cartelera/comunidad#post-{p['id']}", "text": f"{who} te mencionó en «{p.get('title') or 'una publicación'}»"})
        if pr["official"] and p.get("official") and p.get("pinned"):
            out.append({"at": post_when(p), "url": f"/cartelera/comunidad#post-{p['id']}", "text": f"Comunicación importante: {p.get('title') or 'Capital Humano'}"})
    for p in list_posts():                                                             # lo que la moderación decidió sobre lo propio
        if p.get("author") != me:
            continue
        title = p.get("title") or "tu publicación"
        if p.get("status") == "removed":
            why = f" Motivo: {p['removed_reason']}." if p.get("removed_reason") else ""
            verb = "rechazó" if p.get("removed_kind") == "rechazada" else "retiró"
            out.append({"at": p.get("removed_at") or "", "url": "/cartelera/comunidad", "text": f"La moderación de Comunidad {verb} «{title}».{why}"})
        elif p.get("approved_at") and p.get("status") == "published":
            out.append({"at": p["approved_at"], "url": f"/cartelera/comunidad#post-{p['id']}", "text": f"La moderación de Comunidad aprobó «{title}»: ya está publicada."})
    for c in list_comments():
        if c.get("author") == me and c.get("status") == "removed":
            why = f" Motivo: {c['removed_reason']}." if c.get("removed_reason") else ""
            out.append({"at": c.get("removed_at") or "", "url": "/cartelera/comunidad", "text": f"La moderación de Comunidad retiró un comentario tuyo.{why}"})
    comments = [c for c in list_comments() if c.get("status") != "removed"]
    my_comments = {c["id"] for c in comments if c.get("author") == me}
    for c in comments:
        if c.get("author") == me or not target_visible(c.get("target", ""), user, mod):
            continue
        who = names.get(c.get("author", ""), c.get("author_name") or "Colaborador/a")
        url = target_label(c["target"])[2]
        if pr["mentions"] and me in (c.get("mentions") or []):
            out.append({"at": c.get("created_at", ""), "url": url, "text": f"{who} te mencionó en un comentario"})
        elif pr["replies"] and c.get("parent_id") in my_comments:
            out.append({"at": c.get("created_at", ""), "url": url, "text": f"{who} respondió tu comentario"})
        elif pr["replies"] and c.get("target") in mine_posts:
            out.append({"at": c.get("created_at", ""), "url": url, "text": f"{who} comentó tu publicación"})
    out.sort(key=lambda n: n["at"], reverse=True)
    seen = prefs(user)["seen_at"]
    for n in out:
        n["new"] = n["at"] > seen
        n["when"] = svc.fmt_local(n["at"])
    return out[:30]


def fmt_when(value: str) -> str:
    dt = svc.parse_utc(value)
    if not dt:
        return ""
    secs = (_now() - dt).total_seconds()
    if secs < 60:
        return "recién"
    if secs < 3600:
        return f"hace {int(secs // 60)} min"
    if secs < 86400:
        return f"hace {int(secs // 3600)} h"
    if secs < 7 * 86400:
        return f"hace {int(secs // 86400)} d"
    return svc.fmt_local(value, with_time=False)


def wall_posts() -> list[dict]:
    """Muro de Comunidad: EXCLUSIVAMENTE publicaciones publicadas y vigentes, para cualquier persona (Capital Humano incluido). Pendientes,
    programadas, rechazadas y retiradas no se listan, no cuentan en las categorías ni aparecen en la búsqueda: se gestionan en la moderación."""
    rows = [p for p in list_posts() if post_state(p) == "published"]
    return sorted(rows, key=lambda p: (bool(p.get("pinned")), post_when(p)), reverse=True)


def wall(user: dict | None, mod: bool, *, cat: str = "all", kind: str = "all", q: str = "", since: str = "") -> tuple[list[dict], dict]:
    posts = wall_posts()
    counts: dict[str, int] = {}
    for p in posts:
        counts[p["category"]] = counts.get(p["category"], 0) + 1
    counts["all"] = len(posts)
    names = people()
    if cat != "all":
        posts = [p for p in posts if p.get("category") == cat]
    if kind == "oficial":
        posts = [p for p in posts if p.get("official")]
    elif kind == "colab":
        posts = [p for p in posts if not p.get("official")]
    elif kind == "encuesta":
        posts = [p for p in posts if p.get("poll")]
    elif kind == "mias":
        posts = [p for p in posts if p.get("author") == uid(user) or uid(user) in (p.get("mentions") or [])]
    if since and re.fullmatch(r"\d{4}-\d{2}-\d{2}", since):
        posts = [p for p in posts if post_when(p)[:10] >= since]
    if q:
        fq = svc_fold(q)
        posts = [p for p in posts if fq in svc_fold(" ".join([p.get("title", ""), editable_text(p.get("body", ""), names),
                                                               names.get(p.get("author", ""), p.get("author_name", "")),
                                                               CATEGORY_BY_ID.get(p.get("category"), {}).get("name", "")]))]
    return posts, counts


def post_json(p: dict) -> str:
    """Datos para editar una publicación en el publicador (texto con menciones legibles + mapa nombre→persona)."""
    names = people()
    return json.dumps({"id": p["id"], "category": p.get("category"), "title": p.get("title", ""), "body": editable_text(p.get("body", ""), names),
                       "mentions": {names.get(m, m): m for m in p.get("mentions") or []}, "link": p.get("link") or "",
                       "link_label": p.get("link_label") or "", "poll": p.get("poll"), "official": bool(p.get("official")),
                       "pinned": bool(p.get("pinned")), "consent": bool(p.get("consent")),
                       "publish_at": svc.to_local_input(p.get("publish_at")) if p.get("publish_at") else ""}, ensure_ascii=False)
