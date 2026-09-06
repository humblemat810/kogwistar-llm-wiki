import { test, expect } from "@playwright/test";

test("renders a bounded grounded lens with an accessible fallback list", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Think in a living graph." })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Verifier" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Visible claims" })).toBeVisible();
  await expect(page.getByText("Watermark 12")).toBeVisible();
  await expect(page.getByText("offline fixture")).toBeVisible();
});

test("supports pinning and mode selection without changing the bounded snapshot", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Pin" }).click();
  await expect(page.getByText("1 pinned")).toBeVisible();
  await page.getByLabel("Orchestration mode").selectOption("codex");
  await expect(page.getByLabel("Orchestration mode")).toHaveValue("codex");
  await expect(page.getByText("3 visible")).toBeVisible();
});

test("keeps the initial bounded workbench responsive", async ({ page }) => {
  const started = Date.now();
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Visible claims" })).toBeVisible();
  expect(Date.now() - started).toBeLessThan(5_000);
  await expect(page.locator(".claim")).toHaveCount(3);
});

test("does not create horizontal overflow on a narrow viewport", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Visible claims" })).toBeVisible();
  const widths = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(widths.document).toBeLessThanOrEqual(widths.viewport);
  expect(widths.body).toBeLessThanOrEqual(widths.viewport);
});

