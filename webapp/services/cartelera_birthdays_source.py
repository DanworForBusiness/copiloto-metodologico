"""cartelera_birthdays_source.py — Cumpleaños de Cartelera Virtual con el Excel de SharePoint como FUENTE DE VERDAD (solo lectura).

Misma arquitectura que los Catálogos CCHH (`human_capital_catalog_repository`): el Excel se lee vía Microsoft Graph con el token
DELEGADO de LECTURA del Hub (MSAL en el navegador, `User.Read`/`Sites.Read.All`), inyectado por el hook transversal
`data-dw-graph-token` de `hub_auth.js` en un request explícito de refresh, y se MATERIALIZA como read-model en el repositorio de
Cartelera (`settings/birthdays-sharepoint`, write-through a Spaces vía `remote_store`). Todas las experiencias (Gestión → Cumpleaños,
próximos, Inicio, calendario, panel de Comunidad, saludos) leen SOLO ese read-model: no hay una segunda base de cumpleaños ni
sincronización manual. El Hub no tiene identidad de aplicación con permisos de SharePoint (la única app-only es la de Notificaciones,
`Mail.Send`), así que el server nunca lee el Excel por su cuenta.

Política de actualización: cada apertura de Gestión → Cumpleaños pide un refresh con el token de quien la abre (y el botón «Actualizar
desde SharePoint» lo fuerza). Se consulta primero la METADATA del archivo (barata): si su `cTag` (versión del contenido) no cambió, no se
descarga ni se reprocesa; si cambió, se descarga, se parsea y se reemplaza el read-model. Un fallo (sin token, 401/403/404, Excel
ilegible) NUNCA borra el último read-model válido ni cae a datos DEMO: queda registrado en `last_error` y se muestra en Gestión.

Privacidad: del Excel se conserva SOLO nombre, día, mes y autorización de publicación. El año de nacimiento, correos u otros
identificadores nunca se persisten: la identidad estable es un hash (ver `_identity`)."""
from __future__ import annotations

import base64
import hashlib
import io
import re
import threading
import unicodedata
import urllib.error
from datetime import date, datetime

from core import microsoft365
from webapp.services import cartelera_repository as repo

# ── referencia del archivo (hardcodeada en esta primera prueba; sin variables de entorno) ───────────────────────────────────────────
# Discovery humano: SharePoint «Comunicaciones» → CCHH HUB → Cartelera Virtual → Cumpleaños. El enlace es solo el punto de entrada: en
# cada lectura se resuelve la IDENTIDAD ESTABLE del archivo (driveId + itemId), que queda registrada en el read-model.
SHARE_URL = "https://danwor.sharepoint.com/:x:/s/Comunicaciones/IQBnA5KC9-pTRIdekOHH_WUOAXZIAyjF-fqEmKSoIhXPHMc?e=BV6Jfl"
# Identidad estable resuelta (driveId, itemId). Vacía hasta acreditarla con el primer acceso real; con ella se lee por identidad y no
# por enlace (un enlace de uso compartido puede revocarse).
# Acreditada en la primera lectura real (2026-11-26): «Comunicaciones / CCHH HUB / Cartelera virtual / Cumpleaños / Cumpleaños.xlsx»
# (mismo drive que el resto de Capital Humano).
DRIVE_ID = "b!kEfGZTP5uUaCP45K1X5psX3at7lnj6lGvidfF2OUcYoNrL5xTaXaRIqFJOVKAjrE"
ITEM_ID = "01ULA425LHAOJIF57KKNCIOXUQ4HD72ZIO"

# Interruptor de la fuente (constante de código, no de entorno). El arnés visual DEMO lo apaga explícitamente.
ENABLED = True

DOC = ("settings", "birthdays-sharepoint")
GRAPH = "https://graph.microsoft.com/v1.0"
PARSER_VERSION = 2                     # cambia ⇒ se reprocesa el Excel aunque su cTag no haya cambiado
MIN_CHECK_SECONDS = 20                 # dos aperturas seguidas no consultan Graph dos veces
MAX_BYTES = 5 * 1024 * 1024
_lock = threading.Lock()


