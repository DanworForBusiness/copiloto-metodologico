# Beta FastAPI: vertical de contenido + árbol

## Evidencia

- Catálogo real mediante `core.content_registry`.
- Frontmatter y Markdown editorial renderizados en servidor.
- Árbol real de `trees/01_espacios_topologia.yaml` operado con HTMX.
- Alpine para estado local de navegación y panel.
- Tailwind en composición, con tokens visuales del institucional Danwor.
- Tema claro predeterminado y tema oscuro opcional, limitado al panel editorial.
- Navegación anterior/siguiente y selector server-side de content pack.
- Logo animado reutilizado desde el pack activo.
- Paneles redimensionables con preferencias persistidas en el navegador.
- Panel derecho con tabs `Sección`, `Árbol` y `Asistente`.
- Selección explícita de árbol cuando un módulo ofrece múltiples decisiones.
- Tokens editoriales y metadata de frontmatter excluidos del HTML visible.

## Límite arquitectónico encontrado

El repositorio heredado todavía importa Streamlit desde `core/content.py`,
`core/state.py`, `core/llm.py`, `core/corpus.py`, `core/sharepoint_cases.py` y
`core/activity.py`. La beta no introduce esos módulos en la capa HTTP: consume el
registro puro existente y encapsula parsing y árboles en servicios web testeables.

`WizardStore` persiste los árboles en memoria sólo para esta evidencia. Puede reemplazarse por
Redis sin cambiar rutas o fragmentos HTMX.

## Ejecutar

```powershell
python -m uvicorn webapp.main:app --reload --port 8501
```

Abrir `http://localhost:8501`.

> Puerto **8501**: reutiliza el redirect URI ya registrado en Azure
> (`http://localhost:8501/`, heredado del Streamlit original), así el flujo
> MSAL funciona sin registrar uno nuevo. No correr el Streamlit original en
> paralelo: comparten puerto.

## Siguientes verticales

1. Extraer el parser editorial como dominio compartido sin UI.
2. Persistir sesiones y eventos con adapters corporativos.
3. Migrar Entra ID a middleware FastAPI.
4. Integrar el gateway LLM async sin `session_state`.
5. Compilar Tailwind y alojar HTMX/Alpine localmente.
