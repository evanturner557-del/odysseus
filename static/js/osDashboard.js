// CEO / Factory Command dashboard for Autonomous OS V1.
// Numbers come from /api/os/dashboard (including factory.*). No invented balances.
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

  function gbp(n) {
    const v = Number(n || 0);
    return `£${v.toFixed(2)}`;
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

  function renderPipeline(factory) {
    const pipe = (factory && factory.pipeline) || {};
    const stages = pipe.stages || [];
    const byStage = pipe.by_stage || {};
    const counts = pipe.counts || {};
    if (!stages.length) {
      $('#os-factory-pipeline').innerHTML = '<p class="os-empty">No conveyor stages configured.</p>';
      return;
    }
    $('#os-factory-pipeline').innerHTML = stages.map((stage) => {
      const units = byStage[stage] || [];
      const items = units.length
        ? units.map((u) => {
            const kill = u.kill_date ? ` · kill ${esc((u.kill_date || '').slice(0, 10))}` : '';
            return `<li><code>${esc(u.unit_id)}</code> ${esc(u.name || '')} <span class="os-muted">${esc(u.class)}${kill}</span></li>`;
          }).join('')
        : '<li class="os-muted">—</li>';
      return `<div class="os-stage" data-stage="${esc(stage)}">
        <header><strong>${esc(stage)}</strong> <span class="os-count">${esc(counts[stage] || 0)}</span></header>
        <ul>${items}</ul>
      </div>`;
    }).join('');

    const kills = pipe.kill_dates || [];
    $('#os-factory-kills').innerHTML = kills.length
      ? kills.map((k) => `<li>Kill <time>${esc((k.kill_date || '').slice(0, 10))}</time> — <code>${esc(k.unit_id)}</code> ${esc(k.name)} (${esc(k.stage)})</li>`).join('')
      : '<li class="os-muted">No kill dates set</li>';
  }

  function renderHoldco(factory) {
    const pnl = (factory && factory.holdco_pnl) || {};
    const cap = pnl.capital_pool || {};
    const totals = pnl.totals || {};
    const per = pnl.per_unit || [];
    const note = cap.note || 'Internal tracked capital only — not linked to Stripe or bank';
    const unitRows = per.length
      ? per.map((u) => {
          const who = u.class === 'charity'
            ? `donors ${esc(u.donors || 0)}`
            : `customers ${esc(u.customers || 0)}`;
          return `<li><code>${esc(u.unit_id)}</code> ${esc(u.name)} — rev ${gbp(u.revenue_gbp)} / cost ${gbp(u.cost_gbp)} / MRR ${gbp(u.mrr_gbp)} (${who})</li>`;
        }).join('')
      : '<li class="os-muted">No units yet</li>';
    $('#os-factory-holdco').innerHTML = `
      <p class="os-capital"><strong>Capital pool:</strong> ${gbp(cap.remaining_gbp)} remaining
        (limit ${gbp(cap.limit_gbp)}, spent ${gbp(cap.spent_gbp)})</p>
      <p class="os-muted">${esc(note)}. Stripe ${gbp(cap.stripe_balance_gbp)} · Bank ${gbp(cap.bank_balance_gbp)} (not linked).</p>
      <p><strong>Totals:</strong> rev ${gbp(totals.revenue_gbp)} · cost ${gbp(totals.cost_gbp)} · MRR ${gbp(totals.mrr_gbp)} · margin ${esc(totals.margin_pct)}%</p>
      <ul>${unitRows}</ul>`;
  }

  function renderBots(factory) {
    const bots = (factory && factory.bots) || [];
    $('#os-factory-bots').innerHTML = bots.length
      ? bots.map((b) => `<li><strong>${esc(b.name)}</strong> — ${esc(b.status)}${b.last_run_at ? ` <span class="os-muted">${esc((b.last_run_at || '').replace('T', ' ').slice(0, 19))}</span>` : ''}</li>`).join('')
      : '<li class="os-muted">No factory bots</li>';
  }

  function renderApprovals(factory) {
    const q = (factory && factory.approvals_queue) || {};
    const cats = q.categories || ['spend', 'external', 'irreversible'];
    const by = q.by_category || {};
    const counts = q.counts || {};
    $('#os-factory-approvals').innerHTML = cats.map((cat) => {
      const items = by[cat] || [];
      const lis = items.length
        ? items.map((a) => `<li><code>${esc(a.id).slice(0, 8)}</code> ${esc(a.action_name || a.tool_name || 'action')} — ${esc(a.reason || '')}</li>`).join('')
        : '<li class="os-muted">None</li>';
      return `<div class="os-approval-bucket" data-category="${esc(cat)}">
        <header><strong>${esc(cat)}</strong> <span class="os-count">${esc(counts[cat] || 0)}</span></header>
        <ul>${lis}</ul>
      </div>`;
    }).join('');
  }

  function renderFeed(factory) {
    const feed = (factory && factory.orchestrator_feed) || [];
    $('#os-factory-feed').innerHTML = feed.length
      ? feed.slice(0, 25).map((e) => `<li><time>${esc((e.timestamp || '').replace('T', ' ').slice(0, 19))}</time> ${esc(e.summary || e.event_type)} <span class="os-muted">${esc(e.actor || '')}</span></li>`).join('')
      : '<li class="os-muted">No orchestrator events yet</li>';
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
    const cur = (data.costs && data.costs.currency) || 'GBP';
    $('#os-health-cost').textContent = `${(spent / 100).toFixed(2)} ${cur} spent (tracked, not bank)`;

    const factory = data.factory || {};
    renderPipeline(factory);
    renderHoldco(factory);
    renderBots(factory);
    renderApprovals(factory);
    renderFeed(factory);

    const agents = data.agents || [];
    $('#os-agents').innerHTML = agents.length
      ? agents.map((a) => `<li>${esc(a.name || a.role)} — ${esc(a.status)}</li>`).join('')
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
      const attn = $('#os-attention');
      if (attn) attn.innerHTML = `<p class="os-error">${esc(err.message)}</p>`;
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