class SourceError(Exception):
    def __init__(self, kind: str, message: str, status: int | None = None, detail: str = ""):
        super().__init__(message)
        self.kind, self.status, self.detail = kind, status, detail


# ── lectura del read-model (tokenless) ──────────────────────────────────────────────────────────────────────────────────────────────
def snapshot() -> dict | None:
    return repo.get(*DOC)


def rows() -> list[dict]:
    snap = snapshot() or {}
    out = [{**r, "revision": 0, "source": "sharepoint"} for r in snap.get("rows") or []]
    return sorted(out, key=lambda b: (int(b.get("month", 0)), int(b.get("day", 0)), b.get("name", "").lower()))


def get(birthday_id: str) -> dict | None:
    return next((r for r in rows() if r["id"] == birthday_id), None)


def status() -> dict:
    """Estado para la UI de Gestión (sin filas)."""
    snap = snapshot()
    if not snap:
        return {"loaded": False, "last_error": None}
    return {"loaded": bool(snap.get("refreshed_at")), "source": snap.get("source") or {}, "refreshed_at": snap.get("refreshed_at"),
            "checked_at": snap.get("checked_at"), "count": len(snap.get("rows") or []),
            "authorized": sum(1 for r in snap.get("rows") or [] if r.get("authorized")), "identity_basis": snap.get("identity_basis"),
            "consent_column": snap.get("consent_column"), "warnings": snap.get("warnings") or [], "structure": snap.get("structure") or {},
            "last_error": snap.get("last_error")}


# ── Graph (token delegado del usuario) ──────────────────────────────────────────────────────────────────────────────────────────────
def _share_id(url: str) -> str:
    return "u!" + base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def _item_url(drive_id: str = "", item_id: str = "") -> str:
    if drive_id and item_id:
        return f"{GRAPH}/drives/{drive_id}/items/{item_id}"
    return f"{GRAPH}/shares/{_share_id(SHARE_URL)}/driveItem"


def _graph_error(exc: urllib.error.HTTPError) -> SourceError:
    code, message = "", ""
    try:
        import json
        body = json.loads(exc.read().decode("utf-8", "replace") or "{}").get("error") or {}
        code, message = str(body.get("code") or ""), str(body.get("message") or "")[:300]
    except Exception:                                                     # noqa: BLE001 — el cuerpo es opcional
        pass
    detail = f"HTTP {exc.code}{' · ' + code if code else ''}{' · ' + message if message else ''}"
    if exc.code == 401:
        return SourceError("auth_required", "La sesión de datos con Microsoft venció o no se autorizó.", 401, detail)
    if exc.code == 403:
        return SourceError("forbidden", "Microsoft rechazó la lectura del Excel: la cuenta no tiene permiso sobre el archivo o el sitio.", 403, detail)
    if exc.code == 404:
        return SourceError("not_found", "No encontramos el Excel de cumpleaños en SharePoint (enlace o identidad no visibles para la cuenta).", 404, detail)
    return SourceError("fetch", f"SharePoint respondió con un error (HTTP {exc.code}).", exc.code, detail)


def _get_json(url: str, token: str) -> dict:
    try:
        return microsoft365.graph_get_json(url, token)
    except urllib.error.HTTPError as exc:
        raise _graph_error(exc) from None
    except Exception as exc:                                              # noqa: BLE001
        raise SourceError("fetch", f"No pudimos conectar con SharePoint ({type(exc).__name__}).") from None


def _get_bytes(url: str, token: str) -> bytes:
    try:
        data = microsoft365.graph_get_bytes(url, token)
    except urllib.error.HTTPError as exc:
        raise _graph_error(exc) from None
    except Exception as exc:                                              # noqa: BLE001
        raise SourceError("fetch", f"No pudimos descargar el Excel ({type(exc).__name__}).") from None
    if len(data) > MAX_BYTES:
        raise SourceError("workbook", "El Excel supera el tamaño admitido (5 MB).")
    return data


# ── parseo del Excel ────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _norm(text) -> str:
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


