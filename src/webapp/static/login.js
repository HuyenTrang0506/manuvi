(() => {
  const f = document.getElementById('f'), err = document.getElementById('err'), go = document.getElementById('go');
  f.addEventListener('submit', async (e) => {
    e.preventDefault();
    err.textContent = '';
    const user = f.user.value.trim(), password = f.password.value;
    if (!user || !password) { err.textContent = 'Enter your user name and password.'; (user ? f.password : f.user).focus(); return; }
    go.disabled = true; go.textContent = 'Signing in...';
    try {
      const r = await fetch('/api/login', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({user, password})});
      if (r.ok) { location.replace('/'); return; }
      const j = await r.json().catch(() => ({}));
      if (r.status === 429) {
        const m = Math.max(1, Math.ceil((j.retry_after || 60) / 60));
        err.textContent = `Too many attempts. Try again in ${m} minute${m === 1 ? '' : 's'}.`;
      } else if (r.status === 401) {
        err.textContent = 'Wrong user name or password. Check both and try again.';
      } else {
        err.textContent = 'Sign-in is not available right now. Try again in a moment.';
      }
      f.password.value = ''; f.password.focus();
    } catch (_) {
      err.textContent = 'Cannot reach the server. Check your connection and try again.';
    } finally { go.disabled = false; go.textContent = 'Sign in'; }
  });
})();
