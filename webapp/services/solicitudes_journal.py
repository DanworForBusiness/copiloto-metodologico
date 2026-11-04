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


# Coverage of the reconstructed history: WHY a projection may be incomplete, not just a boolean.
# Informative codes describe the scope SharePoint retains (never an operational failure); warning codes mean a
# movement may exist that cannot be verified. `retryable` only when repeating the same read can improve it.
COVERAGE = {
    # code: (level, retryable, message)
    'single_version': ('info', False, 'La solicitud tiene una única versión en SharePoint: todavía no hay movimientos entre versiones.'),
    'retention_limited': ('info', False, 'Historial disponible desde la primera versión conservada por SharePoint.'),
    'version_limit': ('info', False, 'Se muestran los movimientos de las versiones más recientes; las anteriores exceden el límite de lectura.'),
    'version_gap': ('warning', False, 'Algunos movimientos no pueden verificarse porque faltan versiones intermedias.'),
    'ambiguous_version': ('warning', False, 'Algunas versiones de SharePoint tienen una identificación inválida o duplicada: sus movimientos no se muestran.'),
    'unverified_time': ('warning', False, 'Algunas versiones no tienen una fecha verificable: sus movimientos no se muestran.'),
    'malformed_value': ('warning', False, 'Algunos valores de las versiones no tienen un formato verificable: esos cambios no se muestran.'),
    'missing_fields': ('warning', True, 'No se pudieron leer los datos de algunas versiones: sus movimientos no se muestran.'),
    'version_read_error': ('warning', True, 'No se pudieron leer algunas versiones en SharePoint: sus movimientos no se muestran.'),
    'tail_mismatch': ('warning', True, 'La solicitud tiene cambios posteriores a la última versión que informó SharePoint: los movimientos más recientes pueden faltar.'),
}
_LEVEL_ORDER = {'info': 1, 'warning': 2}
TAIL_TOLERANCE = timedelta(seconds=1)


def coverage_summary(codes):
    """Presentation of a set of coverage codes: the most severe level wins; info codes never read as errors."""
    codes = set(codes)
    if 'version_read_error' in codes:
        codes.discard('missing_fields')                                         # una versión que no se pudo leer es la causa de sus datos faltantes: un solo mensaje
    known = sorted({c for c in codes if c in COVERAGE}, key=lambda c: (-_LEVEL_ORDER[COVERAGE[c][0]], c))
    if not known:
        return {'codes': [], 'level': 'complete', 'messages': [], 'retryable': False, 'partial': False}
    level = COVERAGE[known[0]][0]
    return {'codes': known, 'level': level, 'messages': [COVERAGE[c][2] for c in known],
            'retryable': any(COVERAGE[c][1] for c in known), 'partial': level == 'warning'}


