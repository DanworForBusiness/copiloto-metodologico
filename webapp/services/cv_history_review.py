"""cv_history_review.py — presentación «Históricos sin validación» (Capital Humano): SOLO LECTURA, sobre el circuito de excepciones de la importación histórica de CVs.

Dos servicios, ninguno escribe nada ni toca el Banco, el staging, el manifiesto, el libro de ejecución ni SharePoint:

  1. `annotate(run_id, rows)` agrega a CADA fila de la página visible (no al lote entero) una «revisión» legible: la categoría de recuperabilidad (la clasificación EXISTENTE de `cv_history_recoverability.classify`, sin umbrales
     nuevos), las identidades que el extractor encontró con dónde y con qué respaldo, las advertencias en lenguaje llano y lo que hay que confirmar antes de incorporar el documento al Banco. Una propuesta NO es una identidad
     verificada y la presentación nunca lo afirma. La clasificación se calcula solo para las filas que se muestran (una página): nada se recalcula para las 403 ni se guarda.

  2. `resolve_original(run_id, content_key, token)` resuelve el enlace de SharePoint del CV ORIGINAL con Microsoft Graph (`GET /drives/{drive}/items/{item}?$select=webUrl`) a partir de los identificadores del staging y del token
     DELEGADO del usuario autenticado (sus propios permisos). Devuelve el enlace solo para mostrarlo: no descarga el archivo, no guarda la URL ni crea copias. Los errores de Graph se traducen a un mensaje claro y a un código
     estable; los registros de log llevan solo códigos (jamás nombres, emails, URL ni el token)."""
from __future__ import annotations

import logging
import urllib.error
import urllib.parse

from core import microsoft365
from webapp.services import cv_extract, cv_history_inventory as inventory, cv_history_manifest as manifest, cv_history_recoverability as rec, profile_repository
from webapp.services.cv_history_candidate_index import CandidateIndex

_log = logging.getLogger(__name__)

GRAPH_TIMEOUT_S = 15
MAX_CANDIDATES = 3                                                                  # el representante y, si hace falta, hasta dos copias idénticas del mismo contenido
OPEN_STATUSES = ("open", "in_progress", "blocked", "error")                          # solo estas se clasifican: las resueltas y descartadas ya no necesitan revisión

CATEGORY_VIEW = {
    rec.LINK: ("Posible vinculación", "status-badge--tentativo", "Podría ser una persona que ya está en el Banco. Confirmá que el CV le pertenece antes de vincularlo."),
    rec.ASSISTED: ("Alta a confirmar", "status-badge--potential", "Hay una identidad plausible y respaldada. Falta confirmar nombre y apellido y los datos de contacto antes de crear un perfil."),
    rec.CONFLICT: ("Conflicto de identidad", "status-badge--estancada", "La evidencia se contradice. Hace falta una revisión humana explícita; no hay atajo."),
    rec.INSUFFICIENT: ("Información insuficiente", "status-badge--inactivo", "No hay evidencia suficiente para proponer una identidad. Abrí el CV original para completarla."),
    rec.EXTRACTION: ("Problema de lectura", "status-badge--otro", "El documento no se pudo leer bien (por ejemplo escaneado o protegido). Requiere otro tratamiento: abrí el CV original."),
}
SOURCE_LABEL = {"text": "el texto del CV", "filename": "el nombre del archivo"}
EVIDENCE_LABEL = {"LABEL": "viene con una etiqueta de nombre (por ejemplo «Nombre:»)", "EMAIL_STRONG": "el email contiene partes distintivas del nombre", "TEXT_FILE_AGREE": "coincide entre el texto del CV y el nombre del archivo",
                  "NEAR_CONTACT": "está junto a los datos de contacto"}
STATE_LABEL = {"CORROBORATED": "Corroborada por evidencia independiente", "DECLARED": "Declarada en el documento, sin corroboración independiente", "UNANCHORED_AGREEMENT": "Coincide entre texto y archivo, sin dato de contacto que la respalde",
               "STRUCTURAL": "Solo aparece como texto, sin respaldo"}
