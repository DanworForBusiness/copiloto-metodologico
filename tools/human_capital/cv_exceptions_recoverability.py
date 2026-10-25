"""cv_exceptions_recoverability — DIAGNÓSTICO DE SOLO LECTURA de la recuperabilidad de las excepciones abiertas de una importación histórica de CVs de CCHH.

    python -m tools.human_capital.cv_exceptions_recoverability --run ch-20261024T014615Z-6827                       # informe agregado (sin datos personales ni identificadores) por stdout
    python -m tools.human_capital.cv_exceptions_recoverability --run <lote> --json                                  # además, el agregado como JSON
    python -m tools.human_capital.cv_exceptions_recoverability --run <lote> --corroboradas                          # además, el desglose agregado de las altas asistidas con identidad corroborada (niveles de precarga A/B/C/D)
    python -m tools.human_capital.cv_exceptions_recoverability --run <lote> --detail-out /ruta/local/detalle.json   # detalle PROTEGIDO por content_key (ids opacos, códigos y señales; nunca nombres, emails ni teléfonos)

ESTRICTAMENTE DE SOLO LECTURA sobre los datos del negocio: no crea, edita, vincula, descarta ni reintenta nada; no descarga los CV; no toca el manifiesto, el libro de ejecución, el staging, el Banco, los catálogos ni SharePoint. La única escritura posible es
el archivo de `--detail-out`, en una ruta local EXPLÍCITA que debe ser nueva (se crea en modo exclusivo) y NO puede estar dentro de ningún directorio persistido (ni del repositorio de datos ni de lo que Spaces sincroniza).

PROCESO INDEPENDIENTE: este CLI no comparte el estado de la app web. Antes de leer restaura desde Spaces —con el mismo `remote_store.restore()`, acotado, sin sobrescribir lo local y verificado contra el inventario remoto (`spaces_sync`)— el
manifiesto del lote y los perfiles. Si el listado remoto falla, la restauración queda incompleta o un archivo local difiere del remoto, se BLOQUEA sin leer ni informar nada. La restauración solo escribe copias en el disco local de este proceso.

Códigos de salida: 0 ok · 2 bloqueado (modo, restauración, lote inexistente, sin plan o sin staging) · 3 el informe no concilia exactamente con las abiertas del manifiesto · 4 uso inválido (ruta de salida)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tools.human_capital.spaces_sync import sync_from_spaces

EXIT_OK, EXIT_BLOCKED, EXIT_NO_RECONCILE, EXIT_USAGE = 0, 2, 3, 4
IMPORT_HISTORY_DIR = "human-capital/import-history"
PROFILES_DIR = "human-capital/profiles"


def default_deps() -> dict:
    import config
    from core import remote_store
    from webapp.services import cv_history_manifest as manifest, cv_history_recoverability as recoverability
    return {"config": config, "remote_store": remote_store, "manifest": manifest, "recoverability": recoverability}


def _inside_persisted_dir(path: Path, cfg) -> bool:
    base = Path(cfg.BASE_DIR).resolve()
    for name in list(getattr(cfg, "PERSIST_PATHS", [])) + ["human-capital"]:
        try:
            path.resolve().relative_to((base / name).resolve())
            return True
        except ValueError:
            continue
    return False


def _check_detail_path(raw: str, cfg) -> tuple[Path | None, str | None]:
    path = Path(raw)
    if path.exists():
        return None, "La ruta de salida ya existe: el CLI nunca sobrescribe (elegí un archivo nuevo)."
    if _inside_persisted_dir(path, cfg):
        return None, "La ruta de salida cae dentro de un directorio persistido: el detalle solo puede escribirse en una ruta local fuera de los datos del negocio."
    if not path.parent.exists():
        return None, "La carpeta de la ruta de salida no existe."
    return path, None


def render(agg: dict) -> list[str]:
    c = agg["conciliacion"]
    lines = [f"Lote {agg['lote']} — excepciones analizadas: {agg['excepciones_analizadas']}", "",
             "CONCILIACIÓN CON EL MANIFIESTO Y EL LIBRO DE EJECUCIÓN",
             f"  abiertas según el manifiesto: {c['abiertas_segun_manifiesto']} · clasificadas: {c['clasificadas']} · diferencia: {c['diferencia']}",
             f"  por estado: {c['por_estado']}",
             f"  pendientes originales del plan: {c['pendientes_originales']} · adicionales (bloqueadas o con error durante la ejecución): {c['adicionales_bloqueadas_o_con_error']} {c['adicionales_por_motivo']}",
             f"  plan: {c['plan']['pendientes_en_el_plan']} pendientes en el plan · resueltas o descartadas desde entonces: {c['plan']['resueltas_o_descartadas_desde_el_plan']}",
             f"  motivos originales (abiertas): {c['por_motivo_abiertas_originales']}", f"  motivos del plan: {c['plan']['por_motivo_en_el_plan']}",
             f"  estado en el libro de las abiertas: {c['estado_ledger_de_las_abiertas']}", "",
             "DISTRIBUCIÓN POR CATEGORÍA"]
    lines += [f"  {k}: {v}" for k, v in agg["categorias"].items()]
    lines += ["", "MATRIZ MOTIVO ORIGINAL × CATEGORÍA (" + " | ".join(agg["categorias"]) + ")"]
    lines += [f"  {reason}: " + " | ".join(str(counts[k]) for k in agg["categorias"]) for reason, counts in agg["matriz_motivo_x_categoria"].items()]
    v = agg["posible_vinculacion"]
    lines += ["", "MEJOR COINCIDENCIA CON EL BANCO POR DOCUMENTO (NINGUNA implica autorización automática)", f"  {agg['mejor_coincidencia_con_el_banco_por_documento']}", "",
              "POSIBLE VINCULACIÓN", f"  documentos: {v['documentos']} sobre {v['perfiles_distintos']} perfiles distintos · por fuerza: {v['por_fuerza']}",
              f"  con respaldo de nombre en el documento: {v['con_respaldo_de_nombre']} · sin respaldo: {v['sin_respaldo_de_nombre']} · a perfiles creados por este lote: {v['a_perfiles_creados_por_este_lote']}",
              f"  evidencia: {v['por_senal']}", "", "ALTA ASISTIDA (propuesta, NO verificada)", f"  documentos: {agg['alta_asistida']['documentos']} · por ancla: {agg['alta_asistida']['por_ancla']}",
              f"  dato a confirmar: {agg['alta_asistida']['dato_a_confirmar']}", "", "CONFLICTOS DE IDENTIDAD", f"  documentos: {agg['conflictos']['documentos']} · {agg['conflictos']['por_subtipo']}", "",
              "INFORMACIÓN INSUFICIENTE", f"  documentos: {agg['informacion_insuficiente']['documentos']} · {agg['informacion_insuficiente']['por_subtipo']}", "",
              "PROBLEMAS DE EXTRACCIÓN (otro tratamiento técnico)", f"  documentos: {agg['problema_de_extraccion']['documentos']} · {agg['problema_de_extraccion']['por_codigo']}", ""]
    g = agg["agrupaciones_tentativas"]
    lines += ["AGRUPACIONES DE DOCUMENTOS — " + g["aviso"],
              f"  grupos fuertes: {g['grupos_fuertes']} ({g['documentos_en_grupos_fuertes']} CV) · grupos extendidos (STRONG o PROBABLE): {g['grupos_extendidos']} ({g['documentos_en_grupos_extendidos']} CV)",
              f"  enlaces fuertes: {g['enlaces_fuertes']} · fuertes o probables: {g['enlaces_fuertes_o_probables']} · solo por celular: {g['enlaces_solo_celular']} · grupos con identidades mezcladas: {g['grupos_con_identidades_mezcladas']}",
              f"  decisiones humanas estimadas (de {g['documentos_analizados']} CV con evidencia de identidad; los problemas de extracción no se agrupan): sin agrupar {g['decisiones_humanas_estimadas']['sin_agrupar']} · con grupos fuertes {g['decisiones_humanas_estimadas']['con_grupos_fuertes']} · con grupos extendidos {g['decisiones_humanas_estimadas']['con_grupos_extendidos']}",
              f"  tamaño de los grupos extendidos: {g['tamano_de_grupos_extendidos']}", "", "OPORTUNIDADES PRIORIZADAS (propuestas, no decisiones verificadas)"]
    lines += [f"  {o['prioridad']}. {o['oportunidad']}: {o['documentos']} CV · ~{o['decisiones_estimadas']} decisiones · riesgo {o['riesgo']} — {o['nota']}" for o in agg["oportunidades_priorizadas"]]
    lines += ["", f"RIESGOS DETECTADOS (documentos): {agg['riesgos']}", "", "LIMITACIONES"] + [f"  - {x}" for x in agg["limitaciones"]]
    return lines


def render_corroborated(b: dict) -> list[str]:
    c = b["conciliacion"]
    lines = ["", "ALTAS ASISTIDAS CON IDENTIDAD CORROBORADA (opción --corroboradas · solo conteos y códigos)",
             f"  altas asistidas: {b['altas_asistidas']} · corroboradas: {b['corroboradas']} · según el informe: {c['corroboradas_segun_el_informe']} · diferencia: {c['diferencia']} · suma de niveles: {c['suma_de_niveles']} · ejes que no suman: {c['ejes_que_no_suman'] or 'ninguno'}",
             "  NIVEL DE PRECARGA DEL NOMBRE"]
    lines += [f"    {lv}: {b['niveles'][lv]} — {b['significado_de_los_niveles'][lv]}" for lv in b["niveles"]]
    lines += ["  NIVEL POR MOTIVO ORIGINAL (" + " | ".join(b["niveles"]) + ")"] + [f"    {reason}: " + " | ".join(str(counts[lv]) for lv in b["niveles"]) for reason, counts in b["niveles_por_motivo_original"].items()]
    for name, counts in b["ejes"].items():
        lines.append(f"  {name}: {counts}")
    lines += ["  CRUCE motivo original | razones adicionales | evidencia del ancla | emails/explican | nivel"]
    lines += [f"    {x['documentos']:3d}  {x['motivo_original']} | {x['razones_adicionales']} | {x['evidencia_del_ancla']} | {x['emails']}/{x['emails_que_explican']} | {x['nivel']}" for x in b["cruce"]]
    lines.append(f"  {b['aviso']}")
    return lines


def run(argv: list[str] | None = None, deps: dict | None = None, out=None) -> int:
    out = out or sys.stdout
    args = _parser().parse_args(argv)
    deps = deps or default_deps()

    def say(text: str = "") -> None:
        print(text, file=out)

    manifest, recoverability, cfg = deps["manifest"], deps["recoverability"], deps["config"]
    if not manifest.RUN_ID_RE.match(args.run or ""):
        say("BLOQUEADO: RUN_ID_INVALIDO")
        return EXIT_BLOCKED
    detail_path = None
    if args.detail_out:
        detail_path, problem = _check_detail_path(args.detail_out, cfg)
        if problem:
            say(f"USO INVÁLIDO: {problem}")
            return EXIT_USAGE
    for label, directory in (("Lote", f"{IMPORT_HISTORY_DIR}/{args.run}"), ("Perfiles", PROFILES_DIR)):
        info = sync_from_spaces(deps, directory)
        if info["aplica"]:
            say(f"{label} en Spaces: {info.get('remotos', '?')} · locales antes: {info['locales_antes']} · descargados: {info.get('descargados', 0)} · ya idénticos: {info.get('identicos', 0)} · "
                f"locales distintos del remoto: {info.get('locales_distintos', 0)} · fallidos: {info.get('fallidos', 0)} · locales después: {info.get('locales_despues', info['locales_antes'])}")
        if info["bloqueos"]:
            say(f"BLOQUEADO: {', '.join(info['bloqueos'])}")
            say("  No se pudo dejar el disco local fiel a Spaces: no se leyó nada ni se informó nada. Reintentá; si persiste, no usar el informe.")
            return EXIT_BLOCKED
    try:
        inputs = recoverability.load_inputs(args.run)
        report = recoverability.build_report(inputs)
    except manifest.ManifestNotFoundError:
        say("BLOQUEADO: LOTE_INEXISTENTE (tras restaurar desde Spaces el lote no existe en este entorno)")
        return EXIT_BLOCKED
    except recoverability.RecoverabilityError as exc:
        say(f"BLOQUEADO: {exc.code}")
        return EXIT_BLOCKED
    agg = report["aggregate"]
    for line in render(agg):
        say(line)
    corroborated = recoverability.corroborated_breakdown(inputs, report) if args.corroboradas else None
    if corroborated:
        for line in render_corroborated(corroborated):
            say(line)
    if args.json:
        say(json.dumps({**agg, "altas_corroboradas": corroborated} if corroborated else agg, ensure_ascii=False, indent=2))
    if detail_path is not None:
        with open(detail_path, "x", encoding="utf-8") as handle:                         # ÚNICA escritura del CLI: archivo local nuevo y explícito (modo exclusivo)
            json.dump({"lote": agg["lote"], "aviso": "DETALLE PROTEGIDO: content_key y profile_id opacos; sin nombres, emails ni teléfonos. Las propuestas no son identidades verificadas.", "detalle": report["detail"]},
                      handle, ensure_ascii=False, indent=1)
        say(f"Detalle protegido escrito en {detail_path} ({len(report['detail'])} documentos).")
    if agg["conciliacion"]["diferencia"] != 0 or sum(agg["categorias"].values()) != agg["conciliacion"]["abiertas_segun_manifiesto"]:
        say("ATENCIÓN: el informe NO concilia exactamente con las excepciones abiertas del manifiesto.")
        return EXIT_NO_RECONCILE
    if corroborated and (corroborated["conciliacion"]["diferencia"] != 0 or corroborated["conciliacion"]["ejes_que_no_suman"] or corroborated["conciliacion"]["suma_de_niveles"] != corroborated["corroboradas"]):
        say("ATENCIÓN: el desglose de altas corroboradas NO concilia con el informe.")
        return EXIT_NO_RECONCILE
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cv_exceptions_recoverability", description="Diagnóstico de SOLO LECTURA de la recuperabilidad de las excepciones abiertas de una importación histórica de CVs.")
    parser.add_argument("--run", required=True, help="id del lote de importación (ch-AAAAMMDDTHHMMSSZ-xxxx)")
    parser.add_argument("--json", action="store_true", help="agrega el informe agregado en JSON")
    parser.add_argument("--corroboradas", action="store_true", help="agrega el desglose AGREGADO de las altas asistidas con identidad corroborada (motivo, ancla, dato a confirmar, nivel de precarga A/B/C/D)")
    parser.add_argument("--detail-out", default="", help="ruta local NUEVA donde escribir el detalle protegido por content_key (fuera de los directorios persistidos)")
    return parser


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
