# Fixtures de explicabilidad de «No determinable» (nd-explanation/v1)

Ocho perfiles de Assessment **seudonimizados y recortados** (`*.json.gz`) de Judgments reales NO DETERMINABLES (LVL-05 y LVL-06) del corpus gobernado, generados con `canonical-evidence/v4` y `signal-rules/v4`. Mismo criterio que `tests/fixtures/methodological_judgment`: solo las secciones que lee `profile_access.py`; nombres de **artefactos** → alias estables (`ART_0001`…), cliente → `CLIENT`, conexiones → `CONN`, entidades remotas → `REMOTE_ENTITY`, **etiquetas** vaciadas; se conservan los nombres técnicos de campo. Un test verifica que no aparezcan nombres de clientes.

`manifest.json` guarda, por caso, el resultado ESPERADO de `explain_determination` (impedimentos, categoría, tipo de resolución, certeza, corroboración). Los alias de los archivos nombran el *caso*, no hay lógica por cliente.
El motor reproduce idéntico el veredicto y la explicación sobre el alias y sobre el perfil original (verificado al generarlos). **No regenerar a mano**: si cambia la evidencia canónica, se regeneran desde el motor y se revalida.