FLAG_LABEL = {"NAME_MISSING": "No se encontró un nombre.", "NAME_CONFLICT": "Hay nombres que se contradicen.", "NAME_PARTIAL_MATCH": "Los nombres solo coinciden en una palabra.",
              "NAME_UNCORROBORATED": "El nombre no está respaldado por un email ni por el nombre del archivo.", "NAME_ALTERNATIVE": "Hay otros nombres posibles en el documento.",
              "NAME_DECLARED": "El nombre figura en el documento, pero sin corroboración independiente.", "NAME_EDGE_TOKEN_UNEXPLAINED": "Hay una sigla o palabra corta en el borde del nombre que no se pudo explicar.",
              "NAME_ORDER_UNCERTAIN": "No se sabe el orden nombre/apellido: el nombre sale solo del nombre del archivo.", "NAME_SPLIT_AMBIGUOUS": "No se sabe dónde termina el nombre y empieza el apellido.",
              "EMAIL_MISSING": "El documento no trae email.", "EMAIL_MULTIPLE": "El documento trae varios emails: hay que elegir cuál es el de la persona.", "PHONE_MISSING": "El documento no trae celular.",
              "PHONE_MULTIPLE": "El documento trae varios celulares."}
FAILURE_LABEL = {"NO_TEXT": "El documento no tiene texto legible (¿escaneado?).", "PDF_ENCRYPTED": "El PDF está protegido con contraseña.", "EXTRACTION_ERROR": "Falló la lectura del documento.",
                 "FORMAT_UNSUPPORTED": "El formato del archivo no es compatible."}
CONFLICT_LABEL = {"NAME_CONFLICT": "Hay nombres que se contradicen en el documento.", "EMAIL_CONFLICT": "El email del documento pertenece a otra persona.", "EXISTING_NAME_CONFLICT": "El nombre contradice al de un perfil existente.",
                  "EMAIL_NAME_MISMATCH": "El email y el nombre no coinciden.", "MULTIPLE_EXISTING_MATCH": "Coincide con varios perfiles existentes.", "EXISTING_DELETED": "Coincide con un perfil dado de baja.",
                  "IDENTIDADES_CONTRADICTORIAS": "El documento reclama identidades distintas.", "VARIOS_PERFILES_CANDIDATOS": "Hay varios perfiles candidatos en el Banco.",
                  "PERFIL_CANDIDATO_CONTRADICE_EL_DOCUMENTO": "El perfil candidato contradice lo que dice el documento."}
RISK_LABEL = {"SOLO_CELULAR": "La coincidencia con un perfil es solo por celular: no confirma identidad.", "COINCIDENCIA_WEAK_EXISTE": "Existe una coincidencia débil con un perfil: no alcanza para vincular.",
              "PERFIL_CREADO_POR_ESTE_LOTE": "El perfil coincidente lo creó esta misma importación."}

ERROR_MESSAGE = {
    "NO_TOKEN": "Sin token de Microsoft 365 (el navegador no pudo obtenerlo). Volvé a iniciar sesión y reintentá.",
    "NO_STAGING": "Los datos temporales de este lote ya no están disponibles, por eso no se conoce la ubicación del CV original.",
    "NO_LOCATION": "Este documento no guarda la ubicación de su CV original.",
    "TOKEN_EXPIRED": "La sesión de Microsoft 365 venció o no alcanza. Volvé a iniciar sesión y reintentá.",
    "ACCESS_DENIED": "Tu usuario no tiene permiso para abrir ese archivo en SharePoint.",
    "NOT_FOUND": "El archivo ya no está en SharePoint (puede haberse movido o borrado).",
    "GRAPH_UNAVAILABLE": "Microsoft 365 no respondió bien. Reintentá en un momento.",
    "NETWORK": "No se pudo conectar con Microsoft 365. Reintentá en un momento.",
    "BAD_RESPONSE": "Microsoft 365 devolvió una respuesta que no se pudo interpretar.",
    "INVALID_LINK": "Microsoft 365 no devolvió un enlace de SharePoint válido.",
}
_ERROR_RANK = {"ACCESS_DENIED": 4, "GRAPH_UNAVAILABLE": 3, "NETWORK": 3, "BAD_RESPONSE": 2, "INVALID_LINK": 2, "NOT_FOUND": 1}


