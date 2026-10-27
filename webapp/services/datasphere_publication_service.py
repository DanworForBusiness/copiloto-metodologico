"""
datasphere_publication_service.py — adaptador WEB de la PUBLICACIÓN de un modelo de SAP Datasphere al Catálogo gobernado.

Hermano de ``datasphere_assessment_service`` pero con el contrato OPUESTO: PUBLICAR, no evaluar. Una ejecución es

    adquirir evidencia (CSN) en el pool CANÓNICO (``catalog_local/.csn_cache``)
      → regenerar el Catálogo desde ese pool (``generate_catalog=True``)
      → invalidar la caché del Catálogo → el nuevo dominio queda disponible en la pestaña "Catálogo"
      → registrar el assessment CANÓNICO del modelo desde ESA MISMA evidencia (``register_assessment``: sin reconectar ni re-extraer)

Se apoya en la MISMA función que el CLI (``tools/catalog/DSP_CLI_Export_Lineage.py::run_export``), con el modo forzado por el
SERVIDOR (``mode="publish"``): la UI nunca elige el modo, y esta herramienta NO comparte su ruta POST con el Assessment (evaluar y
publicar son acciones de negocio distintas; ver webapp/routers/datasphere_publication.py).

Reusa del servicio de Assessment TODO lo que es manejo de entrada / saneamiento / validación (no es "acción de negocio"):
``FormValues``, ``validate``, ``cli_available``, ``sanitize``/``_trace``, ``requested_features``, ``Outcome``, los catálogos de
mensajes de error y de features, y el contexto de formulario NO sensible por sesión. Lo propio de acá es el ``run()`` que publica.

Secretos: el ``client_secret`` entra por argumento, vive solo durante la extracción, jamás se guarda ni se devuelve ni se registra.
A diferencia del Assessment, esta herramienta SÍ escribe en el pool/Catálogo canónicos (ese es su objetivo); nunca sube a Spaces
salvo que se habilite explícitamente por entorno (``DANWOR_PUBLICATION_MIRROR_UP``).
"""
from __future__ import annotations

import base64
import logging
import os
import threading
import urllib.parse
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import config
from webapp.services import datasphere_assessment_service as asd
from webapp.services import republication_stage

log = logging.getLogger("danwor.datasphere_publication")

# Módulos donde la publicación está disponible (mismo alcance que el Assessment: 04 · Modelado & Desarrollo).
PUBLICATION_MODULES = frozenset({"04_Modelado_y_Desarrollo"})

TITLE_PUBLISH = "No se pudo publicar el modelo"
# Fase semántica única: "extracting" es la inicial (sin evento); al terminar la extracción se regenera el Catálogo.
PHASE_PUBLISHING = "publishing"
PHASE_REGISTERING = "registering"
PHASE_PUBLISHED = "published"
PHASE_COPY = {PHASE_PUBLISHING: "Publicando en el catálogo...", PHASE_REGISTERING: "Registrando el assessment del modelo...",
              PHASE_PUBLISHED: "Catálogo actualizado"}


# ───────────────────────────── disponibilidad / autorización ─────────────────────────────
def tool_enabled(stem: str) -> bool:
    return stem in PUBLICATION_MODULES


# ── capacidad «Publicar» para consultores (apagada por defecto) ──
# Publicar ESCRIBE en el Catálogo gobernado (pool, manifiestos, Catálogo materializado, assessments): una escritura potencialmente destructiva. Abrirla a los consultores exige TRES condiciones, todas a la vez,
# y por eso cada una tiene su propio interruptor/garantía (ver docs/PUBLISH_CONSULTANT_ACCESS.md):
#   1. ``DANWOR_PUBLICATION_CONSULTANTS`` encendida (decisión explícita del dueño; apagada = comportamiento de siempre: solo el dueño);
#   2. las garantías de integridad de la publicación segura ACTIVAS (``DANWOR_PUBLICATION_SAFE``, fase 0 / P1): sin ellas no se concede, aunque 1 esté encendida (falla cerrado). Si el código de la publicación
#      segura no está presente en este despliegue, la garantía no puede estar activa;
#   3. clientes autorizados EXPLÍCITAMENTE para consultores (``DANWOR_PUBLICATION_CONSULTANT_CLIENTS``, lista separada por comas): un consultor solo publica sobre esos clientes. Lista vacía ⇒ no se concede.
# No se distingue «primera publicación» de «republicación»: la identidad vigente no permite demostrarla de forma confiable (ver el documento); lo acota la lista de clientes y lo protege la publicación segura.
CONSULTANTS_ENV = "DANWOR_PUBLICATION_CONSULTANTS"
CONSULTANT_CLIENTS_ENV = "DANWOR_PUBLICATION_CONSULTANT_CLIENTS"
CLIENT_NOT_AUTHORIZED_TITLE = "No se pudo publicar el modelo"
CLIENT_NOT_AUTHORIZED_MESSAGE = ("Tu usuario no está autorizado para publicar en ese cliente. Pedile al administrador que lo habilite o elegí uno de los clientes autorizados.")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def publication_guarantees_active() -> bool:
    """¿Están ACTIVAS las garantías de integridad de la publicación (publicación segura: completitud, persistencia confirmada, candado, manifiestos íntegros)? Falla cerrado: sin el módulo o con el
    interruptor apagado, ``False``."""
    try:
        from tools.catalog import publication_txn
    except Exception:                                                   # noqa: BLE001 — este despliegue no trae la publicación segura
        return False
    check = getattr(publication_txn, "safe_mode_enabled", None)
    try:
        return bool(callable(check) and check())
    except Exception:                                                   # noqa: BLE001
        return False


