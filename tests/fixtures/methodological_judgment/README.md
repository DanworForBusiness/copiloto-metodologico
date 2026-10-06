# Fixtures del benchmark dorado del Juicio metodológico (Gate 8D.1)

Ocho perfiles de Assessment **seudonimizados y recortados** (`*.json.gz`), uno por caso del benchmark humano congelado (Gate 8B/8C). Los carga `tests/_judgment_cases.py`.

- **Origen:** perfiles públicos de Assessment generados por el motor de `origin/main` (evidencia canónica v2) sobre los pools gobernados de los clientes. No se versiona ningún pool, SQL ni dato real.
- **Recorte:** solo las secciones que lee `tools/catalog/methodological_judgment/profile_access.py` (`scope`, `summary`, `areas`, `observations`, `review_required`, `surfaces`, `fact_index`, `canonical_evidence.*`).
- **Seudonimización:** los nombres de artefactos, vistas, campos, flujos y clientes se reemplazan por alias estables y consistentes dentro de cada perfil (la estructura y las relaciones se preservan, los nombres no). Los tests verifican que no aparezcan nombres de clientes (`test_fixtures_are_pseudonymized`).
- **Uso:** los alias de los archivos nombran el *caso* (cliente/rol), no hay lógica por cliente en el motor; `manifest.json` guarda el nivel humano esperado y el tamaño del cierre.
- **No regenerar a mano.** Si cambia la evidencia canónica, se regeneran desde el motor y se vuelve a validar que los 8 niveles se reproduzcan exactamente.
