/* Google connection is real OAuth; the automation demo is always labelled synthetic. */
(() => {
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function renderJobs(jobs, root, actions) {
    root.replaceChildren();
    for (const job of jobs) {
      const row = el('section', null, 'meet-job');
      const labels = {waiting:'Waiting for transcript',processing:'Checking Google',needs_speakers:'Transcript ready',complete:'Notebook ready',failed:'Import needs attention',expired:'Stopped waiting',cancelled:'Cancelled'};
      row.append(el('h3', `${job.provider === 'demo' ? 'Synthetic automation demo' : 'Google Meet import'} · ${labels[job.status] || job.status}`),
        el('p', job.message, 'helper'), el('p', `${job.attempts} checks · ${job.google_requests} recorded Meet API reads`, 'helper'));
      if (job.provider === 'demo') row.append(el('p', 'Scripted capture. No live Google account or meeting was accessed.', 'helper'));
      const buttons = el('div', null, 'notebook-actions');
      function button(label, action) {
        const node = el('button', label, 'button-secondary'); node.type = 'button';
        node.addEventListener('click', action); buttons.append(node);
      }
      if (job.status === 'needs_speakers') button('Confirm speakers', () => actions.prepare(job));
      if (job.status === 'complete' && job.notebook_id) {
        const link = el('a', 'Open notebook ↗'); link.href = '/notebook?id=' + encodeURIComponent(job.notebook_id); buttons.append(link);
      }
      if (['waiting','processing','needs_speakers'].includes(job.status)) button('Cancel import', () => actions.change(job, 'cancel'));
      if (['failed','expired','cancelled'].includes(job.status)) button('Retry import', () => actions.change(job, 'retry'));
      row.append(buttons); root.append(row);
    }
  }
  function init() {
    const get = id => document.getElementById(id), panel = get('meet-automation');
    if (!panel) return;
    let epoch = 0, connected = false, configured = false, busy = false, timer = null, refreshing = false, autoOpen = null, jobFingerprint = '';
    const message = (text, error=false) => {
      get('meet-status').textContent = text; get('meet-status').dataset.state = error ? 'error' : 'done';
      if (error) panel.open = true;
    };
    function controls() {
      get('meet-connect').disabled = busy || !configured;
      get('meet-connect').hidden = connected;
      get('meet-disconnect').hidden = !connected;
      get('meet-disconnect').disabled = get('meet-demo').disabled = get('meet-list').disabled = busy;
      get('meet-picker').hidden = !connected;
    }
    async function request(path, method='GET', body) {
      const response = await fetch('/api/integrations/google' + path, {method, cache:'no-store', ...(body === undefined ? {} : {headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Google import request failed. Check setup and retry.');
      }
      return response.json();
    }
    async function task(work) {
      if (busy) return;
      const current = epoch; busy = true; controls();
      try { await work(current); }
      catch (error) { if (current === epoch) message(error.message, true); }
      finally { if (current === epoch) { busy = false; controls(); } }
    }
    async function prepare(job, current) {
      const data = await request('/jobs/' + encodeURIComponent(job.id) + '/prepare');
      if (current !== epoch) return;
      const event = new CustomEvent('notebook:load-import', {detail:data, cancelable:true});
      window.dispatchEvent(event);
      if (!event.defaultPrevented) message('Transcript ready. Confirm speakers.');
      else message('Save or revert open notebook edits before loading this import.', true);
    }
    async function refresh(current) {
      if (refreshing) return;
      refreshing = true;
      try {
        const data = await request('/jobs');
        if (current !== epoch) return;
        const fingerprint = JSON.stringify(data.jobs);
        if (fingerprint !== jobFingerprint && data.jobs.some(job => ['waiting','processing','needs_speakers'].includes(job.status))) {
          panel.open = true; get('meet-activity').open = true;
        }
        if (fingerprint !== jobFingerprint) renderJobs(data.jobs, get('meet-jobs'), {
          prepare: job => task(token => prepare(job, token)),
          change: (job, action) => task(async token => {
            await request('/jobs/' + encodeURIComponent(job.id) + '/action', 'POST', {action});
            if (token === epoch) { if (action === 'retry') autoOpen = job.id; await refreshLater(token); }
          })
        });
        jobFingerprint = fingerprint;
        const selected = data.jobs.find(job => job.id === autoOpen && job.status === 'needs_speakers');
        if (selected && !busy) { autoOpen = null; await prepare(selected, current); }
      } finally { if (current === epoch) refreshing = false; }
    }
    async function refreshLater(current) { if (!refreshing) await refresh(current); }
    get('meet-connect').addEventListener('click', () => task(async current => {
      const data = await request('/connect', 'POST', {});
      if (current !== epoch) return;
      const url = new URL(data.authorization_url);
      if (url.origin !== 'https://accounts.google.com') throw new Error('Unexpected authorization destination');
      window.location.assign(url.href);
    }));
    get('meet-disconnect').addEventListener('click', () => task(async current => {
      await request('/disconnect', 'POST', {});
      if (current === epoch) { connected = false; get('meet-connection').textContent = 'Disconnected'; message('Connection revoked.'); }
    }));
    get('meet-demo').addEventListener('click', () => task(async current => {
      const job = await request('/demo', 'POST', {});
      if (current !== epoch) return;
      autoOpen = job.id;
      get('meet-activity').open = true;
      message(job.status === 'complete' ? 'Sample already imported. Open it below.' : 'Sample import queued.');
      await refreshLater(current);
    }));
    get('meet-list').addEventListener('click', () => task(async current => {
      const data = await request('/meetings?code=' + encodeURIComponent(get('meet-code').value.trim()));
      if (current !== epoch) return;
      const root = get('meet-meetings'); root.replaceChildren();
      for (const meeting of data.meetings) {
        const when = meeting.start_time ? new Date(meeting.start_time).toLocaleString() : meeting.name;
        const button = el('button', `${when} · ${meeting.end_time ? 'ended' : 'in progress'} · Import`); button.type='button';
        button.addEventListener('click', () => task(async token => {
          const job = await request('/jobs','POST',{conference_record:meeting.name});
          if (token === epoch) { autoOpen = job.id; get('meet-activity').open = true; message('Waiting for the transcript…'); await refreshLater(token); }
        }));
        root.append(button);
      }
      if (!data.meetings.length) root.append(el('p','No accessible meetings found. Check the connected account and filter.','helper'));
      if (data.has_more) root.append(el('p','Showing the most recent 20 matching records. Use the meeting-code filter to narrow the list.','helper'));
    }));
    window.addEventListener('notebook:import-complete', () => {
      message('Notebook saved.');
      refresh(epoch).catch(error => message(error.message,true));
    });
    window.addEventListener('debatebot:authchanged', event => {
      if (event.detail.reset) {
        epoch++; connected = configured = busy = refreshing = false; autoOpen = null; jobFingerprint = '';
        if (timer) clearInterval(timer); timer = null;
        get('meet-jobs').replaceChildren(); get('meet-meetings').replaceChildren(); get('meet-code').value='';
        panel.open = false; get('meet-activity').open = false;
        get('meet-connection').textContent='Checking connection…'; message(''); controls();
      }
      if (event.detail.authenticated) {
        const current=epoch;
        request('/status').then(async data => {
          if (current !== epoch) return;
          connected=data.connected; configured=data.configured; controls();
          get('meet-setup').hidden=configured;
          get('meet-connection').textContent=connected ? 'Connected · read-only' : configured ? 'Not connected' : 'Connection setup required';
          const outcome = new URLSearchParams(window.location.search).get('google');
          if (outcome) panel.open = true;
          if (outcome === 'denied') message('Google access was not approved. Your local notebooks are unchanged.');
          if (outcome === 'connection_failed') message('Connection did not finish. Check credentials, callback URI and local sign-in, then reconnect.',true);
          if (data.worker_error) message(data.worker_error,true);
          if (!data.worker_running) message('The import worker is not running. Start the API with lifespan enabled.',true);
          await refresh(current);
          if (current === epoch) timer=setInterval(() => refresh(current).catch(error => { if (current===epoch) message(error.message,true); }),3000);
        }).catch(error => { if (current===epoch) message(error.message,true); });
      }
    });
  }
  if (typeof module !== 'undefined') module.exports = {renderJobs};
  if (typeof document !== 'undefined') init();
})();