def _client_key(name: str) -> str:
    return " ".join(str(name or "").split()).casefold()


def consultant_clients() -> tuple[str, ...]:
    """Clientes que el dueño autorizó EXPLÍCITAMENTE para publicación por consultores (``DANWOR_PUBLICATION_CONSULTANT_CLIENTS``). Sin comodines."""
    seen: dict[str, str] = {}
    for raw in os.getenv(CONSULTANT_CLIENTS_ENV, "").split(","):
        name = " ".join(raw.split())
        if name and name != "*":
            seen.setdefault(_client_key(name), name)
    return tuple(seen.values())


def consultants_enabled() -> bool:
    """La capacidad está disponible para consultores solo si el interruptor, las garantías de integridad Y al menos un cliente autorizado coinciden."""
    return _env_flag(CONSULTANTS_ENV) and publication_guarantees_active() and bool(consultant_clients())


def can_execute(request) -> bool:
    """Publicar en el Catálogo gobernado: el dueño (``access.is_owner_user``), o —solo con la capacidad para consultores disponible (``consultants_enabled``)— un consultor con sesión válida y perfil COMPLETO del
    Hub (``user_tier == "full"``: el de solo lectura y quien está fuera de la allowlist quedan afuera; el retiro de la allowlist surte efecto en la siguiente request). UN SOLO predicado gobierna la UI (botón «+»,
    panel, deep links) y el backend (POST /run). NO amplía ``access.is_owner``: las capacidades administrativas siguen siendo del dueño (``can_use_client_profiles``, ``can_administer``)."""
    from webapp import access
    from webapp.services import auth_service
    user = auth_service.session_user(request)
    if not user:
        return False
    if access.is_owner_user(user):
        return True
    return consultants_enabled() and auth_service.user_tier(str(user.get("sub") or "")) == "full"


def can_use_client_profiles(request) -> bool:
    """Perfiles de clientes de SharePoint (credenciales OAuth de TODOS los clientes): solo el dueño; un consultor usa SUS credenciales en el formulario."""
    return asd.can_use_client_profiles(request)


def client_authorized(request, client: str) -> bool:
    """¿Puede ESTE usuario publicar sobre ``client``? El dueño, sobre cualquiera; un consultor, solo sobre los clientes autorizados explícitamente (sin distinguir mayúsculas ni espacios repetidos)."""
    from webapp import access
    from webapp.services import auth_service
    if access.is_owner_user(auth_service.session_user(request)):
        return True
    return _client_key(client) in {_client_key(c) for c in consultant_clients()}


def denied_run_events(code: str, message: str) -> str:
    """Cuerpo NDJSON de una ejecución rechazada ANTES de empezar (mismo contrato que el stream del run; no se ejecuta nada)."""
    import json
    return (json.dumps({"t": "start"}) + "\n" + json.dumps({"t": "done", "state": "FAILED", "error_code": code, "message": message, "title": CLIENT_NOT_AUTHORIZED_TITLE,
                                                          "errors": {}, "operation_id": None, "duration_s": 0, "result_url": None}, ensure_ascii=False) + "\n")


