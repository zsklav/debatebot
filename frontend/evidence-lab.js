/* Source and model strings are rendered as text, never interpreted as HTML. */
(() => {
  function el(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (className) node.className = className;
    return node;
  }
  function safeUrl(value) {
    try {
      const url = new URL(value);
      return ["http:", "https:"].includes(url.protocol) && !url.username && !url.password ? url.href : null;
    } catch { return null; }
  }
  function card(title, partial = false) {
    const node = el("section", null, "lab-card" + (partial ? " lab-partial" : ""));
    node.append(el("h3", title));
    return node;
  }
  function sourceCard(citation) {
    const source = citation.source || {};
    const node = card(source.title || "Cited passage");
    node.append(el("span", citation.relation, "lab-verdict"), el("blockquote", citation.quote));
    const href = safeUrl(source.source_url);
    if (href) {
      const link = el("a", "Open original source");
      link.href = href; link.target = "_blank"; link.rel = "noopener noreferrer";
      node.append(link);
    }
    node.append(el("small", [source.authors, source.source_date, source.license].filter(Boolean).join(" · ")),
      el("small", source.representation || "Check source scope before drawing conclusions."));
    if (source.start_line != null) node.append(el("small", `Local summary lines ${source.start_line}–${source.end_line}`));
    if (source.retrieved_at) node.append(el("small", `Page retrieved: ${source.retrieved_at}`));
    return node;
  }
  function renderAssessment(result, root) {
    const node = card("Evidence assessment");
    node.append(el("span", result.verdict.replaceAll("_", " "), "lab-verdict"), el("p", result.explanation),
      el("h4", "Suggested revision"), el("p", result.suggested_revision),
      el("small", result.scope || "Relative to the retrieved corpus; not a universal truth judgment."));
    if (result.relevance_screen) node.append(el("small",
      `${result.relevance_screen.rejected} candidate passages excluded by local keyword screening. ${result.relevance_screen.warning}`));
    root.append(node);
    for (const citation of result.citations || []) root.append(sourceCard(citation));
    if (!(result.citations || []).length) node.append(el("small", "No supporting or contradicting citations supplied."));
    if ((result.retrieved_passages || []).length) {
      const details = el("details", null, "lab-card");
      details.append(el("summary", "Inspect retrieved passages"));
      for (const passage of result.retrieved_passages) {
        details.append(el("h4", passage.title || passage.chunk_id), el("p", passage.text),
          el("small", passage.representation), el("small", `Chunk: ${passage.chunk_id}`));
      }
      root.append(details);
    }
  }
  function renderResult(result, mode, root) {
    root.replaceChildren();
    if (result.run_id) root.append(el("small", `Run ID: ${result.run_id} · Storage: ${result.storage || 'unknown'}`));
    if (result.source_mode === "web") root.append(el("small", `Public web sources · Provider: ${result.search_provider || 'web search'}. Web pages are untrusted evidence, not verified facts.`));
    if (mode === "evidence") {
      const weakness = result.weakness;
      const node = card(`The weak point · ${weakness.severity}/10`);
      node.append(el("blockquote", weakness.weakest_claim), el("h4", "Reasoning weakness"), el("p", weakness.why_weak),
        el("h4", "The attack"), el("p", weakness.the_attack), el("h4", "How to defend it"), el("p", weakness.how_to_defend));
      root.append(node);
      renderAssessment(result.evidence, root);
    } else {
      const partial = result.status !== "complete";
      const insufficient = result.assessment?.verdict === "insufficient_evidence";
      const node = card(partial ? "Research stopped with partial results" : insufficient ? "Research finished — evidence insufficient" : "Research complete", partial);
      node.append(el("span", result.termination_reason.replaceAll("_", " "), "lab-verdict"),
        el("p", `${result.tool_calls} tool calls · ${result.searches} searches · ${Number(result.elapsed_seconds).toFixed(1)} seconds`));
      const candidates = result.candidate_count ?? new Set((result.events || []).flatMap(event => event.output?.results || []).map(hit => hit.chunk_id)).size;
      const inspected = result.inspected_count ?? (result.read_passages || []).length;
      node.append(el("p", `${candidates} candidate passages found · ${inspected} passages opened for assessment`));
      if (result.termination_reason === "no_relevant_evidence") node.append(el("p",
        result.model_calls === 0 ? "Stopped at the local relevance check. 0 LLM calls; no web search was made."
        : "Stopped early because searches produced no relevant local evidence. No web search was made."));
      if (candidates && !inspected) node.append(el("p", "Search matches are only candidates. None were opened as evidence for this assessment."));
      if (partial) node.append(el("p", "The run ended before a final assessment. Available passages are not a factual verdict."));
      if (result.termination_reason === "tool_error") node.append(el("p", "Search or page reading failed. This does not mean evidence is absent. Retry later or check the configured search provider."));
      root.append(node);
      const activity = el("details", null, "lab-card");
      activity.append(el("summary", "Research activity (completed run)"));
      for (const [i, event] of (result.events || []).entries()) {
        const title = event.tool === "local_relevance_preflight" ? "Local relevance check (before agent)" : event.tool === "search_evidence" ? "Search evidence" : event.tool === "read_passage" ? "Read a passage" : event.tool;
        activity.append(el("h4", `${i + 1}. ${title}`));
        if (event.arguments?.query) activity.append(el("p", event.arguments.query));
        for (const hit of event.output?.results || []) activity.append(el("p", hit.title || hit.chunk_id));
        const debug = el("details");
        debug.append(el("summary", "Tool details"), el("pre", JSON.stringify({input: event.arguments, output: event.output}, null, 2)));
        activity.append(debug);
      }
      if (!(result.events || []).length) activity.append(el("p", "No tools executed."));
      root.append(activity);
      if (result.assessment) renderAssessment(result.assessment, root);
      else if ((result.read_passages || []).length) {
        const passages = card("Passages inspected before stopping");
        for (const passage of result.read_passages) passages.append(el("h4", passage.title || passage.chunk_id), el("p", passage.text));
        root.append(passages);
      }
    }
  }
  const byId = id => document.getElementById(id);
  function init() {
    const form = byId("lab-form");
    if (!form) return;
    const input = byId("lab-input"), mode = byId("lab-mode"), button = byId("lab-submit");
    const status = byId("lab-status"), result = byId("lab-result");
    let busy = false;
    let authEpoch = 0;
    const savedId = byId("lab-run-id"), reopen = byId("lab-reopen");
    try { savedId.value = localStorage.getItem("debatebot.run") || ""; } catch {}
    function saveRunId(id) {
      if (!id) return;
      savedId.value = id;
      try { localStorage.setItem("debatebot.run", id); } catch {}
    }
    function controls(disabled) {
      button.disabled = mode.disabled = input.disabled = reopen.disabled = savedId.disabled = disabled;
    }
    if (typeof window !== "undefined") window.addEventListener("debatebot:authchanged", event => {
      if (!event.detail.reset) return;
      authEpoch++; busy = false; controls(false); input.value = savedId.value = "";
      result.replaceChildren(); result.hidden = true; status.textContent = "";
      byId("lab-count").textContent = "0 / 4000";
    });
    function modeChanged() {
      button.textContent = mode.value !== "evidence" ? "Research this claim" : "Analyze with evidence";
      byId("lab-mode-help").textContent = mode.value === "web_research"
        ? "Searches public web sources, then opens pages for cited evidence. Not restricted to the two local studies. Best-effort search may fail."
        : mode.value === "research"
        ? "Researches the local studies. Unrelated claims stop early; results may be partial."
        : "Checks one weak claim against the local studies.";
    }
    mode.addEventListener("change", () => { modeChanged(); result.hidden = true; status.textContent = ""; });
    input.addEventListener("input", () => { byId("lab-count").textContent = `${input.value.length} / 4000`; });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      if (busy) return;
      const value = input.value.trim(), selectedMode = mode.value;
      const requestEpoch = authEpoch;
      if (!value || value.length > 4000) {
        status.dataset.state = "error"; status.textContent = "Enter a claim or argument of up to 4000 characters."; return;
      }
      busy = true; controls(true);
      result.hidden = true; result.replaceChildren(); status.dataset.state = "loading";
      status.textContent = selectedMode === "web_research" ? "Searching public sources… Activity will appear after completion."
        : selectedMode === "research" ? "Researching the local corpus… Activity will appear after completion."
        : "Finding the weak claim and assessing retrieved evidence…";
      try {
        const response = await fetch(selectedMode !== "evidence" ? "/api/research" : "/api/findhole/evidence", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(selectedMode !== "evidence" ? { claim: value, source: selectedMode === "web_research" ? "web" : "local" } : { argument: value, limit: 3 })
        });
        if (requestEpoch !== authEpoch) return;
        if (!response.ok) {
          if (response.status === 503) {
            const detail = await response.json().catch(() => ({}));
            throw new Error(detail.code === "storage_unavailable"
              ? "Application storage is unavailable. Check PostgreSQL and run database migrations on the server."
              : detail.code === "web_unavailable" ? "Web search is unavailable. Check the server's search provider configuration or retry later."
              : "Evidence is not set up. Install retrieval dependencies and run retrieval.py index on the server.");
          }
          if (response.status === 502) throw new Error("The model request or output validation failed. Your input is saved; retry when ready.");
          if ([400, 422].includes(response.status)) throw new Error("The input or evidence context exceeds supported limits. Try a shorter, specific claim.");
          throw new Error("Analysis failed. Check the server and retry.");
        }
        const payload = await response.json();
        if (requestEpoch !== authEpoch) return;
        renderResult(payload, selectedMode, result); saveRunId(payload.run_id);
        result.hidden = false; status.dataset.state = "done"; status.textContent = "Run finished. Review the evidence and its scope below.";
      } catch (error) {
        if (requestEpoch !== authEpoch) return;
        status.dataset.state = "error";
        status.textContent = error instanceof TypeError ? "Could not reach the server. Your input is saved; check your connection and retry." : error.message;
      } finally {
        if (requestEpoch === authEpoch) { busy = false; controls(false); modeChanged(); }
      }
    });
    reopen.addEventListener("click", async () => {
      if (busy) return;
      const id = savedId.value.trim();
      const requestEpoch = authEpoch;
      if (!id || id.length > 128) { status.textContent = "Enter a saved run ID."; return; }
      busy = true; controls(true); result.hidden = true;
      status.dataset.state = "loading"; status.textContent = "Loading saved result…";
      try {
        const response = await fetch(`/api/runs/${encodeURIComponent(id)}`);
        if (requestEpoch !== authEpoch) return;
        if (!response.ok) throw new Error(response.status === 404 ? "Saved run not found. Check the ID and storage configuration." : "Could not load saved result. Check the server and retry.");
        const saved = await response.json();
        if (requestEpoch !== authEpoch) return;
        if (!["research", "evidence"].includes(saved.kind)) throw new Error("Unsupported saved run type.");
        mode.value = saved.result.source_mode === "web" ? "web_research" : saved.kind; modeChanged(); input.value = saved.input_text;
        byId("lab-count").textContent = `${input.value.length} / 4000`;
        renderResult(saved.result, saved.kind, result); saveRunId(saved.id);
        result.hidden = false; status.dataset.state = "done"; status.textContent = "Saved result reopened. No new model call was made.";
      } catch (error) { if (requestEpoch === authEpoch) { status.dataset.state = "error"; status.textContent = error.message; } }
      finally { if (requestEpoch === authEpoch) { busy = false; controls(false); } }
    });
    modeChanged();
  }
  // Exposed for DOM-based renderer tests; no server data is added to global state.
  if (typeof module !== "undefined") module.exports = { safeUrl, renderResult, init };
  if (typeof document !== "undefined") init();
})();
