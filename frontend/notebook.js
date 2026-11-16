/* Transcript and note strings remain text nodes, including in saved history. */
(() => {
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text != null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function stamp(source) {
    if (!source.start_time) return 'Time unavailable';
    return source.start_time + (source.end_time ? ' → ' + source.end_time : '');
  }
  function sourceRow(source, id) {
    const row = el('section', null, 'notebook-source');
    row.id = id;
    row.append(el('h4', `${source.speaker} · ${stamp(source)}`), el('p', source.text));
    return row;
  }
  function renderTimeline(documentData, root) {
    root.replaceChildren();
    const sources = Object.fromEntries(documentData.sources.map(source => [source.id, source]));
    const notes = Object.fromEntries(documentData.notes.map(note => [note.id, note]));
    documentData.timeline.forEach((group, index) => {
      const note = notes[group.note_id], card = el('section', null, 'notebook-group');
      const question = group.question_source_ids.map(id => sources[id].text).join('\n');
      card.append(el('p', `Question ${index + 1}`, 'helper'), el('h2', question || 'Answer without a preceding question', 'notebook-question'));
      if (!group.answer_source_ids.length) card.append(el('p', 'No candidate answer captured.', 'helper'));
      const label = el('label', `Note for question ${index + 1}`, 'sr-only');
      label.htmlFor = note.id;
      const input = el('textarea');
      input.id = note.id; input.dataset.noteId = note.id; input.value = note.body;
      input.maxLength = 1200; input.rows = 3;
      const origin = el('p', note.kind === 'human_note' ? 'Human-edited note' : 'Source excerpt', 'helper');
      input.addEventListener('input', () => { origin.textContent = input.value === note.excerpt ? 'Source excerpt' : 'Edited · unsaved'; });
      card.append(label, input, origin);
      const details = el('details');
      details.append(el('summary', `Sources (${note.source_ids.length})`));
      for (const id of note.source_ids) {
        details.append(sourceRow(sources[id], 'notebook-source-' + id));
      }
      card.append(details); root.append(card);
    });
  }
  function renderHistory(revisions, root) {
    root.replaceChildren();
    for (const revision of revisions) {
      const row = el('details', null, 'notebook-history-entry');
      row.append(el('summary', `Revision ${revision.revision} · ${new Date(revision.created_at).toLocaleString()}`));
      for (const change of revision.changes) {
        if (change.field === 'created') row.append(el('p', 'Notebook created from confirmed sources.'));
        else row.append(el('p', `${change.field}: before`), el('p', change.before || '(empty)'), el('p', 'After'), el('p', change.after || '(empty)'));
      }
      root.append(row);
    }
  }
  function renderDigest(documentData, root) {
    root.replaceChildren();
    const digest = documentData.ai_digest;
    if (!digest) return;
    root.append(el('p', `${digest.model} · ${digest.scope}`, 'helper'));
    const sources = Object.fromEntries(documentData.sources.map(source => [source.id, source]));
    for (const point of digest.points) {
      const row = el('section', null, 'notebook-digest-point'), link = el('a', `View source · ${sources[point.source_id]?.start_time || point.source_id}`);
      link.href = '#notebook-source-' + encodeURIComponent(point.source_id);
      link.addEventListener('click', () => { const target = document.getElementById('notebook-source-' + point.source_id); const details = target?.closest('details'); if (details) details.open = true; });
      row.append(el('p', point.text), el('blockquote', point.quote), link); root.append(row);
    }
  }
  function init() {
    const get = id => document.getElementById(id), form = get('notebook-import-form');
    if (!form) return;
    const status = get('notebook-status'), editor = get('notebook-editor'), timeline = get('notebook-timeline');
    let record = null, preview = null, prepared = '', roles = {}, busy = false, epoch = 0, importContext = null, digestConfigured = false;
    const message = (text, error = false) => { status.textContent = text; status.dataset.state = error ? 'error' : 'done'; };
    const input = () => ({title:get('notebook-title').value, content:get('notebook-content').value, format:get('notebook-format').value});
    const fingerprint = () => JSON.stringify({content:input().content, format:input().format});
    function edits() {
      return {revision:record.revision, title:get('notebook-saved-title').value,
        notes:Array.from(timeline.querySelectorAll('textarea[data-note-id]'), node => ({id:node.dataset.noteId, body:node.value}))};
    }
    function dirty() {
      return record && JSON.stringify(edits()) !== JSON.stringify({revision:record.revision, title:record.document.title,
        notes:record.document.notes.map(note => ({id:note.id, body:note.body}))});
    }
    function ready() {
      const assigned = preview && preview.speakers.every(name => roles[name]);
      const candidates = Object.values(roles).filter(role => role === 'Candidate').length;
      get('notebook-create').disabled = busy || !assigned || candidates !== 1 || prepared !== fingerprint();
      get('notebook-save').disabled = busy || !dirty();
      get('notebook-digest-generate').disabled = busy || !record || !digestConfigured || !get('notebook-digest-consent').checked || dirty();
      get('notebook-dirty').hidden = get('notebook-revert').hidden = !dirty();
      if (record) get('notebook-meta').textContent = dirty() ? '' : record.storage === 'memory' ? 'Local only' : 'Saved';
      if (preview) get('notebook-mapping-status').textContent = assigned && candidates === 1 ? '' : 'Assign every speaker and select one candidate.';
    }
    async function request(path, method = 'GET', body) {
      const response = await fetch(path, {method, cache:'no-store', ...(body === undefined ? {} : {headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)})});
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : 'Check the input limits and speaker labels, then retry.');
      }
      return response.json();
    }
    async function task(work) {
      if (busy) return;
      const current = epoch;
      busy = true;
      localControls(true);
      message('Working…');
      try { await work(current); }
      catch (error) { if (current === epoch) message(error instanceof TypeError ? 'Could not reach the server. Your edits are preserved.' : error.message, true); }
      finally {
        if (current === epoch) {
          busy = false;
          localControls(false);
          ready();
        }
      }
    }
    function localControls(disabled) {
      for (const root of [form, editor, get('notebook-preview')])
        for (const node of root.querySelectorAll('input,textarea,select,button')) node.disabled = disabled;
      get('notebook-new').disabled = get('notebook-sample').disabled = disabled;
    }
    function canLeave() {
      if (!dirty()) return true;
      message('Save your changes or revert unsaved edits before switching notebooks.', true); return false;
    }
    async function recent(current) {
      const data = await request('/api/notebooks');
      if (current !== epoch) return;
      const root = get('notebook-recent'); root.replaceChildren();
      for (const item of data.notebooks) {
        const button = el('button', item.title); button.type = 'button'; button.title = item.title;
        button.addEventListener('click', () => { if (canLeave()) return task(async token => { const data = await request('/api/notebooks/' + encodeURIComponent(item.id)); if (token === epoch) show(data); }); });
        root.append(button);
      }
      if (!data.notebooks.length) root.append(el('p', 'No saved notebooks yet.', 'helper'));
    }
    async function history(current) {
      if (!record) return;
      const id = record.id, data = await request('/api/notebooks/' + encodeURIComponent(id) + '/history');
      if (current === epoch && record?.id === id) renderHistory(data.revisions, get('notebook-history-items'));
    }
    function show(data) {
      record = data; get('notebook-import').hidden = true; editor.hidden = false;
      get('notebook-new').hidden = false; get('notebook-sample').hidden = true;
      get('notebook-saved-title').value = data.document.title;
      get('notebook-source-details').textContent = `${data.document.timeline.length} question groups · revision ${data.revision}`;
      renderTimeline(data.document, timeline);
      renderDigest(data.document, get('notebook-digest'));
      get('notebook-digest-consent').checked = false;
      const provenance = data.document.import_provenance;
      get('notebook-provenance').hidden = !provenance;
      get('notebook-provenance').textContent = !provenance ? '' : provenance.provider === 'demo' ? 'Sample transcript' : 'Google Meet';
      get('notebook-provenance-details').textContent = !provenance ? '' : provenance.provider === 'demo'
        ? 'Synthetic import. No Google account or real meeting was accessed.'
        : `${provenance.entry_count} Google Meet transcript entries. Speakers confirmed by you.`;
      get('notebook-review').href = '/review?notebook=' + encodeURIComponent(data.id);
      get('notebook-export').href = '/api/notebooks/' + encodeURIComponent(data.id) + '/export';
      get('notebook-history-items').replaceChildren(); get('notebook-history').open = false;
      window.history.replaceState(null, '', '/notebook?id=' + encodeURIComponent(data.id));
      message('');
      ready();
    }
    function invalidate() { preview = null; prepared = ''; roles = {}; importContext = null; get('notebook-import-source').hidden = true; get('notebook-preview').hidden = true; ready(); }
    for (const id of ['notebook-content', 'notebook-format']) get(id).addEventListener('input', invalidate);
    form.addEventListener('submit', event => {
      event.preventDefault();
      return task(async current => {
        const data = await request('/api/notebooks/preview', 'POST', input());
        if (current !== epoch) return;
        preview = data; roles = {...data.speaker_roles}; prepared = fingerprint();
        get('notebook-preview').hidden = false;
        const mapping = get('notebook-speakers'), turns = get('notebook-preview-turns'); mapping.replaceChildren(); turns.replaceChildren();
        for (const [index, name] of data.speakers.entries()) {
          const display = importContext?.display_names?.[name];
          const row = el('div', null, 'speaker-assignment'), label = el('label', display ? `${display} · ${name}` : name), select = el('select');
          select.id = `notebook-speaker-${index}`; label.htmlFor = select.id;
          for (const [value, text] of [['','Choose a role'],['Candidate','Candidate'],['Interviewer','Interviewer'],['Exclude','Exclude from review']]) {
            const option = el('option', text); option.value = value; select.append(option);
          }
          select.value = roles[name] || '';
          select.addEventListener('change', () => { if (select.value) roles[name] = select.value; else delete roles[name]; ready(); });
          row.append(label, select); mapping.append(row);
        }
        data.turns.forEach((turn, index) => turns.append(sourceRow(turn, `preview-turn-${index}`)));
        message('');
        if (importContext) get('notebook-preview').scrollIntoView({block:'start', behavior:'auto'});
      });
    });
    get('notebook-create').addEventListener('click', () => task(async current => {
      if (prepared !== fingerprint()) throw new Error('Preview the current transcript first.');
      const sourceJob = importContext?.job_id;
      const data = sourceJob ? await request('/api/integrations/google/jobs/' + encodeURIComponent(sourceJob) + '/complete', 'POST', {title:input().title, speaker_roles:roles})
        : await request('/api/notebooks', 'POST', {...input(), speaker_roles:roles});
      if (current === epoch) {
        show(data); importContext = null;
        if (sourceJob) window.dispatchEvent(new CustomEvent('notebook:import-complete',{detail:{job_id:sourceJob,notebook_id:data.id}}));
        await recent(current);
      }
    }));
    get('notebook-sample').addEventListener('click', () => task(async current => {
      const data = await request('/api/notebooks/sample');
      if (current !== epoch) return;
      get('notebook-title').value = data.title; get('notebook-content').value = data.content; get('notebook-format').value = data.format;
      invalidate(); message('Sample loaded. Preview to continue.');
    }));
    get('notebook-file').addEventListener('change', () => task(async current => {
      const file = get('notebook-file').files[0];
      if (!file) return;
      if (file.size > 240000 || !/\.(txt|md|vtt|json)$/i.test(file.name)) throw new Error('Use a TXT, MD, VTT or JSON file under 240 KB.');
      const text = await file.text();
      if (current !== epoch) return;
      if (text.length > 60000) throw new Error('Keep the transcript within 60,000 characters.');
      get('notebook-content').value = text; get('notebook-format').value = /\.vtt$/i.test(file.name) ? 'vtt' : /\.json$/i.test(file.name) ? 'meet_json' : 'text';
      invalidate(); message('File imported. Preview to continue.');
    }));
    editor.addEventListener('input', ready);
    get('notebook-digest-generate').addEventListener('click', () => task(async current => {
      if (!record || !get('notebook-digest-consent').checked) throw new Error('Authorize external processing before generating AI notes.');
      const data = await request('/api/notebooks/' + encodeURIComponent(record.id) + '/digest', 'POST', {revision:record.revision, consent_to_external_processing:true});
      if (current === epoch) { show(data); message(data.digest_reused ? 'Saved AI notes reused.' : 'AI notes saved. Check them against the sources.'); await recent(current); }
    }));
    get('notebook-save').addEventListener('click', () => task(async current => {
      const data = await request('/api/notebooks/' + encodeURIComponent(record.id), 'PATCH', edits());
      if (current === epoch) { show(data); await recent(current); }
    }));
    get('notebook-revert').addEventListener('click', () => show(record));
    get('notebook-new').addEventListener('click', () => {
      if (!canLeave()) return;
      record = null; editor.hidden = true; get('notebook-import').hidden = false;
      get('notebook-new').hidden = true; get('notebook-sample').hidden = false;
      form.reset(); invalidate(); message(''); window.history.replaceState(null, '', '/notebook');
    });
    for (const id of ['notebook-review', 'notebook-export']) get(id).addEventListener('click', event => { if (!canLeave() || busy) event.preventDefault(); });
    get('notebook-history').addEventListener('toggle', () => { if (get('notebook-history').open && record) history(epoch).catch(error => message(error.message, true)); });
    window.addEventListener('beforeunload', event => { if (dirty()) { event.preventDefault(); event.returnValue = ''; } });
    window.addEventListener('notebook:load-import', event => {
      if (busy || !canLeave()) { event.preventDefault(); return; }
      record=null; editor.hidden=true; get('notebook-import').hidden=false; get('notebook-new').hidden=true; get('notebook-sample').hidden=false;
      const data=event.detail, source=data.notebook_input;
      form.reset(); get('notebook-title').value=source.title; get('notebook-content').value=source.content; get('notebook-format').value=source.format;
      invalidate(); importContext={job_id:data.job_id,display_names:data.display_names};
      get('notebook-import-source').textContent=data.provenance.provider === 'demo' ? 'Sample transcript' : 'Google Meet transcript';
      get('notebook-import-source').hidden=false;
      form.requestSubmit();
    });
    window.addEventListener('debatebot:authchanged', event => {
      if (event.detail.reset) {
        epoch++; busy = false; record = null; digestConfigured = false; form.reset(); invalidate();
        timeline.replaceChildren(); editor.hidden = true; get('notebook-import').hidden = false;
        get('notebook-recent').replaceChildren(); get('notebook-history-items').replaceChildren();
        get('notebook-speakers').replaceChildren(); get('notebook-preview-turns').replaceChildren();
        get('notebook-meta').textContent = ''; get('notebook-review').href = get('notebook-export').href = '#';
        get('notebook-digest').replaceChildren(); get('notebook-digest-consent').checked = false; get('notebook-provenance').textContent='';
        get('notebook-source-details').textContent = get('notebook-provenance-details').textContent = '';
        get('notebook-saved-title').value = ''; get('notebook-new').hidden = true; get('notebook-sample').hidden = false;
        localControls(false);
        message(''); ready();
      }
      if (event.detail.authenticated) {
        const current = epoch;
        recent(current).catch(error => { if (current === epoch) message(error.message, true); });
        request('/api/notebooks/capabilities').then(data => {
          if (current !== epoch) return;
          digestConfigured=data.ai_digest_configured;
          get('notebook-digest-model').textContent=digestConfigured ? `${data.provider} / ${data.model} · at most one model request; no automatic retries.` : 'Configure a server-side model key to generate AI notes. Local excerpt notes remain available.';
          ready();
        }).catch(error => { if (current===epoch) get('notebook-digest-model').textContent=error.message; });
        const id = new URLSearchParams(window.location.search).get('id');
        if (id) task(async token => { const data = await request('/api/notebooks/' + encodeURIComponent(id)); if (token === epoch) show(data); });
      }
    });
  }
  if (typeof module !== 'undefined') module.exports = {renderTimeline, renderHistory, renderDigest, stamp};
  if (typeof document !== 'undefined') init();
})();