def tool_context(request, stem: str) -> dict:
    """Contexto para la plantilla del panel de publicación. Mismo shape que el Assessment (``enabled/can_execute/stem/form/
    feature_options``) salvo que NO hay "recientes": publicar no genera un informe ad-hoc, escribe en el Catálogo. ``form`` son
    SOLO los campos no sensibles de la última ejecución de ESTA sesión (compartidos con el Assessment: mismas credenciales de
    tenant, nunca el secreto)."""
    enabled = tool_enabled(stem)
    can = bool(enabled and can_execute(request))
    owner_only = bool(can and can_use_client_profiles(request))
    values: dict[str, str] = {}
    if can:
        values = asd.load_form_context(asd.session_key(request))
    selected = values.get("features")
    checked = list(asd.OPTIONAL_FEATURES) if selected is None else [f for f in asd.OPTIONAL_FEATURES if f in selected.split(",")]
    options = [{"value": f, "label": asd.FEATURE_LABELS[f], "checked": f in checked} for f in asd.OPTIONAL_FEATURES]
    from webapp.services import datasphere_profiles
    return {"enabled": enabled, "can_execute": can, "stem": stem, "form": values, "feature_options": options,
            "owner_profiles_url": datasphere_profiles.selector_url(stem, owner_only),               # el libro compartido de perfiles es SOLO del dueño
            "allowed_clients": [] if (owner_only or not can) else list(consultant_clients())}      # lo que un consultor puede publicar (informativo; el servidor lo exige igual)


# ───────────────────────────── recuperación: confirmar o deshacer lo que quedó a medias, con la compuerta de lectores ─────────────────────────────
def resolve_pending(root=None) -> list[dict]:
    """Resuelve (VERIFICANDO Spaces) las publicaciones a medias o sin confirmar y descarta las cachés si algo cambió. Lo usa la compuerta de lectores (cada ≤30 s mientras haya un
    bloqueo), el arranque y el servicio antes de publicar. Nunca lanza."""
    from core import publication_gate as gate
    from tools.catalog import publication_txn as txn
    try:
        base = Path(root) if root else _canonical_roots()[0]
        was_closed = gate.read_state(base) is not None
        if gate.is_boot_closed(base):
            _retry_restore_async()                                      # un restore de arranque que falló o quedó incompleto se reintenta sin bloquear al lector
        results = txn.recover_pending(base, timeout=0)
    except Exception:                                                   # noqa: BLE001 — sin candado o sin poder restaurar: sigue pendiente (la compuerta no se abre)
        log.warning("datasphere_publication: la recuperación no pudo completarse", exc_info=True)
        return []
    if results or (was_closed and gate.read_state(base) is None):
        republication_stage.AssessmentStage().finished(None)           # lo restaurado/recuperado es la verdad: la memoria no conserva nada de antes
    for item in results:
        if item.get("action") in ("UNCERTAIN", "BLOCKED"):
            log.error("datasphere_publication: la publicación %s (cliente %s) NO pudo resolverse: %s %s; la lectura del Catálogo sigue bloqueada hasta resolverlo",
                      item.get("operation_id"), item.get("client_dir"), item.get("code"), item.get("detail"))
    return results


_RESTORE_RETRY = {"running": False}


def _retry_restore_async() -> None:
    """Reintenta el restore de arranque (sin sobrescribir lo local) si falló o quedó incompleto: mientras tanto no se puede verificar que lo restaurado sea UNA versión publicada."""
    from core import remote_store
    if _RESTORE_RETRY["running"] or remote_store.restore_status() == "in_progress" or remote_store.sync_blocked_reason() is None:
        return
    _RESTORE_RETRY["running"] = True

    def run() -> None:
        try:
            remote_store.restore()
        finally:
            _RESTORE_RETRY["running"] = False

    threading.Thread(target=run, name="publication-restore-retry", daemon=True).start()


def begin_boot_gate() -> bool:
    """Arranque con la publicación segura y la persistencia remota encendidas: cierra la compuerta de lectores ANTES de servir, hasta que el restore termine y la recuperación verifique lo
    restaurado (un disco perdido con Spaces a medias no puede mostrarse como vigente). True si la cerró."""
    from core import publication_gate as gate, remote_store
    from tools.catalog import publication_txn as txn
    if not (txn.safe_mode_enabled() and remote_store.enabled()):
        return False
    gate.begin_boot(_canonical_roots()[0])
    return True


def _mirror_guard() -> str | None:
    """Guarda del sync periódico: con la publicación segura encendida, NO sube mientras haya una publicación, una verificación de arranque o una recuperación sin resolver."""
    from core import publication_gate as gate
    from tools.catalog import publication_txn as txn
    if not txn.safe_mode_enabled():
        return None
    root = _canonical_roots()[0]
    if gate.read_state(root) is not None:
        return "hay una publicación o una verificación de arranque en curso"
    if txn.has_pending(root):
        return "hay una publicación sin resolver"
    return None


