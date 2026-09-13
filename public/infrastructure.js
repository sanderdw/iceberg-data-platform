let infrastructureTimer;
function stopInfrastructure() { clearTimeout(infrastructureTimer); }
const metricNumber = value => Number.isFinite(value) ? new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 }).format(value) : '—';
function metricBytes(value) {
  if (!Number.isFinite(value)) return '—';
  const unit = value > 0 ? Math.min(4, Math.floor(Math.log(value) / Math.log(1024))) : 0;
  return `${metricNumber(value / 1024 ** unit)} ${['B', 'KiB', 'MiB', 'GiB', 'TiB'][unit]}`;
}
const metricTime = value => value ? new Date(value).toLocaleTimeString('en-GB') : 'awaiting sample';
function metricState(section, maxAge = 60) {
  if (!section || section.status === 'collecting') return 'Collecting';
  if (section.status !== 'online') return 'Unavailable';
  return !section.updatedAt || Date.now() - Date.parse(section.updatedAt) > maxAge * 1000 ? 'Stale' : 'Live';
}
function metricBadge(status) {
  return `<span class="metric-badge ${['Live', 'online', 'running'].includes(status) ? 'metric-good' : 'metric-warning'}">${esc(status)}</span>`;
}
function probeChart(history, name) {
  if (!history?.length) return '<span class="subtle">Awaiting samples</span>';
  const max = Math.max(1, ...history.map(point => point.latencyMs || 0));
  return `<svg class="probe-chart" viewBox="0 0 240 32" role="img" aria-label="${esc(name)}: last ${history.length} probe results; failures marked with a cross"><title>Probe latency; scale 0–${metricNumber(max)} ms</title>${history.map((point, index) => {
    const x = index * 4 + 1;
    return point.ok ? `<line x1="${x}" x2="${x}" y1="31" y2="${31 - Math.max(2, point.latencyMs / max * 28)}" class="probe-ok"/>` : `<path d="M${x - 1} 12l3 6m0-6l-3 6" class="probe-failed"/>`;
  }).join('')}</svg>`;
}
function renderInfrastructure() {
  const target = $('#page-content');
  target.innerHTML = `<section class="infrastructure"><div class="panel-heading"><div><h2>Stack overview</h2><p id="monitor-updated" role="status">Collecting infrastructure metrics…</p></div><button class="button" id="monitor-refresh">${icon('refresh')}Refresh</button></div><div id="monitor-error" role="alert"></div><div id="monitor-content" aria-busy="true"><p class="explorer-note">Waiting for the monitoring service…</p></div></section>`;
  const content = $('#monitor-content'), error = $('#monitor-error'), updated = $('#monitor-updated'), refresh = $('#monitor-refresh');
  let snapshot, busy = false, bucketSort = 'bytes';
  const current = () => content.isConnected && state.data && state.page === 'infrastructure';
  const stamp = (section, age) => `${metricBadge(metricState(section, age))}<span class="subtle">Updated ${esc(metricTime(section?.updatedAt))}</span>`;
  const card = (label, value, note) => `<article class="metric-card"><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`;
  function draw() {
    const { services, containers, postgres, storage, disk } = snapshot;
    const pg = postgres?.data || {}, buckets = storage?.data?.buckets || [], rows = containers?.data || [];
    const probes = services?.data || [];
    const pgLive = metricState(postgres) === 'Live';
    const healthy = probes.filter(row => row.status === 'online').length + (pgLive ? 1 : 0);
    const running = rows.filter(row => row.state === 'running');
    const cpu = running.length && running.every(row => Number.isFinite(row.cpuPercent)) ? running.reduce((sum, row) => sum + row.cpuPercent, 0) : null;
    const memory = running.length && running.every(row => Number.isFinite(row.memoryBytes)) ? running.reduce((sum, row) => sum + row.memoryBytes, 0) : null;
    content.innerHTML = `<div class="metric-cards">
      ${card('Healthy services', metricState(services) === 'Live' ? `${healthy} / ${probes.length + 1}` : '—', `${metricState(services)} · includes PostgreSQL`)}
      ${card('Container CPU', Number.isFinite(cpu) ? `${metricNumber(cpu)}%` : '—', `${metricState(containers)} · 100% = one CPU core`)}
      ${card('Container memory', metricBytes(memory), `${metricState(containers)} · excludes reclaimable file cache`)}
      ${card('Managed bucket data', metricBytes(storage?.data?.bytes), `${metricState(storage, 360)} · ${metricNumber(storage?.data?.objects)} current objects`)}
    </div>
    <section class="metric-section"><div class="metric-heading"><h3>Service availability</h3>${stamp(services)}</div>
      <div class="table-scroll"><table><thead><tr><th>Service</th><th>Status</th><th>Probe latency</th><th>Successful probes</th><th>Recent checks</th></tr></thead><tbody>
      ${probes.map(row => `<tr><td><strong>${esc(row.name)}</strong><small class="subtle">Checked ${esc(metricTime(row.checkedAt))}</small></td><td>${metricBadge(metricState(services) === 'Live' ? row.status : metricState(services))}</td><td>${metricNumber(row.latencyMs)} ms</td><td>${metricNumber(row.availabilityPercent)}% <small class="subtle">${row.samples} checks</small></td><td>${probeChart(row.history, row.name)}</td></tr>`).join('')}
      <tr><td>PostgreSQL<small class="subtle">Connection + statistics query</small></td><td>${metricBadge(metricState(postgres))}</td><td>${metricNumber(pg.latencyMs)} ms</td><td>—</td><td class="subtle">Sampled every 15 seconds</td></tr>
      </tbody></table></div><p class="subtle metric-note">Health probes measure reachability from the collector, not application request latency. Up to 60 checks are retained in memory; history resets when monitoring restarts.</p>
    </section>
    <div class="metric-columns"><section class="metric-section"><div class="metric-heading"><h3>PostgreSQL metadata</h3>${stamp(postgres)}</div><dl class="metric-details">
      ${[['Database size', metricBytes(pg.bytes)], ['Server connections / limit', `${metricNumber(pg.connections)} / ${metricNumber(pg.maxConnections)}`], ['Active / idle in Polaris', `${metricNumber(pg.active)} / ${metricNumber(pg.idle)}`], ['Transactions / second', metricNumber(pg.transactionsPerSecond)], ['Blocked sessions', metricNumber(pg.blocked)], ['Deadlocks since stats reset', metricNumber(pg.deadlocks)]].map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}
      </dl><p class="subtle metric-note">Catalog metadata only. Iceberg table data lives in object storage. Connection counts exclude this collector.</p></section>
      <section class="metric-section"><div class="metric-heading"><h3>Storage filesystem</h3>${stamp(disk)}</div><dl class="metric-details">${[['Capacity', metricBytes(disk?.data?.totalBytes)], ['Used', metricBytes(disk?.data?.usedBytes)], ['Available', metricBytes(disk?.data?.freeBytes)]].map(([label, value]) => `<div><dt>${label}</dt><dd>${value}</dd></div>`).join('')}</dl><p class="subtle metric-note">Filesystem backing RustFS. Used space can include other volumes on the same filesystem.</p></section></div>
    <section class="metric-section"><div class="metric-heading"><h3>Compute resources</h3>${stamp(containers)}</div><div class="table-scroll"><table><thead><tr><th>Container</th><th>State</th><th>CPU</th><th>Memory / limit</th><th>Uptime</th><th>Restarts</th></tr></thead><tbody>${rows.map(row => {
      const uptime = row.state === 'running' && row.startedAt ? Math.max(0, (Date.now() - Date.parse(row.startedAt)) / 60000) : null;
      return `<tr><td>${esc(row.name)}</td><td>${metricBadge(metricState(containers) !== 'Live' ? metricState(containers) : row.status === 'unavailable' ? 'Unavailable' : row.state)}</td><td>${metricNumber(row.cpuPercent)}%</td><td>${metricBytes(row.memoryBytes)} / ${metricBytes(row.memoryLimitBytes)}</td><td>${Number.isFinite(uptime) ? `${metricNumber(uptime < 60 ? uptime : uptime / 60)} ${uptime < 60 ? 'min' : 'hr'}` : '—'}</td><td>${metricNumber(row.restarts)}</td></tr>`;
    }).join('') || '<tr><td colspan="6">No container samples available.</td></tr>'}</tbody></table></div><p class="subtle metric-note">Stack containers only. An uncapped container shows the memory available from Docker as its limit.</p></section>
    <section class="metric-section"><div class="metric-heading"><h3>Buckets by database</h3>${stamp(storage, 360)}<select id="bucket-sort" aria-label="Sort buckets"><option value="bytes">Largest first</option><option value="database">Database name</option></select></div><div class="table-scroll"><table><thead><tr><th>Database / bucket</th><th>Team</th><th>Current object size</th><th>Objects</th><th>Status</th></tr></thead><tbody>${[...buckets].sort((a, b) => bucketSort === 'bytes' ? (b.bytes ?? -1) - (a.bytes ?? -1) : a.database.localeCompare(b.database)).map(row => `<tr><td>${esc(row.database)}<small class="subtle">${esc(row.bucket)}</small></td><td>${esc(state.data.teams.find(team => team.id === row.team)?.name || row.team)}</td><td>${metricBytes(row.bytes)}</td><td>${metricNumber(row.objects)}</td><td>${metricBadge(metricState(storage, 360) === 'Live' ? row.status : metricState(storage, 360))}</td></tr>`).join('') || `<tr><td colspan="5">${storage?.data ? 'No managed buckets yet.' : 'No bucket samples available.'}</td></tr>`}</tbody></table></div><p class="subtle metric-note">Scanned every 5 minutes. Current objects only; previous versions and incomplete uploads are excluded. Failed or oversized scans are unavailable, never counted as empty.</p></section>`;
    const sort = $('#bucket-sort'); sort.value = bucketSort;
    sort.onchange = () => { bucketSort = sort.value; draw(); };
  }
  async function poll() {
    stopInfrastructure();
    if (!current() || busy) return;
    busy = true; refresh.disabled = true;
    try {
      const result = await api('/infrastructure');
      if (!current()) return;
      snapshot = result; error.textContent = ''; draw();
      updated.textContent = `Received ${metricTime(result.sampledAt)} · auto-refresh every 15 seconds`;
    } catch (e) {
      if (!current()) return;
      error.className = 'alert'; error.textContent = e.message;
      updated.textContent = snapshot ? 'Connection lost · showing last received readings' : 'Monitoring unavailable · retrying every 15 seconds';
      if (snapshot) {
        snapshot = { ...snapshot, ...Object.fromEntries(['services', 'postgres', 'containers', 'storage', 'disk'].map(key => [key, { ...snapshot[key], status: 'unavailable' }])) };
        draw();
      } else content.innerHTML = '<p class="explorer-note">No infrastructure readings available yet.</p>';
    } finally {
      busy = false; refresh.disabled = false; content.setAttribute('aria-busy', 'false');
      if (current()) infrastructureTimer = setTimeout(poll, 15000);
    }
  }
  refresh.onclick = poll;
  poll();
}
