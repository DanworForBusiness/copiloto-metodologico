# `webapp/static/vendor/` — terceros servidos por la app

Acá vive **código de terceros que la app sirve ella misma**, en vez de traerlo
de un CDN. Hoy hay exactamente uno.

## `msal-browser-4.30.0.min.js`

| | |
|---|---|
| Paquete | `@azure/msal-browser` |
| Versión | **4.30.0** (dist-tag `lts` de Microsoft) |
| Publicado | 2026-03-18 |
| Origen | `https://unpkg.com/@azure/msal-browser@4.30.0/lib/msal-browser.min.js` |
| Bytes | 332 695 |
| SHA-256 | `2b580165064a6a4ec041ebd44832299172688a23a42c3b81d65314799dba04eb` |
| Licencia | MIT |
| Formato | UMD — expone `globalThis.msal`, se carga con `<script src>` sin build |
| Consumidor | `webapp/static/hub_auth.js` (la autoridad de auth del Hub) |

### Por qué local y no CDN

El resto de los terceros del proyecto (Tailwind, Alpine, htmx) vienen de CDN, y
la tentación era seguir el patrón. Pero esto es **el código que maneja las
credenciales**: un CDN de terceros comprometido, o simplemente caído, deja al
Hub sin autenticación — y en el peor caso con un actor externo dentro del flujo
de tokens. Servirlo nosotros elimina esa dependencia en runtime, y el artefacto
queda versionado en git, así que su integridad la garantiza el repo (que es una
garantía más fuerte que un atributo `integrity`, porque cubre también la
disponibilidad).

Costo: cero. Es un archivo estático más. **No se agregó npm, ni bundler, ni
ningún pipeline de build** — el bundle UMD se carga con un `<script src>`, igual
que los de CDN.

### Por qué 4.30.0 y no la última (5.19.0)

`latest` al momento de elegir era 5.19.0, publicada **cinco días antes**.
4.30.0 es el dist-tag `lts` de Microsoft y lleva **cinco meses** de rodaje.

Toda la API que el Hub usa está en las dos, verificada contra el bundle:
`PublicClientApplication`, `initialize`, `loginPopup`, `acquireTokenSilent`,
`acquireTokenPopup`, `getAllAccounts`, `get/setActiveAccount`,
`InteractionRequiredAuthError`, `logoutPopup`, `clearCache`,
`BrowserCacheLocation`. Y las dos traen `offline_access` en sus
`defaultScopes` (en 4.30.0: `defaultScopes:[OPENID_SCOPE, PROFILE_SCOPE,
OFFLINE_ACCESS_SCOPE]`), así que no hay nada que la app tenga que agregar ni
gestionar.

La razón decisiva no es la antigüedad en sí: **el gate del iframe embebido es
un experimento**, y estrenar simultáneamente una librería nueva *y* su versión
menos rodada agrega una variable que confundiría el diagnóstico. Si el silent
renewal falla en el iframe, queremos poder afirmar que es el contexto y no una
regresión de una release de cinco días.

Migrar a 5.x queda como seguimiento **después** de que el ciclo de vida esté
probado en producción. 5.19.0 trae además dos módulos que 4.30.0 no
(`popup-relay`, `redirect-bridge`) que podrían interesar si el popup necesita
un relay explícito — hoy no lo necesita, porque el opener lee el resultado del
popup directamente (mismo origen).

### `.gitattributes`: sin conversión de line endings

El repo tiene `core.autocrlf = true`. Sin una regla explícita, un checkout
fresco en Windows reescribiría este bundle con CRLF: seguiría funcionando (JS
tolera CRLF) pero su SHA-256 cambiaría, y la verificación de integridad
empezaría a fallar por una razón que no tiene nada que ver con la integridad.
De ahí la regla:

```
webapp/static/vendor/** -text
```

El artefacto se guarda y se restaura byte a byte, igual que lo publica el
registry. `test_git_never_rewrites_the_vendored_bundle` la protege — sin ella,
el test de hash sería una garantía falsa.

### Cómo actualizar

1. Bajar el bundle nuevo y **verificar el SHA-256** contra el que publique el
   registry.
2. Agregarlo con la versión en el nombre del archivo (el pin tiene que ser
   imposible de confundir al leer el `<script src>`).
3. Actualizar la referencia en `base.html`, `landing.html` y
   `auth_callback.html`, más la tabla de acá arriba.
4. Correr `test_hub_auth_msal.py`, que verifica que la versión servida sea la
   declarada y que el bundle traiga la API que la autoridad usa.
5. Borrar el bundle viejo — dos versiones conviviendo es exactamente el tipo
   de ambigüedad que este archivo existe para evitar.