try:
    from core import remote_store as _remote_store
    _remote_store.set_mirror_guard(_mirror_guard)
except Exception:                                                       # noqa: BLE001
    log.warning("datasphere_publication: no se pudo registrar la guarda del sync periódico")


def verify_ledgers(root=None) -> dict[str, list[str]]:
    """``{cliente: diferencias}`` de cada cliente cuyo disco NO coincide con la versión activa de su bitácora (digests de TODA la unidad: pool, linaje, Catálogo y assessments). Es la
    detección de una mezcla —p. ej. un restore desde un Spaces que quedó a medias por una caída— que ninguna publicación completó. Solo LECTURA; vacío = todo coherente."""
    from tools.catalog import publication_ledger as ledger_mod
    from webapp.services import assessment_service as assess
    catalog_root, csn_cache = _canonical_roots()
    base = Path(root) if root else catalog_root
    out: dict[str, list[str]] = {}
    folder = base / ledger_mod.STATE_DIR
    if not folder.is_dir():
        return out
    for lf in sorted(folder.glob("*" + ledger_mod.SUFFIX)):
        client = lf.name[: -len(ledger_mod.SUFFIX)]
        state, data = ledger_mod.load(base, client)
        if state != ledger_mod.L_OK:
            out[client] = ["ledger " + state]
            continue
        problems = ledger_mod.verify(data, ledger_mod.unit_files(base, csn_cache, client, assess.governed_client_dir(client)))
        if problems:
            out[client] = problems
    return out


def boot_recover() -> None:
    """Arranque (después del restore): completa o deshace una publicación que la caída del proceso dejó a medias y VERIFICA que lo restaurado sea una versión publicada (no una mezcla).
    Solo con la publicación segura encendida. Una incoherencia se registra con severidad de error: se corrige republicando el modelo (la republicación detecta el desvío y reescribe la unidad)."""
    from tools.catalog import publication_txn as txn
    if not txn.safe_mode_enabled():
        return
    resolve_pending()
    try:
        from core import publication_gate as gate
        st = gate.read_state(_canonical_roots()[0])
        if st is not None:
            log.error("datasphere_publication: la lectura del Catálogo sigue BLOQUEADA tras el arranque (%s, operación %s, cliente %s); se reintenta sola y se resuelve a mano según docs/PUBLICATION_SAFE_RUNBOOK.md",
                      st.get("reason") or st.get("state"), st.get("operation_id"), st.get("client_dir"))
    except Exception:                                                   # noqa: BLE001
        log.warning("datasphere_publication: no se pudo leer la compuerta tras el arranque", exc_info=True)
    try:
        for client, problems in verify_ledgers().items():
            log.error("datasphere_publication: el cliente %s NO coincide con su última publicación (%d diferencia(s): %s); republicá su modelo para reconstruirlo", client, len(problems), "; ".join(problems[:3]))
    except Exception:                                                   # noqa: BLE001 — la verificación nunca impide el arranque
        log.warning("datasphere_publication: no se pudo verificar la coherencia de las publicaciones", exc_info=True)


try:                                                                    # la compuerta de lectores intenta resolver un bloqueo (UNCERTAIN) sin esperar a la próxima publicación
    from core import publication_gate as _gate
    _gate.set_resolver(resolve_pending)
except Exception:                                                       # noqa: BLE001
    log.warning("datasphere_publication: no se pudo registrar el resolvedor de la compuerta")


# ───────────────────────────── single-flight (espacio de claves propio) ─────────────────────────────
_LOCKS_GUARD = threading.Lock()
_RUNNING: dict[tuple[str, str, str], threading.Lock] = {}


def _lock_for(values: asd.FormValues) -> threading.Lock:
    key = (values.client.lower(), values.space.lower(), values.root_object.lower())
    with _LOCKS_GUARD:
        return _RUNNING.setdefault(key, threading.Lock())


def _mirror_up_enabled() -> bool:
    """Subir a DigitalOcean Spaces (persistencia remota) está APAGADO por defecto: publicar regenera el Catálogo local
    (``generate_catalog=True``), que es lo que la UI necesita mostrar. El espejo remoto se habilita explícitamente por entorno."""
    return os.getenv("DANWOR_PUBLICATION_MIRROR_UP", "").strip().lower() in {"1", "true", "yes", "on"}


