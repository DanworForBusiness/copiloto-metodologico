// Oráculo diferencial de 06 Indicadores: ejecuta las funciones ORIGINALES de `lib/metricas.ts` (app de referencia) sobre las entradas de `dataset`.
// Uso (lo invoca `regenerate.py`, no los tests): TZ=UTC node --experimental-strip-types --no-warnings oracle_run.mjs <dir con metricas.ts/types.ts/publicaciones.ts> < entrada.json > salida.json
// Se excluye `busquedasEstancadas` a propósito: depende del reloj real (`new Date()`); en el Hub se prueba aparte con reloj congelado.
import { pathToFileURL } from "node:url";
import { readFileSync } from "node:fs";
import path from "node:path";

const libDir = path.resolve(process.argv[2]);
const m = await import(pathToFileURL(path.join(libDir, "metricas.ts")).href);
const { procesos, candidatos, entrevistas, publicaciones } = JSON.parse(readFileSync(0, "utf8"));

const periodosEntrevistas = m.periodosConEntrevistas(entrevistas);
const periodosCanal = m.periodosParaEfectividadCanal(publicaciones, candidatos);
const perfiles = { todos: m.entrevistasPorPerfil(entrevistas) };
for (const p of periodosEntrevistas) perfiles[p] = m.entrevistasPorPerfil(entrevistas, p);
const canales = { todos: m.efectividadPorCanalAviso(publicaciones, candidatos) };
for (const p of periodosCanal) canales[p] = m.efectividadPorCanalAviso(publicaciones, candidatos, p);

console.log(JSON.stringify({
  kpis: {
    timeToFill: m.timeToFillPromedio(procesos),
    timeToHire: m.timeToHirePromedio(candidatos),
    conversion: m.tasaConversion(candidatos),
    contratados: m.totalContratados(candidatos),
    descartados: m.totalDescartados(candidatos),
    entrevistasRealizadas: m.totalEntrevistasRealizadas(entrevistas),
  },
  candidatosPorProceso: m.candidatosPorProceso(procesos, candidatos),
  busquedasPorMes: m.busquedasPorMes(procesos),
  ingresosPorMes: m.ingresosPorMes(candidatos),
  entrevistasPorAnio: m.entrevistasPorAnio(entrevistas),
  entrevistasPorMes: m.entrevistasPorMes(entrevistas),
  periodosEntrevistas,
  perfiles,
  efectividadPorFuente: m.efectividadPorFuente(candidatos),
  timeToFillPorProceso: m.timeToFillPorProceso(procesos),
  periodosCanal,
  canales,
}));
