"""Business rules for Operations requests."""
from html import escape
from html.parser import HTMLParser
from datetime import date

CREATION_DATE = "Fecha_x0020_de_x0020_creaci_x00f"
DESCRIPTION = "Descripci_x00f3_n_x0020_de_x0020"
STATUS = "Status_x0020_Arquitectura"
FUNNEL = "Status_x0020_funnel"
ASSIGNEES = "Colaborador_x0020_Asignado"


class StatusCatalogError(ValueError):
    pass


def status_choices(columns):
    column = next((column for column in columns if column.get("name") == STATUS), {})
    choice = column.get("choice")
    choices = choice.get("choices") if isinstance(choice, dict) else None
    if (not isinstance(choices, list) or not choices
            or any(not isinstance(value, str) or not value.strip() for value in choices)
            or len(set(choices)) != len(choices) or column.get("readOnly")):
        raise StatusCatalogError("No se pudo obtener el catálogo de Status Arquitectura desde SharePoint. Reintentá la consulta antes de guardar.")
    return list(choices)


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
            for value in (fields.get("Title") or "", row_project_label(row), plain_text(fields.get(DESCRIPTION)))
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
NARRATIVE_FIELDS = (DESCRIPTION, COMMENTS)
EDITABLE = (STATUS, "Prioridad", PROJECT, ASSIGNEES, PLANNED, ACTUAL, DESCRIPTION, COMMENTS)


def lookup_ids(fields, key):
    raw = fields.get(key + "LookupId")
    if raw is None:
        raw = fields.get(key)
        raw = raw if isinstance(raw, list) else [raw]
        return [str(item.get("LookupId")) for item in raw if isinstance(item, dict) and item.get("LookupId") is not None]
    return [str(item) for item in (raw if isinstance(raw, list) else [raw]) if item not in ("", None)]


def _project_id(value):
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        text = str(value).strip()
        if text.isascii() and text.isdecimal() and text.strip("0"):
            return text.lstrip("0")
    return ""


def project_lookup_id(fields):
    raw = fields.get(PROJECT)
    embedded = raw.get("LookupId", raw.get("lookupId")) if isinstance(raw, dict) else raw
    return _project_id(fields.get(PROJECT + "LookupId")) or _project_id(embedded)


def project_label(fields, catalog=None):
    lookup_id = project_lookup_id(fields)
    raw = fields.get(PROJECT)
    direct = raw.get("LookupValue", raw.get("lookupValue")) if isinstance(raw, dict) else raw
    resolved = (catalog or {}).get(lookup_id)
    for value in (resolved, direct):
        if isinstance(value, str):
            label = value.strip()
            if label and not label.isnumeric():
                return label
    return ""


def row_project_label(row):
    if "project_label" in row:
        return row["project_label"]
    return project_label(row.get("fields", {}))


def form_values(fields):
    result = {key: fields.get(key) or "" for key in EDITABLE}
    for key in (PLANNED, ACTUAL):
        result[key] = result[key][:10]
    result[PROJECT] = project_lookup_id(fields)
    result[ASSIGNEES] = lookup_ids(fields, ASSIGNEES)
    for key in NARRATIVE_FIELDS:
        result[key] = narrative_text(result[key])
    return result


class _NarrativeText(HTMLParser):
    blocks = frozenset("address article blockquote caption dd div dl dt h1 h2 h3 h4 h5 h6 li ol p pre section table tr ul".split())
    hidden_tags = frozenset({"script", "style", "template"})

    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = []

    def boundary(self, separator="\n"):
        if self.parts and not self.parts[-1].endswith(("\n", "\t")):
            self.parts.append(separator)

    def handle_starttag(self, tag, attrs):
        if tag in self.hidden_tags:
            self.hidden.append(tag)
        elif not self.hidden:
            if tag == "br":
                self.parts.append("\n")
            elif tag in self.blocks:
                self.boundary()

    def handle_endtag(self, tag):
        if self.hidden:
            if tag == self.hidden[-1]:
                self.hidden.pop()
            return
        if tag in self.blocks:
            if tag == "tr" and self.parts and self.parts[-1] == "\t":
                self.parts.pop()
            self.boundary()
        elif tag in {"td", "th"}:
            self.boundary("\t")

    def handle_data(self, data):
        if not self.hidden and (data.strip() or not any(char in data for char in "\r\n")):
            self.parts.append(data)


def narrative_text(html):
    parser = _NarrativeText()
    parser.feed(html or "")
    parser.close()
    return normalize_narrative("".join(parser.parts))


def normalize_narrative(value):
    return value.replace("\r\n", "\n").replace("\r", "\n").strip("\n")


