import { useState } from "react";

export type EmailViewerFetch = (input: RequestInfo | URL, init?: RequestInit) => Promise<Response>;

type EmailReview = {
  status: string;
  source_document_id?: string;
  patch_id?: string | null;
};

export type EmailView = {
  status: string;
  workspace_id?: string;
  stream_id?: string;
  source_revision_id?: string;
  subject?: string;
  date?: string | null;
  from?: Array<{ display_name: string; address: string }>;
  to?: Array<{ display_name: string; address: string }>;
  cc?: Array<{ display_name: string; address: string }>;
  body_text?: string;
  attachments?: Array<Record<string, unknown>>;
  mapping_id?: string;
  mapping_status?: string;
  review?: EmailReview;
};

type EmailViewerPanelProps = {
  workspaceId: string;
  apiFetch: EmailViewerFetch;
};

function formatAddresses(values: Array<{ display_name: string; address: string }> | undefined): string {
  return (values ?? []).map((item) => item.display_name ? `${item.display_name} <${item.address}>` : item.address).join(", ");
}

export function EmailViewerPanel({ workspaceId, apiFetch }: EmailViewerPanelProps) {
  const params = new URLSearchParams(window.location.search);
  const [streamId, setStreamId] = useState(params.get("email_stream_id") ?? "");
  const [sourceRevisionId, setSourceRevisionId] = useState(params.get("email_source_revision_id") ?? "");
  const [sourceDocumentId, setSourceDocumentId] = useState(params.get("email_source_document_id") ?? "");
  const [view, setView] = useState<EmailView | null>(null);
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);

  async function loadEmail() {
    if (!streamId.trim() || !sourceRevisionId.trim()) {
      setMessage("Stream ID and source revision ID are required.");
      return;
    }
    setLoading(true);
    setMessage("");
    try {
      const query = new URLSearchParams({
        workspace_id: workspaceId,
        stream_id: streamId,
        source_revision_id: sourceRevisionId,
      });
      const response = await apiFetch(`/api/email/view?${query.toString()}`, { headers: { accept: "application/json" } });
      const payload = await response.json() as EmailView & { detail?: string; error?: string };
      if (!response.ok) throw new Error(payload.detail ?? payload.error ?? `Email request failed (${response.status})`);
      setView(payload);
      setSourceDocumentId(payload.review?.source_document_id ?? sourceDocumentId);
      setMessage(payload.status === "not_found" ? "Email evidence was not found in this workspace." : "Email evidence loaded.");
    } catch (error) {
      setView(null);
      setMessage(error instanceof Error ? error.message : "Email request failed");
    } finally {
      setLoading(false);
    }
  }

  async function acceptMapping() {
    if (!view?.mapping_id || !sourceDocumentId.trim()) {
      setMessage("A source document ID is required before acceptance.");
      return;
    }
    setLoading(true);
    setMessage("");
    try {
      const response = await apiFetch("/api/email/accept", {
        method: "POST",
        headers: { "content-type": "application/json", accept: "application/json" },
        body: JSON.stringify({
          workspace_id: workspaceId,
          stream_id: streamId,
          source_revision_id: sourceRevisionId,
          source_document_id: sourceDocumentId,
          confirmed: true,
        }),
      });
      const payload = await response.json() as { status?: string; detail?: string; error?: string };
      if (!response.ok) throw new Error(payload.detail ?? payload.error ?? `Acceptance failed (${response.status})`);
      const outcome = payload.status === "accepted"
        ? "Mapping was already accepted; no duplicate patch was applied."
        : `Mapping ${payload.status ?? "updated"}.`;
      await loadEmail();
      setMessage(outcome);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Acceptance failed");
      setLoading(false);
    }
  }

  const canAccept = view?.status === "ok" && view.mapping_status !== "accepted" && Boolean(view.mapping_id);
  return (
    <section className="email-viewer-panel" aria-label="Email evidence viewer">
      <div className="email-viewer-heading">
        <div><p className="eyebrow">EMAIL EVIDENCE</p><h2>Inspect a source message</h2></div>
        <span className="email-viewer-status">{view?.mapping_status ?? "not loaded"}</span>
      </div>
      <div className="email-viewer-controls">
        <label>Stream ID<input aria-label="Email stream ID" value={streamId} onChange={(event) => setStreamId(event.target.value)} placeholder="mailbox-stream-id" /></label>
        <label>Source revision ID<input aria-label="Email source revision ID" value={sourceRevisionId} onChange={(event) => setSourceRevisionId(event.target.value)} placeholder="imap:uidvalidity:uid" /></label>
        <label>Source document ID<input aria-label="Email source document ID" value={sourceDocumentId} onChange={(event) => setSourceDocumentId(event.target.value)} placeholder="email-source:..." /></label>
        <button className="primary" disabled={loading} onClick={() => void loadEmail()}>{loading ? "Loading..." : "Load email"}</button>
      </div>
      {message && <p className="email-viewer-message" role="status">{message}</p>}
      {view?.status === "ok" && <div className="email-message">
        <p className="email-subject">{view.subject || "(no subject)"}</p>
        <dl className="email-headers">
          <div><dt>From</dt><dd>{formatAddresses(view.from)}</dd></div>
          <div><dt>To</dt><dd>{formatAddresses(view.to)}</dd></div>
          {view.cc && view.cc.length > 0 && <div><dt>CC</dt><dd>{formatAddresses(view.cc)}</dd></div>}
          {view.date && <div><dt>Date</dt><dd>{view.date}</dd></div>}
        </dl>
        <pre className="email-body">{view.body_text ?? ""}</pre>
        <div className="email-review-actions">
          <span>Mapping {view.mapping_id ?? "unavailable"} / {view.mapping_status ?? "pending"}</span>
          {canAccept && <button onClick={() => void acceptMapping()}>Accept mapping</button>}
        </div>
      </div>}
    </section>
  );
}
