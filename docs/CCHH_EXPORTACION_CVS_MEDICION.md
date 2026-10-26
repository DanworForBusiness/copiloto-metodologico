# CCHH → Candidatos — Descarga de CVs: procedimiento de medición controlada

Estado: **procedimiento y sonda listos y validados con Graph simulado; NO ejecutados contra datos reales.** La exportación definitiva (Fase 2, ver `CCHH_EXPORTACION_CVS_FASE2_REQUISITOS.md`) **no está implementada**: este documento fija cómo
medir, con evidencia y sin riesgo, el presupuesto de tiempo y los límites antes de construirla.

Herramienta: `tools/cchh_export_probe.py` (un solo archivo, solo stdlib; se puede copiar tal cual a la consola de DigitalOcean, sin desplegar). Pruebas: `tests/test_cchh_export_probe.py` (26, con un servidor Graph simulado en localhost y archivos sintéticos).

## 1. Qué se mide y qué no

| Medida | Cómo | Etapa |
|---|---|---|
| Latencia de obtención desde Graph | `ttfb_ms` (hasta los encabezados, ya con el redirect a la URL de descarga) y `transfer_ms` por documento | 1 (real) |
| Tamaño de cada documento | `declared_size` (registro), `content_length` (Graph) y `bytes` (leídos) | 1 |
| Rendimiento | `mb_per_s` por documento y `projection` (MB que entran en 60 s en serie al ritmo mínimo observado) | 1 |
| Construcción del ZIP | `zip_add_ms` por archivo y `zip.build_ms`; comparación almacenado vs DEFLATE con datos sintéticos | 0 y 1 |
| Tiempo total del flujo | `total_elapsed_ms` (descarga + ZIP + limpieza), equivalente al tiempo del manejador sin el proxy | 1 |
| Memoria y disco temporal | cgroup (`memory.max/current/peak`, `cpu.max`), pico de RSS del proceso, espacio libre y velocidad de escritura en el directorio temporal | 0 y 1 |
| **Tiempo HTTP de extremo a extremo y límite del proxy de App Platform** | **No se puede medir con la sonda**: exige un endpoint desplegado y medir desde el navegador | 2 (condicional, §6) |

Lo único afirmado hoy sobre el límite del proxy proviene de respuestas del personal de DigitalOcean en foros (~100 s fijos, con una respuesta que menciona 30 s); **no es documentación oficial**. Hasta medirlo, se toma como cifra no confiable y el diseño debe tolerar un corte
a cualquier momento sin dejar estado inconsistente.

## 2. Qué se registra (y qué nunca)

La sonda imprime **un JSON por stdout** y no persiste nada. Contiene únicamente tiempos, tamaños y códigos de resultado (`ok`, `http_403`, `http_404`, `http_429` con `retry_after_s`, `timeout`, `deadline`, `oversize_*`, `short_read`, `url_error`).
**Nunca**: nombres, emails, URLs, identificadores de documento (salen como «documento 1..3» en el orden en que se pasaron), el token ni contenido; las entradas del ZIP se llaman `doc-N.bin` y los errores son códigos, no el texto de la excepción.
Los bytes de los CVs solo pasan por un directorio temporal que se borra siempre (probado), no se escriben en `human-capital/`, Spaces ni SharePoint, y no se modifica ningún original ni permiso.

## 3. Controles de seguridad de la sonda (verificados con mutaciones)

- **Autorización explícita:** el modo real exige `--confirm AUTORIZADO-POR-EL-OWNER`; sin esa frase no abre red ni archivos.
- **Alcance mínimo:** 1 a 3 documentos; tope por documento 25 MB y total 75 MB.
- **Token solo por entorno** (`DW_GRAPH_TOKEN`), nunca por argumento (no queda en la lista de procesos ni en el historial); la sonda no lo imprime ni lo registra.
- **El token solo puede ir al Graph oficial o a localhost** (para pruebas): cualquier otro `--graph-base` se rechaza.
- **Lectura acotada** (a diferencia de `graph_get_bytes`, que lee todo en memoria sin tope): el tamaño se controla **antes** (tamaño declarado del registro; si excede, ni se pide), **al recibir los encabezados** (`Content-Length`) y **durante** la transferencia (se corta al superar el tope), con
  **plazo total por documento** además del timeout de socket (`read1` evita que un `read(n)` bloquee más allá del plazo). Va a un archivo temporal por trozos de 64 KB: memoria O(trozo).