# Encabezados reconocidos (normalizados). Se buscan por igualdad y luego por contención, en este orden de preferencia.
ROLES = {
    "full_name": ["nombre y apellido", "apellido y nombre", "nombre completo", "colaborador", "colaboradora", "empleado", "persona", "nombre"],
    "first_name": ["nombres", "primer nombre"],
    "last_name": ["apellido", "apellidos"],
    "birth_date": ["fecha de nacimiento", "fecha nacimiento", "nacimiento", "fecha de cumpleanos", "cumpleanos", "cumple", "fecha"],
    "day": ["dia", "dia de nacimiento", "dia cumpleanos"],
    "month": ["mes", "mes de nacimiento", "mes cumpleanos"],
    "email": ["email", "e mail", "mail", "correo", "correo electronico", "usuario", "upn"],
    "employee_id": ["legajo", "id empleado", "numero de empleado", "nro de legajo", "n de legajo", "codigo de empleado", "id colaborador"],
    "consent": ["autorizo", "autorizo publicacion", "autoriza publicacion", "autorizacion de publicacion", "autoriza", "autorizacion", "autorizado", "consentimiento",
                "publicar", "publicacion", "mostrar en cartelera", "mostrar"],
}
_YES = {"si", "s", "yes", "y", "true", "verdadero", "x", "1", "autorizado", "autoriza", "ok", "publicar"}
MONTHS = {m: i for i, m in enumerate(["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
                                      "noviembre", "diciembre"], 1)} | {"setiembre": 9}


def _match_roles(headers: list[str]) -> dict[str, int]:
    found: dict[str, int] = {}
    normed = [_norm(h) for h in headers]
    for role, names in ROLES.items():
        for strict in (True, False):
            for name in names:
                for i, h in enumerate(normed):
                    if not h or i in found.values():
                        continue
                    if (h == name) if strict else (name in h.split() or (len(name) > 4 and name in h)):
                        found[role] = i
                        break
                if role in found:
                    break
            if role in found:
                break
    if "full_name" in found and "last_name" in found and normed[found["full_name"]] == "nombre":
        found["first_name"] = found.pop("full_name")                     # «Nombre» + «Apellido» separados
    return found


def _kind(value) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return "vacío"
    if isinstance(value, bool):
        return "booleano"
    if isinstance(value, (datetime, date)):
        return "fecha"
    if isinstance(value, (int, float)):
        return "número"
    return "texto"


def _day_month(date_value, day_value, month_value) -> tuple[int, int] | None:
    d = m = None
    if isinstance(date_value, (datetime, date)):
        d, m = date_value.day, date_value.month
    elif isinstance(date_value, str) and date_value.strip():
        hit = re.match(r"^\s*(\d{1,2})\s*[/\-.]\s*(\d{1,2})(?:\s*[/\-.]\s*\d{2,4})?\s*$", date_value)
        if hit:
            d, m = int(hit.group(1)), int(hit.group(2))
        else:
            hit = re.match(r"^\s*(\d{1,2})\s+(?:de\s+)?([a-zA-Záéíóú]+)", date_value)
            if hit and _norm(hit.group(2)) in MONTHS:
                d, m = int(hit.group(1)), MONTHS[_norm(hit.group(2))]
    if d is None and day_value not in (None, ""):
        try:
            d = int(float(day_value))
            mv = month_value
            m = MONTHS.get(_norm(mv)) if isinstance(mv, str) and not mv.strip().isdigit() else int(float(mv))
        except (TypeError, ValueError):
            return None
    if not d or not m:
        return None
    try:
        date(2024, int(m), int(d))
    except ValueError:
        return None
    return int(d), int(m)


def _identity(basis: str, key: str) -> str:
    return "sp" + hashlib.sha256(f"cartelera-birthday|{basis}|{key}".encode("utf-8")).hexdigest()[:30]


