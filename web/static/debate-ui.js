/* Debate interactions. Account changes invalidate pending responses. */
(() => {
let sessionId = null;
let activePersonaName = null;
let authGeneration = 0;
let turnPending = false;

const $ = (id) => document.getElementById(id);

async function api(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body) opts.body = JSON.stringify(body);
  const r = await fetch(path, opts);
  if (!r.ok) {
    const messages = {401: "Please sign in again.", 409: "This debate changed in another tab. Reload before continuing.",
      429: "Too many requests. Wait a moment and retry.", 502: "The model could not complete this request. Please retry.",
      503: "The service is temporarily unavailable. Check the server and retry."};
    throw new Error(messages[r.status] || "Request failed. Please retry.");
  }
  return r.json();
}

// Load personas + build buttons
async function loadPersonas() {
  const { personas } = await api("GET", "/api/personas");
  const container = $("persona-buttons");
  container.innerHTML = "";
  for (const p of personas) {
    const btn = document.createElement("button");
    btn.className = "persona-card";
    btn.innerHTML = `<div class="font-semibold">${escapeHtml(p.name)}</div><div class="text-xs text-neutral-400 mt-1">${escapeHtml(({socratic:"Question your assumptions.", examiner:"Challenge vague claims and missing evidence.", steelman:"Face the strongest opposing argument."})[p.key] || p.tagline)}</div>`;
    btn.onclick = async () => {
      const generation = authGeneration;
      const buttons = container.querySelectorAll("button");
      for (const button of buttons) button.disabled = true;
      try { await startDebate(p.key); }
      catch (error) { if (generation === authGeneration) $("debate-status").textContent = error.message; }
      finally { for (const button of buttons) button.disabled = false; }
    };
    container.appendChild(btn);
  }
}

async function startDebate(personaKey) {
  $("debate-status").textContent = "";
  const generation = authGeneration;
  const r = await api("POST", "/api/debate", { persona_key: personaKey });
  if (generation !== authGeneration) return;
  sessionId = r.session_id;
  activePersonaName = r.persona_name;
  try { localStorage.setItem("debatebot.session", sessionId); } catch {}
  $("persona-picker").classList.add("hidden");
  $("debate").classList.remove("hidden");
  $("active-persona").textContent = activePersonaName;
  $("messages").innerHTML = "";
  $("score-panel").classList.add("hidden");
  $("hole-panel").classList.add("hidden");
  $("turn-input").focus();
}

function addMessage(who, text) {
  const div = document.createElement("div");
  const cls = who === "YOU" ? "msg-you" : "msg-bot";
  div.className = cls;
  const label = document.createElement("div");
  label.className = "message-label";
  label.textContent = who === "YOU" ? "You" : activePersonaName || who;
  const body = document.createElement("div");
  body.className = "message-body";
  if (who !== "YOU" && window.DebateMessage) window.DebateMessage.render(body, text);
  else body.textContent = text;
  div.appendChild(label); div.appendChild(body);
  $("messages").appendChild(div);
  div.scrollIntoView({ behavior: "auto", block: "start" });
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}

function turnControls(disabled) {
  for (const id of ["turn-input", "btn-score", "btn-findhole", "btn-newdebate"])
    $(id).disabled = disabled;
  $("turn-form").querySelector("button").disabled = disabled;
}

$("turn-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const input = $("turn-input");
  const msg = input.value.trim();
  const generation = authGeneration;
  if (!msg || !sessionId || turnPending) return;
  turnPending = true;
  turnControls(true);
  $("turn-meta").textContent = "Thinking...";
  try {
    const r = await api("POST", `/api/debate/${sessionId}/turn`, { message: msg });
    if (generation !== authGeneration) return;
    input.value = "";
    addMessage("YOU", msg);
    addMessage(activePersonaName.toUpperCase(), r.reply);
    $("turn-meta").textContent = `Turn ${r.turn_count}`;
  } catch (err) {
    if (generation !== authGeneration) return;
    $("turn-meta").textContent = `Error: ${err.message}`;
  } finally {
    if (generation === authGeneration) { turnPending = false; turnControls(false); input.focus(); }
  }
});

const pendingAnalysis = new Set();
async function showAnalysis(buttonId, panelId, path, body, render, loading) {
  if (pendingAnalysis.has(buttonId)) return;
  const generation = authGeneration;
  const button = $(buttonId), panel = $(panelId);
  pendingAnalysis.add(buttonId); button.disabled = true;
  panel.classList.remove("hidden"); panel.textContent = loading;
  try {
    const result = await api("POST", path, body);
    if (generation === authGeneration) panel.innerHTML = render(result);
  } catch (error) {
    if (generation === authGeneration) panel.textContent = error.message;
  } finally {
    pendingAnalysis.delete(buttonId); button.disabled = false;
  }
}