- **Sin secretos en salida, errores ni trazas:** el analizador de argumentos no repite lo recibido (un `--token` mal escrito no llega a stderr), un error inesperado imprime solo el nombre de la clase (sin traza, rutas, URLs ni ids) y la salida nunca incluye identificadores.
- **Limpieza ante errores, Ctrl+C, SIGTERM y respuestas parciales:** el temporal (CVs parciales y ZIP) vive en un `TemporaryDirectory`; SIGTERM se convierte en una excepción para que se desenrolle (si no, Python muere sin limpiar) y el manejador previo se restaura. Probado con cortes, tope excedido, error al armar el ZIP, interrupción y SIGTERM en medio de la descarga.
- **Plazo total de la corrida** (`--total-deadline`, 120 s por defecto) aplicado también DURANTE cada lectura (acota el plazo de cada documento), además del plazo por documento y el timeout de socket.
- Solo lectura contra Graph (`GET .../drives/{drive}/items/{item}/content`, el mismo camino que ya usan el piloto histórico y la conciliación de documentos), con el token delegado del propio usuario (`User.Read` + `Sites.Read.All`): se respetan sus permisos reales.

## 4. Procedimiento

### Etapa 0 — sin autorización (no toca la red ni CVs)
Entorno y costo de ZIP con datos sintéticos. En la consola de DO, desde el directorio de la app:

```bash
python tools/cchh_export_probe.py --synthetic-bench --bench-files 4 --bench-mb 25
```

(o copiar el archivo y ejecutarlo con `python cchh_export_probe.py ...`). Devuelve memoria/cgroup/disco temporal de la instancia real y el tiempo de armar 100 MB de ZIP almacenado vs DEFLATE.
Referencia local (PC de desarrollo, Windows, 12 CPU, disco local): almacenado ≈ 26 ms por 25 MB; DEFLATE ≈ 580 ms por 25 MB **sin ganancia de tamaño** (datos incompresibles, como PDF/DOCX). Conclusión provisoria: el ZIP definitivo debe ser **almacenado** (`ZIP_STORED`); el cuello de botella será Graph, no la CPU. Falta confirmarlo en la instancia de App Platform.

### Etapa 1 — con autorización explícita del owner (1–3 CVs reales)
Requisitos previos (todos a cargo del owner):
1. Autorización expresa en el chat para leer 1–3 documentos reales (cuántos y que se descartan al terminar).
2. Elegir los documentos **sin leer su contenido**: alcanza con listar los `document_id` (identificadores internos, no datos personales; el nombre del archivo `.json` es el id): `ls human-capital/profile-documents | head -3`. Conviene incluir uno liviano y uno pesado si se conoce el tamaño.
3. Obtener el token delegado **uno mismo** (el asistente no lo ve ni lo maneja): en la consola del navegador, sesión del Hub, `await dwGraphToken()`; dura ~1 h. En la consola de DO, ingresarlo sin eco ni historial: `read -rs DW_GRAPH_TOKEN; export DW_GRAPH_TOKEN`.

Ejecución (desde el directorio de la app, o indicando `--documents-dir` con la ruta de `human-capital/profile-documents` de la instancia, que contiene los registros restaurados):

```bash
python tools/cchh_export_probe.py --real --document-id <id1> --document-id <id2> --confirm AUTORIZADO-POR-EL-OWNER
```

Terminada la corrida: `unset DW_GRAPH_TOKEN`, y pegar en el chat **solo** el JSON de salida (ya no contiene datos personales). Código de salida: 0 todo ok · 2 algún documento falló (el JSON dice cuál por número y por qué) · 3 argumentos/autorización inválidos.
Recomendado repetir 2–3 veces en momentos distintos del día (la latencia de Graph varía) y, si hay, repetir con un documento grande.

#### Procedimiento exacto en DigitalOcean (sin deploy), un paso por vez
La sonda viaja a la consola como texto (líneas `printf … >> archivo`, cada una un comando independiente, sin heredoc ni `set -e`) y se verifica por sha256 antes de usarse; vive en un directorio NUEVO de `/tmp` (`DWD`) hasta el paso 6. Nada se escribe en el directorio de la aplicación.

