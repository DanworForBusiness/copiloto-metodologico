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
    fields = ",".join(("Title", service.PROJECT, service.PROJECT + "LookupId", service.DESCRIPTION, "Prioridad", service.FUNNEL,
                       service.STATUS, service.ASSIGNEES, service.CREATION_DATE, service.PLANNED,
                       service.ASSIGNEES + "LookupId", service.ACTUAL))
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
    projects = {}
    if any(service.project_lookup_id(row.get("fields", {})) for row in rows):
        projects = lookup_options(token, (service.PROJECT,), base=base)[service.PROJECT]
    for row in rows:
        row["project_label"] = service.project_label(row.get("fields", {}), projects)
    return rows


def read_columns(token: str) -> dict:
    return graph_get_json(f"{list_url(token)}/columns", token)


def read_creation_columns(token: str) -> list[dict]:
    base = list_url(token)
    prefix = f"{base}/columns"
    url = prefix + "?$select=name,displayName,required,readOnly,hidden,defaultValue,text,choice,dateTime,lookup,calculated,validation"
    columns = []
    for _ in range(40):
        if not url.startswith(prefix + "?"):
            raise ValueError("Invalid columns continuation URL")
        page = graph_get_json(url, token)
        columns.extend(page["value"])
        url = page.get("@odata.nextLink")
        if not url:
            return columns
    raise ValueError("Columns page limit exceeded")


def create_item(token: str, payload: dict) -> dict:
    from core.microsoft365 import graph_post_json

    return graph_post_json(f"{list_url(token)}/items", token, payload)


def item_url(token: str, item_id: str) -> str:
    return f"{list_url(token)}/items/{quote(item_id, safe='')}"


def read_item(token: str, item_id: str) -> dict:
    return graph_get_json(f"{item_url(token, item_id)}?$expand=fields", token)


def update_item(token: str, item_id: str, payload: dict, etag: str) -> dict:
    from core.microsoft365 import graph_patch_json

    if not etag or etag == "*":
        raise ValueError("A current item ETag is required")
    return graph_patch_json(f"{item_url(token, item_id)}/fields", token, payload, etag=etag)


def delete_item(token: str, item_id: str, etag: str) -> None:
    from core.microsoft365 import graph_delete

    if not etag or etag == "*":
        raise ValueError("A current item ETag is required")
    graph_delete(item_url(token, item_id), token, extra_headers={"If-Match": etag})


def lookup_options(token: str, fields=(service.PROJECT, service.ASSIGNEES, service.STATUS), *, base=None, columns=None) -> dict:
    base = base or list_url(token)
    if columns is None:
        columns = []
        prefix = f"{base}/columns"
        url = prefix
        for _ in range(40):
            if url != prefix and not url.startswith(prefix + "?"):
                raise ValueError("Invalid columns continuation URL")
            page = graph_get_json(url, token)
            columns.extend(page["value"])
            url = page.get("@odata.nextLink")
            if not url:
                break
        else:
            raise ValueError("Columns page limit exceeded")
    definitions = {column["name"]: column for column in columns}
    site = base.rsplit("/lists/", 1)[0]
    result = {service.STATUS: service.status_choices(columns)} if service.STATUS in fields else {}
    for field in fields:
        if field == service.STATUS:
            continue
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
                label = item["fields"].get(lookup["columnName"])
                if label or field != service.PROJECT:
                    options[str(item["id"])] = label or item["id"]
            url = page.get("@odata.nextLink")
            if not url:
                break
        else:
            raise ValueError("Lookup page limit exceeded")
        result[field] = options
    return result