def _canonical_roots() -> tuple[Path, Path]:
    """Raíces CANÓNICAS del Catálogo (las MISMAS que usa ``refresh_catalog`` del Assessment y el CLI): el directorio del Catálogo y
    su pool CSN. A diferencia del Assessment (workspace efímero por operación), publicar escribe acá a propósito."""
    catalog_root = Path(os.getenv("DANWOR_CATALOG_DIR", "").strip() or str(config.BASE_DIR / "catalog_local")).resolve()
    csn_cache = Path(os.getenv("DANWOR_CATALOG_CSN_CACHE", "").strip() or str(catalog_root / ".csn_cache")).resolve()
    return catalog_root, csn_cache


# ───────────────────────────── puente PUBLICAR → assessment canónico ─────────────────────────────
def register_assessment(values: asd.FormValues) -> list[tuple[str, str]]:
    """Registra el assessment CANÓNICO del modelo recién publicado. UNA adquisición, dos registros: la evidencia (CSN) que la
    extracción ya dejó en el pool canónico alimenta el catálogo Y este assessment; NO reconecta a Datasphere ni vuelve a extraer.

    Reusa SIN cambios la integración del Catálogo (``assessment_service.generate``: misma resolución entrada → AM terminal, misma
    llave de vigencia, mismo motor y misma caché operativa ``(cliente, AM)``). Es determinista y sin LLM. NUNCA lanza ni hace fallar la
    publicación: si algo falla, el modelo queda publicado y su assessment en el estado estable que corresponda (p. ej. FAILED →
    reintentable desde el Catálogo). Devuelve ``[(entry_id, estado)]`` de las entradas del Catálogo que contienen el modelo."""
    out: list[tuple[str, str]] = []
    try:
        from webapp.services import assessment_service as assess, catalog_reads
        _mod, entries = catalog_reads._catalog_entries()
        client, root = values.client.strip().lower(), values.root_object.strip()
        for entry in entries:
            if str(entry.cliente).strip().lower() == client and root in {str(a) for a in (entry.artefactos or [])}:
                out.append((entry.entry_id, assess.generate(entry).state))
    except Exception:                                                   # noqa: BLE001 — registrar el assessment jamás rompe la publicación
        log.exception("datasphere_publication: no se pudo registrar el assessment del modelo publicado")
    return out



# ───────────────────────────── contrato de resultado de la publicación SEGURA (fase 0) ─────────────────────────────
@dataclass
class PublicationOutcome(asd.Outcome):
    """``Outcome`` + el estado explícito de la publicación segura. ``state``: SUCCESS solo con ``status == COMPLETED``; PARTIAL (publicado con advertencias) para
    ``COMMITTED_UNCONFIRMED`` / ``COMMITTED_CLEANUP_DEFERRED``; FAILED para el resto. Nunca hay SUCCESS ante un estado degradado."""
    status: str = ""
    durability: str = ""
    stages: dict = field(default_factory=dict)
    deferred_reasons: list = field(default_factory=list)
    failures: list = field(default_factory=list)
    change: str = ""                                          # CHANGED | NO_CHANGE (una republicación idéntica no escribe nada)
    assessment: dict = field(default_factory=dict)           # resumen: regenerados / conservados / inciertos / retirados (P1)