1. **Transferir y verificar** (no ejecuta la sonda): pegar el bloque de líneas; debe imprimir `SONDA OK sha256 2e0f6242…a246`. Si imprime `SONDA CORRUPTA`, no se usa y se repite el pegado.
2. **Bench sintético** (sin token): `PY=$(command -v python3 || command -v python); nice -n 19 "$PY" -I "$DWD/probe.py" --synthetic-bench --bench-files 4 --bench-mb 25; echo "RC=$?"`.
3. **Elegir 3 `document_id` sin ver datos personales:** un listado de solo lectura que imprime únicamente `document_id` y `size_bytes` (≤ 25 MB, con `drive_id`/`item_id`) y propone uno pequeño, uno mediano y uno grande de `/workspace/human-capital/profile-documents` (el `BASE_DIR` de la app).
4. **Token delegado propio, sin eco, sin argumentos:** en una pestaña del Hub con la sesión iniciada, en la consola del navegador `copy(await dwGraphToken())` (no lo imprime; queda en el portapapeles); en la consola de DO: `stty -echo && { printf 'Token: '; IFS= read -r T; stty echo; echo; echo "token cargado: ${#T} caracteres"; }`. `T` es una variable de shell **no exportada**: no queda en el historial (el comando guarda `$T`, no el valor) ni en argumentos.
5. **Lectura controlada (máx. 3 CVs):** `DW_GRAPH_TOKEN="$T" nice -n 19 "$PY" -I "$DWD/probe.py" --real --documents-dir /workspace/human-capital/profile-documents --document-id <id1> --document-id <id2> --document-id <id3> --confirm AUTORIZADO-POR-EL-OWNER; echo "RC=$?"; unset T`. El prefijo hace que el token exista solo para ese comando.
6. **Limpieza:** `unset T; ls -d /tmp/dwprobe-* 2>/dev/null || echo "sin residuos"; rm -rf "$DWD"; unset DWD` y, en el navegador, `copy('')` para vaciar el portapapeles. El token vence solo (≈ 1 h).

La salida de los pasos 2 y 5 es el JSON descrito en §2 (tiempos, tamaños, códigos). Un solo pase por documento: repetir la lectura o ampliar la muestra requiere una nueva autorización.

### Etapa 2 — condicional (solo con nuevo GO y despliegue): proxy y tiempo HTTP
Solo si la Etapa 1 deja poco margen. Un endpoint sintético, solo para el owner y apagado por defecto, que responda tras una espera configurable (sin datos reales) para fijar el límite real del proxy desde el navegador. No se construye ahora.

## 5. Cómo se leerán las mediciones → presupuesto y límites

Datos que se usarán: `mb_per_s_min`, `ttfb_ms_max`, `zip.build_ms`, memoria/disco de la instancia y, si se hizo, el límite del proxy.

Presupuesto de tiempo propuesto para una sola request: **`presupuesto = min(70 s, 0,7 × límite_proxy_medido)`**; mientras el límite no esté medido, **60 s** y no se inicia ningún archivo nuevo pasados los 45 s (así el último archivo en curso, con su plazo propio, termina antes del corte).
Estimación del trabajo: `tiempo ≈ Σ(ttfb) + bytes_totales / rendimiento_mínimo + tiempo_ZIP`. Hipótesis a confirmar (no medidas): a 5 MB/s y 0,3 s de latencia por archivo, 100 MB tardan ~38 s; a 2 MB/s ya serían ~68 s y no entrarían.
Límites iniciales pedidos (25 candidatos, 60 archivos, 100 MB totales, 25 MB por archivo) **solo se adoptan si entran en el presupuesto con margen** (regla: tiempo estimado ≤ 50 % del presupuesto al ritmo mínimo observado); si no, se reducen archivos/bytes totales hasta cumplirla. Memoria: con descarga por trozos a archivo temporal el consumo es O(trozo) (no 25 MB por archivo); disco temporal necesario ≈ ZIP acumulado + un archivo (se borra cada parte al sumarla).

## 6. Si una sola request no resulta confiable: alternativa por tandas

| | Una request (preferida si las mediciones la respaldan) | Por tandas |
|---|---|---|
| Idea | descargar y armar el ZIP dentro del presupuesto; fuera de presupuesto → «tiempo agotado» → parcial con segunda confirmación | varias requests cortas (cada una ≤ presupuesto) descargan a un temporal; una última arma el ZIP y lo entrega |
| Costo | bajo: sin estado entre requests | alto: estado temporal entre requests, reenvío del token en cada tanda (vence ~1 h), limpieza por expiración |
| Riesgos | selecciones grandes terminarán seguido en parcial | el temporal vive en una sola instancia (réplica única como precondición, el disco es efímero y un reinicio lo pierde), más superficie de auditoría (una intención, N tandas, un cierre) y de concurrencia |
| Auditoría | intención → cierre | intención → una entrada por tanda → cierre |

Se presentará la recomendación definitiva con las mediciones en mano. En ambos casos: lectura acotada antes y durante la transferencia, auditoría durable fail-closed escrita **antes** de leer cualquier documento (`remote_store.write_through` confirmado), ZIP temporal fuera de las rutas persistidas con limpieza garantizada, y ningún ZIP parcial se entrega sin segunda confirmación explícita.
