/* Semantic model diagram: datasets as entity boxes, relationships as edges between their join columns.
   Plain SVG with a small layered layout, so it needs no library and stays within the portal's CSP. */
const SD = {width: 256, header: 46, row: 22, gapX: 112, gapY: 28, pad: 24, maxRows: 12, char: 7.3};
const SVG_NS = 'http://www.w3.org/2000/svg';
let diagramCount = 0;

function svgNode(tag, attributes = {}, className) {
  const e = document.createElementNS(SVG_NS, tag);
  Object.entries(attributes).forEach(([k, v]) => e.setAttribute(k, v));
  if (className) e.setAttribute('class', className);
  return e;
}
function svgText(x, y, text, className, width) {
  const max = width ? Math.max(1, Math.floor(width / SD.char)) : Infinity;
  const node = svgNode('text', {x, y}, className);
  node.textContent = text.length > max ? `${text.slice(0, max - 1)}…` : text;
  return node;
}

// Join columns and keys stay visible when a long dataset is cut to SD.maxRows fields.
function visibleFields(dataset, joined) {
  const important = f => dataset.primaryKey.includes(f.name) || joined.has(f.name);
  if (dataset.fields.length <= SD.maxRows) return {fields: dataset.fields, hidden: 0};
  let budget = SD.maxRows - dataset.fields.filter(important).length;
  const fields = dataset.fields.filter(f => important(f) || budget-- > 0);
  return {fields, hidden: dataset.fields.length - fields.length};
}

/* Layered layout. A relationship points from the many side (`from`, usually the fact table) to the
   one side (`to`), so longest-path ranks put facts on the left and the dimensions they use to the right.
   An edge that spans several columns gets a waypoint in each column it crosses, so the boxes there
   leave room for it. Barycenter sweeps then order each column by its neighbours to reduce crossings. */
function semanticLayout(datasets, relationships, joinedBy) {
  const names = datasets.map(d => d.name), byName = new Map(datasets.map(d => [d.name, d]));
  const edges = relationships.filter(r => byName.has(r.from) && byName.has(r.to) && r.from !== r.to);
  const linked = new Set(edges.flatMap(r => [r.from, r.to]));
  const rank = new Map(names.map(n => [n, 0]));
  // A cycle stops growing at the last rank instead of looping.
  for (let pass = 0, changed = true; changed && pass < names.length; pass++) {
    changed = false;
    edges.forEach(r => {
      const next = Math.min(rank.get(r.from) + 1, names.length - 1);
      if (next > rank.get(r.to)) { rank.set(r.to, next); changed = true; }
    });
  }
  // Datasets without relationships get a column of their own after the connected ones.
  if (linked.size) { const last = Math.max(...[...linked].map(n => rank.get(n))); names.forEach(n => { if (!linked.has(n)) rank.set(n, last + 1); }); }
  const neighbours = new Map(names.map(n => [n, []]));
  const waypoints = new Map(edges.map((r, i) => {
    const chain = [];
    for (let c = rank.get(r.from) + 1; c < rank.get(r.to); c++) { const id = `\0${i}:${c}`; chain.push(id); rank.set(id, c); neighbours.set(id, []); }
    const path = [r.from, ...chain, r.to];
    path.slice(1).forEach((n, k) => { neighbours.get(path[k]).push(n); neighbours.get(n).push(path[k]); });
    return [r, chain];
  }));
  const columns = [];
  rank.forEach((c, n) => (columns[c] ||= []).push(n));
  const cols = columns.filter(Boolean);
  // Alternate left-to-right and right-to-left: each column follows the column it was just compared with.
  const position = new Map();
  const place = col => col.forEach((n, i) => position.set(n, (i + 1) / (col.length + 1)));
  cols.forEach(place);
  const order = (col, fixed) => {
    const at = new Map(col.map(n => {
      const near = neighbours.get(n).filter(m => fixed.includes(m));
      return [n, near.length ? near.reduce((sum, m) => sum + position.get(m), 0) / near.length : position.get(n)];
    }));
    col.sort((a, b) => at.get(a) - at.get(b)); place(col);
  };
  for (let sweep = 0; sweep < 4; sweep++) {
    for (let c = 1; c < cols.length; c++) order(cols[c], cols[c - 1]);
    for (let c = cols.length - 2; c >= 0; c--) order(cols[c], cols[c + 1]);
  }
  const boxes = new Map(), points = new Map();
  const height = n => boxes.get(n)?.height ?? 0;
  cols.flat().filter(n => byName.has(n)).forEach(n => {
    const shown = visibleFields(byName.get(n), joinedBy.get(n));
    boxes.set(n, {dataset: byName.get(n), ...shown, height: SD.header + (shown.fields.length + (shown.hidden ? 1 : 0)) * SD.row + 8});
  });
  const heights = cols.map(col => col.reduce((sum, n) => sum + height(n), 0) + (col.length - 1) * SD.gapY);
  const tallest = Math.max(0, ...heights);
  cols.forEach((col, c) => {
    const x = SD.pad + c * (SD.width + SD.gapX);
    let y = SD.pad + (tallest - heights[c]) / 2;
    col.forEach(n => {
      if (boxes.has(n)) Object.assign(boxes.get(n), {x, y}); else points.set(n, {x, y});
      y += height(n) + SD.gapY;
    });
  });
  const route = new Map([...waypoints].map(([r, chain]) => [r, chain.map(id => points.get(id))]));
  return {boxes, edges, route, width: SD.pad * 2 + cols.length * SD.width + Math.max(0, cols.length - 1) * SD.gapX, height: SD.pad * 2 + tallest};
}

