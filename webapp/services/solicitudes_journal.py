"""Signed incremental observations in the existing rich-text column.

The original HTML is never serialized again. New entries are escaped HTML;
only verified entries are given an actor/date in the read-only projection.
"""
from datetime import datetime, timedelta, timezone
from collections import Counter
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


def version_history(html, item_id, versions, *, partial=False, current_modified=None):
    """One history projection: signed observations plus verified adjacent versions.

    Never infer a transition from the current item or the oldest retained version.
    Stable version identities prevent duplicates across reads or UI failures.
    """
    from webapp.services import solicitudes_admission as admission
    rows = history(html, item_id)
    indexed, ambiguous = {}, set()
    for version in versions:
        try:
            identity = version['id']
            parts = identity.split('.')
            if len(parts) != 2 or not all(p.isascii() and p.isdecimal() for p in parts):
                raise ValueError('Unknown version numbering')
            key = tuple(map(int, parts))
            if key in ambiguous:
                continue
            if key in indexed and indexed[key] != version:
                ambiguous.add(key)
                del indexed[key]
                raise ValueError('Conflicting version identity')
            indexed[key] = version
        except (ValueError, KeyError, TypeError, AttributeError):
            partial = True
    ordered = sorted(indexed.items())
    # Retention before the first observed version is not evidence of creation.
    partial = partial or len(ordered) < 2 or (bool(ordered) and ordered[0][0] != (1, 0))
    if current_modified is not None:
        try:
            latest = datetime.fromisoformat(ordered[-1][1]['lastModifiedDateTime'].replace('Z', '+00:00'))
            current = datetime.fromisoformat(current_modified.replace('Z', '+00:00'))
            partial = partial or latest.utcoffset() is None or current.utcoffset() is None or latest != current
        except (IndexError, KeyError, ValueError, TypeError, AttributeError):
            partial = True

    def commitment(fields):
        keys = (service.STATUS, service.PLANNED, service.ACTUAL)
        status = fields.get(service.STATUS)
        if (isinstance(status, str) and status in admission.COMMITMENT_STATES) or any(service.date_key(fields.get(k)) for k in keys[1:]):
            return True
        if (status is not None and not isinstance(status, str)) or any(fields.get(k) and not service.date_key(fields.get(k)) for k in keys[1:]):
            return None
        return False if all(k in fields for k in keys) else None

    for (old_key, old), (new_key, new) in zip(ordered, ordered[1:]):
        contiguous = (new_key == (old_key[0], old_key[1] + 1)
                      or (old_key[1] == 0 and new_key == (old_key[0] + 1, 0)))
        before, after = old.get('fields'), new.get('fields')
        if not contiguous or not isinstance(before, dict) or not isinstance(after, dict):
            partial = True
            continue
        try:
            stamp = datetime.fromisoformat(new['lastModifiedDateTime'].replace('Z', '+00:00'))
            previous_stamp = datetime.fromisoformat(old['lastModifiedDateTime'].replace('Z', '+00:00'))
            if stamp.utcoffset() is None or previous_stamp.utcoffset() is None or stamp < previous_stamp:
                raise ValueError('Unverified version time')
        except (ValueError, KeyError, TypeError, AttributeError):
            partial = True
            continue
        modified_by = new.get('lastModifiedBy')
        identity = modified_by.get('user') if isinstance(modified_by, dict) else None
        identity = identity if isinstance(identity, dict) else {}
        actor = identity.get('displayName') or identity.get('email') or identity.get('id')
        if not isinstance(actor, str) or not actor.strip():
            actor, partial = 'No identificado en SharePoint', True
        common = {'date': stamp.astimezone(ZONE).strftime('%d/%m/%Y %H:%M'), 'at': stamp.isoformat(),
                  'actor': actor, 'version': new['id'], 'source': 'SharePoint'}
        if service.FUNNEL not in before or service.FUNNEL not in after:
            partial = True
        elif before[service.FUNNEL] == admission.REQUESTED and after[service.FUNNEL] == admission.APPROVED:
            prior, effective = commitment(before), commitment(after)
            reason = ('Reconciliación de compromiso existente' if prior is True and effective is True else
                      'Aprobación por compromiso nuevo' if prior is False and effective is True else
                      'Motivo no determinable a partir de las versiones disponibles')
            rows.append({**common, 'id': f'version:{item_id}:{new["id"]}:funnel',
                         'movement': 'Reclasificación de Funnel', 'before': admission.REQUESTED,
                         'after': admission.APPROVED, 'badge_kind': 'funnel', 'text': reason})
        for key, label in ((service.STATUS, 'Cambio de Arquitectura'), (service.PLANNED, 'Cambio de fecha planificada'), (service.ACTUAL, 'Cambio de fecha real')):
            if key not in before or key not in after:
                partial = True
                continue
            a, b = before[key], after[key]
            if key != service.STATUS:
                if any(value and not service.date_key(value) for value in (a, b)):
                    partial = True
                    continue
                a, b = service.date_key(a), service.date_key(b)
            elif any(value is not None and not isinstance(value, str) for value in (a, b)):
                partial = True
                continue
            if a != b:
                rows.append({**common, 'id': f'version:{item_id}:{new["id"]}:{key}', 'movement': label,
                             'before': a if key == service.STATUS else service.display_date(a),
                             'after': b if key == service.STATUS else service.display_date(b),
                             'badge_kind': 'architecture' if key == service.STATUS else '', 'text': ''})
    rows.sort(key=lambda r: datetime.fromisoformat(r['at']) if r.get('at') else datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return rows, partial


def _legacy(html, parsed):
    parts, cursor = [], 0
    for start, end in parsed.ranges:
        parts.append(html[cursor:start])
        cursor = end
    return ''.join(parts) + html[cursor:]


class _LegacyContract(HTMLParser):
    """Compare preserved content, not SharePoint's serialization wrappers.

    This is not a sanitizer. Rendering remains escaped plain text. Formatting
    attributes already present must remain; added provider wrappers may vary.
    """
    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.structure, self.attributes = [], Counter()
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        if tag not in {'div', 'span', 'p', 'br', 'tbody'}:
            self.structure.append(('start', tag))
        for key, value in attrs:
            if key in {'href', 'src', 'alt', 'title', 'colspan', 'rowspan', 'style', 'class'} and value:
                if key == 'class':
                    value = ' '.join(sorted(value.split()))
                elif key == 'style':
                    value = ';'.join(sorted(':'.join(part.strip() for part in rule.split(':', 1))
                                            for rule in value.split(';') if rule.strip()))
                self.attributes[(tag, key, value)] += 1

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag not in {'div', 'span', 'p', 'br', 'tbody', 'img', 'hr', 'input', 'meta', 'link'}:
            self.structure.append(('end', tag))


def confirms_append(expected, observed, item_id, entry_id):
    """Confirm the signed entry and the preserved history after Graph normalization.

    No missing, changed or duplicate signed entries, or lost legacy content,
    links, tables, images or existing formatting attributes are accepted.
    """
    if not isinstance(expected, str) or not isinstance(observed, str):
        return False
    before, after = entries(expected, item_id), entries(observed, item_id)
    before_ids = [e['id'] for e in before.entries]
    after_ids = [e['id'] for e in after.entries]
    if (entry_id not in before_ids or before_ids != after_ids
            or len(set(after_ids)) != len(after_ids) or before.entries != after.entries):
        return False
    a, b = _legacy(expected, before), _legacy(observed, after)
    if service.narrative_text(a) != service.narrative_text(b):
        return False
    old, new = _LegacyContract(a), _LegacyContract(b)
    return old.structure == new.structure and not (old.attributes - new.attributes)


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
