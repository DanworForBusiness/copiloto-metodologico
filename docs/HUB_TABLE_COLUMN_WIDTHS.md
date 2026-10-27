# Anchos de columna persistidos en las tablas del Hub — contrato de compatibilidad

Mecanismo compartido: `initResizableColumns` / `restorePersistedColumnWidths` / `readStoredColumnWidths` en `webapp/static/app.js`. Opt-in por `data-resizable-columns-key="<key>"` en el contenedor; los anchos se guardan en `localStorage` bajo `dw-col-widths:<key>`.

## Qué se guarda

```json
{ "schemaVersion": 2, "containerWidth": 1552, "layout": { "version": 1, "signature": "~|fuente|nombre|…|~" }, "widths": { "seniority": "180px" } }
```

* `schemaVersion` — **formato** de serialización (cómo se escribe el registro). Cambia solo si cambia la forma del JSON.
* `layout.signature` — **estructura** de la tabla que produjo esos anchos. Se deriva sola de las columnas reales (`columnLayoutFor`): `data-column-key` de cada `<col>` (o `<th>`), `~` por cada columna sin key (casilla de selección, Acciones) y, **solo en tablas no reordenables**, el orden. En una tabla con `data-draggable-columns-key` el orden de las columnas con key es una preferencia del usuario y no invalida los anchos (van por key).
* `layout.version` — `data-layout-version` **explícito** del contenedor (default `1`). Se sube a mano **solo** cuando un cambio visual relevante no se ve en la firma (misma lista de columnas, otra distribución) o para invalidar registros legacy. No se sube por cambios cosméticos.
* `containerWidth` — ancho del contenedor al guardar. Fuera de ±8 % (`COLUMN_WIDTHS_TOLERANCE_RATIO`) los anchos no se aplican (la tabla queda responsive) **pero el registro no se elimina**: otro tamaño puede volver a ser válido. Una tabla que necesita scroll horizontal lo conserva.

## Qué pasa al cargar

| Registro guardado | Resultado |
|---|---|
| ninguno | defaults; **no** se escribe nada |
| `schemaVersion 2`, misma versión y firma | se aplica; **no** se reescribe |
| `schemaVersion 2`, otra versión o firma | se **elimina solo esa clave** (`dw-col-widths:<key>`), la tabla usa sus defaults y el próximo resize graba la estructura vigente |
| legacy `schemaVersion 1` (sin firma) | se interpreta como `layout.version 1`: si la tabla sigue en v1 y todas las columnas que nombra existen, se conserva y se **firma una vez** (mismo contenido); si la tabla declara una versión mayor, o nombra columnas inexistentes, se elimina |
| ilegible, widths fuera de `40px…4000px` o no `Npx`, layout mal formado, otra `schemaVersion` | se ignora (no se aplica ni lanza); el resto del storage no se toca |

Nunca se llama a `localStorage.clear()` ni se tocan otras claves (orden de columnas `dw-col-order:*`, filtros, selección, indicadores).

Un resize posterior no mezcla anchos de otra estructura ni de otro ancho de contenedor con el override nuevo.

## Cómo evoluciona una tabla

* **Agregar/quitar/renombrar una columna**: nada que hacer — la firma cambia y los anchos viejos se descartan solos.
* **Cambiar la distribución sin cambiar columnas**: subir `data-layout-version` en el contenedor.
* **Tablas que arman su `<colgroup>` desde un catálogo en JS** (Comercial): pasar la estructura declarativa a `readStoredColumnWidths(key, { tokens: [...keys, ""], reorderable })`.

Candidatos (`cchh-candidatos`) declara `data-layout-version="2"`: sus registros previos a la casilla de selección no tenían firma y se descartan una vez.

Tests: `tests/webapp/app_table_ergonomics.node.js` (K1–K11), `tests/webapp/test_human_capital_candidates_table_ergonomics.py`.
