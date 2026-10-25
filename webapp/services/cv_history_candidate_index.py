"""cv_history_candidate_index.py — índice TEMPORAL en memoria (por request, sin estado compartido) que acota los perfiles que vale la pena comparar con `cv_reconcile.reconcile_with_profiles`.

PROBLEMA. `reconcile_with_profiles` compara a la persona contra TODOS los perfiles con `signals_between` (O(perfiles) por variante, con `fold` y `difflib` por par). Clasificar una página de 50 excepciones contra 800 perfiles costaba ~2,3 s.

QUÉ HACE ESTE MÓDULO. Antes de llamar a la función EXISTENTE, recupera los perfiles que PUEDEN producir alguna señal y se la entrega solo a ellos. No es un segundo motor de identidad: no calcula señales, no decide fuerza y no cambia ningún resultado. La equivalencia es EXACTA porque el conjunto recuperado es un SUPERCONJUNTO de los perfiles con alguna señal, y `reconcile_with_profiles` descarta de todos modos a los que no tienen ninguna (el orden final depende de la fuerza, las señales y el `profile_id`, no del orden de entrada):

  EMAIL_EXACT        -> índice por email normalizado;
  PHONE_MATCH        -> índice por los últimos dígitos del celular;
  NAME_EXACT         -> índice por nombre completo plegado;
  NAME_SWAPPED / NAME_TOKENS_EQUAL -> índice por conjunto de palabras (`cv_reconcile.token_key`): un nombre invertido tiene las mismas palabras, o sea la misma clave (lo documenta `token_key`);
  INITIAL_LAST       -> índice por (apellido plegado, inicial del nombre);
  NAME_SIMILAR       -> `difflib` solo puede dar ratio >= umbral si lo permiten sus cotas superiores documentadas (`real_quick_ratio` y `quick_ratio` >= ratio): se descartan SOLO los pares cuya cota ya queda bajo el umbral;
                        el cálculo del parecido real lo sigue haciendo `signals_between`.
Las cotas se calculan con la misma fórmula que `difflib` (2·coincidencias/longitud), así que la comparación con el umbral es monótona y nunca descarta un par que `ratio()` aceptaría.

Reutiliza los normalizadores de `cv_reconcile` (`norm_email`, `phone_tail`, `_full`, `_tokens`, `token_key`): si cambian, el índice cambia con ellos. Los tests comparan el resultado indexado contra el completo sobre identidades ambiguas.

Puro: sin disco, sin Graph, sin logs. El índice vive lo que dura quien lo construye (un request)."""
from __future__ import annotations

from collections import defaultdict

from webapp.services import cv_reconcile
from webapp.services.cv_extract import fold


def _phone(person: dict) -> str:
    return cv_reconcile.phone_tail(person.get("phone") or person.get("phone_digits"))


def _counts(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for ch in text:
        out[ch] = out.get(ch, 0) + 1
    return out


class CandidateIndex:
    """Índice de una lista de perfiles. `reconcile(person)` devuelve EXACTAMENTE lo mismo que `cv_reconcile.reconcile_with_profiles(person, profiles)`."""

    def __init__(self, profiles: list[dict]):
        self.profiles = list(profiles)
        self._by_email: dict[str, list[int]] = defaultdict(list)
        self._by_phone: dict[str, list[int]] = defaultdict(list)
        self._by_full: dict[str, list[int]] = defaultdict(list)
        self._by_tokens: dict[frozenset, list[int]] = defaultdict(list)
        self._by_initial_last: dict[tuple[str, str], list[int]] = defaultdict(list)
        self._fuzzy: list[tuple[int, int, dict[str, int]]] = []                          # (posición, largo del nombre plegado, conteo de caracteres)
        for i, p in enumerate(self.profiles):
            email = cv_reconcile.norm_email(p.get("email"))
            if email:
                self._by_email[email].append(i)
            phone = _phone(p)
            if phone:
                self._by_phone[phone].append(i)
            full = cv_reconcile._full(p.get("first_name"), p.get("last_name"))
            if full.strip():                                                              # toda señal de nombre exige nombre no vacío de ambos lados
                self._by_full[full].append(i)
                self._by_tokens[cv_reconcile.token_key(p.get("first_name"), p.get("last_name"))].append(i)
                last, initial = fold(p.get("last_name") or ""), fold(p.get("first_name") or "")[:1]
                if last and initial:
                    self._by_initial_last[(last, initial)].append(i)
                self._fuzzy.append((i, len(full), _counts(full)))

    def candidates(self, person: dict) -> list[dict]:
        """Perfiles que pueden coincidir con `person`, en el orden original de la lista."""
        hit: set[int] = set()
        email = cv_reconcile.norm_email(person.get("email"))
        if email:
            hit.update(self._by_email.get(email, ()))
        phone = _phone(person)
        if phone:
            hit.update(self._by_phone.get(phone, ()))
        first, last = person.get("first_name"), person.get("last_name")
        full = cv_reconcile._full(first, last)
        if full.strip():
            hit.update(self._by_full.get(full, ()))
            hit.update(self._by_tokens.get(cv_reconcile.token_key(first, last), ()))
            last_f, initial = fold(last or ""), fold(first or "")[:1]
            if last_f and initial:
                hit.update(self._by_initial_last.get((last_f, initial), ()))
            hit.update(self._similar(full))
        return [self.profiles[i] for i in sorted(hit)]

    def _similar(self, full: str) -> list[int]:
        """Posiciones cuyo parecido PODRÍA llegar al umbral según las cotas superiores de `difflib` (nunca descarta uno que sí llegaría)."""
        threshold = cv_reconcile.SIMILARITY_THRESHOLD
        mine, counts = len(full), _counts(full)
        keep = []
        for i, length, theirs in self._fuzzy:
            total = mine + length
            if 2.0 * min(mine, length) / total < threshold:                                # `real_quick_ratio`
                continue
            small, big = (counts, theirs) if len(counts) <= len(theirs) else (theirs, counts)
            shared = sum(min(n, big.get(ch, 0)) for ch, n in small.items())
            if 2.0 * shared / total >= threshold:                                          # `quick_ratio`
                keep.append(i)
        return keep

    def reconcile(self, person: dict) -> list[dict]:
        return cv_reconcile.reconcile_with_profiles(person, self.candidates(person))