// The y of a field's row; a column the diagram does not show anchors at the header.
function fieldAnchor(box, column) {
  const i = box.fields.findIndex(f => f.name === column);
  return i < 0 ? box.y + SD.header / 2 : box.y + SD.header + i * SD.row + SD.row / 2;
}

function edgePath(source, target, relationship, waypoints) {
  const sy = fieldAnchor(source, relationship.fromColumns[0]), ty = fieldAnchor(target, relationship.toColumns[0]);
  const sx = source.x + SD.width;
  if (target.x > sx) {
    // Curve between columns and run straight through the gap each waypoint keeps in a crossed column.
    let d = `M${sx} ${sy}`, at = {x: sx, y: sy};
    [...waypoints, {x: target.x, y: ty, last: true}].forEach(p => {
      const bend = (p.x - at.x) / 2;
      d += `C${at.x + bend} ${at.y} ${p.x - bend} ${p.y} ${p.x} ${p.y}`;
      if (!p.last) d += `H${p.x + SD.width}`;
      at = {x: p.x + SD.width, y: p.y};
    });
    return d;
  }
  // Same column or pointing back (a cycle): loop around the right-hand side.
  const tx = target.x + SD.width, out = Math.max(sx, tx) + 64;
  return `M${sx} ${sy}C${out} ${sy} ${out} ${ty} ${tx} ${ty}`;
}

function diagramMarkers(defs, id) {
  // Crow's foot on the many side (path start) and a bar on the one side (path end).
  [['many', 'M3 6L12 1M3 6L12 11M3 6H12', 'auto-start-reverse'], ['one', 'M6 1V11', 'auto']].forEach(([kind, d, orient]) => {
    ['', '-on'].forEach(state => {
      const marker = svgNode('marker', {id: `${id}-${kind}${state}`, viewBox: '0 0 12 12', refX: 12, refY: 6, markerWidth: 12, markerHeight: 12, markerUnits: 'userSpaceOnUse', orient}, `sd-marker${state ? ' sd-marker-on' : ''}`);
      marker.append(svgNode('path', {d})); defs.append(marker);
    });
  });
}

