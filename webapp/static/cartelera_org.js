/* Cartelera Virtual · Organigrama (Nosotros): compacta las hojas junto a un hermano con subárbol.
   El árbol CSS (`.org`, conectores con ::before/::after) reserva para cada nodo el ancho de su subárbol; una hoja (p. ej. una persona sin
   reportes) queda entonces a la derecha/izquierda de TODO el subárbol vecino y fuerza scroll horizontal. Acá se la acerca al nodo vecino, en
   el espacio libre a su misma altura (por encima de los hijos del vecino), con un margen negativo, y se recorta el tramo de conector del
   vecino para que la línea termine en la hoja. Límites: (1) separación mínima con la caja del vecino, (2) la hoja no baja hasta la barra de
   los hijos del vecino, (3) el tronco del padre sigue cayendo sobre la barra horizontal (entre el primer y el último hijo).
   Solo presentación: sin JS el organigrama se ve como siempre. Se procesa de lo más profundo a la raíz. */
(function () {
  'use strict';
  var GAP = 24;

  function nodeOf(li) { return li.querySelector(':scope > .og-node, :scope > .og-logo'); }
  function isLeaf(li) { return !li.querySelector(':scope > ul'); }
  function rect(el) { return el.getBoundingClientRect(); }

  // ¿El tronco del padre (centro del ul) cae entre los centros del primer y el último hijo?
  function stemOk(ul) {
    var kids = ul.querySelectorAll(':scope > li');
    if (kids.length < 2) return true;
    var u = rect(ul), c = u.left + u.width / 2;
    var f = rect(nodeOf(kids[0]) || kids[0]), l = rect(nodeOf(kids[kids.length - 1]) || kids[kids.length - 1]);
    return c >= f.left + f.width / 2 - 1 && c <= l.left + l.width / 2 + 1;
  }

  // Aplica el corrimiento `d` (px) de la hoja hacia el vecino (`side` = 'prev' | 'next').
  function apply(leaf, neighbor, side, d) {
    if (side === 'prev') { leaf.style.marginLeft = d ? -d + 'px' : ''; neighbor.style.setProperty('--og-after-trim', d + 'px'); }
    else { leaf.style.marginRight = d ? -d + 'px' : ''; neighbor.style.setProperty('--og-before-trim', d + 'px'); }
  }

  function compact(leaf, neighbor, side) {
    var ln = nodeOf(leaf), nn = nodeOf(neighbor);
    if (!ln || !nn) return;
    var lr = rect(leaf), nr = rect(neighbor), lnr = rect(ln), nnr = rect(nn);
    if (lnr.bottom > nnr.bottom + 14) return;                                       // la hoja es más alta: chocaría con la barra de los hijos
    // Espacio libre a la altura de los nodos: entre la caja del vecino y el borde de su columna, menos la parte vacía de la columna de la hoja.
    var free = side === 'prev'
      ? (nr.right - nnr.right) + (lnr.left - lr.left) - GAP
      : (nnr.left - nr.left) + (lr.right - lnr.right) - GAP;
    var d = Math.floor(Math.max(0, free));
    if (!d) return;
    var ul = leaf.parentElement;
    apply(leaf, neighbor, side, d);
    if (stemOk(ul)) return;
    var lo = 0, hi = d;                                                               // el tronco del padre quedó afuera: mayor d que lo conserva
    for (var i = 0; i < 12 && hi - lo > 1; i++) {
      var mid = Math.floor((lo + hi) / 2);
      apply(leaf, neighbor, side, mid);
      if (stemOk(ul)) lo = mid; else hi = mid;
    }
    apply(leaf, neighbor, side, lo);
  }

  // El tronco del padre puede quedar justo sobre el primer o el último hijo (el límite del corrimiento): ese conector va recto.
  function markStem(ul) {
    var kids = ul.querySelectorAll(':scope > li');
    if (kids.length < 2) return;
    var u = rect(ul), c = u.left + u.width / 2;
    [kids[0], kids[kids.length - 1]].forEach(function (li) {
      var n = rect(nodeOf(li) || li);
      li.classList.toggle('og-stem-through', Math.abs(n.left + n.width / 2 - c) <= 2);
    });
  }

  function run(org) {
    var uls = Array.prototype.slice.call(org.querySelectorAll('ul'));
    uls.sort(function (a, b) { return depth(b) - depth(a); });                       // de lo más profundo a la raíz
    uls.forEach(function (ul) {
      var kids = Array.prototype.slice.call(ul.querySelectorAll(':scope > li'));
      kids.forEach(function (li, i) {
        if (!isLeaf(li)) return;
        var prev = kids[i - 1], next = kids[i + 1];
        if (prev && !isLeaf(prev)) compact(li, prev, 'prev');
        else if (next && !isLeaf(next)) compact(li, next, 'next');
      });
    });
    uls.forEach(markStem);
  }
  function depth(el) { var n = 0; while (el && !el.classList.contains('org')) { el = el.parentElement; n++; } return n; }

  function boot() {
    document.querySelectorAll('.org').forEach(function (org) {
      var go = function () { run(org); };
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(go); else go();
    });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
