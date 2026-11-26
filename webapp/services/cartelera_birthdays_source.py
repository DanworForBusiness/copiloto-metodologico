"""cartelera_birthdays_source.py — PERSISTENCIA del registro de cumpleaños de Cartelera Virtual en el Excel de SharePoint.

El Excel es la ÚNICA fuente de verdad del registro (nombre, fecha, autorización de publicación). Este módulo es la capa de persistencia:
`cartelera_service` conserva las reglas funcionales (validaciones, publicabilidad, saludos) y la UX no sabe de dónde viene el dato. El Hub
sigue gobernando saludos, publicaciones, reacciones y comentarios (por la identidad estable de cada persona).

Autenticación (mecanismos EXISTENTES del Hub, sin app ni permisos nuevos):
  - LECTURA: token DELEGADO de lectura del Hub (MSAL, `User.Read`/`Sites.Read.All`), inyectado por el hook transversal de `hub_auth.js`
    (mismo patrón que Catálogos CCHH).
  - ESCRITURA: token DELEGADO `Sites.ReadWrite.All` (el que ya usa Operaciones → Solicitudes para escribir en SharePoint): el Hub escribe
    solo donde la persona que opera tiene permiso de edición sobre el archivo. El `Files.SelectedOperations.Selected` de CCHH no sirve acá:
    solo cubre recursos con un grant explícito (la carpeta de CVs).
  El Hub no tiene identidad de aplicación con permisos de SharePoint (la única app-only es Notificaciones, `Mail.Send`).

Lectura: read-model materializado en el repositorio de Cartelera (`settings/birthdays-sharepoint`, write-through a Spaces) que todas las
experiencias consumen sin token. Se actualiza al abrir Gestión → Cumpleaños / «Actualizar» (metadata primero; descarga solo si cambió el
`cTag`) y después de cada escritura (con los valores VIVOS del libro). Un fallo nunca borra la última lectura válida ni cae a DEMO.

Escritura: Excel REST API de Graph sobre celdas/filas puntuales (nunca se regenera ni se re-sube el archivo): se preservan hojas, formato y
el resto del contenido. Concurrencia: antes de escribir se releen los valores vivos y se ubica a la persona por su IDENTIDAD (nunca por un
número de fila guardado); si su registro cambió en el Excel desde que se abrió el diálogo, se rechaza (no se pisa el cambio de un tercero).

Identidad estable por fila: correo (si la columna existe y la fila lo tiene) → legajo → nombre + día + mes (débil: cambiar el nombre o la
fecha la cambia). Nunca el número de fila. Privacidad: del Excel se conserva SOLO nombre, día, mes y autorización."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import threading
import unicodedata
import urllib.error
import urllib.parse
from collections import Counter
from datetime import date, datetime, timedelta

from core import microsoft365
from webapp.services import cartelera_repository as repo

# ── referencia del archivo (hardcodeada; sin variables de entorno) ─────────────────────────────────────────────────────────────────
SHARE_URL = "https://danwor.sharepoint.com/:x:/s/Comunicaciones/IQBnA5KC9-pTRIdekOHH_WUOAXZIAyjF-fqEmKSoIhXPHMc?e=BV6Jfl"
# Acreditada en la primera lectura real (2026-11-26): «Comunicaciones / CCHH HUB / Cartelera virtual / Cumpleaños / Cumpleaños.xlsx»
# (mismo drive que el resto de Capital Humano).
DRIVE_ID = "b!kEfGZTP5uUaCP45K1X5psX3at7lnj6lGvidfF2OUcYoNrL5xTaXaRIqFJOVKAjrE"
ITEM_ID = "01ULA425LHAOJIF57KKNCIOXUQ4HD72ZIO"

# Interruptor de la fuente (constante de código, no de entorno). El arnés visual DEMO lo apaga explícitamente.
ENABLED = True

DOC = ("settings", "birthdays-sharepoint")
GRAPH = "https://graph.microsoft.com/v1.0"
PARSER_VERSION = 3                     # cambia ⇒ se reprocesa el Excel aunque su cTag no haya cambiado
MIN_CHECK_SECONDS = 20                 # dos aperturas seguidas no consultan Graph dos veces
MAX_BYTES = 5 * 1024 * 1024
_lock = threading.Lock()
_write_lock = threading.Lock()


class SourceError(repo.CarteleraStorageError):
    """Fallo de la fuente (Graph/Excel). Hereda del error de persistencia del Hub: los routers lo informan con sus mecanismos de siempre."""

    def __init__(self, kind: str, message: str, status: int | None = None, detail: str = ""):
        super().__init__(message)
        self.kind, self.status, self.detail = kind, status, detail


# ── lectura del read-model (tokenless) ──────────────────────────────────────────────────────────────────────────────────────────────
def snapshot() -> dict | None:
    return repo.get(*DOC)


def fingerprint(row: dict) -> int:
    """«Revisión» de un registro del Excel: cambia si cambia su nombre, fecha o autorización (concurrencia optimista del diálogo)."""
    key = f"{row.get('name', '')}|{row.get('day')}|{row.get('month')}|{bool(row.get('authorized'))}"
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16)


def rows() -> list[dict]:
    snap = snapshot() or {}
    out = [{k: v for k, v in r.items() if k != "row"} | {"revision": fingerprint(r), "source": "sharepoint"} for r in snap.get("rows") or []]
    return sorted(out, key=lambda b: (int(b.get("month", 0)), int(b.get("day", 0)), b.get("name", "").lower()))


def get(birthday_id: str) -> dict | None:
    return next((r for r in rows() if r["id"] == birthday_id), None)


def status() -> dict:
    snap = snapshot()
    if not snap:
        return {"loaded": False, "last_error": None}
    return {"loaded": bool(snap.get("refreshed_at")), "source": snap.get("source") or {}, "refreshed_at": snap.get("refreshed_at"),
            "checked_at": snap.get("checked_at"), "count": len(snap.get("rows") or []), "identity_basis": snap.get("identity_basis"),
            "consent_column": snap.get("consent_column"), "warnings": snap.get("warnings") or [], "structure": snap.get("structure") or {},
            "last_error": snap.get("last_error")}


# ── Graph ───────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
def _share_id(url: str) -> str:
    return "u!" + base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def _item_base(drive_id: str = "", item_id: str = "") -> str:
    drive_id, item_id = drive_id or DRIVE_ID, item_id or ITEM_ID
    if drive_id and item_id:
        return f"{GRAPH}/drives/{drive_id}/items/{item_id}"
    return f"{GRAPH}/shares/{_share_id(SHARE_URL)}/driveItem"


def _graph_error(exc: urllib.error.HTTPError, *, writing: bool = False) -> SourceError:
    code, message = "", ""
    try:
        body = json.loads(exc.read().decode("utf-8", "replace") or "{}").get("error") or {}
        code, message = str(body.get("code") or ""), str(body.get("message") or "")[:300]
    except Exception:                                                     # noqa: BLE001 — el cuerpo es opcional
        pass
    detail = f"HTTP {exc.code}{' · ' + code if code else ''}{' · ' + message if message else ''}"
    what = "guardar en" if writing else "leer"
    if exc.code == 401:
        return SourceError("auth_required", "La sesión de datos con Microsoft venció o no se autorizó.", 401, detail)
    if exc.code == 403:
        return SourceError("forbidden", f"Microsoft rechazó {what} el Excel: la cuenta no tiene permiso de "
                                        f"{'edición' if writing else 'lectura'} sobre el archivo.", 403, detail)
    if exc.code == 404:
        return SourceError("not_found", "No encontramos el Excel de cumpleaños en SharePoint (o la hoja cambió).", 404, detail)
    if exc.code in (409, 423):
        return SourceError("locked", "El Excel está bloqueado o en uso exclusivo en este momento. Probá de nuevo en unos segundos.", exc.code, detail)
    return SourceError("fetch", f"SharePoint respondió con un error al {what} el Excel (HTTP {exc.code}).", exc.code, detail)


def _call(fn, *args, writing: bool = False, **kwargs):
    try:
        return fn(*args, **kwargs)
    except urllib.error.HTTPError as exc:
        raise _graph_error(exc, writing=writing) from None
    except SourceError:
        raise
    except Exception as exc:                                              # noqa: BLE001
        raise SourceError("fetch", f"No pudimos conectar con SharePoint ({type(exc).__name__}).") from None


# ── parseo (común a la descarga y a los valores vivos del libro) ───────────────────────────────────────────────────────────────────
def _norm(text) -> str:
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


ROLES = {
    "full_name": ["nombre y apellido", "apellido y nombre", "nombre completo", "colaborador", "colaboradora", "empleado", "persona", "nombre"],
    "first_name": ["nombres", "primer nombre"],
    "last_name": ["apellido", "apellidos"],
    "birth_date": ["fecha de nacimiento", "fecha nacimiento", "nacimiento", "fecha de cumpleanos", "cumpleanos", "cumple", "fecha"],
    "day": ["dia", "dia de nacimiento", "dia cumpleanos"],
    "month": ["mes", "mes de nacimiento", "mes cumpleanos"],
    "email": ["email", "e mail", "mail", "correo", "correo electronico", "usuario", "upn"],
    "employee_id": ["legajo", "id empleado", "numero de empleado", "nro de legajo", "n de legajo", "codigo de empleado", "id colaborador"],
    "consent": ["autorizo", "autorizo publicacion", "autoriza publicacion", "autorizacion de publicacion", "autoriza", "autorizacion", "autorizado",
                "consentimiento", "publicar", "publicacion", "mostrar en cartelera", "mostrar"],
}
_YES = {"si", "s", "yes", "y", "true", "verdadero", "x", "1", "autorizado", "autoriza", "ok", "publicar"}
MONTH_NAMES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
MONTHS = {m: i for i, m in enumerate(MONTH_NAMES, 1)} | {"setiembre": 9}
_NUMERIC_DATE = re.compile(r"^(\s*)(\d{1,2})(\s*[/\-.]\s*)(\d{1,2})(?:(\s*[/\-.]\s*)(\d{2,4}))?(\s*)$")
_NAMED_DATE = re.compile(r"^(\s*)(\d{1,2})(\s+(?:de\s+)?)([A-Za-zÁÉÍÓÚáéíóú]+)(.*)$")


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


def _serial_to_date(value) -> date | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value < 100000:
        return None
    return date(1899, 12, 30) + timedelta(days=int(value))


def _day_month(date_value, day_value, month_value) -> tuple[int, int] | None:
    d = m = None
    serial = _serial_to_date(date_value)
    if isinstance(date_value, (datetime, date)) or serial:
        dv = serial or date_value
        d, m = dv.day, dv.month
    elif isinstance(date_value, str) and date_value.strip():
        hit = _NUMERIC_DATE.match(date_value)
        if hit:
            d, m = int(hit.group(2)), int(hit.group(4))
        else:
            hit = _NAMED_DATE.match(date_value)
            if hit and _norm(hit.group(4)) in MONTHS:
                d, m = int(hit.group(2)), MONTHS[_norm(hit.group(4))]
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


def _locate(sheet_rows: list[tuple[int, list]]) -> tuple[int, list[str], dict[str, int]] | None:
    """Fila de encabezados (en las primeras 10 filas no vacías) con columnas de nombre y fecha."""
    for pos, (_n, cells) in enumerate([r for r in sheet_rows if any(c not in (None, "") for c in r[1])][:10]):
        headers = [str(c).strip() if c is not None else "" for c in cells]
        roles = _match_roles(headers)
        if any(k in roles for k in ("full_name", "first_name")) and ("birth_date" in roles or ("day" in roles and "month" in roles)):
            return pos, headers, roles
    return None


def _cell(cells: list, roles: dict[str, int], role: str):
    i = roles.get(role)
    return cells[i] if i is not None and i < len(cells) else None


def _full_name(cells: list, roles: dict[str, int]) -> str:
    if "full_name" in roles:
        return str(_cell(cells, roles, "full_name") or "").strip()
    return " ".join(str(x).strip() for x in (_cell(cells, roles, "first_name"), _cell(cells, roles, "last_name")) if x not in (None, ""))


def _parse_sheet(sheet: str, sheet_rows: list[tuple[int, list]]) -> dict | None:
    """Filas normalizadas de UNA hoja: `sheet_rows` = [(n.º de fila de Excel, celdas desde la columna A)]. None si no es la hoja de cumpleaños."""
    found = _locate(sheet_rows)
    if not found:
        return None
    _pos, headers, roles = found
    nonempty = [r for r in sheet_rows if any(c not in (None, "") for c in r[1])]
    header_row = nonempty[_pos][0]
    body = [(n, cells) for n, cells in sheet_rows if n > header_row and any(c not in (None, "") for c in cells)]
    warnings: list[str] = []
    if "consent" not in roles:
        warnings.append("El Excel no tiene una columna de autorización de publicación: ningún cumpleaños se muestra en la Cartelera hasta que exista.")
    out, seen, skipped, bases = [], {}, 0, Counter()
    for n, cells in body:
        name = _full_name(cells, roles)
        dm = _day_month(_cell(cells, roles, "birth_date"), _cell(cells, roles, "day"), _cell(cells, roles, "month"))
        if not name or not dm:
            skipped += 1
            continue
        email = str(_cell(cells, roles, "email") or "").strip().lower()
        legajo = str(_cell(cells, roles, "employee_id") or "").strip().lower()
        basis, key = ("email", email) if email else (("employee_id", legajo) if legajo else ("name_date", f"{_norm(name)}|{dm[0]:02d}{dm[1]:02d}"))
        bid = _identity(basis, key)
        if bid in seen:
            warnings.append(f"Fila {n}: duplica la identidad de la fila {seen[bid]}; se conserva la primera.")
            continue
        seen[bid] = n
        bases[basis] += 1
        consent = _cell(cells, roles, "consent")
        authorized = bool(consent) if isinstance(consent, bool) else _norm(consent) in _YES
        out.append({"id": bid, "name": name[:120], "day": dm[0], "month": dm[1], "authorized": authorized, "row": n})
    if bases.get("name_date"):
        warnings.append(f"{bases['name_date']} persona{'s' if bases['name_date'] != 1 else ''} sin correo ni legajo: su identidad se deriva de "
                        "nombre + día + mes (si cambia el nombre o la fecha, sus saludos e interacciones anteriores no se trasladan).")
    if skipped:
        warnings.append(f"{skipped} fila{'s' if skipped != 1 else ''} sin nombre o con fecha inválida no se incorporaron.")
    types = {}
    for j, h in enumerate(headers):
        kinds = sorted({_kind(c[j] if j < len(c) else None) for _n, c in body[:200]} - {"vacío"})
        types[h or f"(columna {j + 1})"] = ", ".join(kinds) or "vacío"
    consent_values = []
    if "consent" in roles:
        counts = Counter("(vacío)" if _cell(c, roles, "consent") in (None, "") else str(_cell(c, roles, "consent")).strip()[:40] for _n, c in body)
        consent_values = [{"value": k, "count": v, "authorized": _norm(k) in _YES} for k, v in counts.most_common(20)]
    basis = "email" if bases and set(bases) == {"email"} else ("name_date" if set(bases) <= {"name_date"} else "mixed")
    return {"sheet": sheet, "header_row": header_row, "headers": headers, "roles": roles, "rows": out, "warnings": warnings[:60],
            "identity_basis": basis, "consent_column": headers[roles["consent"]] if "consent" in roles else None,
            "types": types, "consent_values": consent_values, "body": body}


def _snapshot_fields(parsed: dict, sheets_info: list[dict]) -> dict:
    roles, headers = parsed["roles"], parsed["headers"]
    structure = {"sheets": sheets_info, "used_sheet": parsed["sheet"], "header_row": parsed["header_row"],
                 "roles": {k: headers[v] for k, v in roles.items()}, "types": parsed["types"], "consent_values": parsed["consent_values"]}
    return {"rows": parsed["rows"], "warnings": parsed["warnings"], "identity_basis": parsed["identity_basis"],
            "consent_column": parsed["consent_column"], "structure": structure}


def parse_workbook(xlsx: bytes) -> dict:
    """Descarga completa (`/content`) → campos del read-model. Falla cerrado si ninguna hoja tiene nombre + fecha."""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    except Exception as exc:                                              # noqa: BLE001
        raise SourceError("workbook", f"El archivo no es un .xlsx legible ({type(exc).__name__}).") from None
    sheets, best = [], None
    for ws in wb.worksheets:
        sheet_rows = [(n, list(r)) for n, r in enumerate(ws.iter_rows(values_only=True), start=1)]
        parsed = _parse_sheet(ws.title, sheet_rows)
        sheets.append({"sheet": ws.title, "rows": len([r for r in sheet_rows if any(c not in (None, "") for c in r[1])]),
                       "header_row": parsed["header_row"] if parsed else None, "headers": parsed["headers"] if parsed else []})
        if parsed and best is None:
            best = parsed
    wb.close()
    if best is None:
        raise SourceError("workbook", "No encontramos una hoja con columnas de nombre y fecha de cumpleaños.",
                          detail="; ".join(f"{s['sheet']}: {s['rows']} filas" for s in sheets))
    return _snapshot_fields(best, sheets)


# ── refresh (lectura con token delegado) ────────────────────────────────────────────────────────────────────────────────────────────
def _save(fields: dict) -> dict:
    current = snapshot()
    return repo.save(*DOC, fields, expected_revision=current.get("revision") if current else None)


def _record_error(err: SourceError, actor: str) -> None:
    try:
        _save({"last_error": {"at": repo.now_iso(), "kind": err.kind, "status": err.status, "message": str(err), "detail": err.detail, "by": actor}})
    except Exception:                                                     # noqa: BLE001 — registrar el error nunca tapa el error original
        pass


def _source_meta(token: str, src: dict) -> dict:
    meta = _call(microsoft365.graph_get_json, _item_base(src.get("drive_id", ""), src.get("item_id", "")), token)
    return {"share_url": SHARE_URL, "drive_id": (meta.get("parentReference") or {}).get("driveId") or DRIVE_ID,
            "item_id": meta.get("id") or ITEM_ID, "name": meta.get("name"), "web_url": meta.get("webUrl"), "ctag": meta.get("cTag"),
            "etag": meta.get("eTag"), "last_modified": meta.get("lastModifiedDateTime"),
            "last_modified_by": ((meta.get("lastModifiedBy") or {}).get("user") or {}).get("displayName"),
            "path": (meta.get("parentReference") or {}).get("path")}


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
        try:
            source = _source_meta(graph_token, snap.get("source") or {})
            if not force and snap.get("refreshed_at") and (snap.get("source") or {}).get("ctag") == source["ctag"] \
                    and snap.get("parser_version") == PARSER_VERSION:
                _save({"checked_at": repo.now_iso(), "source": source, "last_error": None})
                return {"changed": False, "checked": True}
            if (snap.get("structure") or {}).get("used_sheet") and snap.get("parser_version") == PARSER_VERSION:
                # Valores VIVOS del libro (Excel REST API): reflejan al instante lo editado en Excel Online o desde el Hub; la descarga de
                # `/content` puede demorar en incorporarlos.
                parsed = _snapshot_fields(_live(graph_token, writing=False), (snap.get("structure") or {}).get("sheets") or [])
            else:
                xlsx = _call(microsoft365.graph_get_bytes, f"{GRAPH}/drives/{source['drive_id']}/items/{source['item_id']}/content", graph_token)
                if len(xlsx) > MAX_BYTES:
                    raise SourceError("workbook", "El Excel supera el tamaño admitido (5 MB).")
                parsed = parse_workbook(xlsx)
        except SourceError as err:
            _record_error(err, actor)
            raise
        changed = _row_keys(parsed["rows"]) != _row_keys(snap.get("rows") or [])
        now = repo.now_iso()
        _save({**parsed, "source": source, "refreshed_at": now, "checked_at": now, "refreshed_by": actor, "last_error": None,
               "parser_version": PARSER_VERSION})
        return {"changed": changed, "checked": True}


def _row_keys(rs: list[dict]) -> list[tuple]:
    return [(r["id"], r["name"], r["day"], r["month"], r["authorized"]) for r in rs]


# ── escritura (Excel REST API, celdas puntuales) ────────────────────────────────────────────────────────────────────────────────────
def _col(i: int) -> str:
    out = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        out = chr(65 + r) + out
    return out


def _sheet_url(sheet: str) -> str:
    return f"{_item_base()}/workbook/worksheets/{urllib.parse.quote(sheet, safe='')}"


def _live(token: str, *, writing: bool = True) -> dict:
    """Valores VIVOS de la hoja de cumpleaños (Excel REST API) ya parseados con la MISMA lógica que la lectura."""
    snap = snapshot() or {}
    sheet = (snap.get("structure") or {}).get("used_sheet")
    if not sheet:
        raise SourceError("workbook", "Todavía no se leyó el Excel de cumpleaños: abrí Gestión → Cumpleaños y probá de nuevo.")
    used = _call(microsoft365.graph_get_json, f"{_sheet_url(sheet)}/usedRange(valuesOnly=true)?$select=address,values,text,rowIndex,columnIndex",
                 token, writing=writing)
    r0, c0 = int(used.get("rowIndex") or 0), int(used.get("columnIndex") or 0)
    values, texts = used.get("values") or [], used.get("text") or []
    sheet_rows = [(r0 + i + 1, [None] * c0 + list(row)) for i, row in enumerate(values)]
    text_rows = {r0 + i + 1: [None] * c0 + list(row) for i, row in enumerate(texts)}
    parsed = _parse_sheet(sheet, sheet_rows)
    if not parsed:
        raise SourceError("workbook", f"La hoja «{sheet}» ya no tiene las columnas de nombre y fecha de cumpleaños.")
    parsed["texts"], parsed["last_row"] = text_rows, (r0 + len(values)) if values else parsed["header_row"]
    parsed["table"] = None
    if writing:
        try:
            tables = _call(microsoft365.graph_get_json, f"{_sheet_url(sheet)}/tables?$select=id,name", token, writing=True).get("value") or []
        except SourceError:
            tables = []
        parsed["table"] = tables[0]["id"] if tables else None
    return parsed


def _date_text(sample: str | None, d: int, m: int, *, keep_year: bool) -> str:
    """Fecha en el MISMO formato que usa el Excel (p. ej. «14/03», «14-3-1990», «14 de marzo»). Sin muestra: «DD/MM»."""
    s = str(sample or "")
    hit = _NUMERIC_DATE.match(s)
    if hit:
        pad = lambda old, v: f"{v:02d}" if len(old) == 2 else str(v)    # noqa: E731
        year = f"{hit.group(5)}{hit.group(6)}" if keep_year and hit.group(6) else ""
        return f"{hit.group(1)}{pad(hit.group(2), d)}{hit.group(3)}{pad(hit.group(4), m)}{year}{hit.group(7) if year else ''}".strip()
    hit = _NAMED_DATE.match(s)
    if hit and _norm(hit.group(4)) in MONTHS:
        word = MONTH_NAMES[m - 1]
        word = word.upper() if hit.group(4).isupper() else (word.capitalize() if hit.group(4)[:1].isupper() else word)
        rest = hit.group(5) if keep_year else ""
        return f"{d}{hit.group(3)}{word}{rest}".strip()
    return f"{d:02d}/{m:02d}"


def _dominant_date_sample(parsed: dict) -> str | None:
    i = parsed["roles"].get("birth_date")
    if i is None:
        return None
    shapes = Counter()
    sample = {}
    for n, _cells in parsed["body"]:
        row_texts = parsed["texts"].get(n) or []
        t = row_texts[i] if i < len(row_texts) else None
        if t:
            shape = re.sub(r"\d", "9", re.sub(r"[A-Za-zÁÉÍÓÚáéíóú]+", "a", str(t)))
            shapes[shape] += 1
            sample.setdefault(shape, str(t))
    return sample[shapes.most_common(1)[0][0]] if shapes else None


def _consent_tokens(parsed: dict) -> tuple[str, str]:
    i = parsed["roles"].get("consent")
    yes, no = Counter(), Counter()
    for _n, cells in parsed["body"]:
        v = cells[i] if i is not None and i < len(cells) else None
        if v in (None, "") or isinstance(v, bool):
            continue
        (yes if _norm(v) in _YES else no)[str(v).strip()] += 1
    y = yes.most_common(1)[0][0] if yes else "SI"
    n = no.most_common(1)[0][0] if no else ("NO" if y.isupper() else "No")
    return y, n


def _split_name(name: str, old_first: str = "", old_last: str = "") -> tuple[str, str]:
    if old_first and name.lower().startswith(old_first.lower() + " "):
        return old_first, name[len(old_first):].strip()
    if old_last and name.lower().endswith(" " + old_last.lower()):
        return name[: -len(old_last)].strip(), old_last
    first, _, last = name.partition(" ")
    return first, last.strip()


def _row_cells(parsed: dict, *, name: str, d: int, m: int, authorized: bool, n: int | None) -> dict[int, str]:
    """{columna: texto} a escribir. `n` = fila existente (edición: conserva formato de fecha/año y la división nombre/apellido)."""
    roles = parsed["roles"]
    texts = (parsed["texts"].get(n) or []) if n else []
    old = lambda role: (str(texts[roles[role]]) if role in roles and roles[role] < len(texts) and texts[roles[role]] is not None else "")  # noqa: E731
    cells: dict[int, str] = {}
    if "full_name" in roles:
        cells[roles["full_name"]] = name
    else:
        first, last = _split_name(name, old("first_name"), old("last_name"))
        cells[roles["first_name"]] = first
        if "last_name" in roles:
            cells[roles["last_name"]] = last
    if "birth_date" in roles:
        cells[roles["birth_date"]] = _date_text(old("birth_date") or _dominant_date_sample(parsed), d, m, keep_year=bool(n))
    else:
        cells[roles["day"]] = str(d)
        month_sample = old("month") or ""
        cells[roles["month"]] = (MONTH_NAMES[m - 1].capitalize() if month_sample and not month_sample.strip().isdigit() else str(m))
    if "consent" in roles:
        yes, no = _consent_tokens(parsed)
        cells[roles["consent"]] = yes if authorized else no
    return cells


def _patch_cell(token: str, sheet: str, address: str, text: str) -> None:
    """Escribe UNA celda como texto (formato «@»: Excel no reinterpreta «15/03» como fecha)."""
    _call(microsoft365.graph_patch_json, f"{_sheet_url(sheet)}/range(address='{address}')", token,
          {"values": [[text]], "numberFormat": [["@"]]}, writing=True)


def _require_write_token(token: str) -> None:
    if not token:
        raise SourceError("auth_required", "Falta la autorización de Microsoft para guardar en el Excel. Volvé a intentarlo y aceptá el acceso.")


def _after_write(token: str, actor: str) -> None:
    """Read-model desde los valores VIVOS recién escritos (la descarga de `/content` puede demorar en reflejarlos)."""
    parsed = _live(token)
    snap = snapshot() or {}
    sheets = (snap.get("structure") or {}).get("sheets") or []
    now = repo.now_iso()
    _save({**_snapshot_fields(parsed, sheets), "refreshed_at": now, "checked_at": now, "refreshed_by": actor, "last_error": None,
           "parser_version": PARSER_VERSION,
           # sin cTag a propósito: la próxima apertura vuelve a leer los valores vivos y reconcilia con el archivo.
           "source": {**(snap.get("source") or {}), "ctag": None}})


def _find(parsed: dict, birthday_id: str) -> dict | None:
    return next((r for r in parsed["rows"] if r["id"] == birthday_id), None)


def save(birthday_id: str | None, revision: int | None, *, name: str, day: int, month: int, authorized: bool, token: str, actor: str) -> dict:
    """Alta (sin id) o modificación de UNA persona en el Excel. Concurrencia: el registro vivo debe coincidir con el que se editó."""
    _require_write_token(token)
    with _write_lock:
        parsed = _live(token)
        if birthday_id:
            current = _find(parsed, birthday_id)
            if not current:
                raise repo.CarteleraConcurrentModificationError("Esa persona ya no está en el Excel (la modificaron o la eliminaron). Recargá antes de guardar.")
            if revision is not None and fingerprint(current) != int(revision):
                raise repo.CarteleraConcurrentModificationError("Ese cumpleaños cambió en el Excel desde que abriste el diálogo. Recargá para ver el valor actual.")
            n = current["row"]
            wanted = _row_cells(parsed, name=name, d=day, m=month, authorized=authorized, n=n)
            texts = parsed["texts"].get(n) or []
            for col, text in wanted.items():
                if (str(texts[col]) if col < len(texts) and texts[col] is not None else "") != text:
                    _patch_cell(token, parsed["sheet"], f"{_col(col)}{n}", text)
        else:
            if any(_norm(r["name"]) == _norm(name) and (r["day"], r["month"]) == (day, month) for r in parsed["rows"]):
                from webapp.services.cartelera_service import CarteleraValidationError
                raise CarteleraValidationError("Esa persona ya está cargada con esa fecha en el Excel.")
            _append(token, parsed, [_row_cells(parsed, name=name, d=day, m=month, authorized=authorized, n=None)])
        _after_write(token, actor)
    return get(birthday_id) or {} if birthday_id else {}


def add_many(items: list[dict], *, token: str, actor: str) -> int:
    """Carga masiva: agrega las filas nuevas en UNA sola escritura al final de la hoja (o de su tabla)."""
    _require_write_token(token)
    if not items:
        return 0
    with _write_lock:
        parsed = _live(token)
        existing = {(_norm(r["name"]), r["day"], r["month"]) for r in parsed["rows"]}
        fresh = [i for i in items if (_norm(i["name"]), i["day"], i["month"]) not in existing]
        if fresh:
            _append(token, parsed, [_row_cells(parsed, name=i["name"], d=i["day"], m=i["month"], authorized=i["authorized"], n=None) for i in fresh])
            _after_write(token, actor)
        return len(fresh)


def _append(token: str, parsed: dict, new_rows: list[dict[int, str]]) -> None:
    width = max(max(c.keys()) for c in new_rows) + 1
    first_col = min(min(c.keys()) for c in new_rows)
    values = [[c.get(j, "") for j in range(first_col, width)] for c in new_rows]
    if parsed.get("table"):
        tbl = _call(microsoft365.graph_get_json, f"{_item_base()}/workbook/tables/{parsed['table']}/range?$select=columnIndex,columnCount",
                    token, writing=True)
        t0, tw = int(tbl.get("columnIndex") or 0), int(tbl.get("columnCount") or width)
        rows_values = [[c.get(t0 + j, "") for j in range(tw)] for c in new_rows]
        _call(microsoft365.graph_post_json, f"{_item_base()}/workbook/tables/{parsed['table']}/rows/add", token,
              {"values": rows_values}, writing=True)
        return
    start = parsed["last_row"] + 1
    address = f"{_col(first_col)}{start}:{_col(width - 1)}{start + len(values) - 1}"
    _call(microsoft365.graph_patch_json, f"{_sheet_url(parsed['sheet'])}/range(address='{address}')", token,
          {"values": values, "numberFormat": [["@"] * len(values[0]) for _ in values]}, writing=True)


def delete(birthday_id: str, *, token: str, actor: str) -> None:
    """Elimina la FILA de la persona en el Excel (se desplazan las de abajo; las demás hojas y columnas no se tocan)."""
    _require_write_token(token)
    with _write_lock:
        parsed = _live(token)
        current = _find(parsed, birthday_id)
        if not current:
            raise repo.CarteleraConcurrentModificationError("Esa persona ya no está en el Excel (la modificaron o la eliminaron). Recargá la vista.")
        n = current["row"]
        if parsed.get("table"):
            index = n - parsed["header_row"] - 1
            _call(microsoft365.graph_delete, f"{_item_base()}/workbook/tables/{parsed['table']}/rows/itemAt(index={index})", token, writing=True)
        else:
            _call(microsoft365.graph_post_accepted, f"{_sheet_url(parsed['sheet'])}/range(address='{n}:{n}')/delete", token, {"shift": "Up"},
                  writing=True)
        _after_write(token, actor)