function semanticDiagram(model, {onOpenTable} = {}) {
  const id = `sd${++diagramCount}`, wrap = element('div', undefined, 'semantic-diagram sd-fit');
  const names = new Set(model.datasets.map(d => d.name));
  const joinedBy = new Map(model.datasets.map(d => [d.name, new Set()]));
  model.relationships.forEach(r => {
    r.fromColumns.forEach(c => joinedBy.get(r.from)?.add(c));
    r.toColumns.forEach(c => joinedBy.get(r.to)?.add(c));
  });
  const layout = semanticLayout(model.datasets, model.relationships, joinedBy);
  const svg = svgNode('svg', {viewBox: `0 0 ${layout.width} ${layout.height}`, width: layout.width, height: layout.height, role: 'img'});
  svg.setAttribute('aria-label', `Diagram of ${model.name}: ${model.datasets.length} datasets and ${layout.edges.length} relationships. The Datasets and Metrics tabs list the same information as text.`);
  const defs = svgNode('defs'); diagramMarkers(defs, id); svg.append(defs);
  const edgeLayer = svgNode('g'), boxLayer = svgNode('g');
  svg.append(edgeLayer, boxLayer);

  const edges = layout.edges.map(r => {
    const path = svgNode('path', {d: edgePath(layout.boxes.get(r.from), layout.boxes.get(r.to), r, layout.route.get(r)), 'marker-start': `url(#${id}-many)`, 'marker-end': `url(#${id}-one)`}, 'sd-edge');
    const title = svgNode('title'); title.textContent = `${r.name || 'Relationship'}: ${r.from} (${r.fromColumns.join(', ')}) → ${r.to} (${r.toColumns.join(', ')})`;
    const group = svgNode('g', {}, 'sd-link'); group.append(title, path, svgNode('path', {d: path.getAttribute('d')}, 'sd-hit'));
    edgeLayer.append(group);
    return {relationship: r, group, path};
  });
  const boxes = new Map();
  layout.boxes.forEach((box, name) => {
    const d = box.dataset, g = svgNode('g', {transform: `translate(${box.x} ${box.y})`}, 'sd-box');
    const title = svgNode('title'); title.textContent = [d.name, d.source, d.description].filter(Boolean).join('\n');
    g.append(title, svgNode('rect', {width: SD.width, height: box.height, rx: 4}, 'sd-frame'), svgNode('rect', {width: SD.width, height: SD.header, rx: 4}, 'sd-head'));
    g.append(svgText(12, 20, d.name, 'sd-name', SD.width - 24), svgText(12, 37, d.source || 'No source', 'sd-source', SD.width - 24));
    box.fields.forEach((f, i) => {
      const y = SD.header + i * SD.row, joined = joinedBy.get(name).has(f.name), key = d.primaryKey.includes(f.name);
      const row = svgNode('g', {transform: `translate(0 ${y})`}, `sd-field${joined ? ' sd-joined' : ''}`);
      if (i) row.append(svgNode('line', {x1: 0, x2: SD.width, y1: 0, y2: 0}, 'sd-rule'));
      const tag = key ? 'PK' : model.relationships.some(r => r.from === name && r.fromColumns.includes(f.name)) ? 'FK' : f.dimension ? '◆' : '';
      if (tag) row.append(svgText(12, 15, tag, key ? 'sd-tag sd-key' : 'sd-tag'));
      const type = f.datatype ? svgText(SD.width - 12, 15, f.datatype.toLowerCase(), 'sd-type', 80) : null;
      row.append(svgText(40, 15, f.name, 'sd-column', SD.width - 52 - (type ? Math.min(80, f.datatype.length * SD.char) + 8 : 0)));
      if (type) row.append(type);
      g.append(row);
    });
    if (box.hidden) g.append(svgText(40, SD.header + box.fields.length * SD.row + 15, `+${box.hidden} more ${box.hidden === 1 ? 'field' : 'fields'}`, 'sd-more'));
    if (d.table && onOpenTable) {
      // The header opens the dataset's table, like "Open table →" on the Datasets tab.
      g.classList.add('sd-open'); g.setAttribute('tabindex', '0'); g.setAttribute('role', 'button');
      g.setAttribute('aria-label', `Open table ${[...d.table.namespace, d.table.name].join('.')}`);
      g.addEventListener('click', () => onOpenTable(d));
      g.addEventListener('keydown', event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpenTable(d); } });
    }
    boxLayer.append(g); boxes.set(name, g);
  });

  // Hover or focus highlights a dataset with its relationships, or a relationship with its two datasets.
  function highlight(active) {
    svg.classList.toggle('sd-focus', Boolean(active));
    boxes.forEach((g, name) => g.classList.toggle('sd-on', Boolean(active?.names.has(name))));
    edges.forEach(e => {
      const on = Boolean(active?.edges.has(e));
      e.group.classList.toggle('sd-on', on);
      e.path.setAttribute('marker-start', `url(#${id}-many${on ? '-on' : ''})`);
      e.path.setAttribute('marker-end', `url(#${id}-one${on ? '-on' : ''})`);
    });
  }
  boxes.forEach((g, name) => {
    const linked = new Set(edges.filter(e => e.relationship.from === name || e.relationship.to === name));
    const active = {edges: linked, names: new Set([name, ...[...linked].flatMap(e => [e.relationship.from, e.relationship.to])])};
    ['pointerenter', 'focus'].forEach(type => g.addEventListener(type, () => highlight(active)));
    ['pointerleave', 'blur'].forEach(type => g.addEventListener(type, () => highlight(null)));
  });
  edges.forEach(e => {
    const active = {edges: new Set([e]), names: new Set([e.relationship.from, e.relationship.to])};
    e.group.addEventListener('pointerenter', () => highlight(active));
    e.group.addEventListener('pointerleave', () => highlight(null));
  });

  const toolbar = element('div', undefined, 'sd-toolbar');
  const legend = element('p', 'PK primary key · FK join column · ◆ dimension · crow’s foot marks the many side. Hover a dataset to follow its relationships.', 'hint');
  const size = element('button', 'Actual size', 'quiet');
  size.addEventListener('click', () => { const fit = wrap.classList.toggle('sd-fit'); size.textContent = fit ? 'Actual size' : 'Fit to width'; });
  toolbar.append(legend, size);
  const canvas = element('div', undefined, 'sd-canvas'); canvas.append(svg);
  wrap.append(toolbar, canvas);
  const dangling = model.relationships.filter(r => !names.has(r.from) || !names.has(r.to) || r.from === r.to);
  if (dangling.length) wrap.append(element('p', `Not drawn: ${dangling.map(r => `${r.name || 'a relationship'} (${r.from} → ${r.to})`).join('; ')}. A relationship is drawn only between two different datasets of this model.`, 'hint'));
  return wrap;
}
