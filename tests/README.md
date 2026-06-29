# Tests — Red de seguridad del router

Esta carpeta fija el comportamiento **actual** del router del asistente
(`ui/tools_panel.py::_flush_pending_free_question` y los scorers que lo
alimentan) **antes** de refactorizarlo. Es una *characterization test suite*:
no juzga si la decisión es la correcta, solo garantiza que un refactor no la
cambie sin que nos enteremos.

## Por qué existe

El refactor planificado extrae la cascada de routing (~310 líneas embebidas en
un God Module de 3.7k) a una función pura `core/routing.py::route()`. Para
hacerlo "sin cambiar funcionalidad" necesitamos una prueba que detecte cualquier
desvío. Esta suite es esa prueba.

## Cómo corre sin Streamlit

La cascada **no** llama al LLM ni a la red: solo lee contenido (cacheado) y
escribe claves en `session_state` indicando qué rama ganó. Por eso podemos
ejecutarla headless:

- `fake_streamlit.py` — stub mínimo de Streamlit (session_state = dict, cache
  pasivo, catch-all para el resto). Se instala en `sys.modules` desde
  `conftest.py` antes de importar la app. `openai` también se stubea.
- `routing_harness.py` — `run_route()` corre la cascada y extrae la decisión
  normalizada; `run_scorers()` caracteriza los scorers puros uno por uno.
- `prompts_corpus.yaml` — 32 prompts reales por módulo del pack `datasphere_core`.
- `golden/routing_decisions.json` — rama elegida + mensaje de chat por prompt.
- `golden/routing_scorers.json` — salida de cada scorer por prompt.

## Uso

```bash
# Venv de test (pyyaml + pytest; streamlit/openai van stubeados)
python -m venv .venv-test
./.venv-test/Scripts/python -m pip install pyyaml pytest

# Verificar que el comportamiento NO cambió (modo refactor / CI)
./.venv-test/Scripts/python -m pytest tests/ -q

# Regenerar el golden a partir del comportamiento ACTUAL
#   (solo cuando un cambio de comportamiento es intencional y revisado)
UPDATE_GOLDEN=1 ./.venv-test/Scripts/python -m pytest tests/test_routing_characterization.py::test_regenerate_golden -s
```

## Cobertura (honesta)

Dos capas, porque la cascada por sí sola no alcanza:

1. **Decisiones (cascada).** Con el corpus actual se ejercitan 5 ramas:
   `_asset_section_suggestion`, `_asset_suggestion`, `_section_suggestion`,
   `_pending_classify`, `_tree_suggestion`. Las ramas
   `_asset_multi_suggestion`, `_combined_suggestion`, `_oos_redirect`,
   `_cds_delta_selector` y `_pending_llm_direct` **no se alcanzan** porque el
   matcher de assets/secciones gana antes — hallazgo real sobre el router.

2. **Scorers (independiente del orden).** Fija las funciones puras que el
   refactor reutiliza: `get_out_of_scope_redirect`, `_needs_cds_delta_choice`,
   `_looks_like_sap_support_case_query`, etc. quedan pineadas aunque la cascada
   no las seleccione. 10 de 11 scorers tienen señal no trivial; `matched_assets`
   (multi-asset) queda fijado en "vacío" para todo el corpus.

**Implicación para el refactor:** si `route()` reutiliza los scorers verbatim y
porta el orden de la cascada tal cual, ambos goldens deben seguir verdes sin
tocarse. Cualquier rojo es un cambio de funcionalidad — intencional o bug.
