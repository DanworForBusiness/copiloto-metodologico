"""pagination.py — paginación server-side GENÉRICA del Hub (cálculo de páginas + ventana con «…» + URLs que conservan el estado de la consulta).

Origen: el paginador canónico de «Uso de métricas» (`activity-pager`, tools.css; `activity_analytics._page_window`). `page_window` es EXACTAMENTE esa función (ahora compartida;
`activity_analytics` la importa y no cambia su comportamiento). `paginate` generaliza el cálculo que ya hacían inline esas superficies (tamaño válido o default, `total_pages`,
página acotada a [1, total_pages]) y le suma las URLs de la navegación, para que el markup compartido (`partials/pager.html`) no arme querystrings a mano.

Contrato de orden (lo garantiza quien llama): dataset completo → filtros → búsqueda → orden → total filtrado → `paginate` (slice). NUNCA paginar antes de filtrar.
"""
from __future__ import annotations

from typing import Any, Sequence
from urllib.parse import urlencode

# Contrato de tablas de Capital Humano (ver HANDOFF «CCHH TABLE PAGINATION STANDARD»).
CCHH_PAGE_SIZES: tuple[int, ...] = (50, 100, 200, 300)
CCHH_DEFAULT_PAGE_SIZE = 50


def page_window(page: int, total_pages: int, radius: int = 2) -> list[int | None]:
    """Números de página a mostrar en el paginador: siempre primera/última, +/- `radius` alrededor de la actual, con `None` como separador "…" en los huecos.
    Ej. page=4, total=10, radius=2 → [1, None, 2,3,4,5,6, None, 10]."""
    if total_pages <= 1:
        return [1]
    window = {1, total_pages}
    for p in range(page - radius, page + radius + 1):
        if 1 <= p <= total_pages:
            window.add(p)
    ordered = sorted(window)
    result: list[int | None] = []
    prev = None
    for p in ordered:
        if prev is not None and p - prev > 1:
            result.append(None)
        result.append(p)
        prev = p
    return result


def parse_positive_int(value: Any, default: int) -> int:
    """Entero positivo tolerante: un valor ausente/inválido/≤0 cae al default (nunca 422 ni página inválida)."""
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _qs(params: dict[str, Any], **extra: Any) -> str:
    """Querystring con los criterios ACTIVOS (los vacíos no viajan) más los extra."""
    merged = {k: v for k, v in params.items() if v not in (None, "")}
    merged.update({k: v for k, v in extra.items() if v not in (None, "")})
    return urlencode(merged)


def paginate(items: Sequence[Any], *, page: Any = 1, page_size: Any = None, base_url: str = "", params: dict[str, Any] | None = None,
             sizes: Sequence[int] = CCHH_PAGE_SIZES, default_size: int = CCHH_DEFAULT_PAGE_SIZE,
             page_param: str = "page", size_param: str = "page_size") -> dict:
    """Pagina `items` (YA filtrados y ordenados). `page_size` inválido o fuera de `sizes` → `default_size`; `page` se acota a [1, total_pages] (nunca una página vacía por conservar
    un índice viejo). Devuelve el slice y todo lo que el paginador compartido necesita: `total` (TOTAL filtrado, para el contador), `page`, `page_size`, `page_sizes`, `total_pages`,
    `page_window`, `show` (hay más filas que el tamaño mínimo → el paginador/selector tiene sentido) y `urls`: `{page: url}` para cada página visible + `first/prev/next/last` +
    `size` (base SIN página, a la que el `<select>` agrega `page_size` → vuelve a la página 1). `params` = criterios activos (filtros/búsqueda/orden) que TODA url conserva."""
    params = dict(params or {})
    sizes = tuple(sizes)
    size = parse_positive_int(page_size, default_size)
    if size not in sizes:
        size = default_size
    total = len(items)
    total_pages = max(1, -(-total // size)) if total else 1
    current = max(1, min(parse_positive_int(page, 1), total_pages))
    start = (current - 1) * size
    window = page_window(current, total_pages)

    def url(p: int) -> str:
        return f"{base_url}?{_qs(params, **{page_param: p, size_param: size})}"
    return {
        "items": list(items[start:start + size]),
        "total": total, "page": current, "page_size": size, "page_sizes": sizes, "total_pages": total_pages, "page_window": window,
        "show": total > min(sizes),
        "range_start": start + 1 if total else 0, "range_end": min(start + size, total),
        "urls": {**{p: url(p) for p in window if p is not None}, "first": url(1), "prev": url(max(1, current - 1)), "next": url(min(total_pages, current + 1)), "last": url(total_pages),
                 "size": f"{base_url}?{_qs(params)}", "reset": f"{base_url}?{_qs({}, **{size_param: size})}"},
        "size_param": size_param, "page_param": page_param,
    }