def version_history_coverage(html, item_id, versions, *, issues=(), current_modified=None):
    """One history projection: signed observations plus verified ADJACENT versions, and a structured coverage.

    Never infer a transition from the current item, the oldest retained version or across missing versions.
    Graph omits empty columns from a version's `fields`: an absent operational column is an EMPTY value, not
    missing metadata (a version without any `fields` object is). Stable version identities prevent duplicates.
    """
    from webapp.services import solicitudes_admission as admission
    rows = history(html, item_id)
    codes = set(issues)
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
            codes.add('ambiguous_version')
    ordered = sorted(indexed.items())
    if len(ordered) == 1 and ordered[0][0] == (1, 0):
        codes.add('single_version')                                             # created and never edited: nothing to reconstruct, nothing wrong
    elif ordered and ordered[0][0] != (1, 0):
        codes.add('retention_limited')                                          # retention before the first observed version is not evidence of creation
    if current_modified is not None and ordered:
        try:
            latest = datetime.fromisoformat(ordered[-1][1]['lastModifiedDateTime'].replace('Z', '+00:00'))
            current = datetime.fromisoformat(current_modified.replace('Z', '+00:00'))
            if latest.utcoffset() is None or current.utcoffset() is None:
                codes.add('unverified_time')
            elif current - latest > TAIL_TOLERANCE:                             # two Graph representations of the same instant may differ in precision
                codes.add('tail_mismatch')
        except (KeyError, ValueError, TypeError, AttributeError):
            codes.add('unverified_time')

    def value(fields, key):
        return fields.get(key)                                                  # absent = empty (Graph omits empty columns)

    def commitment(fields):
        status = value(fields, service.STATUS)
        dates = (value(fields, service.PLANNED), value(fields, service.ACTUAL))
        if (isinstance(status, str) and status in admission.COMMITMENT_STATES) or any(service.date_key(d) for d in dates):
            return True
        if (status is not None and not isinstance(status, str)) or any(d and not service.date_key(d) for d in dates):
            return None
        return False

    for (old_key, old), (new_key, new) in zip(ordered, ordered[1:]):
        contiguous = (new_key == (old_key[0], old_key[1] + 1)
                      or (old_key[1] == 0 and new_key == (old_key[0] + 1, 0)))
        before, after = old.get('fields'), new.get('fields')
        if not contiguous:
            codes.add('version_gap')
            continue
        if not isinstance(before, dict) or not isinstance(after, dict):
            codes.add('missing_fields')
            continue
        try:
            stamp = datetime.fromisoformat(new['lastModifiedDateTime'].replace('Z', '+00:00'))
            previous_stamp = datetime.fromisoformat(old['lastModifiedDateTime'].replace('Z', '+00:00'))
            if stamp.utcoffset() is None or previous_stamp.utcoffset() is None or stamp < previous_stamp:
                raise ValueError('Unverified version time')
        except (ValueError, KeyError, TypeError, AttributeError):
            codes.add('unverified_time')
            continue
        modified_by = new.get('lastModifiedBy')
        modified_by = modified_by if isinstance(modified_by, dict) else {}
        identity = modified_by.get('user') if isinstance(modified_by.get('user'), dict) else {}
        actor = identity.get('displayName') or identity.get('email') or identity.get('id')
        if not (isinstance(actor, str) and actor.strip()):
            app = modified_by.get('application') if isinstance(modified_by.get('application'), dict) else {}
            name = app.get('displayName')
            # An unattributed version only affects WHO, not whether the change happened: it is shown explicitly, never invented.
            actor = f'Aplicación: {name}' if isinstance(name, str) and name.strip() else 'No identificado en SharePoint'
        common = {'date': stamp.astimezone(ZONE).strftime('%d/%m/%Y %H:%M'), 'at': stamp.isoformat(),
                  'actor': actor, 'version': new['id'], 'source': 'SharePoint'}
        if value(before, service.FUNNEL) == admission.REQUESTED and value(after, service.FUNNEL) == admission.APPROVED:
            prior, effective = commitment(before), commitment(after)
            reason = ('Reconciliación de compromiso existente' if prior is True and effective is True else
                      'Aprobación por compromiso nuevo' if prior is False and effective is True else
                      'Motivo no determinable a partir de las versiones disponibles')
            rows.append({**common, 'id': f'version:{item_id}:{new["id"]}:funnel',
                         'movement': 'Reclasificación de Funnel', 'before': admission.REQUESTED,
                         'after': admission.APPROVED, 'badge_kind': 'funnel', 'text': reason})
        for key, label in ((service.STATUS, 'Cambio de Arquitectura'), (service.PLANNED, 'Cambio de fecha planificada'), (service.ACTUAL, 'Cambio de fecha real')):
            a, b = value(before, key), value(after, key)
            if key != service.STATUS:
                if any(v and not service.date_key(v) for v in (a, b)):
                    codes.add('malformed_value')
                    continue
                a, b = service.date_key(a), service.date_key(b)
            elif any(v is not None and not isinstance(v, str) for v in (a, b)):
                codes.add('malformed_value')
                continue
            if a != b:
                rows.append({**common, 'id': f'version:{item_id}:{new["id"]}:{key}', 'movement': label,
                             'before': a if key == service.STATUS else service.display_date(a),
                             'after': b if key == service.STATUS else service.display_date(b),
                             'badge_kind': 'architecture' if key == service.STATUS else '', 'text': ''})
    rows.sort(key=lambda r: datetime.fromisoformat(r['at']) if r.get('at') else datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return rows, coverage_summary(codes)


def version_history(html, item_id, versions, *, partial=False, current_modified=None):
    """Compatibility wrapper: `(rows, partial)` where `partial` means a WARNING-level gap (retention and a single
    version are informative scope, not partial coverage)."""
    rows, coverage = version_history_coverage(html, item_id, versions, issues=('version_read_error',) if partial else (),
                                              current_modified=current_modified)
    return rows, coverage['partial']


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
