(function () {
  const runId = window.__RUN_ID__;
  if (!runId) return;
  const logEl = document.getElementById("action-log");
  const statusEl = document.getElementById("run-status");

  const es = new EventSource(`/wg/hunt/${runId}/stream`);

  es.onmessage = (evt) => {
    if (!evt.data) return;
    let msg;
    try {
      msg = JSON.parse(evt.data);
    } catch {
      return;
    }
    if (msg.kind === "stream-end") {
      statusEl.textContent = msg.status || "done";
      statusEl.className = "status-" + (msg.status || "done");
      es.close();
      // Reload once at the very end so that listings/messages render.
      setTimeout(() => window.location.reload(), 600);
      return;
    }

    statusEl.textContent = "running";
    statusEl.className = "status-running";

    const li = document.createElement("li");
    li.dataset.kind = msg.kind;
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = msg.kind;
    const summary = document.createElement("span");
    summary.className = "summary";
    summary.textContent = msg.summary;
    li.appendChild(kind);
    li.appendChild(summary);
    if (msg.detail) {
      const pre = document.createElement("pre");
      pre.textContent = msg.detail;
      li.appendChild(pre);
    }
    logEl.prepend(li);
  };

  es.onerror = () => {
    statusEl.textContent = "disconnected";
    statusEl.className = "status-failed";
  };
})();