$("btn-score").addEventListener("click", () => {
  if (sessionId) return showAnalysis("btn-score", "score-panel", `/api/debate/${sessionId}/score`,
    undefined, renderScore, "Scoring… Unchanged results are reused for 10 minutes.");
});
$("btn-findhole").addEventListener("click", () => {
  if (sessionId) return showAnalysis("btn-findhole", "hole-panel", `/api/debate/${sessionId}/findhole`,
    undefined, renderHole, "Finding the weakest claim…");
});

$("btn-newdebate").addEventListener("click", () => {
  authGeneration++;
  sessionId = null;
  try { localStorage.removeItem("debatebot.session"); } catch {}
  $("debate").classList.add("hidden");
  $("persona-picker").classList.remove("hidden");
  $("score-panel").classList.add("hidden");
  $("hole-panel").classList.add("hidden");
  $("messages").replaceChildren();
  $("turn-meta").textContent = "";
});

$("btn-standalone-findhole").addEventListener("click", () => {
  const argument = $("standalone-arg").value.trim();
  if (argument) return showAnalysis("btn-standalone-findhole", "standalone-hole-panel", "/api/findhole",
    {argument}, renderHole, "Finding the weakest claim…");
});

function renderBar(score) {
  const pct = Math.max(0, Math.min(10, Number(score) || 0)) * 10;
  const color = score >= 8 ? "bg-green-700" : score >= 5 ? "bg-yellow-700" : "bg-red-700";
  return `<div class="w-full bg-neutral-800 rounded h-2 overflow-hidden"><div class="${color} h-2" style="width:${pct}%"></div></div>`;
}

function renderScore(s) {
  const row = (label, axis) => `
    <div class="mb-3">
      <div class="flex justify-between text-sm mb-1"><span>${label}</span><span class="font-mono">${axis.score}/10</span></div>
      ${renderBar(axis.score)}
      <div class="text-xs text-neutral-400 mt-1">${escapeHtml(axis.rationale)}</div>
    </div>`;
  return `
    <h3 class="text-lg font-semibold mb-3">Argument score — overall ${s.overall}/10</h3>
    ${row("Logical coherence",    s.logical_coherence)}
    ${row("Evidence quality",     s.evidence_quality)}
    ${row("Opposition handling",  s.opposition_handling)}
    ${row("Clarity",              s.clarity)}
    <div class="mt-4 p-3 bg-neutral-900 rounded text-sm"><strong>Verdict:</strong> ${escapeHtml(s.summary)}</div>
  `;
}

function renderHole(h) {
  return `
    <h3 class="text-lg font-semibold mb-2">The hole — severity ${h.severity}/10</h3>
    <div class="mb-3">
      <div class="text-xs text-neutral-400 uppercase tracking-wide">Weakest claim (verbatim)</div>
      <div class="text-amber-200 italic mt-1">"${escapeHtml(h.weakest_claim)}"</div>
    </div>
    <div class="mb-3">
      <div class="text-xs text-neutral-400 uppercase tracking-wide">Why it's weak</div>
      <div class="mt-1">${escapeHtml(h.why_weak)}</div>
    </div>
    <div class="mb-3">
      <div class="text-xs text-neutral-400 uppercase tracking-wide">The attack</div>
      <div class="mt-1">${escapeHtml(h.the_attack)}</div>
    </div>
    <div>
      <div class="text-xs text-neutral-400 uppercase tracking-wide">How to defend it</div>
      <div class="mt-1 text-green-200">${escapeHtml(h.how_to_defend)}</div>
    </div>
  `;
}

async function resumeDebate() {
  const generation = authGeneration;
  let saved;
  try { saved = localStorage.getItem("debatebot.session"); } catch { return; }
  if (!saved) return;
  try {
    const data = await api("GET", `/api/debate/${encodeURIComponent(saved)}`);
    if (generation !== authGeneration) return;
    sessionId = saved; activePersonaName = data.persona_name;
    $("persona-picker").classList.add("hidden"); $("debate").classList.remove("hidden");
    $("active-persona").textContent = activePersonaName;
    for (const message of data.messages || []) addMessage(message.role === "user" ? "YOU" : activePersonaName.toUpperCase(), message.content);
    $("turn-meta").textContent = `${data.turn_count} turns restored`;
  } catch { /* Keep the stored ID so a temporary outage does not erase it. */ }
}
loadPersonas().then(resumeDebate).catch(() => {
  $("persona-buttons").textContent = "Could not load personas. Check the server and reload.";
});
window.addEventListener("debatebot:authchanged", event => {
  if (!event.detail.reset) return;
  authGeneration++;
  turnPending = false; turnControls(false);
  sessionId = null; activePersonaName = null;
  $("messages").replaceChildren(); $("debate").classList.add("hidden");
  $("persona-picker").classList.remove("hidden");
  for (const id of ["score-panel", "hole-panel", "standalone-hole-panel"]) {
    $(id).replaceChildren(); $(id).classList.add("hidden");
  }
  $("turn-input").value = ""; $("turn-meta").textContent = ""; $("standalone-arg").value = "";
  try { localStorage.removeItem("debatebot.session"); localStorage.removeItem("debatebot.run"); } catch {}
});

})();
