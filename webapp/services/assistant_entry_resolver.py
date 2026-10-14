"""Resolvedor de IDENTIDAD de un modelo del Catálogo a partir de lo que el usuario escribió (paso 4B.0).

Un modelo se identifica por el nombre con que lo ve el usuario: el TÍTULO que muestra el Catálogo (``presenter.entry_title``) es la clave principal; el identificador técnico del modelo terminal (AM)
y el dominio son claves secundarias. El CLIENTE solo desambigua: jamás selecciona un modelo por sí solo. ``entry_id`` sigue siendo la identidad autoritativa: este módulo solo decide A CUÁL
``entry_id`` se refiere el texto, nunca inventa ni completa nombres.

Reglas (todas deterministas, sin similitud aproximada ni inferencia):
  * Una clave coincide cuando TODAS sus palabras aparecen consecutivas en la pregunta (se conservan las palabras cortas: «MP» o «AM» son identidad).
  * Un nombre entre «comillas» es explícito: tiene que ser IGUAL a una clave (también el dominio compartido, que entonces es ambiguo).
  * Entre dos coincidencias anidadas gana la más larga («Sell Out» no es «Sell Out Hojobar»).
  * Un cliente nombrado acota el universo; si nombra más de uno el resultado lo informa. Un cliente solo no selecciona un modelo.
  * Los nombres de línea de negocio o de cliente («Ventas») son alcance, no nombre de modelo, salvo entre comillas.
  * Un dominio que comparten varias entradas no identifica a ninguna (solo existe entre comillas, y es ambiguo).

Módulo PURO: no lee el Catálogo, el Assessment ni la sesión; quien lo usa le entrega las identidades ya leídas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from core.utils import norm_text

UNIQUE, AMBIGUOUS, MULTIPLE, NONE = "UNIQUE", "AMBIGUOUS", "MULTIPLE", "NONE"
_QUOTED = re.compile(r"«([^»]{1,120})»|“([^”]{1,120})”|\"([^\"]{1,120})\"")
_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Identity:
    """Lo que el resolvedor sabe de una entrada (todo ya leído de los contratos gobernados)."""
    entry_id: str
    client: str
    title: str                 # título del Catálogo ("" si el modelo no tiene identidad terminal: no es una clave)
    technical: str = ""        # identificador técnico del modelo terminal (p. ej. «AM_RESERVA_MP»)
    domain: str = ""           # dominio legado (clave secundaria)


@dataclass(frozen=True)
class Resolution:
    status: str                              # UNIQUE | AMBIGUOUS | MULTIPLE | NONE
    matches: tuple[Identity, ...] = ()       # UNIQUE: una; AMBIGUOUS: las que comparten el nombre; MULTIPLE: todas las nombradas; NONE: ninguna
    quoted: bool = False                     # el usuario entrecomilló un nombre (explícito)
    clients: tuple[str, ...] = ()            # clientes nombrados (informa; un cliente solo nunca selecciona)

    @property
    def unique(self) -> Identity | None:
        return self.matches[0] if self.status == UNIQUE else None


def tokens(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(norm_text(text)))


def _find(haystack: tuple[str, ...], needle: tuple[str, ...]) -> list[tuple[int, int]]:
    n = len(needle)
    if not n:
        return []
    return [(i, i + n) for i in range(len(haystack) - n + 1) if haystack[i:i + n] == needle]


def _keys(i: Identity, shared_domains: set[tuple[str, ...]]) -> list[tuple[tuple[str, ...], str]]:
    """Las claves del modelo, (palabras, origen): título, identificador técnico y dominio propio (no compartido)."""
    out = []
    title = tokens(i.title)
    if title:
        out.append((title, "title"))
    technical = tokens(i.technical)
    if technical and technical != title:
        out.append((technical, "technical"))
    domain = tokens(i.domain)
    if domain and domain != title and domain not in shared_domains:
        out.append((domain, "domain"))
    return out


def suggest(terms: list[str] | tuple[str, ...], identities: list[Identity] | tuple[Identity, ...], clients: tuple[str, ...] | list[str] = (), limit: int = 8) -> tuple[Identity, ...]:
    """CANDIDATOS (nunca una selección) para un nombre que NO coincide exacto: los modelos cuyas palabras de título, identificador técnico o dominio contienen TODAS las palabras pedidas
    (igualdad, o prefijo de al menos 3 letras: «doc» → «document»). Un cliente nombrado acota. Sirve solo para ofrecerle al usuario opciones que ÉL confirma; ``resolve`` sigue sin fuzzy."""
    wanted = list(dict.fromkeys(t for t in terms if t))
    if not wanted or not (len(wanted) >= 2 or any(len(t) >= 3 for t in wanted)):
        return ()
    domain_count: dict[tuple[str, ...], int] = {}
    for i in identities:
        d = tokens(i.domain)
        if d:
            domain_count[d] = domain_count.get(d, 0) + 1
    shared = {d for d, n in domain_count.items() if n > 1}                    # un dominio compartido no nombra a ningún modelo (ni siquiera como sugerencia)
    found = []
    for i in identities:
        if clients and i.client not in clients:
            continue
        words = {w for keys, _origin in _keys(i, shared) for w in keys}
        if all(any(w == t or (len(t) >= 3 and w.startswith(t)) for w in words) for t in wanted):
            found.append(i)
    return tuple(sorted(found, key=lambda i: (i.client.lower(), i.title.lower(), i.entry_id))[:limit])


def _inside(span: tuple[int, int], others: list[tuple[int, int]]) -> bool:
    return any(o[0] <= span[0] and span[1] <= o[1] for o in others)


def resolve(question: str, identities: list[Identity] | tuple[Identity, ...], clients: tuple[str, ...] | list[str] = (), reserved: tuple[str, ...] | list[str] = ()) -> Resolution:
    """A qué modelo se refiere ``question``. ``clients``: nombres de cliente del Catálogo; ``reserved``: frases que son ALCANCE y no nombre de modelo (líneas de negocio)."""
    words = tokens(question)
    named = tuple(c for c in clients if _find(words, tokens(c)))
    universe = [i for i in identities if not named or i.client in named]                          # un cliente nombrado acota; sin cliente, todo el Catálogo
    domain_count: dict[tuple[str, ...], int] = {}
    for i in identities:                                                                           # «compartido» se mide en TODO el Catálogo, no solo en el cliente nombrado
        d = tokens(i.domain)
        if d:
            domain_count[d] = domain_count.get(d, 0) + 1
    shared = {d for d, n in domain_count.items() if n > 1}

    quoted = [tokens(m) for m in (g for groups in _QUOTED.findall(question) for g in groups if g)]
    quoted = [q for q in quoted if q]
    if quoted:
        hits: list[Identity] = []
        groups_found = 0
        for q in quoted:
            by_name = [i for i in universe if any(keys == q for keys, origin in _keys(i, set()) if origin != "domain")]
            found = by_name or [i for i in universe if any(keys == q for keys, origin in _keys(i, set()) if origin == "domain")]       # entrecomillado: IGUAL a una clave; el nombre del modelo antes que el dominio (compartido incluido)
            if found:
                groups_found += 1
                hits.extend(i for i in found if i not in hits)
        if not hits:
            return Resolution(NONE, (), True, named)
        status = UNIQUE if len(hits) == 1 else (AMBIGUOUS if groups_found == 1 else MULTIPLE)
        return Resolution(status, tuple(hits), True, named)

    blocked = [s for phrase in reserved for s in _find(words, tokens(phrase))] + [s for c in named for s in _find(words, tokens(c))]
    best: dict[str, tuple[int, int]] = {}
    for i in universe:
        for key, _origin in _keys(i, shared):
            for span in _find(words, key):
                if _inside(span, blocked):
                    continue                                                                         # «Ventas»/«Stock / Inventario»/un cliente: es alcance, no un nombre de modelo
                if i.entry_id not in best or span[1] - span[0] > best[i.entry_id][1] - best[i.entry_id][0]:
                    best[i.entry_id] = span
    by_id = {i.entry_id: i for i in universe}
    survivors = [eid for eid, s in best.items()
                 if not any(o != eid and best[o][0] <= s[0] and s[1] <= best[o][1] and best[o][1] - best[o][0] > s[1] - s[0] for o in best)]      # gana la coincidencia anidada más larga
    if not survivors:
        return Resolution(NONE, (), False, named)
    spans = {best[e] for e in survivors}
    matches = tuple(by_id[e] for e in sorted(survivors, key=lambda e: (best[e], by_id[e].client.lower(), by_id[e].title.lower(), e)))
    if len(spans) > 1:
        return Resolution(MULTIPLE, matches, False, named)
    return Resolution(UNIQUE if len(matches) == 1 else AMBIGUOUS, matches, False, named)
