const fallback = {
  state: 'WARMING',
  stateDetail: 'Awaiting the next verified 100Ways monitor run.',
  pending: 0,
  threshold: 50,
  remaining: 50,
  verified: 'Not yet recorded',
  runUrl: 'https://github.com/nastechresearch/100Ways/actions',
  upstreamSha: '—',
  baselineSha: '—',
  candidateSha: '—',
  syncState: 'ON HOLD',
  gates: [
    ['Direct Hermes fetch', 'WAITING'],
    ['Source delta audit', 'WAITING'],
    ['Branding and owned assets', 'WAITING'],
    ['Final tree conformance', 'WAITING'],
    ['Candidate test suite', 'WAITING'],
    ['#344 review PR', 'HUMAN REVIEW']
  ],
  history: [{ title: 'No public run evidence has been published yet.', detail: 'The site is ready for the first sanitized status payload.' }]
};

const $ = id => document.getElementById(id);
const fmt = value => value || '—';
function render(data) {
  const d = { ...fallback, ...data };
  $('state').textContent = fmt(d.state);
  $('state-detail').textContent = fmt(d.stateDetail);
  $('pending').textContent = `${d.pending}/${d.threshold}`;
  $('remaining').textContent = `${d.remaining} commit(s) remaining`;
  $('verified').textContent = fmt(d.verified);
  $('run-link').innerHTML = d.runUrl && d.runUrl !== '—' ? `<a href="${d.runUrl}" target="_blank" rel="noreferrer">Open Actions run ↗</a>` : 'No run link';
  $('upstream-sha').textContent = fmt(d.upstreamSha);
  $('baseline-sha').textContent = fmt(d.baselineSha);
  $('candidate-sha').textContent = fmt(d.candidateSha);
  $('sync-state').textContent = fmt(d.syncState);
  $('progress-value').textContent = `${d.pending}/${d.threshold}`;
  $('progress-copy').textContent = d.pending >= d.threshold ? 'Automatic full sync enabled' : `${d.remaining} more commit(s) before full sync`;
  $('progress-bar').style.width = `${Math.min(100, Math.round((Number(d.pending) / Math.max(1, Number(d.threshold))) * 100))}%`;
  const live = $('live-badge'); live.textContent = d.state === 'PASS' || d.state === 'CURRENT' ? 'Verified status' : 'Public status'; live.className = `badge ${d.state === 'PASS' || d.state === 'CURRENT' ? 'good' : 'warn'}`;
  $('gates-grid').innerHTML = d.gates.map(([name, status]) => `<article class="gate"><div class="gate-top"><strong>${name}</strong><span class="status">${status}</span></div><small>${status === 'PASS' ? 'Evidence recorded and bound to this run.' : status === 'HUMAN REVIEW' ? 'Requires a human decision; no automatic publication.' : 'Awaiting the corresponding verified run.'}</small></article>`).join('');
  $('history-count').textContent = `${d.history.length} recorded event${d.history.length === 1 ? '' : 's'}`;
  $('history-list').innerHTML = d.history.map(event => `<article class="event"><strong>${event.title}</strong><small>${event.detail || ''}</small></article>`).join('');
}
async function load() {
  try {
    const response = await fetch('./status.json', { cache: 'no-store' });
    if (!response.ok) throw new Error('status unavailable');
    render(await response.json());
  } catch (error) {
    render(fallback);
  }
}
load();
