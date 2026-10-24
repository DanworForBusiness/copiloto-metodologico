"""fix_contador_sr_profiles — saneamiento ONE-OFF del Banco de CVs de CCHH: la especialidad histórica `Contador SR` pasa a `Contador` y, si el seniority está VACÍO, se asigna `Senior`.

    python -m tools.human_capital.fix_contador_sr_profiles                                   # --dry-run (por defecto): SOLO lectura; cantidades, conflictos, antes/después y la frase de confirmación
    python -m tools.human_capital.fix_contador_sr_profiles --execute --confirm SANEAR-CONTADOR-<env8>-<digest12>
    python -m tools.human_capital.fix_contador_sr_profiles --json                             # el informe completo como JSON (sin datos personales)

REGLAS
  * Selección por TOKEN: `profile_skills` es texto libre «Esp1, Esp2»; se separa por coma y se compara cada especialidad EXACTA (`Contador SR`, tras recortar espacios). Nada de coincidencia parcial: `Contador SRL`, `Contador SR Impuestos`
    o `Contador Senior` NO se tocan. Las variantes que solo difieren en mayúsculas (`contador sr`) NO se modifican: se cuentan como «variantes ignoradas» para que se revisen a mano.
  * Se reemplaza SOLO ese token por `Contador` (si la persona ya tenía `Contador`, no se duplica); las demás especialidades y el texto que las separa quedan tal cual.
  * Seniority: vacío -> `Senior` (el VALOR EXACTO se valida contra el catálogo gobernado vigente antes de tocar nada; si no está, el programa se bloquea). Ya `Senior` -> no se toca. Cualquier otro valor informado es un CONFLICTO: el perfil NO
    se modifica (ni la especialidad: se perdería la señal «SR» sin dejar rastro) y se informa para revisión humana.
  * Perfiles dados de baja (borrado lógico): nunca se modifican.
  * Toda escritura pasa por `human_capital_service.update_profile` (validación compartida, catálogos gobernados, revisión optimista y escritura/compensación de `profile_repository`): este programa NO escribe archivos JSON ni objetos de Spaces.
    Los campos que no se tocan (documentos, `import_origin`, tags, notas, fuente, potencial…) se conservan: el repositorio fusiona sobre el registro actual (el servicio solo explicita como `null` los campos
    de negocio que faltaban en el registro, sin cambiar su significado).
  * FUENTE DE DATOS: `profile_repository.list()` lee SOLO el disco local; en producción ese disco lo puebla `remote_store.restore()` al arrancar la APP WEB (lifespan), no un proceso aparte. Por eso este CLI, que corre en
    otro proceso, restaura los perfiles desde Spaces con ese mismo mecanismo (acotado a `human-capital/profiles`, sin sobrescribir) y VERIFICA contra el inventario remoto antes de leer: si el listado remoto falla, la
    restauración queda incompleta, un archivo local difiere del remoto o hay menos perfiles locales que objetos remotos, se bloquea sin leer ni escribir. El informe muestra remotos / locales antes / descargados / idénticos.
  * `--execute` exige `--confirm` con la frase derivada del ENTORNO y del PLAN exacto (perfil, revisión y valores nuevos): si algo cambió desde el dry-run, la frase deja de valer y no se escribe nada.
  * Revisión optimista por perfil: ante una modificación concurrente se vuelve a leer ese perfil (una vez) y se re-evalúa; si persiste se informa y se sigue. Ante un fallo de persistencia (Spaces) el programa se DETIENE con el estado
    exacto (hechos / pendientes). Es IDEMPOTENTE y REANUDABLE sin estado propio: un perfil ya corregido deja de seleccionarse, así que repetirlo solo procesa lo que falta.

SALIDA: sin nombres, emails ni teléfonos: solo un identificador corto del perfil (8 caracteres del id opaco), especialidades y seniority antes/después, y conteos.
Códigos: 0 ok · 2 bloqueado (modo/catálogo/restauración) · 4 confirmación faltante o inválida (cero cambios) · 5 falla de persistencia durante la ejecución (reanudable) · 6 terminó pero quedaron perfiles por
modificación concurrente o rechazo de validación (volver a correr)."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import sys
from pathlib import Path

TARGET_TOKEN = "Contador SR"
REPLACEMENT_TOKEN = "Contador"
SENIORITY_VALUE = "Senior"
CONFIRM_PREFIX = "SANEAR-CONTADOR"
CONCURRENT_RETRIES = 1
PROFILES_DIR = "human-capital/profiles"            # directorio persistido que este CLI lee y escribe (misma clave remota que usa el Hub)
EXIT_OK, EXIT_BLOCKED, EXIT_NOT_CONFIRMED, EXIT_FAILED, EXIT_INCOMPLETE = 0, 2, 4, 5, 6

_BUSINESS_FIELDS = ("first_name", "last_name", "email", "phone", "linkedin_url", "location", "profile_skills", "seniority", "source", "tags", "notes", "potential_rating", "potential_mark")


# ═══════════════════ utilidades puras ═══════════════════
def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def short_id(profile_id: str) -> str:
    return str(profile_id)[:8]


def split_specialties(text: str | None) -> list[str]:
    """Segmentos CRUDOS de `profile_skills` (separados por coma, sin recortar): permiten reconstruir el texto sin tocar el resto."""
    return str(text or "").split(",")


def has_target_token(text: str | None) -> bool:
    return any(seg.strip() == TARGET_TOKEN for seg in split_specialties(text))


def has_ignored_variant(text: str | None) -> bool:
    """`contador sr`, `CONTADOR SR`…: parecido pero NO exacto. No se modifica; se cuenta para revisión humana."""
    return any(seg.strip() != TARGET_TOKEN and " ".join(seg.split()).casefold() == TARGET_TOKEN.casefold() for seg in split_specialties(text))


def replace_target_token(text: str | None) -> str:
    """Reemplaza SOLO el token exacto `Contador SR` por `Contador`, conservando el resto del texto (orden, separadores y espacios). Si `Contador` ya estaba, el reemplazado se elimina (sin duplicar)."""
    segments = split_specialties(text)
    already = any(seg.strip() == REPLACEMENT_TOKEN for seg in segments)
    out: list[str] = []
    dropped_head = False
    for index, seg in enumerate(segments):
        if seg.strip() != TARGET_TOKEN:
            out.append(seg)
        elif not already:
            lead = seg[: len(seg) - len(seg.lstrip())]
            trail = seg[len(seg.rstrip()):]
            out.append(f"{lead}{REPLACEMENT_TOKEN}{trail}")
            already = True                                      # un segundo `Contador SR` en el mismo perfil tampoco se duplica
        elif index == 0:
            dropped_head = True                                 # se elimina el reemplazado (ya existe `Contador`): el siguiente pasa a ser el primero, sin el espacio que lo separaba
    if dropped_head and out:
        out[0] = out[0].lstrip()
    result = ",".join(out)
    return result if result.strip() else REPLACEMENT_TOKEN


def seniority_state(value, wanted: str) -> str:
    """`empty` (vacío) | `ok` (ya es el valor buscado) | `conflict` (otro valor ya informado)."""
    current = str(value or "").strip()
    if not current:
        return "empty"
    return "ok" if current.casefold() == wanted.casefold() else "conflict"


def plan_profile(profile: dict, *, wanted_seniority: str) -> dict | None:
    """Plan para UN perfil, o `None` si no corresponde (dado de baja, sin el token exacto). `status`: `change` | `conflict`."""
    if profile.get("deleted_at") or not has_target_token(profile.get("profile_skills")):
        return None
    state = seniority_state(profile.get("seniority"), wanted_seniority)
    before = {"profile_skills": profile.get("profile_skills"), "seniority": profile.get("seniority")}
    if state == "conflict":
        return {"profile_id": profile["profile_id"], "revision": profile.get("revision", 1), "status": "conflict", "before": before, "after": None, "seniority_action": "conflict"}
    after = {"profile_skills": replace_target_token(profile.get("profile_skills")), "seniority": wanted_seniority if state == "empty" else profile.get("seniority")}
    return {"profile_id": profile["profile_id"], "revision": profile.get("revision", 1), "status": "change", "before": before, "after": after,
            "seniority_action": "set" if state == "empty" else "keep"}


def build_plan(profiles: list[dict], *, wanted_seniority: str) -> dict:
    items = [p for p in (plan_profile(profile, wanted_seniority=wanted_seniority) for profile in profiles) if p is not None]
    items.sort(key=lambda p: p["profile_id"])
    return {
        "items": items,
        "total_profiles": len(profiles),
        "deleted_skipped": sum(1 for p in profiles if p.get("deleted_at") and has_target_token(p.get("profile_skills"))),
        "ignored_variants": sum(1 for p in profiles if not p.get("deleted_at") and not has_target_token(p.get("profile_skills")) and has_ignored_variant(p.get("profile_skills"))),
    }


def plan_digest(plan: dict) -> str:
    """Huella del plan EXACTO (perfil, revisión y valores nuevos de los que se van a cambiar): ata la confirmación a lo que se revisó."""
    rows = [[p["profile_id"], p["revision"], p["after"]["profile_skills"], p["after"]["seniority"]] for p in plan["items"] if p["status"] == "change"]
    return _sha(json.dumps(rows, ensure_ascii=False, separators=(",", ":")))[:12]


def environment(deps: dict) -> dict:
    cfg = deps["config"]
    facts = {"mode": str(getattr(cfg, "PERSISTENCE_MODE", "")), "bucket": str(getattr(cfg, "SPACES_BUCKET", "") or ""), "prefix": str(getattr(cfg, "SPACES_PREFIX", "") or "")}
    facts["env8"] = _sha(json.dumps(facts, sort_keys=True))[:8]
    return facts


def confirmation_phrase(env: dict, plan: dict) -> str:
    return f"{CONFIRM_PREFIX}-{env['env8']}-{plan_digest(plan)}"


def summarize(plan: dict) -> dict:
    items = plan["items"]
    changes = [p for p in items if p["status"] == "change"]
    return {
        "perfiles_leidos": plan["total_profiles"],
        "con_especialidad_contador_sr": len(items) + plan["deleted_skipped"],
        "a_modificar": len(changes),
        "con_especialidades_multiples": sum(1 for p in changes if len([s for s in p["before"]["profile_skills"].split(",") if s.strip()]) > 1),
        "seniority_a_asignar": sum(1 for p in changes if p["seniority_action"] == "set"),
        "seniority_ya_correcto": sum(1 for p in changes if p["seniority_action"] == "keep"),
        "conflictos_seniority": sum(1 for p in items if p["status"] == "conflict"),
        "dados_de_baja_omitidos": plan["deleted_skipped"],
        "variantes_ignoradas": plan["ignored_variants"],
    }


def preview_lines(plan: dict, *, limit: int | None = None) -> list[str]:
    lines = []
    for p in plan["items"][:limit]:
        if p["status"] == "conflict":
            lines.append(f"  {short_id(p['profile_id'])}  CONFLICTO  seniority informado distinto de '{SENIORITY_VALUE}': {p['before']['seniority']!r}  (no se modifica)  skills: {p['before']['profile_skills']!r}")
        else:
            lines.append(f"  {short_id(p['profile_id'])}  skills: {p['before']['profile_skills']!r} -> {p['after']['profile_skills']!r}  |  seniority: {p['before']['seniority'] or '(vacío)'} -> {p['after']['seniority']}")
    return lines


# ═══════════════════ dependencias (inyectables en los tests) ═══════════════════
def default_deps() -> dict:
    import config
    from core import remote_store
    from webapp.services import human_capital_catalog_repository as catalog_repo, human_capital_service as service, profile_repository
    return {"config": config, "remote_store": remote_store, "service": service, "repo": profile_repository, "catalog": catalog_repo}


def preflight(deps: dict, wanted_seniority: str, *, need_write: bool) -> list[str]:
    """Bloqueos ANTES de leer o escribir nada: modo de persistencia y valor EXACTO de seniority en el catálogo gobernado vigente."""
    blockers: list[str] = []
    mode = str(getattr(deps["config"], "PERSISTENCE_MODE", ""))
    rs = deps["remote_store"]
    if need_write and rs.read_only_mode():
        blockers.append("MODE_READ_ONLY")
    if mode == "remote" and not rs.enabled():
        blockers.append("REMOTE_NOT_CONFIGURED")
    if wanted_seniority not in deps["catalog"].catalog_values("seniority"):
        blockers.append("SENIORITY_NOT_IN_GOVERNED_CATALOG")
    return blockers


# ═══════════════════ fuente de datos: restauración desde Spaces ═══════════════════
@contextlib.contextmanager
def _persisting_only(cfg, names: list[str]):
    """Acota SOLO durante la restauración los directorios persistidos del proceso (`restore()` y `list_persisted_objects()` los leen de `config.PERSIST_PATHS`): este CLI no necesita bajar catálogos de casos, reportes ni
    el resto de `human-capital`. Se restaura el valor original al salir, así `push_path` y el resto de la ejecución ven la configuración completa."""
    saved = cfg.PERSIST_PATHS
    cfg.PERSIST_PATHS = list(names)
    try:
        yield
    finally:
        cfg.PERSIST_PATHS = saved


def sync_profiles_from_spaces(deps: dict) -> dict:
    """Trae al disco local los perfiles persistidos en Spaces con el mecanismo existente (`remote_store.restore`, sin sobrescribir lo local) y los verifica contra el inventario remoto. Solo lectura del bucket. Devuelve el
    informe (`aplica=False` si no hay lectura remota autorizada: modo `local` o sin credenciales, donde la fuente de verdad es el disco) y `bloqueos`."""
    rs, cfg = deps["remote_store"], deps["config"]
    info: dict = {"aplica": False, "bloqueos": []}
    if not rs.read_enabled():
        return info
    local_dir = Path(cfg.BASE_DIR) / PROFILES_DIR

    def local_count() -> int:
        return sum(1 for _ in local_dir.glob("*.json")) if local_dir.exists() else 0

    info.update(aplica=True, locales_antes=local_count())
    with _persisting_only(cfg, [PROFILES_DIR]):
        try:
            remote = rs.list_persisted_objects(PROFILES_DIR)
        except rs.RemoteReadError:
            info["bloqueos"].append("RESTORE_LISTADO_REMOTO_FALLIDO")
            return info
        info["remotos"] = sum(1 for rel in remote if rel.endswith(".json"))
        rs.restore()                                                       # MISMA función que el arranque de la app web; overwrite=False: nunca pisa un archivo local distinto
    report = dict(rs.last_restore_report)
    info.update(descargados=report.get("downloaded", 0), identicos=report.get("reconciled", 0), locales_distintos=report.get("skipped_existing", 0), fallidos=report.get("failed", 0),
                estado=rs.restore_status(), locales_despues=local_count())
    if rs.restore_status() != "completed" or report.get("failed") or report.get("listing_failed") or info["locales_despues"] < info["remotos"]:
        info["bloqueos"].append("RESTORE_INCOMPLETO")
    if report.get("skipped_existing"):
        info["bloqueos"].append("RESTORE_LOCAL_DISTINTO_DEL_REMOTO")
    return info


# ═══════════════════ ejecución ═══════════════════
def _fields_for_update(profile: dict, after: dict) -> dict:
    """Campos de negocio que espera `update_profile` (texto): los actuales, con solo los dos que cambian. El potencial en estrellas se pasa como texto (el servicio lo vuelve a validar)."""
    fields = {name: profile.get(name) for name in _BUSINESS_FIELDS}
    if fields["potential_rating"] is not None:
        fields["potential_rating"] = str(fields["potential_rating"])
    fields.update(after)
    return fields


def execute(plan: dict, deps: dict, *, wanted_seniority: str) -> dict:
    service, repo = deps["service"], deps["repo"]
    results: list[dict] = []
    aborted: str | None = None
    for item in [p for p in plan["items"] if p["status"] == "change"]:
        outcome = {"id": short_id(item["profile_id"]), "result": "pending"}
        results.append(outcome)
        if aborted:
            continue
        for attempt in range(CONCURRENT_RETRIES + 1):
            current = repo.get(item["profile_id"])
            fresh = plan_profile(current, wanted_seniority=wanted_seniority) if current is not None else None
            if fresh is None:
                outcome["result"] = "ya_corregido_o_no_aplica"           # otro proceso lo corrigió, lo dio de baja o dejó de tener el token
                break
            if fresh["status"] == "conflict":
                outcome["result"] = "conflicto_seniority"
                break
            try:
                # La identidad (nombre/email) no cambia: el aviso de posible duplicado no aplica (podría frenar perfiles con homónimos ya existentes).
                service.update_profile(item["profile_id"], fresh["revision"], _fields_for_update(current, fresh["after"]), duplicate_confirmed=True)
                outcome["result"] = "modificado"
                break
            except repo.ProfileConcurrentModificationError:
                outcome["result"] = "modificacion_concurrente"
                continue                                               # se vuelve a leer y a evaluar con la versión nueva
            except (repo.ProfileStorageError, repo.ProfileRemoteOutcomeUnknownError, repo.ProfileNotFoundError) as exc:
                outcome["result"] = "fallo_persistencia"
                outcome["error"] = type(exc).__name__
                aborted = type(exc).__name__                           # se detiene: reanudable (lo hecho ya no se vuelve a seleccionar)
                break
            except ValueError as exc:
                outcome["result"] = "rechazado_por_validacion"
                outcome["error"] = str(exc)[:160]
                break
    counts: dict[str, int] = {}
    for r in results:
        counts[r["result"]] = counts.get(r["result"], 0) + 1
    return {"results": results, "counts": counts, "aborted": aborted, "pendientes": counts.get("pending", 0)}


# ═══════════════════ CLI ═══════════════════
def run(argv: list[str] | None = None, deps: dict | None = None, out=None) -> int:
    out = out or sys.stdout
    args = _parser().parse_args(argv)
    deps = deps or default_deps()
    execute_mode = bool(args.execute)

    def say(text: str = "") -> None:
        print(text, file=out)

    blockers = preflight(deps, args.seniority, need_write=execute_mode)
    if blockers:
        say(f"BLOQUEADO: {', '.join(blockers)}")
        if "SENIORITY_NOT_IN_GOVERNED_CATALOG" in blockers:
            say(f"  El valor '{args.seniority}' no existe tal cual en el catálogo gobernado de Seniority (valores vigentes: {', '.join(deps['catalog'].catalog_values('seniority')) or 'catálogo no disponible'}). No se leyó ni se escribió nada.")
        return EXIT_BLOCKED
    env = environment(deps)
    sync = sync_profiles_from_spaces(deps)
    if sync["aplica"]:
        say(f"Perfiles en Spaces: {sync.get('remotos', '?')} · locales antes: {sync['locales_antes']} · descargados: {sync.get('descargados', 0)} · ya idénticos: {sync.get('identicos', 0)} · "
            f"locales distintos del remoto: {sync.get('locales_distintos', 0)} · fallidos: {sync.get('fallidos', 0)} · locales después: {sync.get('locales_despues', sync['locales_antes'])}")
    if sync["bloqueos"]:
        say(f"BLOQUEADO: {', '.join(sync['bloqueos'])}")
        say("  No se pudo dejar el disco local fiel a Spaces (ver cifras de arriba): no se leyó ningún perfil ni se escribió nada. Reintentá; si persiste, no ejecutar.")
        return EXIT_BLOCKED
    plan = build_plan(deps["repo"].list(), wanted_seniority=args.seniority)
    summary = summarize(plan)
    phrase = confirmation_phrase(env, plan)
    report = {"modo": "ejecucion" if execute_mode else "dry-run", "entorno": {"persistencia": env["mode"], "env8": env["env8"]}, "restauracion": sync, "resumen": summary}
    say(f"Modo: {report['modo']}  |  persistencia: {env['mode']} ({env['env8']})")
    for key, value in summary.items():
        say(f"  {key}: {value}")
    if sync["aplica"] and summary["perfiles_leidos"] == 0:
        say("ATENCIÓN: 0 perfiles leídos aunque la persistencia es remota: verificá el bucket/prefijo (env8) antes de seguir.")
    shown = plan["items"] if args.limit == 0 else plan["items"][: args.limit]
    if shown:
        say("Antes -> después (identificador corto del perfil; sin datos personales):")
        for line in preview_lines({"items": shown}):
            say(line)
        if len(shown) < len(plan["items"]):
            say(f"  … {len(plan['items']) - len(shown)} más (usar --limit 0 para verlos todos)")
    if summary["conflictos_seniority"]:
        say(f"ATENCIÓN: {summary['conflictos_seniority']} perfil(es) con seniority distinto de '{args.seniority}': NO se modifican (revisar a mano).")
    if not execute_mode:
        say("Dry-run: no se escribió nada.")
        if summary["a_modificar"]:
            say(f"Para ejecutar: python -m tools.human_capital.fix_contador_sr_profiles --execute --confirm {phrase}")
        if args.json:
            say(json.dumps(report, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not summary["a_modificar"]:
        say("Nada para modificar: no quedan perfiles con la especialidad exacta 'Contador SR' pendientes de corregir (idempotente).")
        if args.json:
            say(json.dumps({**report, "ejecucion": {"results": [], "counts": {}, "aborted": None, "pendientes": 0}}, ensure_ascii=False, indent=2))
        return EXIT_OK
    if args.confirm != phrase:
        say("CONFIRMACIÓN FALTANTE O INVÁLIDA: no se modificó nada. La frase depende del entorno y del plan exacto; si los perfiles cambiaron desde el dry-run hay que volver a correrlo.")
        say(f"Frase vigente: {phrase}")
        return EXIT_NOT_CONFIRMED
    result = execute(plan, deps, wanted_seniority=args.seniority)
    report["ejecucion"] = result
    say("Informe final:")
    for key, value in sorted(result["counts"].items()):
        say(f"  {key}: {value}")
    if result["aborted"]:
        say(f"DETENIDO por {result['aborted']}: {result['pendientes']} perfil(es) sin procesar. Es reanudable: volvé a correr dry-run y ejecución (lo ya corregido no se vuelve a tocar).")
    if args.json:
        say(json.dumps(report, ensure_ascii=False, indent=2))
    if result["aborted"]:
        return EXIT_FAILED
    return EXIT_INCOMPLETE if (result["counts"].get("modificacion_concurrente") or result["counts"].get("rechazado_por_validacion")) else EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fix_contador_sr_profiles", description="Saneamiento one-off: especialidad `Contador SR` -> `Contador` y seniority vacío -> `Senior`.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="solo lectura (es el comportamiento por defecto)")
    mode.add_argument("--execute", action="store_true", help="aplica los cambios (requiere --confirm)")
    parser.add_argument("--confirm", default="", help="frase que imprime el dry-run")
    parser.add_argument("--seniority", default=SENIORITY_VALUE, help=f"valor exacto de Seniority a asignar cuando está vacío (default: {SENIORITY_VALUE})")
    parser.add_argument("--limit", type=int, default=50, help="cuántos perfiles listar antes/después (0 = todos)")
    parser.add_argument("--json", action="store_true", help="agrega el informe completo en JSON")
    return parser


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