def parse_workbook(xlsx: bytes) -> dict:
    """Estructura + filas normalizadas. NUNCA inventa: sin columna de autorización ⇒ nadie queda autorizado; sin un identificador
    personal (correo o legajo) ⇒ la identidad se deriva de nombre + día + mes y se informa como identidad débil."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    except Exception as exc:                                              # noqa: BLE001
        raise SourceError("workbook", f"El archivo no es un .xlsx legible ({type(exc).__name__}).") from None
    sheets, best = [], None
    for ws in wb.worksheets:
        grid = [list(r) for r in ws.iter_rows(values_only=True)]
        grid = [r for r in grid if any(c not in (None, "") for c in r)] if grid else []
        info = {"sheet": ws.title, "rows": len(grid), "header_row": None, "headers": [], "types": {}, "roles": {}}
        for idx, row in enumerate(grid[:10]):
            headers = [str(c).strip() if c is not None else "" for c in row]
            roles = _match_roles(headers)
            has_name = any(k in roles for k in ("full_name", "first_name"))
            has_date = "birth_date" in roles or ("day" in roles and "month" in roles)
            if has_name and has_date:
                body = grid[idx + 1:]
                types = {}
                for j, h in enumerate(headers):
                    seen = sorted({_kind(r[j] if j < len(r) else None) for r in body[:200]} - {"vacío"})
                    types[h or f"(columna {j + 1})"] = ", ".join(seen) or "vacío"
                info.update(header_row=idx + 1, headers=headers, types=types, rows=len(body),
                            roles={k: headers[v] for k, v in roles.items()})
                if best is None:
                    best = (ws.title, headers, roles, body, idx)
                break
        sheets.append(info)
    wb.close()
    if best is None:
        raise SourceError("workbook", "No encontramos una hoja con columnas de nombre y fecha de cumpleaños.",
                          detail="; ".join(f"{s['sheet']}: {s['rows']} filas" for s in sheets))
    sheet, headers, roles, body, header_idx = best
    basis = "email" if "email" in roles else ("employee_id" if "employee_id" in roles else "name_date")
    warnings: list[str] = []
    if basis == "name_date":
        warnings.append("El Excel no tiene correo ni legajo: la identidad de cada persona se deriva de nombre + día + mes. Si cambia el nombre o la "
                        "fecha, se considera otra persona (sus saludos e interacciones anteriores no se trasladan).")
    if "consent" not in roles:
        warnings.append("El Excel no tiene una columna de autorización de publicación: ningún cumpleaños se muestra en la Cartelera hasta que exista.")
    cell = lambda r, role: (r[roles[role]] if role in roles and roles[role] < len(r) else None)   # noqa: E731
    out, seen, skipped = [], {}, 0
    for n, r in enumerate(body, start=header_idx + 2):              # n.º de fila aproximado (sin contar filas vacías)
        if "full_name" in roles:
            name = str(cell(r, "full_name") or "").strip()
        else:
            name = " ".join(str(x).strip() for x in (cell(r, "first_name"), cell(r, "last_name")) if x not in (None, ""))
        dm = _day_month(cell(r, "birth_date"), cell(r, "day"), cell(r, "month"))
        if not name or not dm:
            skipped += 1
            continue
        if basis == "email":
            key = str(cell(r, "email") or "").strip().lower()
        elif basis == "employee_id":
            key = str(cell(r, "employee_id") or "").strip().lower()
        else:
            key = f"{_norm(name)}|{dm[0]:02d}{dm[1]:02d}"
        if not key:
            skipped += 1
            warnings.append(f"Fila {n}: sin {'correo' if basis == 'email' else 'legajo'}; no se incorpora (no tiene identidad estable).")
            continue
        bid = _identity(basis, key)
        if bid in seen:
            warnings.append(f"Fila {n}: duplica la identidad de la fila {seen[bid]}; se conserva la primera.")
            continue
        seen[bid] = n
        consent = cell(r, "consent")
        authorized = bool(consent) if isinstance(consent, bool) else _norm(consent) in _YES
        out.append({"id": bid, "name": name[:120], "day": dm[0], "month": dm[1], "authorized": authorized, "row": n})
    if skipped:
        warnings.append(f"{skipped} fila{'s' if skipped != 1 else ''} sin nombre o con fecha inválida no se incorporaron.")
    structure = {"sheets": sheets, "used_sheet": sheet, "roles": {k: headers[v] for k, v in roles.items()}}
    if "consent" in roles:                                                # cómo se interpretó cada valor de la autorización (sin nombres)
        counts: dict[str, int] = {}
        for r in body:
            raw = cell(r, "consent")
            label = "(vacío)" if raw in (None, "") else str(raw).strip()[:40]
            counts[label] = counts.get(label, 0) + 1
        structure["consent_values"] = [{"value": k, "count": v, "authorized": _norm(k) in _YES or k is True}
                                       for k, v in sorted(counts.items(), key=lambda kv: -kv[1])][:20]
    return {"rows": out, "warnings": warnings[:60], "identity_basis": basis,
            "consent_column": headers[roles["consent"]] if "consent" in roles else None, "structure": structure}


# ── refresh (token-carrying) ────────────────────────────────────────────────────────────────────────────────────────────────────────
def _save(fields: dict) -> dict:
    current = snapshot()
    return repo.save(*DOC, fields, expected_revision=current.get("revision") if current else None)


def _record_error(err: SourceError, actor: str) -> None:
    try:
        _save({"last_error": {"at": repo.now_iso(), "kind": err.kind, "status": err.status, "message": str(err), "detail": err.detail,
                              "by": actor}})
    except Exception:                                                     # noqa: BLE001 — registrar el error nunca tapa el error original
        pass


def refresh(graph_token: str, *, actor: str, force: bool = False) -> dict:
    """Lee el Excel si cambió y reemplaza el read-model. Devuelve `{"changed": bool, "checked": bool}`; lanza `SourceError`."""
    if not graph_token:
        err = SourceError("auth_required", "Falta la autorización de Microsoft para leer el Excel.")
        _record_error(err, actor)
        raise err
    with _lock:
        snap = snapshot() or {}
        if not force and snap.get("checked_at") and not snap.get("last_error"):
            try:
                age = (datetime.fromisoformat(repo.now_iso()) - datetime.fromisoformat(snap["checked_at"])).total_seconds()
            except ValueError:
                age = MIN_CHECK_SECONDS
            if age < MIN_CHECK_SECONDS:
                return {"changed": False, "checked": False}
        src = snap.get("source") or {}
        try:
            meta = _get_json(_item_url(DRIVE_ID or src.get("drive_id", ""), ITEM_ID or src.get("item_id", "")), graph_token)
            drive_id = (meta.get("parentReference") or {}).get("driveId") or DRIVE_ID
            source = {"share_url": SHARE_URL, "drive_id": drive_id, "item_id": meta.get("id") or ITEM_ID, "name": meta.get("name"),
                      "web_url": meta.get("webUrl"), "ctag": meta.get("cTag"), "etag": meta.get("eTag"),
                      "last_modified": meta.get("lastModifiedDateTime"),
                      "last_modified_by": ((meta.get("lastModifiedBy") or {}).get("user") or {}).get("displayName"),
                      "path": (meta.get("parentReference") or {}).get("path")}
            if not force and snap.get("refreshed_at") and src.get("ctag") and src.get("ctag") == source["ctag"]                     and snap.get("parser_version") == PARSER_VERSION:
                _save({"checked_at": repo.now_iso(), "source": source, "last_error": None})
                return {"changed": False, "checked": True}
            xlsx = _get_bytes(f"{GRAPH}/drives/{source['drive_id']}/items/{source['item_id']}/content", graph_token)
            parsed = parse_workbook(xlsx)
        except SourceError as err:
            _record_error(err, actor)
            raise
        now = repo.now_iso()
        changed = [(r["id"], r["name"], r["day"], r["month"], r["authorized"]) for r in parsed["rows"]] != \
                  [(r["id"], r["name"], r["day"], r["month"], r["authorized"]) for r in snap.get("rows") or []]
        _save({**parsed, "source": source, "refreshed_at": now, "checked_at": now, "refreshed_by": actor, "last_error": None,
               "parser_version": PARSER_VERSION})
        return {"changed": changed, "checked": True}
