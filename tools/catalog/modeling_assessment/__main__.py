"""CLI del assessment shadow: `python -m tools.catalog.modeling_assessment --inbound <dir> --client <C> --am <AM> --out <dir>`.

Argumentos canonicos: `--inbound`, `--client`, `--am`, `--out`, mas `--domain` (si el cliente tiene varios dominios) y `--list-models`.
Alias aceptados: `--analytic-model` (= `--am`) y `--output` (= `--out`).
NO hay destino de salida por defecto ni forma de saltar la guarda de rutas persistidas: el destino debe ser explicito, local y fuera de los
directorios que `mirror_up()` sube al bucket (`paths.resolve_output_dir`).

Codigos de salida: 0 ok · 2 uso/validacion (argumentos, destino inseguro, cliente o AM inexistente) · 3 pool no cargable (se escribe un documento INDETERMINATE).
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from . import paths, shadow

EXIT_OK, EXIT_USAGE, EXIT_POOL = 0, 2, 3


class _AyudaEnEspanol(argparse.HelpFormatter):
    def add_usage(self, usage, actions, groups, prefix=None):
        super().add_usage(usage, actions, groups, prefix if prefix is not None else "uso: ")


class _ParserEnEspanol(argparse.ArgumentParser):
    """argparse imprime sus errores en ingles: se traducen los mensajes estandar para que toda la salida al usuario sea espanol."""

    def error(self, message):
        message = message.replace("the following arguments are required: ", "faltan argumentos obligatorios: ")
        message = message.replace("unrecognized arguments: ", "argumentos no reconocidos: ")
        message = re.sub(r"argument (.+?): expected one argument", r"argumento \1: se esperaba un valor", message)
        self.print_usage(sys.stderr)
        self.exit(EXIT_USAGE, f"{self.prog}: error: {message}\n")


def build_parser() -> argparse.ArgumentParser:
    p = _ParserEnEspanol(prog="python -m tools.catalog.modeling_assessment", add_help=False, formatter_class=_AyudaEnEspanol,
                         description="Autoevaluación de Modelado v2 (validación en paralelo): perfil de evidencia, cobertura y confianza")
    p._optionals.title = "opciones"
    p.add_argument("-h", "--help", action="help", help="muestra esta ayuda y termina")
    p.add_argument("--inbound", required=True, help="raíz del conjunto de artefactos exportado (explícita; sin valor por defecto)")
    p.add_argument("--client", required=True, help="cliente (directorio del conjunto de artefactos)")
    p.add_argument("--domain", default=None, help="dominio, solo si el cliente tiene más de uno")
    p.add_argument("--am", "--analytic-model", dest="am", default=None, help="modelo analítico terminal a evaluar")
    p.add_argument("--out", "--output", dest="out", default=None, help="directorio de salida EXPLÍCITO, local y fuera de los directorios persistidos")
    p.add_argument("--list-models", action="store_true", help="lista los modelos analíticos terminales del cliente y termina (no escribe nada)")
    return p


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return EXIT_USAGE


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    inbound = Path(args.inbound)
    if args.list_models:
        try:
            if not inbound.is_dir():
                return _fail(f"--inbound no es un directorio: {inbound}")
            for name in shadow.list_terminal_models(inbound, args.client, args.domain):
                print(name)
            return EXIT_OK
        except (shadow.ClientNotFoundError, shadow.AmbiguousClientDomainError) as exc:
            return _fail(str(exc))
    try:
        result = shadow.run(inbound=inbound, client=args.client, am=args.am, out=args.out, domain=args.domain)
    except paths.OutputDirRequiredError as exc:
        return _fail(f"--out es obligatorio: {exc}")
    except paths.ShadowOutputPersistenceError as exc:
        return _fail(str(exc))
    except (shadow.ClientNotFoundError, shadow.AmbiguousClientDomainError, shadow.UnknownTerminalAmError, shadow.TerminalAmSelectionRequired,
            FileNotFoundError) as exc:
        return _fail(str(exc))
    print(f"evaluación: {result.document['assessment_state']}")
    print(f"json:       {result.json_path}")
    print(f"markdown:   {result.markdown_path}")
    print(f"manifiesto: {result.manifest_path}")
    if result.exit_code:
        print(f"error: {result.document['summary'].get('error')}", file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