# ═══════════════════ revisión legible de cada fila ═══════════════════
def _identity_view(alt: dict, proposed: bool) -> dict:
    notes: list[str] = []
    edge = alt.get("edge_token") or {}
    if edge.get("kind") == "UNEXPLAINED":
        notes.append(f"La palabra corta «{edge.get('token')}» al borde del nombre no está explicada por el email.")
    elif edge.get("kind") == "AMBIGUOUS":
        notes.append(f"Se quitó «{edge.get('token')}» (puede ser una sigla) porque el email respalda el resto del nombre.")
    if alt.get("normalization"):
        notes.append(f"Se limpiaron adornos del nombre. Texto original: «{alt['normalization'].get('original')}».")
    return {"name": alt.get("tokens") or "", "proposed": proposed, "state": STATE_LABEL.get(alt.get("state"), "Sin clasificar"), "found_in": [SOURCE_LABEL.get(s, s) for s in alt.get("sources") or []],
            "evidence": [EVIDENCE_LABEL.get(e, e) for e in alt.get("evidence") or []], "notes": notes}


def _review(row: dict, group: dict | None, detail: dict | None) -> dict | None:
    """Revisión legible de UNA fila (None si no hay datos temporales o la fila no se clasifica). PURA."""
    extraction = (group or {}).get("extraction")
    if not extraction or detail is None:
        return None
    label, badge, hint = CATEGORY_VIEW[detail["categoria"]]
    decision = extraction.get("name_decision") or {}
    alternatives = decision.get("alternatives") or []
    proposed = decision.get("proposed")
    identities = [_identity_view(a, i == proposed) for i, a in enumerate(alternatives)]
    flags = [FLAG_LABEL[f] for f in extraction.get("flags") or [] if f in FLAG_LABEL]
    failure = FAILURE_LABEL.get(extraction.get("failure"), extraction.get("failure"))
    why_conflict = [CONFLICT_LABEL.get(c, c) for c in detail["evidencia"]] if detail["categoria"] == rec.CONFLICT else []
    return {"category": detail["categoria"], "label": label, "badge": badge, "hint": hint, "failure": failure, "identities": identities, "warnings": flags, "conflicts": why_conflict,
            "risks": [RISK_LABEL[r] for r in detail["riesgos"] if r in RISK_LABEL], "confirm": list(detail["confirmar"]),
            "proposal": f"{extraction.get('first_name')} {extraction.get('last_name') or ''}".strip() if extraction.get("first_name") else None, "prefill": _prefill(extraction)}


_NOT_SAFE_FLAGS = {"NAME_ALTERNATIVE", "NAME_CONFLICT", "NAME_PARTIAL_MATCH", "NAME_UNCORROBORATED", "NAME_MISSING"}


def _prefill(extraction: dict) -> dict:
    """Qué se puede PRECARGAR en el formulario de borrador sin elegir por el owner. El nombre y el apellido solo con la evidencia más fuerte: nivel A de precarga (sin advertencias de partición, orden ni token de borde)
    Y identidad corroborada por evidencia independiente Y sin otras identidades en juego. El email solo si el documento trae exactamente uno que identifique a una persona (no de rol); el celular solo si hay uno.
    En cualquier otro caso no se elige nada: se informan las alternativas y por qué no se precargó."""
    flags = set(extraction.get("flags") or [])
    level = rec.preload_level(extraction)
    safe_name = level == "A" and extraction.get("name_state") == "CORROBORATED" and not (flags & _NOT_SAFE_FLAGS)
    emails = rec._usable_emails(extraction)
    out = {"level": level, "first_name": "", "last_name": "", "email": "", "phone": "", "emails": emails, "why": "",
           "names": [a.get("tokens") for a in (extraction.get("name_decision") or {}).get("alternatives") or [] if a.get("tokens")]}
    if safe_name:
        out["first_name"], out["last_name"] = extraction.get("first_name") or "", extraction.get("last_name") or ""
    elif extraction.get("failure"):
        out["why"] = "el documento no se pudo leer."
    elif level == "A":
        out["why"] = "la identidad no está corroborada por evidencia independiente."
    else:
        out["why"] = rec.PRELOAD_MEANING[level].split(" (")[0] + "."
    if len(emails) == 1:
        out["email"] = emails[0]
    if extraction.get("phone") and cv_extract.PHONE_MULTIPLE not in flags:
        out["phone"] = extraction["phone"]
    return out


