"""Microsoft Graph source for the Operations requests list."""
from urllib.parse import quote

from core.microsoft365 import graph_get_json
from webapp.services import solicitudes_service as service

GRAPH = "https://graph.microsoft.com/v1.0"
LIST_ID = "5d054bcc-8516-4fd5-b538-2d4cc5d754fb"


def list_url(token: str) -> str:
    site = graph_get_json(f"{GRAPH}/sites/danwor.sharepoint.com:/sites/Gestion", token)
    return f"{GRAPH}/sites/{quote(site['id'], safe=',')}/lists/{LIST_ID}"


def read_requests(token: str) -> list[dict]:
    """Read every authorized page before applying global search and filters."""
    fields = ",".join(("Title", service.PROJECT, service.DESCRIPTION, "Prioridad", service.FUNNEL,
                       service.STATUS, service.ASSIGNEES, service.CREATION_DATE))
    base = list_url(token)
    url = f"{base}/items?$expand=fields($select={fields})"
    rows = []
    for _ in range(40):
        if not url.startswith(f"{GRAPH}/sites/"):
            raise ValueError("Invalid Graph continuation URL")
        page = graph_get_json(url, token)
        rows.extend(page["value"])
        url = page.get("@odata.nextLink")
        if not url:
            break
    else:
        raise ValueError("Graph page limit exceeded")
    unresolved = []
    for row in rows:
        fields = row.get("fields", {})
        ids = service.lookup_ids(fields, service.PROJECT)
        if ids and not service.values(fields.get(service.PROJECT)):
            unresolved.append((fields, ids[0]))
    if unresolved:
        projects = lookup_options(token, (service.PROJECT,), base=base)[service.PROJECT]
        for fields, project_id in unresolved:
            if project_id in projects:
                fields[service.PROJECT] = projects[project_id]
    return rows


def read_columns(token: str) -> dict:
    return graph_get_json(f"{list_url(token)}/columns", token)


def item_url(token: str, item_id: str) -> str:
    return f"{list_url(token)}/items/{quote(item_id, safe='')}"


def read_item(token: str, item_id: str) -> dict:
    return graph_get_json(f"{item_url(token, item_id)}?$expand=fields", token)


def update_item(token: str, item_id: str, payload: dict, etag: str) -> dict:
    from core.microsoft365 import graph_patch_json

    return graph_patch_json(f"{item_url(token, item_id)}/fields", token, payload, etag=etag)


def lookup_options(token: str, fields=(service.PROJECT, service.ASSIGNEES), *, base=None) -> dict:
    base = base or list_url(token)
    columns = graph_get_json(f"{base}/columns", token)["value"]
    definitions = {column["name"]: column for column in columns}
    site = base.rsplit("/lists/", 1)[0]
    result = {}
    for field in fields:
        lookup = definitions[field]["lookup"]
        label = quote(lookup["columnName"], safe="")
        target = quote(lookup["listId"].strip("{}"), safe="")
        prefix = f"{site}/lists/{target}/items"
        url = f"{prefix}?$expand=fields($select={label})"
        options = {}
        for _ in range(40):
            if not url.startswith(prefix + "?"):
                raise ValueError("Invalid lookup continuation URL")
            page = graph_get_json(url, token)
            for item in page["value"]:
                options[str(item["id"])] = item["fields"].get(lookup["columnName"]) or item["id"]
            url = page.get("@odata.nextLink")
            if not url:
                break
        else:
            raise ValueError("Lookup page limit exceeded")
        result[field] = options
    return result
