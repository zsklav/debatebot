(() => {
  const byId = id => document.getElementById(id);
  const form = byId('auth-form'), status = byId('auth-status');
  let busy = false;
  function show(data, reset) {
    const title = byId('auth-title'), description = byId('account-description');
    if (title) title.textContent = data.authenticated ? 'Your workspace is ready.' : 'Sign in to DebateBot';
    if (description) description.textContent = data.authenticated ? 'Research a claim or pick a debate partner below.' : 'Sign in to save your debates and research results.';
    document.documentElement?.setAttribute?.('data-authenticated', String(data.authenticated));
    form.hidden = data.authenticated;
    byId('auth-user').hidden = !data.authenticated;
    byId('auth-logout').hidden = !data.authenticated || !data.required;
    byId('app-workspace').hidden = !data.authenticated;
    byId('auth-user').textContent = data.authenticated ? data.user.username : '';
    status.textContent = data.authenticated ? (data.required ? 'Your saved records are account-scoped.' : 'Local demo mode: authentication is disabled.') : 'Sign in or create an account to continue.';
    window.dispatchEvent(new CustomEvent('debatebot:authchanged', {detail: {reset, authenticated: data.authenticated}}));
  }
  function controls(disabled) {
    for (const node of form.querySelectorAll('input,button')) node.disabled = disabled;
    byId('auth-logout').disabled = disabled;
  }
  async function identity(reset = false) {
    const response = await fetch('/api/auth/me', {cache: 'no-store'});
    if (!response.ok) throw new Error('Sign-in service unavailable. Check PostgreSQL and migrations.');
    show(await response.json(), reset);
  }
  form.addEventListener('submit', async event => {
    event.preventDefault(); if (busy) return;
    busy = true; controls(true); status.textContent = 'Checking account…';
    try {
      const mode = event.submitter?.value === 'register' ? 'register' : 'login';
      const response = await fetch(`/api/auth/${mode}`, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({username: byId('auth-username').value, password: byId('auth-password').value})});
      if (!response.ok) {
        const messages = {401:'Invalid username or password.',409:'Username unavailable.',422:'Use a valid username and a password of 12–128 characters.',
          429:'Too many attempts. Wait a minute and retry.',503:'Account storage unavailable. Check PostgreSQL and migrations.',403:'Sign-in origin rejected. Check server origin configuration.'};
        throw new Error(messages[response.status] || 'Sign-in failed. Please retry.');
      }
      await identity(true);
    } catch (error) { status.textContent = error.message; }
    finally { byId('auth-password').value = ''; busy = false; controls(false); }
  });
  byId('auth-logout').addEventListener('click', async () => {
    if (busy) return; busy = true; controls(true);
    try {
      const response = await fetch('/api/auth/logout', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
      if (!response.ok) throw new Error('Sign-out failed. Retry before leaving this device.');
      await identity(true);
    } catch (error) { status.textContent = error.message; }
    finally { busy = false; controls(false); }
  });
  identity().catch(error => { status.textContent = error.message; });
})();