def annotate(run_id: str, rows: list[dict | None]) -> list[dict | None]:
    """Agrega `review` a cada fila ABIERTA de la página (clasificación de recuperabilidad solo para estas filas). No escribe nada; si una fila no se puede clasificar, queda sin `review` y la pantalla sigue andando."""
    todo = [r for r in rows if r and r.get("status") in OPEN_STATUSES and r.get("available")]
    if not todo:
        return rows
    live = [p for p in profile_repository.list() if not p.get("deleted_at")]
    index = CandidateIndex(live)                                                      # temporal, de ESTE request: acota las comparaciones sin cambiar ningún resultado (ver cv_history_candidate_index)
    for r in todo:
        try:
            group = manifest.get_group(run_id, r["content_key"])
            detail, _variants = rec.classify(r, group, manifest.get_ledger(run_id, r["content_key"]), live, run_id, index)
            r["review"] = _review(r, group, detail)
        except Exception:                                                              # una fila rara no debe romper la pantalla: sin revisión y sin datos personales en el log
            _log.warning("cv_history_review: no se pudo clasificar run=%s key=%s", run_id, r.get("content_key"))
            r["review"] = None
    return rows


# ═══════════════════ CV original (Microsoft Graph, token delegado del usuario) ═══════════════════
def _candidates(group: dict) -> list[tuple[str, str]]:
    """(drive_id, item_id) del representante y de las copias idénticas del mismo contenido, sin repetir."""
    seen: list[tuple[str, str]] = []
    for ref in [group.get("representative") or {}, *(group.get("members") or [])]:
        pair = (ref.get("drive_id"), ref.get("item_id"))
        if pair[0] and pair[1] and pair not in seen:
            seen.append(pair)
    return seen[:MAX_CANDIDATES]


def _safe_web_url(value) -> str | None:
    """Solo https://*.sharepoint.com sin credenciales: nada de lo que devuelva Graph se enlaza sin validar."""
    if not isinstance(value, str):
        return None
    parsed = urllib.parse.urlparse(value.strip())
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host.endswith(".sharepoint.com") or parsed.username or parsed.password:
        return None
    return value.strip()


def _error_code(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return {401: "TOKEN_EXPIRED", 403: "ACCESS_DENIED", 404: "NOT_FOUND"}.get(exc.code, "GRAPH_UNAVAILABLE" if exc.code == 429 or exc.code >= 500 else "BAD_RESPONSE")
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
        return "NETWORK"
    return "BAD_RESPONSE"


def _fail(code: str) -> dict:
    return {"ok": False, "code": code, "message": ERROR_MESSAGE[code]}


def resolve_original(run_id: str, content_key: str, token: str) -> dict:
    """Enlace de SharePoint del CV original de UN contenido. `{ok, url, name}` o `{ok: False, code, message}`. Lanza `ManifestNotFoundError` si el lote no existe. No guarda nada ni descarga el archivo."""
    run = manifest.get_run(run_id)
    if not token:
        return _fail("NO_TOKEN")
    if (run.get("staging") or {}).get("state") != "PRESENT":
        return _fail("NO_STAGING")
    group = manifest.get_group(run_id, content_key)
    if not group:
        return _fail("NO_LOCATION")
    candidates = _candidates(group)
    if not candidates:
        return _fail("NO_LOCATION")
    worst = "NOT_FOUND"
    for drive_id, item_id in candidates:
        url = f"{inventory.GRAPH_BASE}/drives/{urllib.parse.quote(drive_id, safe='!,')}/items/{urllib.parse.quote(item_id, safe='')}?$select=webUrl"
        try:
            data = microsoft365.graph_get_json(url, token, timeout=GRAPH_TIMEOUT_S)
        except Exception as exc:                                                       # urllib/HTTP/red/JSON: se traduce a un código; el mensaje crudo de Graph podría citar nombres y no se registra
            code = _error_code(exc)
            _log.info("cv_history_review: CV original run=%s key=%s resultado=%s", run_id, content_key, code)
            if code == "TOKEN_EXPIRED":
                return _fail(code)
            if _ERROR_RANK.get(code, 0) > _ERROR_RANK.get(worst, 0):
                worst = code
            continue
        link = _safe_web_url((data or {}).get("webUrl") if isinstance(data, dict) else None)
        if link:
            _log.info("cv_history_review: CV original run=%s key=%s resultado=OK", run_id, content_key)
            return {"ok": True, "url": link, "name": (group.get("representative") or {}).get("name")}
        worst = "INVALID_LINK" if _ERROR_RANK["INVALID_LINK"] > _ERROR_RANK.get(worst, 0) else worst
    return _fail(worst)