STATE_PARTIAL = "PARTIAL"
TITLE_PARTIAL = "Publicación parcial"
# Mensajes estables (códigos del contrato). Jamás texto crudo del CLI, rutas ni secretos.
PUBLICATION_MESSAGES = {
    "EXTRACTION_INCOMPLETE": "La extracción desde Datasphere quedó incompleta (falló la lectura o el listado de algún objeto). No se modificó el Catálogo. Reintentá; si persiste, revisá la conexión y los permisos.",
    "POOL_IDENTITY_CONFLICT": "Un objeto ya está publicado desde otro Space o tenant con contenido distinto. No se modificó nada. Revisá el cliente, el Space y el tenant.",
    "ASSESSMENT_FAILED": "No se pudo preparar el assessment de los modelos afectados por esta publicación. No se activó nada: se conserva la última versión válida del Catálogo y de sus assessments.",
    "PERSISTENCE_FAILED": "Spaces no aceptó la versión nueva. Se mantuvo la última versión confirmada (en el servidor y en Spaces). Reintentá la publicación.",
    "PERSISTENCE_UNCERTAIN": "No se pudo verificar en Spaces si la publicación quedó guardada. La lectura del Catálogo queda bloqueada temporalmente hasta resolverlo; se reintenta solo.",
    "PERSISTENCE_DIVERGED": "Se mantuvo la última versión confirmada en el servidor, pero Spaces todavía no la refleja. No se puede publicar hasta resolverlo; se reintenta solo.",
    "PERSISTENCE_NOT_AVAILABLE": "La persistencia en Spaces no está disponible en este modo. No se modificó nada.",
    "REMOTE_NOT_READY": "Spaces todavía no terminó de restaurar el estado (o lo hizo de forma incompleta). No se modificó nada; reintentá en unos minutos.",
    "REMOTE_LISTING_FAILED": "No se pudo leer el inventario de Spaces para verificar la publicación. No se modificó nada; reintentá en unos minutos.",
    "REMOTE_NOT_RECONCILED": "Hay archivos locales de este cliente que difieren de Spaces y no se reconciliaron. No se modificó nada; avisá al equipo técnico.",
    "POOL_ROOT_MISMATCH": "El assessment leería un pool distinto del que se está publicando. No se modificó nada; avisá al equipo técnico.",
    "CLIENT_KEY_CONFLICT": "El Catálogo ya tiene más de una variante del nombre de este cliente que solo difiere en mayúsculas o minúsculas. No se modificó nada; pedí a un administrador que unifique los directorios del cliente.",
    "CATALOG_BUSY": "Hay otra operación sobre el Catálogo en curso. No se modificó nada; reintentá en unos minutos.",
    "RECOVERY_REQUIRED": "Hay una operación anterior a medias que no pudo recuperarse. No se publica hasta resolverla; avisá al equipo técnico.",
    "CATALOG_GENERATION_FAILED": "La extracción terminó, pero falló la regeneración del catálogo. Se restauró el estado anterior del Catálogo.",
    "VERIFY_FAILED": "La publicación no pasó la verificación posterior. Se restauró el estado anterior del Catálogo.",
    "APPLY_FAILED": "La publicación falló al aplicarse. Se restauró el estado anterior del Catálogo.",
    "PERSISTENCE_UNCONFIRMED": "Publicado en el Catálogo local, pero la persistencia en Spaces NO está confirmada. Reintentá la publicación (es idempotente) para confirmarla.",
    "PERSISTENCE_MODE_NOT_REMOTE": "El servidor tiene credenciales de Spaces pero no está en modo de persistencia remota: lo publicado se perdería en el próximo reinicio. No se modificó nada; avisá al equipo técnico (DANWOR_PERSISTENCE=remote).",
    "PERSISTENCE_NOT_WRITABLE": "La persistencia remota está indicada pero la escritura en Spaces no está habilitada. No se modificó nada; avisá al equipo técnico.",
    "PERSISTENCE_NOT_REQUESTED": "La publicación segura exige confirmar en Spaces y esa confirmación no está activada en el servidor. No se modificó nada; avisá al equipo técnico (DANWOR_PUBLICATION_MIRROR_UP).",
    "CATALOG_NOT_PERSISTED": "El directorio del Catálogo no está entre los que se guardan en Spaces. No se modificó nada; avisá al equipo técnico (SPACES_PERSIST_PATHS).",
    "ASSESSMENT_NOT_PERSISTED": "Los assessments no se guardarían en Spaces (el almacén 'governed-assessments' no está cubierto por la persistencia). No se modificó nada; avisá al equipo técnico (SPACES_PERSIST_PATHS).",
    "ASSESSMENT_STORE_UNAVAILABLE": "El almacén de assessments no tiene un directorio válido, así que no habría dónde guardarlos. No se modificó nada; avisá al equipo técnico.",
    "CLEANUP_DEFERRED": "Publicado, pero no se limpiaron los objetos obsoletos porque no se pudo verificar la propiedad (manifiesto ilegible o restauración incompleta). No se borró nada; reintentá más tarde.",
}


