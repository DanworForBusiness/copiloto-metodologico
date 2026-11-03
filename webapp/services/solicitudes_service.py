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