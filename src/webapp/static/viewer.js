/* Page viewer: original page and Vietnamese page side by side, from the job's own files. Vietnamese UI only. */
(() => {
  const SCALES = [0.5, 0.75, 1, 1.25, 1.5, 2];
  const ZOOMS = [1, 1.5, 2, 3];
  const panes = $('#panes');
  let pages = [], cur = 0, scale = 1, fitMode = true, version = '', syncing = false, info = null;
  $('#who').textContent = CFG.user ? `${CFG.user}` : '';
  $('#title').textContent = CFG.name || '';
  if (CFG.pdfReady) { const a = $('#dl'); a.hidden = false; a.href = `/download/${encodeURIComponent(CFG.jobId)}/vi_pdf`; }

  async function vget(path) {
    const r = await fetch(path, {headers: {'X-Manuvi-Token': CFG.token}, credentials: 'same-origin'});
    if (r.status === 401) { location.replace('/'); throw new Error('Phiên đăng nhập đã hết. Hãy đăng nhập lại.'); }
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : 'Không tải được dữ liệu (mã ' + r.status + ').');
    return j;
  }
  const label = (p) => p.label;
  const chipState = (p) => (p.review || p.layout_review) ? 'review' : 'done';
  const chipWord = (p) => (p.review || p.layout_review) ? 'cần xem lại' : (p.vi_index === null ? 'chưa dịch' : 'đã dịch');

  function strip() {
    $('#strip').innerHTML = pages.map((p, i) =>
      `<button type="button" class="pg" role="listitem" data-i="${i}" data-s="${chipState(p)}" ${i === cur ? 'aria-current="page"' : ''} aria-label="Trang ${esc(label(p))}: ${chipWord(p)}" title="${esc(label(p))}: ${chipWord(p)}">${esc(label(p))}</button>`).join('');
    $('#labels').innerHTML = pages.map((p) => `<option value="${esc(label(p))}"></option>`).join('');
  }

  const dpr = () => Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  const zoomFor = (w) => ZOOMS.find((z) => z >= scale * dpr()) || ZOOMS[ZOOMS.length - 1];
  function fitScale(p) {
    const diff = $('#diff').checked;
    const avail = (document.documentElement.clientWidth - 32 - (diff ? 0 : 16)) / (diff ? 1 : 2);
    return Math.max(0.25, Math.min(2, avail / p.src_size[0]));
  }

  function img(which, p, idx) {
    const w = (which === 'vi' ? (p.vi_size || p.src_size) : p.src_size)[0] * scale;
    const alt = which === 'vi' ? `Trang bản dịch tiếng Việt ${label(p)}` : `Trang gốc tiếng Anh ${label(p)}`;
    return `<img class="vimg" data-w="${which}" src="/pages/${encodeURIComponent(CFG.jobId)}/${which}/${idx}.png?z=${zoomFor()}" width="${Math.round(w)}" alt="${esc(alt)}" decoding="async">`;
  }

  function markBoxes(p) {
    if (!p.marks || !p.marks.length || !p.vi_size || !$('#marks').checked) return '';
    return p.marks.map((m, i) => `<span class="vmark" data-t="${m.t}" data-b="${m.b.join(',')}" role="img" aria-label="${m.t === 'layout' ? 'Chỗ bố cục cần xem lại' : 'Câu dịch cần xem lại'} số ${i + 1}"><b aria-hidden="true">!</b></span>`).join('');
  }
  function placeMarks(p) {
    panes.querySelectorAll('.vmark').forEach((el) => {
      const [x0, y0, x1, y1] = el.dataset.b.split(',').map(Number);
      const [W, H] = p.vi_size;
      el.style.setProperty('left', (100 * x0 / W) + '%'); el.style.setProperty('top', (100 * y0 / H) + '%');
      el.style.setProperty('width', (100 * (x1 - x0) / W) + '%'); el.style.setProperty('height', (100 * (y1 - y0) / H) + '%');
    });
  }

  function show() {
    const p = pages[cur];
    if (!p) return;
    if (fitMode) scale = fitScale(p);
    const diff = $('#diff').checked;
    const hasVi = p.vi_index !== null && p.vi_index !== undefined;
    const noVi = `<div class="note vnote">${p.scanned ? 'Trang quét, chưa có lớp chữ nên chưa dịch (cần OCR).' : 'Trang này chưa được dịch.'}</div>`;
    const orig = `<section class="vpane" aria-label="Trang gốc"><h2><span class="lang">EN</span> Trang gốc ${esc(label(p))}</h2><div class="vscroll" tabindex="0" data-w="orig"><div class="vpage">${img('orig', p, p.pdf_page - 1)}</div></div></section>`;
    const vi = hasVi
      ? `<section class="vpane" aria-label="Bản dịch tiếng Việt"><h2><span class="lang vi">VI</span> Bản dịch ${esc(label(p))}</h2><div class="vscroll" tabindex="0" data-w="vi"><div class="vpage">${img('vi', p, p.vi_index)}${markBoxes(p)}</div></div></section>`
      : `<section class="vpane" aria-label="Bản dịch tiếng Việt"><h2><span class="lang vi">VI</span> Bản dịch ${esc(label(p))}</h2>${noVi}</section>`;
    if (diff && hasVi) {
      panes.innerHTML = `<section class="vpane wide" aria-label="Chồng ảnh so sánh"><h2>So sánh ${esc(label(p))}: chỗ khác nhau hiện màu tối</h2><div class="vscroll" tabindex="0" data-w="orig"><div class="vpage vstack">${img('orig', p, p.pdf_page - 1)}${img('vi', p, p.vi_index).replace('class="vimg"', 'class="vimg vtop"')}</div></div></section>`;
    } else panes.innerHTML = orig + vi;
    panes.setAttribute('aria-busy', 'false');
    placeMarks(p);
    $('#pos').textContent = `Trang ${label(p)} (${cur + 1}/${pages.length})${p.review || p.layout_review ? ' - cần xem lại' : ''}`;
    $('#zoomval').textContent = Math.round(scale * 100) + '%';
    $('#prev').disabled = cur === 0; $('#next').disabled = cur === pages.length - 1;
    $('#goto').value = label(p);
    strip();
    try { history.replaceState(null, '', '#/' + encodeURIComponent(label(p))); } catch (_) {}
    panes.querySelectorAll('.vscroll').forEach((s) => s.addEventListener('scroll', () => onScroll(s)));
  }

  function onScroll(src) {
    if (!$('#sync').checked || syncing) return;
    syncing = true;
    panes.querySelectorAll('.vscroll').forEach((o) => {
      if (o === src) return;
      const mx = src.scrollWidth - src.clientWidth, my = src.scrollHeight - src.clientHeight;
      o.scrollLeft = mx > 0 ? (src.scrollLeft / mx) * (o.scrollWidth - o.clientWidth) : 0;
      o.scrollTop = my > 0 ? (src.scrollTop / my) * (o.scrollHeight - o.clientHeight) : 0;
    });
    requestAnimationFrame(() => { syncing = false; });
  }

  function go(i) {
    if (i < 0 || i >= pages.length) return;
    cur = i;
    show();
    prefetch();
  }
  function prefetch() {
    [cur + 1].forEach((i) => { const p = pages[i]; if (!p) return; new Image().src = `/pages/${encodeURIComponent(CFG.jobId)}/orig/${p.pdf_page - 1}.png?z=${zoomFor()}`;
      if (p.vi_index !== null && p.vi_index !== undefined) new Image().src = `/pages/${encodeURIComponent(CFG.jobId)}/vi/${p.vi_index}.png?z=${zoomFor()}`; });
  }
  function zoomTo(s) { fitMode = false; scale = s; show(); }
  const stepScale = (dir) => { const cand = SCALES.filter((s) => dir > 0 ? s > scale + 0.01 : s < scale - 0.01); return dir > 0 ? cand[0] : cand[cand.length - 1]; };

  $('#prev').onclick = () => go(cur - 1);
  $('#next').onclick = () => go(cur + 1);
  $('#zin').onclick = () => { const s = stepScale(1); if (s) zoomTo(s); };
  $('#zout').onclick = () => { const s = stepScale(-1); if (s) zoomTo(s); };
  $('#zfit').onclick = () => { fitMode = true; show(); };
  $('#diff').onchange = show;
  $('#marks').onchange = show;
  $('#strip').onclick = (e) => { const b = e.target.closest('button[data-i]'); if (b) go(Number(b.dataset.i)); };
  $('#goto').onkeydown = (e) => {
    if (e.key !== 'Enter') return;
    const v = e.target.value.trim();
    const i = pages.findIndex((p) => label(p).toLowerCase() === v.toLowerCase());
    if (i >= 0) go(i); else say(`Không có trang "${v}" trong tài liệu này.`);
  };
  addEventListener('keydown', (e) => {
    if (e.target.closest && e.target.closest('input,select,textarea')) return;
    if (e.key === 'ArrowLeft') go(cur - 1); else if (e.key === 'ArrowRight') go(cur + 1);
  });
  addEventListener('resize', () => { if (fitMode) show(); });

  (async () => {
    try {
      info = await vget(`/api/jobs/${encodeURIComponent(CFG.jobId)}/viewer`);
      pages = info.pages || [];
      version = info.version;
      if (!pages.length) throw new Error('Chưa có trang để xem.');
      const want = decodeURIComponent((location.hash || '').replace(/^#\//, ''));
      const i = pages.findIndex((p) => label(p) === want);
      go(i >= 0 ? i : 0);
    } catch (e) {
      panes.setAttribute('aria-busy', 'false');
      panes.innerHTML = `<div class="panel" role="alert"><h2>Chưa xem được</h2><p>${esc(e.message)}</p></div>`;
    }
  })();
})();
