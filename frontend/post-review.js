/* All transcript/model strings are text nodes. No HTML is accepted from models. */
(() => {
  const element = (tag, text, className) => {
    const node = document.createElement(tag);
    if (text != null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  };
  function sourcesView(sources, prefix) {
    const details = element('details', null, 'lab-card');
    details.append(element('summary', `Source excerpts (${sources.length})`));
    for (const source of sources) {
      const row = element('section', null, 'review-source');
      row.id = prefix + source.id;
      row.append(element('h4', `${source.speaker} · ${source.kind} lines ${source.start_line}–${source.end_line}${source.start_time ? ' · ' + source.start_time : ''}`),
        element('p', source.text));
      details.append(row);
    }
    return details;
  }
  function renderReport(report, root) {
    root.replaceChildren();
    if (typeof report.notebook_id === 'string' && /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/.test(report.notebook_id)) {
      const link = element('a', 'Open the source interview notebook', 'helper');
      link.href = '/notebook?id=' + encodeURIComponent(report.notebook_id); root.append(link);
    }
    if (report.demo) root.append(element('p', 'Sample report: outputs are scripted. No model calls.', 'review-banner'));
    if (report.status !== 'complete') root.append(element('p', 'Partial review. No consolidated assessment is available.', 'review-banner'));
    const labels = Object.fromEntries(report.criteria.map(c => [c.id, c.label]));
    function findings(items, parent) {
      for (const finding of items) {
        const card = element('section', null, 'lab-card');
        const head = element('div', null, 'finding-head');
        head.append(element('h3', labels[finding.criterion_id] || finding.criterion_id),
          element('span', finding.status.replaceAll('_', ' '), 'lab-verdict'));
        card.append(head, element('p', finding.explanation));
        if (finding.resolution) card.append(element('small', finding.resolution.replaceAll('_', ' ')));
        for (const citation of finding.evidence) {
          card.append(element('blockquote', citation.quote));
          const link = element('a', `View source · ${citation.source_id}`, 'review-citation');
          link.href = '#report-source-' + encodeURIComponent(citation.source_id);
          link.addEventListener('click', () => { const node = document.getElementById('report-source-' + citation.source_id); if (node?.parentElement) node.parentElement.open = true; });
          card.append(link);
        }
        parent.append(card);
      }
    }
    if (report.final) {
      const summary = element('section', null, 'lab-card');
      summary.append(element('h3', 'Review summary'), element('p', report.final.summary));
      root.append(summary);
      findings(report.final.findings, root);
    }
    const reviews = element('details', null, 'lab-card');
    reviews.append(element('summary', `Reviewer assessments (${report.reviews.length})`));
    for (const review of report.reviews) { reviews.append(element('h4', review.reviewer_id)); findings(review.findings, reviews); }
    root.append(reviews, sourcesView(report.sources, 'report-source-'));
    const audit = element('details', null, 'lab-card');
    audit.append(element('summary', 'Run details'), element('p', `Report: ${report.run_id || 'unsaved'} · ${report.storage || 'unknown storage'}`),
      element('p', `Model requests: ${report.audit.model_requests} · ${report.audit.elapsed_seconds}s`),
      element('pre', JSON.stringify(report.audit, null, 2)), element('p', report.scope, 'review-scope'));
    root.append(audit);
  }
  // Local templates only: choosing one makes no request and still needs preview and consent.
  const PRESETS = {
    backend: {job: 'Backend engineering intern: build and maintain HTTP APIs, design relational data, and keep writes safe under concurrent use.',
      criteria: [['API design', 'Explain how endpoints, validation and error responses were designed, with trade-offs.'],
        ['Databases', 'Describe schema design, constraints, transactions or migrations used in real work.'],
        ['Debugging', 'Walk through a real bug: how it was found, the cause and the fix.']]},
    frontend: {job: 'Frontend engineering intern: build accessible, responsive user interfaces and connect them to APIs.',
      criteria: [['UI building', 'Explain how components, state and layout were built for a real feature.'],
        ['API integration', 'Describe how the interface called APIs and handled loading and error states.'],
        ['Accessibility', 'Describe concrete steps taken for keyboard use, labels or contrast.']]},
    ml: {job: 'ML / AI engineering intern: build and evaluate model-backed features, including retrieval and LLM output checks.',
      criteria: [['Retrieval', 'Explain how documents were chunked, embedded and retrieved, and why.'],
        ['Evaluation', 'Describe how model output quality was measured and what the results showed.'],
        ['LLM safety', 'Explain checks against invented output, prompt injection or unsafe use.']]},
  };
  function applyPreset(name, byId) {
    const preset = PRESETS[name];
    if (!preset) return false;
    byId('review-job').value = preset.job;
    for (let n = 1; n <= 3; n++) {
      const [label, description] = preset.criteria[n - 1] || ['', ''];
      byId(`criterion-name-${n}`).value = label; byId(`criterion-description-${n}`).value = description;
    }
    const optional = document.querySelector('.optional-criteria');
    if (optional) optional.open = true;
    return true;
  }
  function init() {
    const byId = id => document.getElementById(id);
    const form = byId('review-form');
    if (!form) return;
    let busy = false, epoch = 0, configured = false, prepared = '', notebookId = null;
    const status = byId('review-status'), result = byId('review-result'), preview = byId('review-preview');
    function payload() {
      const criteria = [];
      for (let n = 1; n <= 3; n++) {
        const label = byId(`criterion-name-${n}`).value.trim(), description = byId(`criterion-description-${n}`).value.trim();
        if (label || description) criteria.push({id:`criterion_${n}`, label, description});
      }
      return {transcript:byId('review-transcript').value, job_description:byId('review-job').value,
        resume:byId('review-resume').value, criteria, ...(notebookId ? {notebook_id:notebookId} : {})};
    }
    function ready() { byId('review-run').disabled = busy || !configured || !byId('review-consent').checked || prepared !== JSON.stringify(payload()); }
    function controls(disabled) {
      busy = disabled;
      for (const node of form.querySelectorAll('input,textarea,button,select')) node.disabled = disabled;
      byId('review-demo').disabled = byId('review-reopen').disabled = disabled;
      ready();
    }
    function invalidate() { prepared = ''; byId('review-consent').checked = false; preview.hidden = true; result.hidden = true; ready(); }
    async function request(path, body) {
      const response = await fetch(path, body === undefined ? {cache:'no-store'} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        const first = Array.isArray(data.detail) ? data.detail[0] : null;
        const field = first && Array.isArray(first.loc) ? first.loc.filter(part => part !== 'body').join(' > ') : '';
        throw new Error(typeof data.detail === 'string' ? data.detail : field ? `Check "${field}": ${first.msg}` : response.status === 401 ? 'Sign in to continue.' : 'Request failed. Check input limits and server setup.');
      }
      return response.json();
    }
    function show(report) {
      renderReport(report, result); result.hidden = false;
      byId('review-run-id').value = report.run_id || '';
      status.textContent = report.demo ? '' : report.status === 'complete' ? 'Review ready.' : 'Partial review saved.';
      status.dataset.state = 'done';
      result.scrollIntoView({block:'start', behavior:'auto'});
    }
    async function task(operation) {
      if (busy) return;
      const current = epoch; controls(true); status.dataset.state = 'loading'; status.textContent = 'Working…';
      try { await operation(current); }
      catch (error) { if (current === epoch) { status.dataset.state = 'error'; status.textContent = error instanceof TypeError ? 'Could not reach the server. Your input is preserved.' : error.message; } }
      finally { if (current === epoch) controls(false); }
    }
    function unlinkNotebook() { notebookId = null; byId('review-origin').hidden = true; byId('review-origin').replaceChildren(); }
    form.addEventListener('input', event => {
      if (event.target.id === 'review-preset') {
        if (applyPreset(event.target.value, byId)) { invalidate(); status.textContent = 'Preset filled. Edit it if needed, then preview the sources.'; }
      }
      else if (event.target.id === 'review-consent') ready();
      else { if (event.target.id === 'review-transcript') unlinkNotebook(); invalidate(); }
    });
    byId('review-file').addEventListener('change', () => task(async current => {
      const file = byId('review-file').files[0];
      if (!file) return;
      if (file.size > 240000 || !/\.(txt|md)$/i.test(file.name)) throw new Error('Use a .txt or .md transcript under 240 KB, or import VTT/JSON in Interview Notebook.');
      const text = await file.text();
      if (current !== epoch) return;
      if (text.length > 60000) throw new Error('Keep the transcript within 60,000 characters.');
      byId('review-transcript').value = text; unlinkNotebook(); invalidate(); status.textContent = 'Transcript imported. Check speaker labels and preview the sources.';
    }));
    byId('review-prepare').addEventListener('click', () => task(async current => {
      if (!form.reportValidity()) return;
      const input = payload(), data = await request('/api/reviews/prepare', input);
      if (current !== epoch) return;
      preview.replaceChildren(sourcesView(data.sources, 'preview-source-')); preview.hidden = false;
      prepared = JSON.stringify(input); status.textContent = 'Sources ready. Review them before continuing.';
    }));
    form.addEventListener('submit', event => {
      event.preventDefault();
      if (!configured || !byId('review-consent').checked || prepared !== JSON.stringify(payload())) return;
      return task(async current => {
        status.textContent = 'Reviewers are assessing the shared evidence. Up to 3 model calls; this may take a minute.';
        result.hidden = true;
        const report = await request('/api/reviews/run', {...payload(), consent_to_external_processing:true, evidence_confirmed:true});
        if (current === epoch) show(report);
      });
    });
    byId('review-demo').addEventListener('click', () => task(async current => {
      status.textContent = 'Running synthetic sample through retrieval and the review graph…';
      const report = await request('/api/reviews/demo', {});
      if (current === epoch) show(report);
    }));
    byId('review-reopen').addEventListener('click', () => task(async current => {
      const id = byId('review-run-id').value.trim();
      if (!id || id.length > 128) throw new Error('Enter a saved report ID.');
      const report = await request('/api/reviews/' + encodeURIComponent(id));
      if (current === epoch) { show(report); status.textContent = 'Saved report reopened. No new model call was made.'; }
    }));
    window.addEventListener('debatebot:authchanged', event => {
      if (event.detail.reset) {
        epoch++; busy = false; form.reset(); prepared = ''; configured = false;
        preview.replaceChildren(); result.replaceChildren(); preview.hidden = result.hidden = true;
        byId('review-run-id').value = ''; status.textContent = ''; controls(false);
        byId('review-models').textContent = '';
        unlinkNotebook();
      }
      if (event.detail.authenticated) {
        const current = epoch;
        request('/api/reviews/configuration').then(data => {
          if (current !== epoch) return;
          configured = data.configured;
          byId('review-configuration').textContent = !configured ? 'Model setup required for live reviews.'
            : data.daily_limit ? `Live reviews left today: ${data.daily_remaining} of ${data.daily_limit}.` : '';
          byId('review-models').textContent = configured ? data.models.map(m => `${m.provider} / ${m.model}`).join(' · ')
            : 'No model providers connected.';
          ready();
        }).catch(error => { if (current === epoch) byId('review-configuration').textContent = error.message; });
        const id = new URLSearchParams(window.location.search).get('notebook');
        if (id) task(async token => {
          const notebook = await request('/api/notebooks/' + encodeURIComponent(id));
          if (token !== epoch) return;
          notebookId = notebook.id; byId('review-transcript').value = notebook.document.transcript;
          invalidate();
          const origin = byId('review-origin'), link = element('a', notebook.document.title);
          link.href = '/notebook?id=' + encodeURIComponent(notebook.id);
          origin.replaceChildren(link, element('span', ' · original transcript')); origin.hidden = false;
          status.textContent = '';
        });
      }
    });
  }
  if (typeof module !== 'undefined') module.exports = {renderReport};
  if (typeof document !== 'undefined') init();
})();
