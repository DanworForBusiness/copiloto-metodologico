# Catálogo de Vistas/Flujos CDS — Danwor (PILOTO, no productivo)

Convierte los objetos CDS implementados en proyectos reales en un catálogo
markdown que el copiloto interno consume para responder *"¿tenemos un modelo de
stock?"* → *"Sí: Biogenesis 🟢 (referencia), Danone 🟡, Loma Negra 🟠. ¿Por
cliente?"*, **ordenado por mirada Danwor** para no copiar malos ejemplos.

> ⚠️ **No productivo.** Vive aislado en `tools/catalog/`. NO se pushea a prod
> hasta validar. Excluir esta carpeta de cualquier `git add`.

---

## 1. Para el colaborador que recolecta (exportar + subir)

Por cada **flujo/modelo** (ej. un modelo de stock de un cliente), exportá desde
Datasphere **capa por capa, de arriba hacia abajo**:

1. **Modelo analítico** (consumo) → exportar a `.json` (CSN).
2. **Vistas de propagación/armonización** → exportar a `.json`.
   - Si es **vista gráfica** (no SQL estándar): además del `.json`, hacé
     *SQL preview* y guardá el código como `<MismoNombre>.txt`.
3. **Ingesta** (tablas remotas/locales) → `.json`.
4. **Artefactos locales asociados** si los hay (Data Flow, Replication Flow) → `.json`.

**Dónde subir:** una carpeta por cliente y dominio →
`<Cliente>/<Dominio>/` (ej. `Biogenesis/Stock/`). Poné TODOS los `.json`/`.txt`
de ese flujo ahí. **El nombre de la carpeta define el cliente** (no hay que
tipearlo). *(En la versión final esa carpeta será una biblioteca de SharePoint.)*

No hace falta editar los JSON ni entender su contenido: el script cruza todo.

## 2. Cómo corre el generador (produce el catálogo)

```bash
python tools/catalog/generate_catalog.py --in <Cliente>/<Dominio> --out <salida>
```
Cruza los archivos, reconstruye el **linaje**, infiere capa/artefacto/uso contra
la **convención §28 (módulo 04)**, evalúa el **semáforo de cumplimiento** y emite:
- `<salida>/<Cliente>/<Dominio>/flujo_<Dominio>.md` — el flujo (unidad principal).
- `<salida>/<Cliente>/<Dominio>/vistas/<Vista>.md` — una por artefacto.

Cada `.md` separa el frontmatter en dos bloques:
- **`auto:`** — lo genera el script (linaje, compliance, campos). NO editar a mano.
- **`manual:`** — lo completa el consultor/senior: `intent_negocio`,
  `compliance_veredicto_confirmado`, notas. Una re-corrida refresca `auto:` sin pisar `manual:`.

## 3. Cómo corre el consumo (lo que usa el copiloto)

```bash
python tools/catalog/catalog_service.py --catalog <salida> --q "necesito un modelo de stock"
```
Carga los `flujo_*.md`, hace retrieval (con sinónimos de dominio stock↔inventario),
agrupa por cliente y arma el grounding **ordenado por mirada Danwor**.

## 4. El semáforo de cumplimiento (eje = calidad de la extracción)

| Señal (detectada del CSN) | Efecto |
|---|---|
| `ABAP_CDS.*` (CDS view corporativa) | 🟢 ideal |
| `ABAPTABLES.NSDM_V_*` / `_V_*` (vista estándar SAP) | nota, aceptable |
| `ABAPTABLES.<tabla cruda>` (MBEW, MARD…) | 🔴 alto — viola desacople |
| BW Bridge (virtualización) | 🟡 medio — válido pero específico |
| Misma entidad por >1 conexión (ECC+S/4 en paralelo) | 🔴 alto |
| Desvíos de nombres §28 | 🔵 nota (no bloquea; LoB/sufijos opcionales) |

**Veredicto:** hay 🔴 → `parcial` · BW Bridge → `buena` · extracción CDS ideal → `plena`.

Validado en 3 clientes: **Biogenesis** 🟢 plena (CDS) · **Danone** 🟡 buena (BW Bridge)
· **Loma Negra** 🟠 parcial (tablas ABAP crudas + ECC/S4 paralelo).

## 5. Convención de nombres §28 (referencia rápida)

`<Capa><Artefacto><Uso>_<Origen>_<libre>` — Capa `1-4` · Artefacto `T/V` ·
Uso `F/R/D/A/T/H`. "Otros Artefactos" (sin prefijo de capa): `DF/RF/FT/IL/TC/AM/EM/DAC`.
LoB (`MD/FNC/IVT/PUR/SND`) y sufijos (`_PBI/_SPM`) son **sugeridos, opcionales**.

## 6. Estado y pendientes (para retomar)

- ✅ **Fase A — generador** (`generate_catalog.py`): parser CSN, linaje, §28, semáforo. Validado.
- ✅ **Fase B — consumo (núcleo)** (`catalog_service.py`): carga, retrieval, grounding, preview.
- ⏳ **Fase B — integración pendiente:**
  1. **Adapter SharePoint en vivo** en `load_catalog()` (Graph, token delegado,
     leer `.md` de una biblioteca — mismo patrón que `core/sharepoint_cases.py`).
  2. **Cablear en `assistant_service`** el grounding real (con disclaimer IA).
- ⏳ Cuando validemos: decidir publicación (recién ahí se considera productivo).

Muestras de prueba y salidas: en el scratchpad de la sesión (`catalog_in/`, `catalog_out/`),
reconstruidas con `make_fixtures.py` / `make_fixtures_bio.py` desde los exports reales.
