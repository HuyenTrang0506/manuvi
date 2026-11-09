/* Shared helpers: config, API calls, escaping, protected-code pills. No external dependencies. */
const CFG = JSON.parse(document.getElementById('cfg').textContent);
const $ = (s, r = document) => r.querySelector(s);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const reduceMotion = matchMedia('(prefers-reduced-motion: reduce)').matches;

let toastTimer;
function say(text, ms = 4000) {
  const t = $('#toast') || $('#msg');
  if (!t) return;
  t.textContent = text;
  t.hidden = false;
  clearTimeout(toastTimer);
  if (t.id === 'toast') toastTimer = setTimeout(() => { t.hidden = true; t.textContent = ''; }, ms);
}

async function api(path, opt = {}) {
  const headers = {'X-Manuvi-Token': CFG.token};
  let body = opt.body;
  if (opt.raw) headers['Content-Type'] = opt.raw;
  else if (opt.method && opt.method !== 'GET') { headers['Content-Type'] = 'application/json'; body = JSON.stringify(opt.body ?? {}); }
  const r = await fetch(path, {method: opt.method || 'GET', headers, body, credentials: 'same-origin'});
  if (r.status === 401) { location.replace('/'); throw new Error('Your session ended. Sign in again.'); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'Request failed (' + r.status + ')');
  return j;
}

/* Protected codes: display-only highlighting of part numbers, wire codes, values with units and figure refs.
   The underlying text is never changed; everything is HTML-escaped first. */
const CODE_RE = /(⟦P\d+⟧|\b[A-Z]{2}\d{2}-[A-Z0-9][A-Z0-9-]*\b|\b\d+(?:[.,]\d+)?\s?(?:N·m|Nm|N\.m|kPa|MPa|bar|psi|rpm|mm|cm|kg|V|A|Ω|°C|Hz)\b|\bFig\.\s?\d+(?:-\d+)?)/g;
function withCodes(text) {
  return String(text ?? '').split(CODE_RE).map((part, i) => i % 2 ? `<span class="code">${esc(part)}</span>` : esc(part)).join('');
}

const STATE_LABEL = {queued: 'Waiting for a worker', running: 'Translating', needs_review: 'Needs your approval',
  done: 'Ready', failed: 'Stopped with an error', cancelled: 'Cancelled'};
const ERROR_COPY = {
  INTAKE: "The section list doesn't match the PDF pages. Check the sections and the page map, then upload the manual again.",
  BUDGET: 'The cost cap for this job was reached. Raise the cap to continue.',
  NO_VI_FONT: 'The Vietnamese PDF could not be drawn because the worker has no Vietnamese font. The Excel files are ready.',
  RENDER_FAILED: 'The Vietnamese PDF could not be drawn. The Excel files are ready.',
  NO_TEXT_PAGES: 'No text pages were found in the chosen sections. Scanned pages need OCR first.'};
const errorCopy = (c) => !c ? '' : (ERROR_COPY[c] || 'The worker stopped unexpectedly. Try the upload again or ask for help with the code ' + c + '.');
const stateTag = (s) => `<span class="st" data-s="${esc(s)}">${esc(STATE_LABEL[s] || s)}</span>`;
function pageStrip(states) {
  const e = Object.entries(states || {});
  if (!e.length) return '';
  const word = {done: 'translated', review: 'needs attention', pending: 'waiting'};
  return `<div class="strip" role="list" aria-label="Pages">${e.map(([l, s]) =>
    `<span class="pg" role="listitem" data-s="${esc(s)}" title="${esc(l)}: ${word[s] || s}" aria-label="Page ${esc(l)}: ${word[s] || s}">${esc(l)}</span>`).join('')}</div>
   <div class="legend" aria-hidden="true"><span class="l-done">translated</span><span class="l-review">needs attention</span><span>waiting</span></div>`;
}
