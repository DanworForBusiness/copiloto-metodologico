"""Business rules for Operations requests."""
from html.parser import HTMLParser

TRANSITIONS = {
    "": ("Pendiente",),
    "Pendiente": ("Planificado", "Suspendido"),
    "Planificado": ("Pendiente", "En curso", "Suspendido"),
    "En curso": ("Planificado", "Suspendido", "Finalizado"),
    "Suspendido": ("Pendiente", "Planificado", "En curso"),
    "Finalizado": (),
}
CREATION_DATE = "Fecha_x0020_de_x0020_creaci_x00f"
DESCRIPTION = "Descripci_x00f3_n_x0020_de_x0020"
STATUS = "Status_x0020_Arquitectura"
FUNNEL = "Status_x0020_funnel"
ASSIGNEES = "Colaborador_x0020_Asignado"


def allowed_destinations(current: str | None) -> tuple[str, ...]:
    return TRANSITIONS.get(current or "", ())


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_text(value: str | None) -> str:
    parser = _PlainText()
    parser.feed(value or "")
    parser.close()
    return " ".join(" ".join(parser.parts).split())


def select_rows(rows: list[dict], query: str, filters: dict) -> list[dict]:
    query = query.strip().casefold()
    result = []
    for row in rows:
        fields = row.get("fields", {})
        if any(value and value not in values(fields.get(key)) for key, value in filters.items()):
            continue
        if query and not any(
            query in value.casefold()
            for value in (" ".join(values(fields.get("Proyecto"))), plain_text(fields.get(DESCRIPTION)))
        ):
            continue
        result.append(row)
    return sorted(result, key=lambda row: row.get("fields", {}).get(CREATION_DATE) or "", reverse=True)


def values(value) -> list[str]:
    if isinstance(value, list):
        return [label for item in value for label in values(item)]
    if isinstance(value, dict):
        value = value.get("LookupValue") or value.get("lookupValue")
    return [str(value)] if value is not None and value != "" else []

PLANNED = "Fecha_x0020_de_x0020_inicio_x002"
ACTUAL = "Fecha_x0020_de_x0020_inicio_x0020"
COMMENTS = "Comentarios_x0020_de_x0020_Arqui"
PROJECT = "Proyecto"
EDITABLE = (STATUS, "Prioridad", PROJECT, ASSIGNEES, PLANNED, ACTUAL, DESCRIPTION, COMMENTS)


def lookup_ids(fields, key):
    raw = fields.get(key + "LookupId")
    if raw is None:
        raw = fields.get(key)
        raw = raw if isinstance(raw, list) else [raw]
        return [str(item.get("LookupId")) for item in raw if isinstance(item, dict) and item.get("LookupId") is not None]
    return [str(item) for item in (raw if isinstance(raw, list) else [raw]) if item not in ("", None)]


def form_values(fields):
    result = {key: fields.get(key) or "" for key in EDITABLE}
    for key in (PLANNED, ACTUAL):
        result[key] = result[key][:10]
    result[PROJECT] = next(iter(lookup_ids(fields, PROJECT)), "")
    result[ASSIGNEES] = lookup_ids(fields, ASSIGNEES)
    return result


class _SafeMarkup(HTMLParser):
    tags = set("p div span br strong b em i u s strike ul ol li a img table caption thead tbody tfoot tr td th h1 h2 h3 h4 h5 h6 blockquote pre code hr".split())
    attributes = {"a": {"href", "title"}, "img": {"src", "alt", "title", "width", "height"},
                  "td": {"colspan", "rowspan"}, "th": {"colspan", "rowspan", "scope"}}

    def handle_starttag(self, tag, attrs):
        from urllib.parse import urlsplit

        if tag not in self.tags:
            raise ValueError("El HTML incluye un elemento no admitido.")
        for name, value in attrs:
            if name not in self.attributes.get(tag, set()) or value is None:
                raise ValueError("El HTML incluye un atributo no admitido.")
            if name in {"href", "src"}:
                url = urlsplit(value)
                if url.scheme.lower() not in ("", "https", "http") or value.startswith(("//", "\\")) or any(ord(c) < 32 for c in value):
                    raise ValueError("El HTML incluye un enlace no admitido.")

    def handle_endtag(self, tag):
        if tag not in self.tags:
            raise ValueError("El HTML incluye un elemento no admitido.")

    def handle_decl(self, decl):
        raise ValueError("El HTML incluye una declaración no admitida.")


def changes(current, submitted, options):
    from datetime import date

    if set(submitted) - set(EDITABLE):
        raise ValueError("Se intentó editar un campo protegido.")
    result = {}
    for key, value in submitted.items():
        if key == ASSIGNEES:
            if value == current[key]:
                continue
            if len(current[key]) > 1:
                raise ValueError("La asignación múltiple existente debe conservarse.")
            if len(value) > 1 or any(item not in options[key] for item in value):
                raise ValueError("Seleccioná un único colaborador válido.")
            result[key + "LookupId@odata.type"] = "Collection(Edm.String)"
            result[key + "LookupId"] = value
            continue
        if value == current[key]:
            continue
        if key == STATUS and value not in allowed_destinations(current[key]):
            raise ValueError("La transición de estado no está permitida.")
        if key == "Prioridad" and value not in ("", "Baja", "Media", "Alta"):
            raise ValueError("Seleccioná una prioridad válida.")
        if key == PROJECT:
            if value and value not in options[key]:
                raise ValueError("Seleccioná un proyecto válido.")
            result[key + "LookupId"] = value or None
        elif key in (PLANNED, ACTUAL):
            if value and date.fromisoformat(value).isoformat() != value:
                raise ValueError("Ingresá una fecha válida.")
            result[key] = value + "T00:00:00Z" if value else None
        else:
            if key in (DESCRIPTION, COMMENTS):
                _SafeMarkup().feed(value)
            result[key] = value or None
    return result
