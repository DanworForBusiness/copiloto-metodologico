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


def edit_permissions(token: str, item: dict, user: dict) -> tuple[bool, bool]:
    """Request/Architecture rights from persisted identities, never display names."""
    from webapp import access
    from webapp.services.reporting_repository import ReportingRepository, ReportingConfigurationError, user_info_identity
    import config

    if access.is_owner_user(user):
        return True, True
    actor = str(user.get("sub") or "").strip().lower()
    if not actor:
        return False, False
    creator = str(((item.get("createdBy") or {}).get("user") or {}).get("email") or "").strip().lower()
    fields = item.get("fields") or {}
    if not creator and fields.get("AuthorLookupId"):
        site = list_url(token).rsplit('/lists/', 1)[0]
        author = graph_get_json(f"{site}/lists/{quote(config.SHAREPOINT_USER_INFORMATION_LIST_ID, safe='')}/items/{quote(str(fields['AuthorLookupId']), safe='')}?$expand=fields", token)
        creator = user_info_identity(author.get("fields"))
    assigned = False
    ids = service.lookup_ids(fields, service.ASSIGNEES)
    if ids:
        try:
            _, people = ReportingRepository().get_colaboradores_padron(token)
        except ReportingConfigurationError as exc:
            raise ValueError("No se pudo resolver la identidad del colaborador asignado.") from exc
        assigned = str(people.get(actor) or "") in ids
    return actor == creator or assigned, assigned


def read_item_versions(token: str, item_id: str) -> dict:
    """Read-only, bounded detail history. No event store and no extra writes.

    The documented per-version GET expands fields when the collection omits them.
    A limit or failed version read makes coverage partial, never an empty success.
    """
    from urllib.error import HTTPError, URLError
    prefix = item_url(token, item_id) + '/versions'
    url, versions, seen, partial, issues = prefix, [], set(), False, set()
    for _ in range(40):
        if url != prefix and not url.startswith(prefix + '?'):
            raise ValueError('Invalid version continuation URL')
        if url in seen:
            raise ValueError('Repeated version continuation URL')
        seen.add(url)
        page = graph_get_json(url, token)
        versions.extend(page['value'])
        url = page.get('@odata.nextLink')
        if len(versions) >= 200:
            partial = bool(url) or len(versions) > 200
            if partial:
                issues.add('version_limit')                                     # Graph lists newest first: the most recent 200 are kept
            versions = versions[:200]
            break
        if not url:
            break
    else:
        partial = True
        issues.add('version_limit')
    result = []
    for version in versions:
        version = dict(version)
        if not isinstance(version.get('fields'), dict):
            try:
                identity = version['id']
                if not isinstance(identity, str) or not identity or not all(p.isascii() and p.isdecimal() for p in identity.split('.')):
                    raise ValueError('Invalid version identity')
                detail = graph_get_json(prefix + '/' + quote(identity, safe='') + '?$expand=fields', token)
                if detail.get('id') != identity:
                    raise ValueError('Mismatched version identity')
                version = detail
            except (HTTPError, URLError, TimeoutError, ValueError, KeyError):
                partial = True
                issues.add('version_read_error')
        result.append(version)
    return {'versions': result, 'partial': partial, 'issues': sorted(issues)}


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
