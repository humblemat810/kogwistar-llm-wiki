"""Static, safe browser shell for the ACL-checked email evidence viewer."""

from __future__ import annotations


def render_email_viewer_plugin() -> str:
    """Return a self-contained viewer page with no embedded email content."""

    return """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>LLM-Wiki Email Evidence</title>
  <style>
    :root { color-scheme: light dark; font: 16px system-ui, sans-serif; }
    body { max-width: 70rem; margin: 2rem auto; padding: 0 1rem; }
    form { display: grid; grid-template-columns: repeat(3, 1fr); gap: .6rem; }
    input, button { font: inherit; padding: .55rem; }
    button { cursor: pointer; }
    #status { margin: 1rem 0; }
    #body { white-space: pre-wrap; overflow-wrap: anywhere; }
    #mapping { white-space: pre-wrap; overflow-wrap: anywhere; }
    #actions { display: grid; gap: .6rem; margin-top: 1rem; }
    #statement { min-height: 5rem; width: 100%; box-sizing: border-box; }
  </style>
</head>
<body>
  <main>
    <h1>Email evidence</h1>
    <p>Only authorized, immutable evidence is displayed. HTML mail is rendered as text.</p>
    <form id="lookup">
      <label>Workspace <input name="workspace_id" required></label>
      <label>Stream <input name="stream_id" required></label>
      <label>Source revision <input name="source_revision_id" required></label>
      <button type="submit">Load evidence</button>
    </form>
    <p id="status" role="status"></p>
    <article id="result" hidden>
      <h2 id="subject"></h2>
      <p id="metadata"></p>
      <pre id="body"></pre>
      <h3>Mapping review</h3>
      <p id="review"></p>
      <pre id="mapping"></pre>
      <section id="actions" hidden>
        <button id="accept" type="button">Confirm mapping acceptance</button>
        <label>Memory statement
          <textarea id="statement" required></textarea>
        </label>
        <button id="promote" type="button">Confirm memory promotion</button>
      </section>
    </article>
  </main>
  <script>
    (() => {
      const form = document.getElementById("lookup");
      const status = document.getElementById("status");
      const result = document.getElementById("result");
      const subject = document.getElementById("subject");
      const metadata = document.getElementById("metadata");
      const body = document.getElementById("body");
      const review = document.getElementById("review");
      const mapping = document.getElementById("mapping");
      const actions = document.getElementById("actions");
      const accept = document.getElementById("accept");
      const statement = document.getElementById("statement");
      const promote = document.getElementById("promote");
      let currentPayload = null;
      const text = (node, value) => { node.textContent = value == null ? "" : String(value); };
      const postJson = async (path, payload) => {
        const response = await fetch(path, {
          method: "POST",
          credentials: "same-origin",
          headers: { "accept": "application/json", "content-type": "application/json" },
          body: JSON.stringify(payload)
        });
        const result = await response.json();
        if (!response.ok) {
          throw new Error(result.detail || result.error || `HTTP ${response.status}`);
        }
        return result;
      };
      const load = async () => {
        result.hidden = true;
        text(status, "Loading...");
        const query = new URLSearchParams(new FormData(form));
        const response = await fetch(`/api/email/view?${query.toString()}`, {
          credentials: "same-origin",
          headers: { "accept": "application/json" }
        });
        const payload = await response.json();
        if (!response.ok || payload.status !== "ok") {
          throw new Error(payload.detail || payload.status || `HTTP ${response.status}`);
        }
        currentPayload = payload;
        text(subject, payload.subject || "(no subject)");
        text(metadata, `Revision: ${payload.source_revision_id || ""} | Digest: ${payload.content_sha256 || ""}`);
        text(body, payload.body_text || "");
        text(review, `Mapping: ${payload.mapping_id || "none"} | Status: ${payload.mapping_status || "unknown"}`);
        text(mapping, JSON.stringify(payload.structural_proposals || {}, null, 2));
        actions.hidden = false;
        accept.disabled = payload.mapping_status === "accepted";
        promote.disabled = payload.mapping_status !== "accepted";
        result.hidden = false;
        text(status, "Loaded authorized evidence.");
      };
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          await load();
        } catch (error) {
          text(status, `Unable to load evidence: ${error.message || error}`);
        }
      });
      accept.addEventListener("click", async () => {
        if (!currentPayload) return;
        try {
          text(status, "Accepting mapping...");
          await postJson("/api/email/accept", {
            workspace_id: currentPayload.workspace_id,
            stream_id: currentPayload.stream_id,
            source_revision_id: currentPayload.source_revision_id,
            source_document_id: currentPayload.source_document_id,
            confirmed: true,
            confidence: 0.75
          });
          await load();
        } catch (error) {
          text(status, `Unable to accept mapping: ${error.message || error}`);
        }
      });
      promote.addEventListener("click", async () => {
        if (!currentPayload || !statement.value.trim()) {
          text(status, "Enter a memory statement before promotion.");
          return;
        }
        try {
          text(status, "Promoting confirmed memory...");
          await postJson("/api/email/memory/promote", {
            workspace_id: currentPayload.workspace_id,
            stream_id: currentPayload.stream_id,
            source_revision_id: currentPayload.source_revision_id,
            statement: statement.value.trim(),
            confirmed: true
          });
          text(status, "Memory promotion completed.");
        } catch (error) {
          text(status, `Unable to promote memory: ${error.message || error}`);
        }
      });
    })();
  </script>
</body>
</html>
"""


__all__ = ["render_email_viewer_plugin"]