def _safe_outcome(values: asd.FormValues, result, trace: list, secrets: tuple, roots: dict, started: datetime, user_email: str, on_line, on_phase) -> "PublicationOutcome":
    """Traduce el ``ExportResult`` de la publicación segura al ``Outcome`` de la UI. SUCCESS ⇔ ``status == COMPLETED``."""
    from tools.catalog import publication_txn as txn
    code = result.error_code or ""
    common = dict(trace=trace, operation_id=result.operation_id, duration_s=result.duration_s, status=result.status, durability=result.durability,
                  stages=dict(result.stages), deferred_reasons=list(result.deferred_reasons), failures=list(result.failures))
    if result.status in txn.SUCCESS_STATUSES or result.status in txn.PARTIAL_STATUSES:
        # P1: el assessment YA es parte de la unidad activada (se preparó, validó y escribió ANTES de activar, y las cachés se descartaron antes de abrir la compuerta): no hay registro posterior.
        if on_phase is not None:
            on_phase(PHASE_PUBLISHED)
        summary = dict((result.applied or {}).get("assessment") or {})
        common["change"] = result.outcome or ""
        common["assessment"] = summary
        if on_line is not None:
            changes = ", ".join(f"{k} {len(v)}" for k, v in summary.items() if v)
            on_line("Assessment: " + (changes or "sin cambios"))
        duration = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
        common["duration_s"] = duration
        if result.status in txn.SUCCESS_STATUSES:
            log.info("datasphere_publication op=%s user=%s client=%s space=%s model=%s status=COMPLETED change=%s durability=%s duration=%.1fs files=%s",
                     result.operation_id, user_email, values.client, values.space, values.root_object, result.outcome, result.durability, duration, result.exported_files)
            return PublicationOutcome("SUCCESS", stage="publish", terminal_am=values.root_object, **common)
        log.warning("datasphere_publication op=%s user=%s client=%s space=%s model=%s status=%s code=%s", result.operation_id, user_email, values.client, values.space,
                    values.root_object, result.status, code)
        return PublicationOutcome(STATE_PARTIAL, code, PUBLICATION_MESSAGES.get(code, "Publicación parcial."), stage="publish", title=TITLE_PARTIAL,
                                  terminal_am=values.root_object, **common)
    log.warning("datasphere_publication op=%s user=%s client=%s space=%s model=%s status=%s code=%s", result.operation_id, user_email, values.client, values.space,
                values.root_object, result.status, code)
    message = PUBLICATION_MESSAGES.get(code) or asd.ERROR_MESSAGES.get(code) or asd.ERROR_MESSAGES["EXTRACTION_FAILED"]
    return PublicationOutcome("FAILED", code or "EXTRACTION_FAILED", message, stage="publish", title=TITLE_PUBLISH, **common)


