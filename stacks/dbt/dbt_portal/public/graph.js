'use strict';
// Layered DAG layout (longest path + barycenter ordering) drawn as SVG, with pan, zoom and
// upstream/downstream highlighting. No dependencies: the page's CSP allows only this origin.
(() => {
  const NS = 'http://www.w3.org/2000/svg';
  const W = 210, H = 58, GAP_X = 90, GAP_Y = 22;
  const KIND = {source: 'source', seed: 'seed', model: 'model', snapshot: 'snapshot', exposure: 'exposure'};

  function el(tag, attrs = {}, parent) {
    const node = document.createElementNS(NS, tag);
    for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
    if (parent) parent.append(node);
    return node;
  }

  function layout(nodes, edges) {
    const ids = new Set(nodes.map(n => n.id));
    const parents = new Map(nodes.map(n => [n.id, []])), children = new Map(nodes.map(n => [n.id, []]));
    for (const e of edges) if (ids.has(e.from) && ids.has(e.to)) { parents.get(e.to).push(e.from); children.get(e.from).push(e.to); }
    // Longest path layering; dbt graphs are acyclic, a guard keeps a broken artifact from looping.
    const layer = new Map();
    const depth = (id, seen = new Set()) => {
      if (layer.has(id)) return layer.get(id);
      if (seen.has(id)) return 0;
      seen.add(id);
      const value = parents.get(id).length ? Math.max(...parents.get(id).map(p => depth(p, seen) + 1)) : 0;
      layer.set(id, value);
      return value;
    };
    nodes.forEach(n => depth(n.id));
    const layers = [];
    for (const n of nodes) (layers[layer.get(n.id)] ||= []).push(n.id);
    layers.forEach(l => l.sort());
    const position = new Map();
    const index = () => layers.forEach(l => l.forEach((id, i) => position.set(id, i)));
    index();
    const centre = (id, links) => {
      const values = links.get(id).map(n => position.get(n)).filter(v => v !== undefined);
      return values.length ? values.reduce((a, b) => a + b, 0) / values.length : position.get(id);
    };
    for (let sweep = 0; sweep < 6; sweep++) {
      const down = sweep % 2 === 0;
      const order = down ? layers.slice(1) : layers.slice(0, -1).reverse();
      for (const l of order) { l.sort((a, b) => centre(a, down ? parents : children) - centre(b, down ? parents : children)); index(); }
    }
    const tallest = Math.max(1, ...layers.map(l => l.length));
    const coords = new Map();
    layers.forEach((l, x) => {
      const offset = (tallest - l.length) * (H + GAP_Y) / 2;
      l.forEach((id, y) => coords.set(id, {x: x * (W + GAP_X), y: offset + y * (H + GAP_Y)}));
    });
    return {coords, parents, children, width: layers.length * (W + GAP_X) - GAP_X, height: tallest * (H + GAP_Y) - GAP_Y};
  }

  function walk(start, links) {
    const seen = new Set(), stack = [start];
    while (stack.length) for (const next of links.get(stack.pop()) || []) if (!seen.has(next)) { seen.add(next); stack.push(next); }
    return seen;
  }

  function shorten(text, max) { return text.length > max ? text.slice(0, max - 1) + '…' : text; }

  class PipelineGraph {
    constructor(svg, {onSelect} = {}) {
      this.svg = svg; this.onSelect = onSelect; this.view = {x: 0, y: 0, k: 1};
      this.root = el('g', {}, svg);
      this.edgeLayer = el('g', {}, this.root); this.nodeLayer = el('g', {}, this.root);
      this.nodes = new Map(); this.edges = []; this.selected = null; this.query = '';
      this.bindPanZoom();
    }

    bindPanZoom() {
      let drag = null;
      this.svg.addEventListener('pointerdown', event => {
        if (event.target.closest('.node')) return;
        drag = {x: event.clientX, y: event.clientY, view: {...this.view}}; this.svg.classList.add('dragging');
        this.svg.setPointerCapture(event.pointerId);
      });
      this.svg.addEventListener('pointermove', event => {
        if (!drag) return;
        this.view.x = drag.view.x + event.clientX - drag.x; this.view.y = drag.view.y + event.clientY - drag.y; this.apply();
      });
      const stop = () => { drag = null; this.svg.classList.remove('dragging'); };
      this.svg.addEventListener('pointerup', stop); this.svg.addEventListener('pointercancel', stop);
      this.svg.addEventListener('wheel', event => {
        event.preventDefault();
        const box = this.svg.getBoundingClientRect(), mx = event.clientX - box.left, my = event.clientY - box.top;
        const k = Math.min(2.5, Math.max(0.15, this.view.k * Math.exp(-event.deltaY * 0.0015)));
        this.view.x = mx - (mx - this.view.x) * k / this.view.k; this.view.y = my - (my - this.view.y) * k / this.view.k;
        this.view.k = k; this.apply();
      }, {passive: false});
      this.svg.addEventListener('click', event => { if (!event.target.closest('.node')) this.select(null); });
    }

    apply() { this.root.setAttribute('transform', `translate(${this.view.x},${this.view.y}) scale(${this.view.k})`); }

    fit() {
      if (!this.shape) return;
      const box = this.svg.getBoundingClientRect(), pad = 40;
      const k = Math.min(1.2, Math.max(0.15, Math.min((box.width - pad * 2) / Math.max(1, this.shape.width),
        (box.height - pad * 2) / Math.max(1, this.shape.height))));
      this.view = {k, x: (box.width - this.shape.width * k) / 2, y: (box.height - this.shape.height * k) / 2};
      this.apply();
    }

    render(nodes, edges, statuses = null) {
      this.edgeLayer.replaceChildren(); this.nodeLayer.replaceChildren(); this.nodes.clear(); this.edges = [];
      this.shape = layout(nodes, edges);
      const {coords} = this.shape;
      for (const e of edges) {
        const a = coords.get(e.from), b = coords.get(e.to);
        if (!a || !b) continue;
        const x1 = a.x + W, y1 = a.y + H / 2, x2 = b.x, y2 = b.y + H / 2, dx = Math.max(30, (x2 - x1) / 2);
        const path = el('path', {class: 'edge', d: `M${x1},${y1} C${x1 + dx},${y1} ${x2 - dx},${y2} ${x2},${y2}`}, this.edgeLayer);
        this.edges.push({...e, path});
      }
      for (const node of nodes) {
        const at = coords.get(node.id), status = (statuses && statuses.get(node.id)) || node.status || 'never_run';
        const g = el('g', {class: 'node', transform: `translate(${at.x},${at.y})`, tabindex: '0', role: 'button'}, this.nodeLayer);
        g.dataset.id = node.id;
        el('rect', {class: 'box', width: W, height: H, rx: 10}, g);
        el('rect', {class: `stripe kind-${KIND[node.type] || 'model'}`, x: 0, y: 10, width: 3, height: H - 20}, g);
        const kind = el('text', {class: `kind kind-${KIND[node.type] || 'model'}`, x: 14, y: 17}, g);
        kind.textContent = `${node.type}${node.materialized && node.type === 'model' ? ' · ' + node.materialized : ''}`;
        const name = el('text', {class: 'name', x: 14, y: 35}, g); name.textContent = shorten(node.name, 26);
        const meta = el('text', {class: 'meta', x: 14, y: 50}, g);
        const tests = node.tests || {}, plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;
        const testText = tests.fail ? `✕ ${tests.fail} failing` : (tests.warn ? `! ${plural(tests.warn, 'warning')}` : (tests.pass ? `✓ ${plural(tests.pass, 'test')}` : ''));
        meta.textContent = shorten([node.namespace && node.table ? `${node.namespace}.${node.table}` : '', testText].filter(Boolean).join('  '), 36);
        el('circle', {class: status, cx: W - 16, cy: 16, r: 5}, g);
        const title = el('title', {}, g); title.textContent = `${node.id}\n${status.replace('_', ' ')}`;
        g.addEventListener('click', event => { event.stopPropagation(); this.select(node.id); });
        g.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); this.select(node.id); } });
        this.nodes.set(node.id, {node, g});
      }
      this.decorate();
    }

    select(id, {silent = false} = {}) {
      this.selected = id && this.nodes.has(id) ? id : null;
      this.decorate();
      if (!silent && this.onSelect) this.onSelect(this.selected);
    }

    center(id) {
      const at = this.shape && this.shape.coords.get(id);
      if (!at) return;
      const box = this.svg.getBoundingClientRect();
      this.view.x = box.width * 0.35 - (at.x + W / 2) * this.view.k; this.view.y = box.height / 2 - (at.y + H / 2) * this.view.k;
      this.apply();
    }

    highlight(query) { this.query = (query || '').trim().toLowerCase(); this.decorate(); }

    decorate() {
      const related = new Set();
      if (this.selected) {
        related.add(this.selected);
        walk(this.selected, this.shape.parents).forEach(id => related.add(id));
        walk(this.selected, this.shape.children).forEach(id => related.add(id));
      }
      for (const [id, {node, g}] of this.nodes) {
        const match = this.query && (node.name.toLowerCase().includes(this.query) || id.toLowerCase().includes(this.query));
        g.classList.toggle('selected', id === this.selected);
        g.classList.toggle('match', Boolean(match));
        g.classList.toggle('dim', (this.selected && !related.has(id)) || (Boolean(this.query) && !match && !this.selected));
      }
      for (const e of this.edges) {
        const lit = this.selected && related.has(e.from) && related.has(e.to);
        e.path.classList.toggle('lit', Boolean(lit));
        e.path.classList.toggle('dim', Boolean(this.selected && !lit));
      }
    }
  }
  window.PipelineGraph = PipelineGraph;
})();
