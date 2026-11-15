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
      row.append(element('h4', `${source.speaker} · ${source.kind} lines ${source.start_line}–${source.end_line}`),
        element('p', source.text));
      details.append(row);
    }
    return details;
  }
  function renderReport(report, root) {
    root.replaceChildren();
    if (report.demo) root.append(element('p', 'Synthetic sample: reviewer and judge outputs are scripted. No live models were evaluated and no API calls were made.', 'review-banner'));
    if (report.status !== 'complete') root.append(element('p', 'Partial review: a model request or evidence validation failed. No consolidated assessment is available.', 'review-banner'));
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
      element('pre', JSON.stringify(report.audit, null, 2)));
    root.append(audit, element('p', report.scope, 'review-scope'));
  }
  function init() {
    const byId = id => document.getElementById(id);
    const form = byId('review-form');
    if (!form) return;
    let busy = false, epoch = 0, configured = false, prepared = '';
    const status = byId('review-status'), result = byId('review-result'), preview = byId('review-preview');
    function payload() {
      const criteria = [];
      for (let n = 1; n <= 3; n++) {
        const label = byId(`criterion-name-${n}`).value.trim(), description = byId(`criterion-description-${n}`).value.trim();
        if (label || description) criteria.push({id:`criterion_${n}`, label, description});
      }
      return {transcript:byId('review-transcript').value, job_description:byId('review-job').value,
        resume:byId('review-resume').value, criteria};
    }
    function ready() { byId('review-run').disabled = busy || !configured || !byId('review-consent').checked || prepared !== JSON.stringify(payload()); }
    function controls(disabled) {
      busy = disabled;
      for (const node of form.querySelectorAll('input,textarea,button')) node.disabled = disabled;
      byId('review-demo').disabled = byId('review-reopen').disabled = disabled;
      ready();
    }
    function invalidate() { prepared = ''; byId('review-consent').checked = false; preview.hidden = true; result.hidden = true; ready(); }
    async function request(path, body) {
      const response = await fetch(path, body === undefined ? {cache:'no-store'} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(typeof data.detail === 'string' ? data.detail : response.status === 401 ? 'Sign in to continue.' : 'Request failed. Check input limits and server setup.');
      }
      return response.json();
    }
    function show(report) {
      renderReport(report, result); result.hidden = false;
      byId('review-run-id').value = report.run_id || '';
      status.textContent = report.demo ? 'Synthetic sample complete. No external model call was made.' : report.status === 'complete' ? 'Review ready for human inspection.' : 'Partial review saved. Inspect the available evidence.';
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
    form.addEventListener('input', event => { if (event.target.id === 'review-consent') ready(); else invalidate(); });
    byId('review-file').addEventListener('change', () => task(async current => {
      const file = byId('review-file').files[0];
      if (!file) return;
      if (file.size > 56000 || !/\.(txt|md)$/i.test(file.name)) throw new Error('Use a .txt or .md transcript under 56 KB.');
      const text = await file.text();
      if (current !== epoch) return;
      if (text.length > 14000) throw new Error('Keep the transcript within 14,000 characters.');
      byId('review-transcript').value = text; invalidate(); status.textContent = 'Transcript imported. Check speaker labels and preview the sources.';
    }));
    byId('review-prepare').addEventListener('click', () => task(async current => {
      if (!form.reportValidity()) return;
      const input = payload(), data = await request('/api/reviews/prepare', input);
      if (current !== epoch) return;
      preview.replaceChildren(sourcesView(data.sources, 'preview-source-')); preview.hidden = false;
      prepared = JSON.stringify(input); status.textContent = 'Source preview ready. Review it before authorizing external processing.';
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
      }
      if (event.detail.authenticated) {
        const current = epoch;
        request('/api/reviews/configuration').then(data => {
          if (current !== epoch) return;
          configured = data.configured;
          byId('review-configuration').textContent = configured ? 'External routes: ' + data.models.map(m => `${m.provider} / ${m.model}`).join(' · ')
            : 'Live council is not configured yet. Source preview and the synthetic sample are available.';
          ready();
        }).catch(error => { if (current === epoch) byId('review-configuration').textContent = error.message; });
      }
    });
  }
  if (typeof module !== 'undefined') module.exports = {renderReport};
  if (typeof document !== 'undefined') init();
})();
