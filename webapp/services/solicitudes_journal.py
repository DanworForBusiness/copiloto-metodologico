"""Signed incremental observations in the existing rich-text column.

The original HTML is never serialized again. New entries are escaped HTML;
only verified entries are given an actor/date in the read-only projection.
"""
from datetime import datetime, timedelta, timezone
from html import escape
from html.parser import HTMLParser
from uuid import uuid4

from jose import JWTError, jwt

from webapp.services import auth_service
from webapp.services.busqueda_repository import BUSINESS_TZ
from webapp.services import solicitudes_service as service

ISSUER = "danwor-solicitudes-observation-v1"
ZONE = BUSINESS_TZ


def _encode(data):
    return jwt.encode({"iss": ISSUER, **data}, auth_service.SESSION_SECRET, algorithm="HS256")


def _decode(token, kind, item_id):
    try:
        data = jwt.decode(token, auth_service.SESSION_SECRET, algorithms=["HS256"], issuer=ISSUER)
        if data.get("kind") != kind or data.get("item") != str(item_id):
            raise ValueError("scope")
        if not isinstance(data.get("id"), str) or len(data["id"]) != 32:
            raise ValueError("identity")
        return data
    except (JWTError, ValueError, TypeError) as exc:
        raise ValueError("La observación requiere una nueva versión del formulario. Conservá tu borrador y releé la solicitud.") from exc


def issue(item_id, actor):
    if not actor:
        return ""
    return _encode({"kind": "ticket", "id": uuid4().hex, "item": str(item_id),
                    "actor_id": actor, "exp": datetime.now(timezone.utc) + timedelta(hours=24)})


def validate_column(columns):
    column = next((c for c in columns if c.get("name") == service.COMMENTS), {})
    text = column.get("text") or {}
    if (column.get("readOnly") is not False or column.get("calculated") is not None
            or text.get("allowMultipleLines") is not True
            or text.get("appendChangesToExistingText") is not False
            or text.get("textType") != "richText"):
        raise ValueError("No se pudo confirmar la configuración de Comentarios. Tu observación se conserva sin enviar.")
    return text


class _Entries(HTMLParser):
    def __init__(self, html, item_id):
        super().__init__(convert_charrefs=False)
        self.html, self.item_id = html, item_id
        self.offsets = [0]
        for line in html.splitlines(keepends=True):
            self.offsets.append(self.offsets[-1] + len(line))
        self.depth, self.active, self.entries, self.ranges = 0, None, [], []

    def _position(self):
        line, col = self.getpos()
        return self.offsets[line - 1] + col

    def handle_starttag(self, tag, attrs):
        if tag != "div":
            return
        self.depth += 1
        if self.active:
            return
        token = dict(attrs).get("data-dw-observation")
        if not token:
            return
        try:
            data = _decode(token, "entry", self.item_id)
            stamp = datetime.fromisoformat(data["at"])
            if stamp.utcoffset() != timedelta(0) or not all(isinstance(data[k], str) for k in ("actor", "text", "movement")):
                return
            self.active = (self.depth, self._position(), data)
        except (ValueError, KeyError, TypeError):
            pass

    def handle_endtag(self, tag):
        if tag != "div":
            return
        if self.active and self.depth == self.active[0]:
            _, start, data = self.active
            end = self._position() + len("</div>")
            actual = service.narrative_text(self.html[start:end])
            expected = data["header"] + "\n" + data["text"]
            if actual.split() == expected.split():
                self.entries.append(data)
                self.ranges.append((start, end))
            self.active = None
        self.depth = max(0, self.depth - 1)


def entries(html, item_id):
    parser = _Entries(html or "", item_id)
    parser.feed(html or "")
    parser.close()
    return parser


def history(html, item_id):
    parsed = entries(html, item_id)
    rows = [{"id": e["id"], "movement": e["movement"], "date": datetime.fromisoformat(e["at"]).astimezone(ZONE).strftime("%d/%m/%Y %H:%M"),
             "actor": e["actor"], "text": e["text"], "at": e["at"]} for e in parsed.entries]
    rows.sort(key=lambda row: datetime.fromisoformat(row["at"]), reverse=True)
    original, cursor, rest = html or "", 0, []
    for start, end in parsed.ranges:
        rest.append(original[cursor:start])
        cursor = end
    rest.append(original[cursor:])
    legacy = service.narrative_text("".join(rest))
    if legacy.strip():
        rows.append({"movement": "Observación heredada", "date": "—", "actor": "—", "text": legacy, "at": ""})
    return rows


def append(original, text, ticket, item_id, user, columns, *, movement="Observación de Arquitectura", now=None):
    definition = validate_column(columns)
    if not isinstance(text, str) or not text.strip():
        raise ValueError("Ingresá una observación.")
    identity = _decode(ticket, "ticket", item_id)
    if identity.get("actor_id") != user.get("sub"):
        raise ValueError("La observación no pertenece a tu formulario.")
    if any(e["id"] == identity["id"] for e in entries(original, item_id).entries):
        raise ValueError("Esta observación ya está registrada. Releé la solicitud antes de enviar otra intervención.")
    at = now or datetime.now(timezone.utc)
    if at.tzinfo is None:
        raise ValueError("La fecha del servidor debe ser inequívoca.")
    actor = user.get("email") or user.get("name") or user["sub"]
    text = service.normalize_narrative(text.strip())
    # The visible header includes the UTC offset even when read outside the Hub.
    header = at.astimezone(ZONE).isoformat(timespec="seconds") + " — " + actor
    data = {"kind": "entry", "id": identity["id"], "item": str(item_id), "at": at.astimezone(timezone.utc).isoformat(),
            "actor": actor, "text": text, "header": header, "movement": movement}
    token = _encode(data)
    addition = '<div data-dw-observation="' + escape(token, quote=True) + '"><p>' + escape(header) + '</p><p>' + escape(text).replace('\n', '<br>') + '</p></div>'
    combined = (original or "") + addition
    limit = definition.get("maxLength")
    if isinstance(limit, int) and limit > 0 and len(combined.encode("utf-16-le")) // 2 > limit:
        raise ValueError("La observación supera la capacidad disponible de Comentarios. Acortá el texto; no se modificó el historial.")
    return combined, identity["id"]