def changes(current, submitted, options):
    from datetime import date

    if set(submitted) - set(EDITABLE):
        raise ValueError("Se intentó editar un campo protegido.")
    result = {}
    for key, value in submitted.items():
        if key in NARRATIVE_FIELDS:
            value = normalize_narrative(value)
        # Preserve untouched nulls; every explicit state must be in SharePoint's catalog.
        if key == STATUS and (value or value != current[key]):
            catalog = options.get(STATUS)
            if not isinstance(catalog, list) or not catalog:
                raise StatusCatalogError("No se pudo validar el catálogo de Status Arquitectura desde SharePoint. Tu borrador se conserva.")
            if value not in catalog:
                raise ValueError("Seleccioná un Status Arquitectura válido del catálogo de SharePoint.")
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
            if key in NARRATIVE_FIELDS and value:
                value = "<div>" + escape(value, quote=False).replace("\n", "<br>") + "</div>"
            result[key] = value or None
    return result

def date_key(value):
    """Keep the calendar day supplied by SharePoint, without timezone conversion."""
    if not value:
        return ""
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except (TypeError, ValueError):
        return ""


def display_date(value):
    day = date_key(value)
    return date.fromisoformat(day).strftime("%d/%m/%Y") if day else "—"


SORT_COLUMNS = {
    "title": ("Solicitud", 20),
    "project": ("Proyecto", 15),
    "created": ("Creada", 10),
    "planned": ("Inicio", 10),
    "priority": ("Prioridad", 7),
    "funnel": ("Funnel", 9),
    "architecture": ("Arquitectura", 10),
    "collaborator": ("Colaborador", 9),
}


def sort_rows(rows, column, direction):
    """Sort the filtered universe, retaining creation order for ties."""
    if column not in SORT_COLUMNS:
        return rows
    priority_order = {"baja": 1, "media": 2, "alta": 3}

    def key(row):
        fields = row.get("fields", {})
        value = {
            "title": fields.get("Title"),
            "project": row_project_label(row),
            "created": date_key(fields.get(CREATION_DATE)),
            "planned": date_key(fields.get(PLANNED)),
            "priority": fields.get("Prioridad"),
            "funnel": fields.get(FUNNEL),
            "architecture": fields.get(STATUS),
            "collaborator": ", ".join(values(fields.get(ASSIGNEES))),
        }[column]
        text = (value or "").strip().casefold()
        return (priority_order.get(text, 0), text) if column == "priority" and text else text

    populated, empty = [], []
    for row in rows:
        value = key(row)
        (populated if value else empty).append((value, row))
    populated.sort(key=lambda pair: pair[0], reverse=direction == "desc")
    return [row for _, row in populated + empty]


KPIS = (
    # (clave, rótulo, modificador de color del primitivo `.activity-kpi`)
    ("aprobadas", "Aprobadas", "activity-kpi--positive"),
    ("en_curso", "En curso", "activity-kpi--info"),
    ("pendientes", "Pendientes", "activity-kpi--attention"),
    ("sin_movimiento", "Sin movimiento", "activity-kpi--alert"),
)


def kpi_match(row, key):
    """¿La solicitud entra en el alcance del KPI `key`? Mismos criterios literales que `kpi_counts`."""
    fields = row.get("fields", {})
    funnel, status = values(fields.get(FUNNEL)), values(fields.get(STATUS))
    return {"aprobadas": "Aprobado para Arquitectura" in funnel, "en_curso": "En curso" in status, "pendientes": "Pendiente" in status,
            "sin_movimiento": "Solicitado" in funnel and "Pendiente" in status}.get(key, False)


def kpi_counts(rows):
    """Indicadores operativos sobre el conjunto YA filtrado y buscado, antes de paginar y sin importar Vigentes/Cerradas.
    Criterios literales y NO excluyentes (una solicitud puede sumar en Pendientes y en Sin movimiento):
      Aprobadas = Funnel «Aprobado para Arquitectura»; En curso = Arquitectura «En curso»; Pendientes = Arquitectura «Pendiente»;
      Sin movimiento = Funnel «Solicitado» Y Arquitectura «Pendiente»."""
    return {key: sum(1 for row in rows if kpi_match(row, key)) for key, _label, _tone in KPIS}


def classify_rows(rows):
    """Presentation only: rejected Funnel wins; unknown states stay visible."""
    groups = {'vigentes': [], 'cerradas': []}
    unknown_ids = []
    for row in rows:
        fields = row.get('fields', {})
        status = fields.get(STATUS)
        rejected = fields.get(FUNNEL) == 'Rechazado, Aplica Operaciones'
        closed = rejected or status in ('Finalizado', 'Suspendido')
        groups['cerradas' if closed else 'vigentes'].append(row)
        if not rejected and status not in ('Pendiente', 'Planificado', 'En curso', 'Finalizado', 'Suspendido'):
            unknown_ids.append(str(row['id']))
    return groups, unknown_ids
