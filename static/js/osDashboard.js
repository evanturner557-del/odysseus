// CEO dashboard for Autonomous OS V1. Numbers come from /api/os/dashboard.
export default function initOsDashboard() {
  const panel = document.getElementById('os-dashboard-panel');
  if (!panel) return { open: openOs, close: closeOs };

  const $ = (sel) => panel.querySelector(sel);

  async function api(path, opts) {
    const res = await fetch(path, {
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', ...(opts && opts.headers) },
      ...opts,
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (_) { data = { raw: text }; }
    if (!res.ok) {
      const detail = (data && (data.detail || data.error)) || res.statusText;
      throw new Error(detail);
    }
    return data;
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function card(d) {
    const opts = Array.isArray(d.options) ? d.options : [];
    const buttons = opts.map((o) => {
      const id = o.id || o;
      const label = o.label || o;
      return `<button type="button" class="os-decision-btn" data-decision="${esc(id)}" data-approval="${esc(d.related_approval_id || '')}">${esc(label)}</button>`;
    }).join('');
    return `<article class="os-attention-card">
      <header>
        <span class="os-urgency">U${esc(d.urgency)}</span>
        <span class="os-importance">I${esc(d.importance)}</span>
        <strong>${esc(d.what)}</strong>
      </header>
      <p class="os-why">${esc(d.why)}</p>
      <p><em>Required decision:</em> ${esc(d.what)}</p>
      <p><em>Recommendation:</em> ${esc(d.recommendation)}</p>
      <p><em>If you wait:</em> ${esc(d.consequence_of_waiting)}</p>
      <p class="os-thesis"><em>Thesis:</em> ${esc(d.thesis || '')}</p>
      <p class="os-counter"><em>Counter:</em> ${esc(d.counterargument || '')}</p>
      <div class="os-card-actions">${buttons}</div>
    </article>`;
  }

  async function refresh() {
    const data = await api('/api/os/dashboard');
    const h = data.health || {};
    $('#os-health-status').textContent = h.stopped ? 'STOPPED' : (h.paused ? 'PAUSED' : (h.status || 'unknown'));
    $('#os-health-status').dataset.state = h.stopped ? 'stopped' : (h.paused ? 'paused' : 'running');
    $('#os-health-level').textContent = `Autonomy ${h.autonomy_level}`;
    $('#os-health-cycles').textContent = `${h.cycle_count || 0} cycles`;
    $('#os-health-pending').textContent = `${h.pending_approvals || 0} awaiting you`;
    const spent = (data.costs && data.costs.spent_cents) || 0;
    $('#os-health-cost').textContent = `${(spent / 100).toFixed(2)} USD spent (from events)`;

    const agents = data.agents || [];
    $('#os-agents').innerHTML = agents.length
      ? agents.map((a) => `<li>${esc(a.role)} — ${esc(a.status)}</li>`).join('')
      : '<li>No agent runs yet</li>';

    const opps = data.opportunities || [];
    $('#os-opportunities').innerHTML = opps.length
      ? opps.map((o) => `<li><code>${esc(o.public_id)}</code> ${esc(o.title)} <span class="os-score">${esc(o.score)}</span></li>`).join('')
      : '<li>None yet — run a cycle</li>';

    const exps = data.experiments || [];
    $('#os-experiments').innerHTML = exps.length
      ? exps.map((e) => `<li>${esc(e.name)} — ${esc(e.status)}${e.result && e.result.usable_hits != null ? ` (${e.result.usable_hits} hits)` : ''}</li>`).join('')
      : '<li>None yet</li>';

    const attn = data.attention || [];
    $('#os-attention').innerHTML = attn.length
      ? attn.map(card).join('')
      : '<p class="os-empty">No human decisions waiting.</p>';

    const metrics = data.metrics || [];
    $('#os-metrics').innerHTML = metrics.length
      ? metrics.slice(0, 12).map((m) => `<li>${esc(m.name)} = ${esc(m.value)} ${esc(m.unit || '')}</li>`).join('')
      : '<li>No metrics recorded</li>';

    const nba = data.next_action || {};
    $('#os-next-action').textContent = `${nba.action || '—'} — ${nba.reason || ''}`;

    const events = data.events || [];
    $('#os-events').innerHTML = events.length
      ? events.slice(0, 15).map((e) => `<li><time>${esc((e.timestamp || '').replace('T', ' ').slice(0, 19))}</time> ${esc(e.event_type)} ${esc(e.status || '')}</li>`).join('')
      : '<li>No events</li>';
  }

  async function openOs() {
    panel.classList.add('open');
    panel.setAttribute('aria-hidden', 'false');
    try { await refresh(); } catch (err) {
      $('#os-attention').innerHTML = `<p class="os-error">${esc(err.message)}</p>`;
    }
  }

  function closeOs() {
    panel.classList.remove('open');
    panel.setAttribute('aria-hidden', 'true');
    if (window._restoreSidebarIfRouteCollapsed) window._restoreSidebarIfRouteCollapsed();
  }

  panel.querySelector('#os-close-btn')?.addEventListener('click', closeOs);
  panel.querySelector('#os-refresh-btn')?.addEventListener('click', () => refresh().catch((e) => alert(e.message)));
  panel.querySelector('#os-cycle-btn')?.addEventListener('click', async () => {
    try {
      await api('/api/os/cycle', { method: 'POST', body: JSON.stringify({ use_mock_search: true }) });
      await refresh();
    } catch (e) { alert(e.message); }
  });
  panel.querySelector('#os-stop-btn')?.addEventListener('click', async () => {
    try {
      await api('/api/os/stop', { method: 'POST', body: '{}' });
      await refresh();
    } catch (e) { alert(e.message); }
  });
  panel.querySelector('#os-start-btn')?.addEventListener('click', async () => {
    try {
      await api('/api/os/start', { method: 'POST', body: '{}' });
      await refresh();
    } catch (e) { alert(e.message); }
  });
  panel.querySelector('#os-pause-btn')?.addEventListener('click', async () => {
    try {
      await api('/api/os/pause', { method: 'POST', body: '{}' });
      await refresh();
    } catch (e) { alert(e.message); }
  });

  panel.addEventListener('click', async (ev) => {
    const btn = ev.target.closest('.os-decision-btn');
    if (!btn) return;
    const approval = btn.getAttribute('data-approval');
    const decision = btn.getAttribute('data-decision');
    if (!approval) { alert('No approval id on this card'); return; }
    const path = decision === 'reject'
      ? `/api/os/approvals/${approval}/reject`
      : decision === 'override'
        ? `/api/os/approvals/${approval}/override`
        : `/api/os/approvals/${approval}/approve`;
    const note = decision === 'override' ? (prompt('Override note (required)') || '') : '';
    if (decision === 'override' && !note.trim()) return;
    try {
      await api(path, { method: 'POST', body: JSON.stringify({ note }) });
      await refresh();
    } catch (e) { alert(e.message); }
  });

  return { open: openOs, close: closeOs, refresh };
}