test("clicking a claim requests an anchored bounded expansion and carries pins", async ({ page }) => {
  const lensRequests: string[] = [];
  await page.route("**/api/lens**", async (route) => {
    lensRequests.push(route.request().url());
    await route.abort();
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Pin" }).click();
  await page.getByRole("button", { name: /Outcome reward/ }).click();
  await expect.poll(() => lensRequests.length).toBeGreaterThan(0);
  expect(lensRequests.at(-1)).toContain("explicit_anchor_id=outcome");
  expect(lensRequests.at(-1)).toContain("pinned_node_id=verifier");
});

test("workspace query parameter is carried into live graph requests", async ({ page }) => {
  let requestedWorkspace = "";
  await page.route("**/api/lens**", async (route) => {
    requestedWorkspace = new URL(route.request().url()).searchParams.get("workspace_id") ?? "";
    await route.abort();
  });
  await page.goto("/?workspace_id=team-alpha");
  await page.getByRole("button", { name: /Outcome reward/ }).click();
  await expect.poll(() => requestedWorkspace).toBe("team-alpha");
});

test("proposal validation is confirmation-gated and visible in the evidence drawer", async ({ page }) => {
  await page.route("**/api/proposal/validate", async (route) => {
    await route.fulfill({ json: { accepted: true, reason: "ready_for_explicit_confirmation" } });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Validate proposal" }).click();
  await expect(page.getByRole("status")).toHaveText("Ready for explicit confirmation");
});

test("a browser query reaches the Codex listener contract and shows its grounded response", async ({ page }) => {
  let received: Record<string, unknown> | undefined;
  let pollCount = 0;
  const response = {
    mode: "codex",
    agent_status: "active",
    answer: {
      text: "Listener inspected the bounded lens and proposes no change.",
      outcome: "no_change",
      lens_id: "lens:listener",
      source_watermark: 12,
      cited_entity_ids: ["verifier"],
      insufficiency_reason: null,
    },
    snapshot: {
      lens_id: "lens:listener",
      workspace_id: "rl-fixture",
      source_watermark: 12,
      projected_at_ms: 1,
      completeness: "bounded",
      nodes: [], edges: [], hyperedges: [], participations: [],
      anchor_explanations: [], selection_explanations: [],
      omitted_summary: {}, query_timing_ms: 1,
    },
    history: { id: "history:1", session_id: "browser-1", lens_id: "lens:listener", outcome: "no_change", created_at_ms: 1 },
  };
  await page.route("**/api/interactions**", async (route) => {
    if (route.request().method() === "POST") {
      received = route.request().postDataJSON() as Record<string, unknown>;
      await route.fulfill({ status: 202, json: { interaction_id: "turn-1", workspace_id: "rl-fixture", session_id: "browser-1", status: "pending", submitted_at_ms: 1, completed_at_ms: null, response: null, error: null } });
      return;
    }
    pollCount += 1;
    await route.fulfill({
      json: {
        interaction_id: "turn-1", workspace_id: "rl-fixture", session_id: "browser-1",
        status: pollCount > 1 ? "completed" : "pending", submitted_at_ms: 1,
        completed_at_ms: pollCount > 1 ? 2 : null, response: pollCount > 1 ? response : null, error: null,
      },
    });
  });
  await page.goto("/");
  await page.getByLabel("Orchestration mode").selectOption("codex");
  await page.getByLabel("Ask the graph").fill("Should this change?");
  await page.getByRole("button", { name: "Explore" }).click();
  await expect(page.getByText("Listener inspected the bounded lens and proposes no change.")).toBeVisible();
  await expect(page.getByText("agent active")).toBeVisible();
  await expect(page.getByText("No grounded candidates in this scoped lens.")).toBeVisible();
  expect(received).toMatchObject({ query_text: "Should this change?", mode: "codex", workspace_id: "rl-fixture" });
  expect(pollCount).toBeGreaterThan(1);
});

test("a live Codex failure is visible and never becomes an offline answer", async ({ page }) => {
  await page.route("**/api/interactions**", async (route) => {
    await route.fulfill({ status: 503, json: { error: "service_unavailable", detail: "Codex unavailable" } });
  });
  await page.goto("/");
  await page.getByLabel("Orchestration mode").selectOption("codex");
  await page.getByRole("button", { name: "Explore" }).click();
  await expect(page.getByRole("alert")).toContainText("Workbench request failed");
  await expect(page.getByText("offline fixture")).toBeVisible();
  await expect(page.getByText("Offline fixture context contains")).toHaveCount(0);
});

test("a cockpit proposal remains review-only until the user explicitly confirms it", async ({ page }) => {
  const proposal = {
    operation: "maintenance_patch", lens_id: "lens:proposal", source_watermark: 12,
    target_ids: [], evidence_ids: ["verifier"], maintenance_patch: { patch_id: "p-1" },
  };
  await page.route("**/api/interactions**", async (route) => {
    if (route.request().method() === "POST") {
      await route.fulfill({ status: 202, json: { interaction_id: "proposal-1", workspace_id: "rl-fixture", session_id: "browser", status: "pending", submitted_at_ms: 1, completed_at_ms: null, response: null, error: null } });
      return;
    }
    await route.fulfill({ json: {
      interaction_id: "proposal-1", workspace_id: "rl-fixture", session_id: "browser", status: "completed", submitted_at_ms: 1, completed_at_ms: 2, error: null,
      response: {
        mode: "codex", agent_status: "cockpit_active",
        answer: { text: "A grounded proposal is ready for review.", outcome: "proposal", lens_id: "lens:proposal", source_watermark: 12, cited_entity_ids: ["verifier"], insufficiency_reason: null, proposal },
        snapshot: sampleSnapshot(), history: { id: "history:proposal", session_id: "browser", lens_id: "lens:proposal", outcome: "proposal", created_at_ms: 2 },
        proposal_request: { workspace_id: "rl-fixture", query_text: "Add it" },
      },
    } });
  });
  let confirmation: Record<string, unknown> | undefined;
  await page.route("**/api/proposal/confirm", async (route) => {
    confirmation = route.request().postDataJSON() as Record<string, unknown>;
    await route.fulfill({ json: { status: "applied", lens: sampleSnapshot() } });
  });
  await page.goto("/");
  await page.getByLabel("Orchestration mode").selectOption("codex");
  await page.getByLabel("Ask the graph").fill("Add it");
  await page.getByRole("button", { name: "Explore" }).click();
  await expect(page.getByRole("button", { name: "Confirm proposed graph change" })).toBeVisible();
  await page.getByRole("button", { name: "Confirm proposed graph change" }).click();
  await expect(page.getByText("Proposal applied")).toBeVisible();
  await expect(page.getByRole("button", { name: "Confirm proposed graph change" })).toHaveCount(0);
  expect(confirmation).toMatchObject({ confirmed: true, workspace_id: "rl-fixture", interaction_id: "proposal-1", proposal });
});

test("a live Codex lens failure stays visible instead of replacing the graph with the fixture", async ({ page }) => {
  await page.route("**/api/lens**", async (route) => {
    await route.fulfill({ status: 503, json: { error: "service_unavailable" } });
  });
  await page.goto("/");
  await page.getByLabel("Orchestration mode").selectOption("codex");
  await page.getByRole("button", { name: /Outcome reward/ }).click();
  await expect(page.getByRole("alert")).toContainText("Lens request failed (503)");
  await expect(page.getByText("Verifier").first()).toBeVisible();
});

test("a newer Codex query cancels stale polling and owns the visible answer", async ({ page }) => {
  let submission = 0;
  await page.route("**/api/interactions**", async (route) => {
    if (route.request().method() === "POST") {
      submission += 1;
      await route.fulfill({ status: 202, json: { interaction_id: `turn-${submission}`, workspace_id: "rl-fixture", session_id: "browser", status: "pending", submitted_at_ms: submission, completed_at_ms: null, response: null, error: null } });
      return;
    }
    const interactionId = new URL(route.request().url()).searchParams.get("interaction_id") ?? "";
    const isSecond = interactionId === "turn-2";
    await route.fulfill({ json: {
      interaction_id: interactionId, workspace_id: "rl-fixture", session_id: "browser",
      status: isSecond ? "completed" : "pending", submitted_at_ms: 1, completed_at_ms: isSecond ? 2 : null,
      error: null,
      response: isSecond ? {
        mode: "codex", agent_status: "active",
        answer: { text: "The second question owns this answer.", outcome: "no_change", lens_id: "lens:second", source_watermark: 12, cited_entity_ids: [], insufficiency_reason: null },
        snapshot: { ...sampleSnapshot(), lens_id: "lens:second" },
        history: { id: "history:2", session_id: "browser", lens_id: "lens:second", outcome: "no_change", created_at_ms: 2 },
      } : null,
    } });
  });
  await page.goto("/");
  await page.getByLabel("Orchestration mode").selectOption("codex");
  await page.getByLabel("Ask the graph").fill("First question");
  await page.getByLabel("Ask the graph").press("Enter");
  await page.getByLabel("Ask the graph").fill("Second question");
  await page.getByLabel("Ask the graph").press("Enter");
  await expect(page.getByText("The second question owns this answer.")).toBeVisible();
  expect(submission).toBe(2);
});

function sampleSnapshot() {
  return {
    lens_id: "lens:test", workspace_id: "rl-fixture", source_watermark: 12, projected_at_ms: 1,
    completeness: "bounded", nodes: [], edges: [], hyperedges: [], participations: [],
    anchor_explanations: [], selection_explanations: [], omitted_summary: {}, query_timing_ms: 1,
  };
}
