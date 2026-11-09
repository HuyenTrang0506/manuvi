(() => {
  const main = $('#main');
  const P = CFG.prefix || '';           // '' locally, '/api/jobs/<id>' in the portal
  const A = (path) => (P ? P : '/api') + path;
  const isFounder = CFG.role === 'founder';
  const canReview = CFG.canReview !== false;
  $('#who').textContent = CFG.user ? `${CFG.user} (${CFG.role})` : '';
  if (CFG.portal) { $('#back').hidden = false; $('#home').setAttribute('href', '/'); }
  let keysOn = false;
  try { keysOn = localStorage.getItem('manuvi.keys') === '1'; } catch (_) {}
  const keysBox = $('#keys');
  keysBox.checked = keysOn;
  keysBox.onchange = () => { keysOn = keysBox.checked; try { localStorage.setItem('manuvi.keys', keysOn ? '1' : '0'); } catch (_) {} say(keysOn ? 'Keyboard shortcuts on.' : 'Keyboard shortcuts off.'); };
  let rows = [], sel = 0, cur = null, filters = {decision: 'pending'}, firstPaint = true;
  const skeleton = () => '<div aria-busy="true" aria-label="Loading">' + '<span class="sk tall"></span>'.repeat(3) + '</div>';

  function route() {
    const h = location.hash || '#/queue';
    document.querySelectorAll('.top nav a').forEach((a) => {
      if (a.getAttribute('href') === h.split('/').slice(0, 2).join('/')) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
    const p = h.slice(2).split('/');
    const run = ({queue: viewQueue, seg: () => viewSeg(decodeURIComponent(p[1] || '')), terms: viewTerms, status: viewStatus, audit: viewAudit}[p[0]] || viewQueue);
    Promise.resolve(run()).catch((e) => { main.innerHTML = `<div class="panel" role="alert"><h1>Cannot show this page</h1><p>${esc(e.message)}</p></div>`; }).finally(() => main.focus({preventScroll: true}));
  }
  addEventListener('hashchange', route);

  const hydrate = (root) => root.querySelectorAll('[data-v]').forEach((el) => el.style.setProperty('--v', el.dataset.v));
  const roleNote = () => (!isFounder && !canReview) ? '<p class="note">You can read everything here. The founder has not allowed approvals for your role on this manual.</p>' : (!isFounder ? '<p class="note">You can approve and comment on regular segments. Safety segments are approved by the founder only.</p>' : '');
  const pageChip = (r) => `<span class="pg" data-s="${r.status === 'ok' && r.decision === 'approved' ? 'done' : r.status === 'ok' ? 'pending' : 'review'}" title="Page ${esc(r.section)}-${esc(r.page)}">${esc(r.section ? r.section + '-' : '')}${esc(r.page)}</span>`;

  async function viewQueue() {
    document.title = 'Review queue - Manuvi';
    if (firstPaint) main.innerHTML = `<h1>Review queue</h1>${skeleton()}`;
    const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v));
    const [secs, list] = await Promise.all([api(A('/sections')), api(A('/queue?') + qs)]);
    rows = list; sel = Math.min(sel, Math.max(rows.length - 1, 0));
    const opt = (n, vals) => `<select id="f-${n}" aria-label="Filter by ${n}"><option value="">${n}: any</option>${vals.map((v) => `<option ${filters[n] === v ? 'selected' : ''}>${esc(v)}</option>`).join('')}</select>`;
    const rise = firstPaint ? ' rise' : '';
    firstPaint = false;
    main.innerHTML = `<h1>Review queue <span class="meta">${rows.length} segment${rows.length === 1 ? '' : 's'}</span></h1>${roleNote()}
      <div class="filters" role="search">${opt('status', ['error', 'needs_review', 'warn', 'ok'])}${opt('kind', ['signal', 'step', 'prose', 'table', 'label'])}${opt('section', secs)}${opt('decision', ['pending', 'approved', 'rejected'])}
        <input id="f-q" type="search" placeholder="Search text" aria-label="Search text" value="${esc(filters.q || '')}">
        ${canReview ? '<button id="bulk" type="button">Approve ticked segments</button>' : ''}</div>
      <p class="hint">Safety segments are never approved in bulk. Turn on keyboard shortcuts in the header to use <kbd>j</kbd> <kbd>k</kbd> to move and <kbd>Enter</kbd> to open.</p>
      ${rows.length ? `<ol class="queue">${rows.map((r, i) => `<li class="qrow${r.kind === 'signal' ? ' safety' : ''}${rise}" data-i="${i}" aria-current="${i === sel}">
        <span class="cb">${canReview && r.kind !== 'signal' ? `<input type="checkbox" data-id="${esc(r.id)}" aria-label="Tick ${esc(r.id)}">` : ''}</span>
        <a class="id" href="#/seg/${encodeURIComponent(r.id)}">${esc(r.id)}</a>${pageChip(r)}
        <span class="${r.kind === 'signal' ? 'signal' : ''}">${r.kind === 'signal' ? '⚠ Safety' : esc(r.kind)}</span>
        <span><span class="${esc(r.status)}">${esc(r.status.replace('_', ' '))}</span>, <span class="${esc(r.decision)}">${esc(r.decision)}</span></span>
        <span class="src" lang="en">${esc(r.source)}</span></li>`).join('')}</ol>`
        : `<div class="panel"><h2>Nothing in this view</h2><p>${filters.decision === 'pending' ? 'Every segment here has a decision.' : 'No segments match these filters.'} Clear a filter to see more.</p><button id="clear" type="button">Clear filters</button></div>`}`;
    for (const n of ['status', 'kind', 'section', 'decision']) $('#f-' + n).onchange = (e) => { filters[n] = e.target.value; sel = 0; viewQueue(); };
    $('#f-q').onchange = (e) => { filters.q = e.target.value; sel = 0; viewQueue(); };
    const clear = $('#clear'); if (clear) clear.onclick = () => { filters = {}; viewQueue(); };
    const bulk = $('#bulk');
    if (bulk) bulk.onclick = async () => {
      const ids = [...document.querySelectorAll('input[data-id]:checked')].map((c) => c.dataset.id);
      if (!ids.length) return say('Tick the segments to approve first.');
      try {
        const r = await api(A('/bulk-approve'), {method: 'POST', body: {ids}});
        say(`Approved ${r.approved.length}. ${r.skipped.length} skipped: safety, error or changed-code segments need an individual look.`);
        viewQueue();
      } catch (e) { say('Not approved: ' + e.message); }
    };
  }
  function moveSel(d) {
    if (!rows.length) return;
    sel = Math.max(0, Math.min(rows.length - 1, sel + d));
    document.querySelectorAll('.qrow').forEach((li, i) => { li.setAttribute('aria-current', i === sel); if (i === sel) li.scrollIntoView({block: 'nearest'}); });
  }

  async function viewSeg(id) {
    cur = await api(A('/segments/' + encodeURIComponent(id)));
    const s = cur, safety = s.kind === 'signal';
    const mayDecide = canReview && (!safety || isFounder);
    document.title = `${s.id} - Manuvi review`;
    main.innerHTML = `<p><a href="#/queue">&larr; Queue</a> &middot; ${s.prev ? `<a href="#/seg/${encodeURIComponent(s.prev)}">Previous</a>` : 'Previous'} &middot; ${s.next ? `<a href="#/seg/${encodeURIComponent(s.next)}">Next</a>` : 'Next'}</p>
      <h1>${esc(s.id)} ${pageChip(s)} <span class="badge ${esc(s.status)}">${esc(s.status.replace('_', ' '))}</span> <span class="badge ${esc(s.decision)}">${esc(s.decision)}</span></h1>
      <p class="meta">Page ${esc(s.page)}, section ${esc(s.section)}, kind ${esc(s.kind)}, risk ${(+s.risk).toFixed(2)}</p>
      ${safety ? `<div class="safety-note" role="note"><strong>Safety segment.</strong> ${isFounder ? 'Only you can approve it, one at a time. Check every word and number.' : 'Only the founder can approve this one. You can read it and leave a comment.'}</div>` : ''}
      ${s.issues.length ? `<h2>Issues</h2><ul>${s.issues.map((i) => `<li>${esc(typeof i === 'string' ? i : JSON.stringify(i))}</li>`).join('')}</ul>` : ''}
      <div class="pair">
        <section class="pane" aria-labelledby="hs"><h2 id="hs"><span class="lang">EN</span> Source</h2><div class="doc" lang="en">${withCodes(s.source)}</div></section>
        <section class="pane" aria-labelledby="ht"><h2 id="ht"><span class="lang vi">VI</span> <label for="tgt">Target${isFounder ? ', editable' : ''}</label></h2>
          ${isFounder ? `<textarea id="tgt" lang="vi">${esc(s.target)}</textarea><div class="section"><button id="save" type="button">Save edit</button></div>`
            : `<div class="doc" lang="vi" id="tgt">${withCodes(s.target)}</div>`}</section>
      </div>
      <div class="section actions">
        ${mayDecide ? '<button class="primary" id="b-a" type="button">Approve (a)</button>' : ''}
        ${isFounder ? '<button id="b-r" type="button">Reject (r)</button>' : ''}
        ${mayDecide ? '<button id="b-n" type="button">Flag for review (n)</button>' : ''}
      </div>
      ${canReview ? `<div class="section field"><label for="cm">Comment</label><input id="cm" type="text" maxlength="500" autocomplete="off"><p class="hint">Saved in the audit log with your name.</p><button id="b-c" type="button">Add comment</button></div>` : ''}
      <p class="hint">Shortcuts (turn them on in the header): <kbd>a</kbd> approve, <kbd>r</kbd> reject, <kbd>n</kbd> flag, <kbd>j</kbd> <kbd>k</kbd> next and previous.</p>`;
    const on = (sel_, fn) => { const el = $(sel_); if (el) el.onclick = fn; };
    on('#save', saveEdit); on('#b-a', () => decide('approve')); on('#b-r', () => decide('reject')); on('#b-n', () => decide('needs_review'));
    on('#b-c', async () => {
      const text = $('#cm').value.trim(); if (!text) return say('Write a comment first.');
      try { await api(A(`/segments/${encodeURIComponent(cur.id)}/comment`), {method: 'POST', body: {text}}); $('#cm').value = ''; say('Comment saved.'); }
      catch (e) { say('Not saved: ' + e.message); }
    });
  }
  async function saveEdit() { await api(A('/segments/' + encodeURIComponent(cur.id)), {method: 'PUT', body: {target: $('#tgt').value}}); say('Edit saved.'); viewSeg(cur.id); }
  async function decide(action) {
    try {
      const t = $('#tgt');
      if (isFounder && t && t.value !== cur.target) await api(A('/segments/' + encodeURIComponent(cur.id)), {method: 'PUT', body: {target: t.value}});
      await api(A(`/segments/${encodeURIComponent(cur.id)}/decision`), {method: 'POST', body: {action, nonce: cur.nonce}});
      const word = {approve: 'Approved', reject: 'Rejected', needs_review: 'Flagged for review'}[action];
      say(`${word} ${cur.id}.`);
      main.classList.add('flash'); setTimeout(() => main.classList.remove('flash'), 300);
      if (cur.next) location.hash = '#/seg/' + encodeURIComponent(cur.next); else viewSeg(cur.id);
    } catch (e) { say('Not done: ' + e.message); }
  }

  async function viewTerms() {
    document.title = 'Terms - Manuvi review';
    const t = await api(A('/terms'));
    main.innerHTML = `<h1>Candidate terms <span class="meta">${t.length}</span></h1>${isFounder ? '' : '<p class="note">Only the founder edits terms.</p>'}
      ${t.length ? `<div class="scroll"><table class="tbl"><thead><tr><th scope="col">English</th><th scope="col">Vietnamese</th><th scope="col">Decision</th>${isFounder ? '<th scope="col">Actions</th>' : ''}</tr></thead><tbody>
      ${t.map((x) => `<tr><td lang="en">${esc(x.en)}</td><td>${isFounder ? `<input type="text" lang="vi" data-vi="${esc(x.id)}" value="${esc(x.vi)}" aria-label="Vietnamese for ${esc(x.en)}">` : esc(x.vi || '-')}</td><td class="${esc(x.decision)}">${esc(x.decision)}</td>
      ${isFounder ? `<td><div class="actions"><button type="button" data-t="${esc(x.id)}" data-a="approve">Approve</button><button type="button" data-t="${esc(x.id)}" data-a="edit">Save edit</button><button type="button" data-t="${esc(x.id)}" data-a="reject">Reject</button></div></td>` : ''}</tr>`).join('')}</tbody></table></div>`
      : '<div class="panel"><h2>No candidate terms</h2><p>Terms the translator is unsure about show up here for approval.</p></div>'}`;
    main.querySelectorAll('button[data-t]').forEach((b) => b.onclick = async () => {
      const id = b.dataset.t;
      try { await api(A('/terms/' + encodeURIComponent(id)), {method: 'POST', body: {action: b.dataset.a, vi: main.querySelector(`input[data-vi="${CSS.escape(id)}"]`).value}}); say('Term updated.'); viewTerms(); }
      catch (e) { say('Not saved: ' + e.message); }
    });
  }

  async function viewStatus() {
    document.title = 'Status - Manuvi review';
    const s = await api(A('/status'));
    main.innerHTML = `<h1>Status</h1><p>${(s.progress * 100).toFixed(0)}% decided (${s.total - s.by_decision.pending} of ${s.total} segments)</p>
      <div class="meter gap" role="progressbar" aria-label="Segments decided" aria-valuenow="${Math.round(s.progress * 100)}" aria-valuemin="0" aria-valuemax="100" data-v="${s.progress}"><i></i></div>
      <div class="cols"><section class="panel"><h2>By status</h2><ul>${Object.entries(s.by_status).map(([k, v]) => `<li>${esc(k.replace('_', ' '))}: ${v}</li>`).join('')}</ul></section>
      <section class="panel"><h2>By decision</h2><ul>${Object.entries(s.by_decision).map(([k, v]) => `<li>${esc(k)}: ${v}</li>`).join('')}</ul></section></div>
      <p class="section">Safety segments approved: ${s.safety_approved} of ${s.safety_total}. Terms pending: ${s.terms_pending}. Cost so far: $${(+s.cost_usd).toFixed(2)}.</p>`;
    hydrate(main);
  }

  async function viewAudit() {
    document.title = 'Audit log - Manuvi review';
    const a = await api(A('/audit'));
    main.innerHTML = `<h1>Audit log</h1>${a.length ? `<div class="scroll"><table class="tbl"><thead><tr><th scope="col">Time</th><th scope="col">Who</th><th scope="col">Action</th><th scope="col">Item</th><th scope="col">Old</th><th scope="col">New</th></tr></thead><tbody>
      ${a.map((x) => `<tr><td>${esc(x.ts)}</td><td>${esc(x.user || '-')}</td><td>${esc(x.action)}</td><td>${esc(x.id)}</td><td>${esc(JSON.stringify(x.old))}</td><td>${esc(JSON.stringify(x.new))}</td></tr>`).join('')}</tbody></table></div>`
      : '<div class="panel"><h2>No activity yet</h2><p>Every edit, decision and comment is recorded here with the user name.</p></div>'}`;
  }

  addEventListener('keydown', (e) => {
    if (!keysOn || e.ctrlKey || e.metaKey || e.altKey || /INPUT|TEXTAREA|SELECT/.test(e.target.tagName)) return;
    const inSeg = location.hash.startsWith('#/seg/');
    if (e.key === 'j' || e.key === 'k') {
      const d = e.key === 'j' ? 1 : -1;
      if (inSeg) { const t = d > 0 ? cur.next : cur.prev; if (t) location.hash = '#/seg/' + encodeURIComponent(t); } else moveSel(d);
    } else if (!inSeg && e.key === 'Enter' && rows[sel] && e.target === document.body) location.hash = '#/seg/' + encodeURIComponent(rows[sel].id);
    else if (inSeg && canReview && (isFounder || e.key !== 'r') && {a: 'approve', r: 'reject', n: 'needs_review'}[e.key]) decide({a: 'approve', r: 'reject', n: 'needs_review'}[e.key]);
  });
  route();
})();
