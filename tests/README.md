# Tests — Red de seguridad del router

`tests/test_routing_corpus_regression.py` fija el comportamiento **actual** del
router del asistente (`webapp/services/assistant_routing.py::AssistantRoutingService.route`)
contra un corpus de preguntas reales. Es una *characterization test suite*: no
juzga si la decisión es la correcta, solo garantiza que un cambio de contenido
(`section_meta`, `out_of_scope_redirect`, headings) en un módulo no mueva
silenciosamente a qué módulo/sección deriva una pregunta real, sin que nos
enteremos.

> **Nota histórica**: este README describía antes una suite equivalente sobre
> `ui/tools_panel.py`/`core/routing.py` (Streamlit), retirada en la migración a
> FastAPI junto con su stub (`fake_streamlit.py`) y su golden (`golden/routing_decisions.json`,
> que quedó huérfano — no lo tocamos ni lo reusamos). El corpus real
> (`prompts_corpus.yaml`, 26 preguntas reales por módulo) sí sobrevivió y se
> reconectó contra el router actual en `test_routing_corpus_regression.py`
> (golden nuevo: `golden/routing_decisions_v2.json`).

## Cómo corre

No llama al LLM ni a la red: `route()` solo resuelve contenido cacheado y
scorers puros (`core/routing_scorers.py`, `core/prompts.py`). Corre headless
con el resto de la suite:

```bash
# Verificar que el routing NO cambió
.venv/Scripts/python.exe -m pytest tests/test_routing_corpus_regression.py -q

# Regenerar el golden tras un cambio de contenido intencional y revisado
UPDATE_GOLDEN=1 .venv/Scripts/python.exe -m pytest \
    tests/test_routing_corpus_regression.py::test_regenerate_golden -s
```

## Cobertura

`prompts_corpus.yaml` — 32 casos reales sobre el pack `datasphere_core`
(módulos 01-05), etiquetados por `intent` (solo documentación legible, no se
asierta: `section`, `conceptual`, `tree`, `oos_XX`, `support`, `fallback`,
`cds_delta`). Cada caso pinea la decisión completa de `route()` (mensaje +
acciones) en el golden — incluye casos donde el `intent` documentado (p. ej.
`oos_02`, redirect por keyword) en realidad NO es la rama que gana: el matcher
de secciones cross-módulo (`_find_cross_module_section`) suele resolver antes
y con más precisión. Eso también queda pineado — es información real sobre
cómo se comporta el router hoy, no un error a corregir acá.

**Implicación práctica**: antes de editar `section_meta`, `out_of_scope_redirect`
o un heading de un módulo existente, correr esta suite. Si algo se pone rojo y
el cambio es intencional (contenido revisado), regenerar el golden con
`UPDATE_GOLDEN=1`; si no lo es, es una regresión real de routing.
