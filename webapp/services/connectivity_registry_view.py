"""connectivity_registry_view.py — vista «Tenants y ambientes» del Catálogo de conectividad (módulo 01, F2.2c.1).

Solo PRESENTACIÓN y AUTORIZACIÓN de lectura sobre el registro `connectivity-registry/v1` (estado por replay en
`connectivity_registry_store.load_state`). No escribe: la escritura es del router `connectivity_registry` (owner-only).

Lectura: MISMO criterio que la extracción F2.1 del módulo 01 (`datasphere_assessment_service.can_execute`: dueño o consultor con perfil
completo del Hub) y, por cliente, la restricción OPCIONAL de clientes de consultores ya existente (`datasphere_publication_service.client_authorized`).
Clientes elegibles: los que tienen conexiones PUBLICADAS en el Catálogo ∪ los que ya tienen eventos en el registro (sin alta libre de clientes).
Registrar un ambiente NO homologa conexiones: eso es `connectivity-homologation/v1` (F0), que este registro no lee ni escribe.
"""
from __future__ import annotations

import uuid

from core import remote_store
from tools.catalog.connectivity_governance import registry as reg
from webapp.services import connectivity_registry_store as store

# Motivos del replay → lenguaje de negocio. Nunca nombran tenants ni clientes ajenos.
REASON_TEXT = {
    "TENANT_ALREADY_REGISTERED": "Ese tenant_id ya está registrado. Los identificadores son inmutables y no se reutilizan, aunque el tenant esté desactivado.",
    "TENANT_BELONGS_TO_ANOTHER_CLIENT": "Ese tenant_id ya está registrado. Los identificadores son únicos en todo el registro y no se reutilizan.",
    "TENANT_NOT_REGISTERED": "El tenant no está registrado para este cliente.",
    "TENANT_DEACTIVATED": "El tenant está desactivado: no admite vínculos ni asignaciones nuevas.",
    "TENANT_HAS_ACTIVE_BINDINGS": "No se puede desactivar: primero desvinculá sus hosts y revocá sus asignaciones vigentes.",
    "HOST_BOUND_TO_ANOTHER_TENANT": "Ese host ya está vinculado a otro tenant. Si el vínculo es un error, desvinculalo primero desde ese tenant.",
    "UNBINDS_NON_ACTIVE_BINDING": "Ese vínculo de host ya no está vigente.",
    "SPACE_ALREADY_ASSIGNED": "Ese Space ya tiene una asignación vigente en este tenant. Para cambiarla usá «Reasignar».",
    "SUPERSEDES_WITHOUT_ACTIVE_ASSIGNMENT": "La asignación que querés reemplazar ya no está vigente.",
    "REVOKES_NON_ACTIVE_ASSIGNMENT": "Esa asignación ya no está vigente.",
    "DUPLICATE_ID_DIFFERENT_CONTENT": "El identificador del evento ya existe con otro contenido.",
}

KIND_TEXT = {
    reg.TENANT_REGISTERED: "Tenant registrado",
    reg.HOST_BOUND: "Host vinculado",
    reg.HOST_UNBOUND: "Host desvinculado",
    reg.SPACE_ASSIGNED: "Space asignado",
    reg.ASSIGNMENT_REVOKED: "Asignación revocada",
    reg.TENANT_DEACTIVATED: "Tenant desactivado",
}

NOTICE_TEXT = {
    "register-tenant": "Tenant registrado y confirmado en el registro.",
    "bind-host": "Host vinculado y confirmado en el registro.",
    "unbind-host": "Host desvinculado y confirmado en el registro.",
    "assign-space": "Asignación registrada y confirmada en el registro.",
    "revoke-assignment": "Asignación revocada y confirmada en el registro.",
    "deactivate-tenant": "Tenant desactivado y confirmado en el registro.",
}


def reason_text(reason: str) -> str:
    return REASON_TEXT.get(reason, "La operación contradice el registro vigente.")


def can_read(request) -> bool:
    from webapp.services import datasphere_assessment_service as asd
    return asd.can_execute(request)


def client_readable(request, client: str) -> bool:
    from webapp.services import datasphere_publication_service as pub
    return pub.client_authorized(request, client)


def _published_clients() -> list:
    from webapp.services import catalog_reads
    return [c["cliente"] for c in catalog_reads._catalog_connection_clients()]


def load():
    """(estado, error) — fail-closed: un registro ilegible NO se muestra a medias."""
    try:
        return store.load_state(), None
    except store.HistoryIntegrityError:
        return None, "El registro tiene objetos inconsistentes: no se muestra un estado dudoso. Revisalo antes de operar."
    except remote_store.RemoteReadError:
        return None, "El almacén gobernado no está disponible en este entorno: no se puede leer ni modificar el registro."


def clients_for(request, state) -> list:
    names = set(_published_clients())
    if state:
        names |= {t["client"] for t in state["tenants"].values()}
    return sorted((c for c in names if client_readable(request, c)), key=str.casefold)


def _tenant_view(t: dict) -> dict:
    spaces = sorted(t["spaces"].values(), key=lambda s: (reg.ENVIRONMENTS.index(s["environment"]), s["space"]))
    counts = {env: sum(1 for s in spaces if s["environment"] == env) for env in reg.ENVIRONMENTS}
    history = [{**h, "label": KIND_TEXT.get(h["kind"], h["kind"])} for h in reversed(t["history"])]
    return {**t, "spaces_list": spaces, "env_counts": counts, "history_view": history,
            "can_deactivate": t["active"] and not t["hosts"] and not t["spaces"]}


def view_model(request, *, client: str = "", tenant_id: str = "", notice: str = "", is_owner: bool = False) -> dict:
    state, error = load()
    clients = clients_for(request, state) if state is not None else sorted(
        (c for c in _published_clients() if client_readable(request, c)), key=str.casefold)
    selected = client if client in clients else ""
    tenants, rejected = [], []
    if state is not None and selected:
        from webapp.services import connectivity_capture_service as capsvc
        tenants = [{**_tenant_view(t), "captures": capsvc.captures_view(state, selected, t["tenant_id"])} for t in reg.tenants_of(state, selected)]
        if is_owner:
            rejected = [{**r, "text": reason_text(r["reason"]), "label": KIND_TEXT.get(r.get("kind"), r.get("kind") or "")}
                        for r in state["rejected"] if r.get("client") == selected]
    open_tenant = tenant_id if any(t["tenant_id"] == tenant_id for t in tenants) else (tenants[0]["tenant_id"] if len(tenants) == 1 else "")
    return {"clients": clients, "client": selected, "requested_client": client, "tenants": tenants, "open_tenant": open_tenant,
            "rejected": rejected, "error": error, "is_owner": is_owner, "environments": reg.ENVIRONMENTS,
            "notice": NOTICE_TEXT.get(notice, ""), "request_id": lambda: uuid.uuid4().hex}
