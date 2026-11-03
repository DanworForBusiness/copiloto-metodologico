"""Microsoft Graph source for the Operations requests list."""
from urllib.parse import quote

from core.microsoft365 import graph_get_json

GRAPH = "https://graph.microsoft.com/v1.0"
LIST_ID = "5d054bcc-8516-4fd5-b538-2d4cc5d754fb"


def list_url(token: str) -> str:
    site = graph_get_json(f"{GRAPH}/sites/danwor.sharepoint.com:/sites/Gestion", token)
    return f"{GRAPH}/sites/{quote(site['id'], safe=',')}/lists/{LIST_ID}"


def read_requests(token: str) -> list[dict]:
    """Read every authorized page before applying global search and filters."""
    url = f"{list_url(token)}/items?$expand=fields"
    rows = []
    for _ in range(40):
        if not url.startswith(f"{GRAPH}/sites/"):
            raise ValueError("Invalid Graph continuation URL")
        page = graph_get_json(url, token)
        rows.extend(page["value"])
        url = page.get("@odata.nextLink")
        if not url:
            return rows
    raise ValueError("Graph page limit exceeded")


def read_columns(token: str) -> dict:
    return graph_get_json(f"{list_url(token)}/columns", token)