# ───────────────────────────── ejecución (PUBLICAR) ─────────────────────────────
def run(values: asd.FormValues, client_secret: str, *, user_email: str = "", on_line=None, on_phase=None) -> asd.Outcome:
    """Valida y ejecuta la PUBLICACIÓN: extracción + regeneración del Catálogo (MISMA función que el CLI, ``mode="publish"``,
    ``generate_catalog=True``) contra las raíces canónicas, y luego invalida la caché del Catálogo. El secreto no se almacena ni se
    devuelve. ``on_line`` recibe, en tiempo real y saneada, cada línea del extractor."""
    op_id = uuid.uuid4().hex[:10]
    errors = asd.validate(values, client_secret)
    if errors:
        return asd.Outcome("INVALID", "INVALID_PARAMETER", asd.ERROR_MESSAGES["INVALID_PARAMETER"], errors=errors, operation_id=op_id)
    if not asd.cli_available():
        log.warning("datasphere_publication op=%s status=FAILED code=DATASPHERE_CLI_UNAVAILABLE", op_id)
        return asd.Outcome("FAILED", "DATASPHERE_CLI_UNAVAILABLE", asd.ERROR_MESSAGES["DATASPHERE_CLI_UNAVAILABLE"],
                           operation_id=op_id, stage="extraction", title=TITLE_PUBLISH)
    lock = _lock_for(values)
    if not lock.acquire(blocking=False):
        return asd.Outcome("FAILED", "OPERATION_ALREADY_RUNNING", asd.ERROR_MESSAGES["OPERATION_ALREADY_RUNNING"],
                           operation_id=op_id, stage="extraction", title=TITLE_PUBLISH)
    # Formas derivadas del secreto que podrían aparecer en una línea de log (Basic = base64(id:secret), urlencoded): se redactan.
    secrets = tuple(dict.fromkeys(s for s in (
        client_secret,
        base64.b64encode(f"{values.client_id}:{client_secret}".encode("utf-8")).decode("ascii"),
        urllib.parse.quote(client_secret, safe=""), urllib.parse.quote_plus(client_secret)) if s))
    try:
        from tools.catalog import DSP_CLI_Export_Lineage as dsp
        catalog_root, csn_cache = _canonical_roots()
        catalog_root.mkdir(parents=True, exist_ok=True)
        csn_cache.mkdir(parents=True, exist_ok=True)
        from tools.catalog import publication_txn as txn
        safe = txn.safe_mode_enabled()                                   # el interruptor se lee UNA vez por operación
        try:                                                             # con o sin interruptor: un journal a medias nunca se pisa (apagar el interruptor no deja estados mixtos)
            txn.recover_pending(catalog_root)
        except txn.RecoveryError:
            return PublicationOutcome("FAILED", "RECOVERY_REQUIRED", PUBLICATION_MESSAGES["RECOVERY_REQUIRED"], operation_id=op_id, stage="publish", title=TITLE_PUBLISH,
                                      status=txn.RECOVERY_REQUIRED)
        roots = {str(catalog_root): "<catalog_local>", str(csn_cache): "<pool>", str(config.BASE_DIR): "<app>"}
        request = dsp.ExportRequest(
            host=values.host, client_id=values.client_id, client_secret=client_secret, token_url=values.token_url,
            space=values.space, root_object=values.root_object, client=values.client,
            catalog_root=catalog_root, csn_cache_dir=csn_cache,         # raíces CANÓNICAS: acá SÍ se escribe el Catálogo
            generate_catalog=True, mirror_up=(True if safe else _mirror_up_enabled()),   # seguro: la confirmación remota es OBLIGATORIA; si no, Spaces solo si se habilita
            mode="publish",                                             # forzado en el servidor: la UI no elige el modo
            features_requested=asd.requested_features(values),         # SOLO lo pedido: las dependencias las resuelve el extractor
            safe=safe,
            stage_hooks=(republication_stage.AssessmentStage() if safe else None),     # P1: el assessment gobernado forma parte de la unidad (preparar → validar → activar)
        )

        def live(line: str) -> None:
            if on_line is not None:
                on_line(asd.sanitize(line, secrets, roots)[:2000])

        if on_phase is not None:
            on_phase(PHASE_PUBLISHING)
        started = datetime.now(timezone.utc)
        with dsp.capture_output(on_line=live) as cap:
            result = dsp.run_export(request)
        trace = asd._trace(cap.lines, secrets, roots)
        if safe:
            return _safe_outcome(values, result, trace, secrets, roots, started, user_email, on_line, on_phase)
        if not result.ok:
            code = result.error_code or "EXTRACTION_FAILED"
            log.warning("datasphere_publication op=%s user=%s client=%s space=%s model=%s status=FAILED code=%s duration=%.1fs",
                        op_id, user_email, values.client, values.space, values.root_object, code, result.duration_s)
            backend = asd._CODE_PREFIX_RE.sub("", asd.sanitize(result.message, secrets, roots)[:500], count=1)
            message = backend if (str(result.message).startswith("PRECHECK_") or code in asd.BACKEND_WORDED_CODES) \
                else asd.ERROR_MESSAGES.get(code, asd.ERROR_MESSAGES["EXTRACTION_FAILED"])
            stage = "assessment" if code in {"CATALOG_GENERATION_FAILED", "STORAGE_UPLOAD_FAILED"} else "extraction"
            return asd.Outcome("FAILED", code, message, trace=trace, operation_id=op_id, duration_s=result.duration_s,
                               stage=stage, title=TITLE_PUBLISH)
        # Publicado → invalidar la caché del Catálogo para que el nuevo dominio aparezca de inmediato.
        try:
            from webapp import main as hub_main
            hub_main._invalidate_catalog_cache()
        except Exception:                                               # noqa: BLE001
            log.warning("datasphere_publication op=%s no se pudo invalidar el caché del catálogo", op_id)
        # Mismo pool, sin reconectar: el assessment canónico del modelo publicado queda registrado junto con el Catálogo.
        if on_phase is not None:
            on_phase(PHASE_REGISTERING)
        registered = register_assessment(values)
        if on_line is not None:
            for _eid, state in registered:
                on_line(f"Assessment del modelo registrado: {state}")
        if on_phase is not None:
            on_phase(PHASE_PUBLISHED)
        duration = round((datetime.now(timezone.utc) - started).total_seconds(), 1)
        log.info("datasphere_publication op=%s user=%s client=%s space=%s model=%s status=SUCCESS duration=%.1fs files=%s",
                 op_id, user_email, values.client, values.space, values.root_object, duration, result.exported_files)
        return asd.Outcome("SUCCESS", trace=trace, operation_id=op_id, duration_s=duration, stage="publish",
                           terminal_am=values.root_object)
    except Exception as exc:                                            # noqa: BLE001 — jamás se muestra el texto crudo
        log.warning("datasphere_publication op=%s status=FAILED code=EXTRACTION_FAILED exc=%s", op_id, type(exc).__name__)
        return asd.Outcome("FAILED", "EXTRACTION_FAILED", asd.ERROR_MESSAGES["EXTRACTION_FAILED"], trace=[f"{type(exc).__name__}"],
                           operation_id=op_id, stage="extraction", title=TITLE_PUBLISH)
    finally:
        lock.release()


# Reexport para que la plantilla/el router compartan los mismos textos de campo que el Assessment.
owner_email = asd.owner_email
session_key = asd.session_key
save_form_context = asd.save_form_context
FormValues = asd.FormValues
