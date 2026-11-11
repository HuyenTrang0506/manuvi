(() => {
  const main = $('#main');
  const isFounder = CFG.role === 'founder';
  let timer = null;
  $('#who').textContent = `${CFG.user} (${CFG.role})`;
  if (!isFounder) $('#nav-new').hidden = true;
  $('#logout').onclick = async () => { try { await api('/api/logout', {method: 'POST'}); } finally { location.replace('/'); } };

  const skeleton = (n = 3) => `<div aria-busy="true" aria-label="Loading">${'<span class="sk tall"></span>'.repeat(n)}</div>`;
  const money = (v) => '$' + (+v || 0).toFixed(2);
  const prioBadge = (j) => (j.priority_sections || []).length ? `<span class="badge prio"><span aria-hidden="true">\u2605</span> Priority: section ${j.priority_sections.map(esc).join(', ')}</span>` : '';
  const ACTIVE = new Set(['queued', 'running']);
  const hydrate = (root) => root.querySelectorAll('[data-v]').forEach((el) => el.style.setProperty('--v', el.dataset.v)); // CSP forbids inline style attributes

  function setTitle(t) { document.title = t ? `${t} - Manuvi` : 'Manuvi'; }
  function focusMain() { main.focus({preventScroll: true}); }
  function mark(h) {
    document.querySelectorAll('.top nav a').forEach((a) => {
      if (a.getAttribute('href') === h) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
    });
  }

  async function route() {
    clearInterval(timer);
    const h = location.hash || '#/jobs';
    const [, name, id] = h.split('/');
    mark(name === 'new' ? '#/new' : '#/jobs');
    try {
      if (name === 'new' && isFounder) viewNew();
      else if (name === 'job' && id) await viewJob(decodeURIComponent(id));
      else await viewJobs();
    } catch (e) {
      main.innerHTML = `<div class="panel" role="alert"><h1>Something went wrong</h1><p>${esc(e.message)}</p><a class="btn" href="#/jobs">Back to manuals</a></div>`;
    }
    focusMain();
  }
  addEventListener('hashchange', route);

  /* ---- manuals list / landing */
  function jobRow(j) {
    const done = j.chunks ? j.chunks - j.pending : 0;
    return `<li><a class="job rise" href="#/job/${esc(j.id)}" data-id="${esc(j.id)}">
      <div><h2>${esc(j.name)}</h2>${prioBadge(j)}<div class="meta">Sections ${esc(j.sections)} &middot; ${esc(j.created_at.slice(0, 10))} &middot; <span data-f="cost">${money(j.cost_usd)}</span></div></div>
      <div data-f="state">${stateTag(j.state)}</div>
      <div class="meter" role="progressbar" aria-label="Progress for ${esc(j.name)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(j.progress * 100)}" data-v="${j.progress}"><i></i></div>
    </a></li>`;
  }
  function patchRow(li, j) {
    li.querySelector('[data-f=state]').innerHTML = stateTag(j.state);
    li.querySelector('[data-f=cost]').textContent = money(j.cost_usd);
    const m = li.querySelector('.meter');
    m.style.setProperty('--v', j.progress);
    m.setAttribute('aria-valuenow', Math.round(j.progress * 100));
  }
  async function viewJobs(first = true) {
    setTitle('Manuals');
    if (first) main.innerHTML = `<h1>Manuals</h1>${skeleton()}`;
    const jobs = await api('/api/jobs');
    if (!jobs.length) {
      main.innerHTML = isFounder ? `<section class="empty">
        <h1>Technical manuals, translated and checked.</h1>
        <p class="lead">Upload a manual and get Vietnamese pages back with every part number, value and safety warning kept exactly as printed.</p>
        <div class="specimen" aria-label="Example"><p lang="en">${withCodes('Tighten bolt to 12 Nm. See Fig. 3-2.')}</p><p lang="vi">${withCodes('Siết bu lông đến 12 Nm. Xem Fig. 3-2.')}</p></div>
        <a class="btn primary" href="#/new">Upload a manual</a></section>`
        : `<section class="empty"><h1>No manuals yet</h1><p class="lead">Manuals appear here once the founder uploads them. You will be able to read and, when allowed, approve segments.</p></section>`;
      return;
    }
    const ul = $('.jobs', main);
    if (first || !ul) {
      main.innerHTML = `<h1>Manuals</h1><ul class="jobs">${jobs.map(jobRow).join('')}</ul>`;
      hydrate(main);
    } else {
      jobs.forEach((j) => { const li = ul.querySelector(`[data-id="${j.id}"]`); if (li) patchRow(li, j); });
    }
    if (jobs.some((j) => ACTIVE.has(j.state))) timer = setInterval(() => viewJobs(false).catch(() => {}), 5000);
  }

  /* ---- upload */
  function viewNew() {
    setTitle('Upload a manual');
    main.innerHTML = `<h1>Upload a manual</h1>
    <form id="nf" class="panel narrow" novalidate>
      <div class="field"><label for="nm">Manual name</label><input id="nm" type="text" maxlength="120" autocomplete="off" required>
        <p class="hint">Shown in your list, for example "Excavator service manual, section 23".</p></div>
      <div class="field"><span class="lbl" id="pl">PDF file</span>
        <label class="drop" id="drop" for="pf"><input id="pf" type="file" accept="application/pdf,.pdf" aria-describedby="ph">
          <strong id="dn">Drop the PDF here or choose a file</strong><span class="hint" id="ph">PDF only, up to ${CFG.maxMb} MB. Stored privately; it is only ever read as a PDF.</span></label></div>
      <div class="field"><label for="sc">Sections to translate</label><input id="sc" type="text" placeholder="23:1-42,43:1-22" autocomplete="off" spellcheck="false" aria-describedby="sh" required>
        <p class="hint" id="sh">One or more SECTION:FIRST-LAST, separated by commas. The numbers are the printed page numbers.</p></div>
      <div class="field"><label for="pm">Page map (optional)</label><textarea id="pm" rows="3" spellcheck="false" aria-describedby="mh"></textarea>
        <p class="hint" id="mh">Only needed when printed page labels cannot be read. One line per section: SECTION=PDF page of its first page, for example 23=187.</p></div>
      <div class="field"><label for="pr">Priority sections (optional)</label><input id="pr" type="text" placeholder="43" autocomplete="off" spellcheck="false" aria-describedby="prh">
        <p class="hint" id="prh">Section names to translate first, separated by commas. They are worked on, and reported, before the rest.</p></div>
      <p id="ferr" class="err-text" role="alert"></p>
      <div class="meter gap" id="up" hidden role="progressbar" aria-label="Upload progress" aria-valuemin="0" aria-valuemax="100" data-v="0"><i></i></div>
      <div class="actions"><button class="primary" id="go" type="submit">Start translation</button><a class="btn" href="#/jobs">Cancel</a></div>
    </form>`;
    const f = $('#nf'), drop = $('#drop'), pf = $('#pf');
    const setFile = (file) => {
      if (!file) { drop.removeAttribute('data-has'); $('#dn').textContent = 'Drop the PDF here or choose a file'; return; }
      drop.setAttribute('data-has', ''); $('#dn').textContent = `${file.name} (${(file.size / 1048576).toFixed(1)} MB)`;
    };
    pf.onchange = () => setFile(pf.files[0]);
    ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.setAttribute('data-over', ''); }));
    ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.removeAttribute('data-over'); }));
    drop.addEventListener('drop', (e) => { if (e.dataTransfer.files[0]) { pf.files = e.dataTransfer.files; setFile(pf.files[0]); } });
    f.onsubmit = async (e) => {
      e.preventDefault();
      const err = $('#ferr'); err.textContent = '';
      const name = $('#nm').value.trim(), file = pf.files[0], sections = $('#sc').value.trim().replace(/\s+/g, '');
      const bad = (msg, el) => { err.textContent = msg; el.focus(); };
      if (!name) return bad('Give the manual a name.', $('#nm'));
      if (!file) return bad('Choose the PDF to upload.', pf);
      if (!/\.pdf$/i.test(file.name)) return bad('That file is not a PDF. Choose a file ending in .pdf.', pf);
      if (file.size > CFG.maxMb * 1048576) return bad(`That PDF is larger than ${CFG.maxMb} MB. Split it or upload fewer sections.`, pf);
      if (!/^[A-Za-z0-9_-]{1,16}:\d+-\d+(,[A-Za-z0-9_-]{1,16}:\d+-\d+)*$/.test(sections)) return bad('Write sections as SECTION:FIRST-LAST, for example 23:1-42,43:1-22.', $('#sc'));
      const page_map = {};
      for (const line of $('#pm').value.split('\n').map((l) => l.trim()).filter(Boolean)) {
        const m = /^([A-Za-z0-9_-]+)\s*=\s*(\d+)$/.exec(line);
        if (!m) return bad(`Page map line "${line}" should look like 23=187.`, $('#pm'));
        page_map[m[1]] = +m[2];
      }
      const go = $('#go'); go.disabled = true; go.textContent = 'Uploading...';
      try {
        const priority_sections = $('#pr').value.split(',').map((x) => x.trim()).filter(Boolean);
        const {id} = await api('/api/jobs', {method: 'POST', body: {name, sections, page_map, priority_sections}});
        await new Promise((resolve, reject) => {
          const x = new XMLHttpRequest(); x.open('PUT', `/api/jobs/${id}/pdf`);
          x.setRequestHeader('Content-Type', 'application/pdf'); x.setRequestHeader('X-Manuvi-Token', CFG.token);
          const bar = $('#up'); bar.hidden = false;
          x.upload.onprogress = (ev) => { if (ev.lengthComputable) { bar.style.setProperty('--v', ev.loaded / ev.total); bar.setAttribute('aria-valuenow', Math.round(100 * ev.loaded / ev.total)); } };
          x.onload = () => { if (x.status < 300) resolve(); else { let d = ''; try { d = JSON.parse(x.responseText).detail; } catch (_) {} reject(new Error(d || 'Upload failed (' + x.status + ')')); } };
          x.onerror = () => reject(new Error('The upload was interrupted. Check your connection and try again.'));
          x.send(file);
        });
        await api(`/api/jobs/${id}/submit`, {method: 'POST'});
        say('Uploaded. Translation will start when a worker is free.');
        location.hash = `#/job/${id}`;
      } catch (ex) {
        err.textContent = ex.message; go.disabled = false; go.textContent = 'Start translation'; $('#up').hidden = true;
      }
    };
  }

  /* ---- one manual */
  const DL = [['vi_pdf', 'Vietnamese PDF'], ['interleaved_pdf', 'English and Vietnamese pages, side by side'], ['segments_xlsx', 'Segments (Excel)'], ['terms_xlsx', 'Terms (Excel)']];
  function jobHtml(j) {
    const translated = j.chunks ? j.chunks - j.pending : 0;
    const pageVals = Object.values(j.page_states || {});
    const pagesDone = pageVals.filter((s) => s === 'done').length;
    const canReview = j.chunks > 0 && j.state !== 'queued';
    return `<p><a href="#/jobs">&larr; All manuals</a></p>
    <h1>${esc(j.name)}</h1>
    ${prioBadge(j)}
    <p class="meta"><span id="jst">${stateTag(j.state)}</span> &middot; Sections ${esc(j.sections)} &middot; Started by ${esc(j.owner)} on ${esc(j.created_at.slice(0, 10))}</p>
    ${j.error_code ? `<p class="err-text" role="alert">${esc(errorCopy(j.error_code))}</p>` : ''}
    <section class="panel" aria-labelledby="ph1"><h2 id="ph1">Progress</h2>
      <div class="meter gap" role="progressbar" aria-label="Translation progress" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(j.progress * 100)}" data-v="${j.progress}"><i></i></div>
      <p>${j.chunks ? `${translated} of ${j.chunks} text blocks translated` : 'Waiting for the worker to read the PDF'}${pageVals.length ? `, ${pagesDone} of ${pageVals.length} pages complete` : ''}. Cost so far ${money(j.cost_usd)}.</p>
      ${pageStrip(j.page_states)}
    </section>
    ${sectionTable(j)}
    <section class="section" aria-labelledby="ah"><h2 id="ah">Actions</h2><div class="actions">
      ${canReview ? `<a class="btn primary" href="/review/${esc(j.id)}">${isFounder ? 'Review segments' : 'Read segments'}</a>` : ''}
      ${j.outputs.vi_pdf && (isFounder || j.reviewer_can_read) ? `<a class="btn" href="/view/${esc(j.id)}">Xem từng trang</a>` : ''}
      ${isFounder && j.state === 'needs_review' ? '<button id="resume" type="button">Continue after my approvals</button>' : ''}
      ${isFounder && j.state === 'done' ? '<button id="resume" type="button">Rebuild files from my approvals</button>' : ''}
      ${isFounder && !['done', 'failed', 'cancelled'].includes(j.state) ? '<button id="cancel" class="danger" type="button">Cancel job</button>' : ''}
    </div><p id="cbox" class="msg" role="status"></p>
    ${j.state === 'needs_review' ? '<p class="note">Safety warnings wait for a person. Approve each one in the review queue, then continue.</p>' : ''}
    ${isFounder ? `<div class="field"><label class="cb check"><input id="ra" type="checkbox" ${j.reviewer_can_review ? 'checked' : ''}> Let the reviewer approve non-safety segments</label><p class="hint">Safety segments always need the founder.</p></div>` : ''}
    </section>
    <section class="section" aria-labelledby="dh"><h2 id="dh">Downloads</h2>
      <div class="dl">${DL.map(([k, t]) => j.outputs[k] ? `<a class="btn" href="/download/${esc(j.id)}/${k}">${esc(t)}</a>` : `<span class="btn" aria-disabled="true">${esc(t)}<span class="sr"> (not ready yet)</span></span>`).join('')}</div>
      ${Object.values(j.outputs).some(Boolean) ? '' : '<p class="hint">Files appear here when the translation is ready.</p>'}
    </section>`;
  }
  function sectionTable(j) {
    const e = Object.entries(j.section_progress || {});
    if (!e.length) return '';
    const prio = new Set(j.priority_sections || []);
    return `<section class="section" aria-labelledby="sh1"><h2 id="sh1">Progress by section</h2><div class="scroll"><table class="tbl"><thead><tr><th scope="col">Section</th><th scope="col">Translated</th><th scope="col">Cost</th></tr></thead><tbody>${e.map(([k, v]) =>
      `<tr><th scope="row">${esc(k)} ${prio.has(k) ? '<span class="badge prio"><span aria-hidden="true">\u2605</span> Priority</span>' : ''}</th><td>${v.chunks - v.pending} of ${v.chunks}</td><td>${money(v.cost_usd)}</td></tr>`).join('')}</tbody></table></div></section>`;
  }
  async function viewJob(id, first = true) {
    if (first) { setTitle('Manual'); main.innerHTML = skeleton(2); }
    const j = await api('/api/jobs/' + encodeURIComponent(id));
    setTitle(j.name);
    main.innerHTML = jobHtml(j);
    hydrate(main);
    const act = async (path, okMsg, body) => {
      try { await api(`/api/jobs/${id}/${path}`, {method: 'POST', body}); say(okMsg); viewJob(id, false); }
      catch (e) { $('#cbox').textContent = e.message; }
    };
    const r = $('#resume'); if (r) r.onclick = () => act('resume', 'Queued. A worker will pick it up shortly.');
    const c = $('#cancel');
    if (c) c.onclick = () => {
      $('#cbox').innerHTML = 'Cancel this job? Finished pages stay available. <button id="yes" class="danger" type="button">Cancel job</button> <button id="no" type="button">Keep running</button>';
      $('#yes').onclick = () => act('cancel', 'Job cancelled.'); $('#no').onclick = () => { $('#cbox').textContent = ''; c.focus(); }; $('#yes').focus();
    };
    const ra = $('#ra'); if (ra) ra.onchange = () => act('reviewer-access', ra.checked ? 'Reviewer can now approve non-safety segments.' : 'Reviewer is read-only again.', {allow: ra.checked});
    if (ACTIVE.has(j.state)) timer = setTimeout(() => { if (location.hash === `#/job/${id}`) viewJob(id, false).catch(() => {}); }, 4000);
  }
  route();
})();
