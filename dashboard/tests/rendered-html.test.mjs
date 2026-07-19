import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", { headers: { accept: "text/html" } }),
    { ASSETS: { fetch: async () => new Response("Not found", { status: 404 }) } },
    { waitUntil() {}, passThroughOnException() {} },
  );
}

test("server-renders the Aurora explorer shell", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);
  assert.match(response.headers.get("content-security-policy") ?? "", /default-src 'self'/);
  assert.match(response.headers.get("content-security-policy") ?? "", /media-src 'self' blob:/);
  assert.equal(response.headers.get("x-frame-options"), "DENY");

  const html = await response.text();
  assert.match(html, /<title>Aurora Run Explorer \| Fischer Product Lab<\/title>/i);
  assert.match(html, /og-fischer\.png/i);
  assert.match(html, /Assembling the incident dossier/i);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/i);
});

test("showcase manifest is portable and internally complete", async () => {
  const root = new URL("../public/data/", import.meta.url);
  const manifest = JSON.parse(await readFile(new URL("manifest.json", root), "utf8"));

  assert.match(manifest.schema_version, /^1\./);
  assert.ok(manifest.stories.length >= 4);
  assert.ok(manifest.artifacts.length >= 17);
  assert.ok(manifest.stories.some((story) => story.id === "fallback-budget-contrast"));
  assert.ok(manifest.stories.some((story) => story.artifact_ids.includes(manifest.default_artifact_id)));

  const artifactIds = new Set(manifest.artifacts.map((artifact) => artifact.id));
  assert.equal(artifactIds.size, manifest.artifacts.length);

  for (const story of manifest.stories) {
    assert.ok(story.artifact_ids.length > 0);
    for (const artifactId of story.artifact_ids) assert.ok(artifactIds.has(artifactId));
    for (const field of [
      "business_context",
      "what_is_being_tested",
      "why_it_matters",
      "what_to_watch",
      "simple_explanation",
      "everyday_analogy",
      "business_value",
      "build_rationale",
    ]) {
      assert.equal(typeof story.metadata[field], "string");
      assert.ok(story.metadata[field].length > 30);
    }
    assert.equal(story.metadata.exact_steps.length, 4);
    for (const step of story.metadata.exact_steps) {
      assert.equal(typeof step.label, "string");
      assert.equal(typeof step.explanation, "string");
      assert.ok(step.label.length > 3);
      assert.ok(step.explanation.length > 30);
    }
  }

  for (const artifact of manifest.artifacts) {
    assert.ok(!artifact.path.startsWith("/") && !artifact.path.includes(".."));
    const envelope = JSON.parse(await readFile(new URL(artifact.path, root), "utf8"));
    assert.equal(envelope.id, artifact.id);
    assert.equal(envelope.schema_version, manifest.schema_version);
    if (artifact.type === "run") {
      assert.ok(Array.isArray(envelope.data.trace));
      assert.ok(envelope.metrics && typeof envelope.metrics === "object");
    }
  }
});

test("budget story preserves atomic grant and denial proof", async () => {
  const root = new URL("../public/data/", import.meta.url);
  const manifest = JSON.parse(await readFile(new URL("manifest.json", root), "utf8"));
  const byId = new Map(manifest.artifacts.map((artifact) => [artifact.id, artifact]));
  const faultRef = byId.get("budget-fault-with-fallback");
  const controlRef = byId.get("budget-control-with-fallback");
  assert.ok(faultRef && controlRef);

  const [fault, control] = await Promise.all([
    readFile(new URL(faultRef.path, root), "utf8").then(JSON.parse),
    readFile(new URL(controlRef.path, root), "utf8").then(JSON.parse),
  ]);

  const faultKinds = fault.data.trace.map((event) => event.kind);
  const requestIndex = faultKinds.indexOf("budget_reservation_requested");
  const deniedIndex = faultKinds.indexOf("budget_reservation_denied");
  const skippedIndex = faultKinds.indexOf("fallback_skipped");
  assert.ok(requestIndex >= 0 && requestIndex < deniedIndex && deniedIndex < skippedIndex);
  assert.deepEqual(
    {
      decision: fault.metadata.scoped_budget.decision,
      ledgerUnchanged: fault.metadata.scoped_budget.ledger_unchanged,
      noDispatch: fault.metadata.scoped_budget.no_dispatch,
      scopedCost: fault.metadata.scoped_budget.scoped_usage_cost_units,
      zeroSpend: fault.metadata.scoped_budget.zero_spend,
    },
    {
      decision: "denied",
      ledgerUnchanged: true,
      noDispatch: true,
      scopedCost: 0,
      zeroSpend: true,
    },
  );
  const skippedTask = fault.data.task_results.find((task) => task.task_id === "broad_log_scan");
  assert.deepEqual(
    [skippedTask.status, skippedTask.attempts, skippedTask.cost_units, skippedTask.tool_calls],
    ["cancelled", 0, 0, 0],
  );

  const controlKinds = control.data.trace.map((event) => event.kind);
  assert.ok(
    controlKinds.indexOf("budget_reservation_requested") <
      controlKinds.indexOf("budget_reservation_granted"),
  );
  assert.ok(
    controlKinds.indexOf("budget_reservation_granted") <
      controlKinds.indexOf("task_reassigned"),
  );
  assert.equal(control.metadata.scoped_budget.decision, "granted");
  assert.equal(control.metadata.scoped_budget.scoped_usage_cost_units, 4);
});

test("starter preview dependencies are fully removed", async () => {
  const [page, packageJson] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../package.json", import.meta.url), "utf8"),
  ]);

  assert.match(page, /Aurora Run Explorer/);
  assert.match(page, /Plain-English demo guide/);
  assert.match(page, /What exactly happens in this demo/);
  assert.match(page, /The value it demonstrates/);
  assert.match(page, /Why we built this demo/);
  assert.match(page, /Active investigation period/);
  assert.match(page, /Business milestones/);
  assert.match(page, /Response approved/);
  for (const field of [
    "what_to_watch",
    "simple_explanation",
    "everyday_analogy",
    "business_value",
    "build_rationale",
    "exact_steps",
  ]) assert.match(page, new RegExp(field));
  assert.doesNotMatch(page, /_sites-preview|SkeletonPreview|dangerouslySetInnerHTML/);
  assert.doesNotMatch(packageJson, /react-loading-skeleton|site-creator-vinext-starter/);
});

test("ElevenLabs narration assets and portfolio copy stay aligned", async () => {
  const [page, transcript, captions, audio] = await Promise.all([
    readFile(new URL("../app/page.tsx", import.meta.url), "utf8"),
    readFile(new URL("../public/media/aurora-portfolio-walkthrough.txt", import.meta.url), "utf8"),
    readFile(new URL("../public/media/aurora-portfolio-walkthrough.vtt", import.meta.url), "utf8"),
    readFile(new URL("../public/media/aurora-portfolio-walkthrough.wav", import.meta.url)),
  ]);

  assert.match(page, /81-second walkthrough/);
  assert.match(page, /aurora-portfolio-walkthrough\.wav/);
  assert.match(page, /URL\.createObjectURL/);
  assert.match(page, /chapterReady/);
  assert.doesNotMatch(page, /77-second walkthrough|aurora-portfolio-walkthrough\.mp3/);
  assert.match(page, /Limits and takeaway/);
  assert.match(transcript, /reliable AI is not just a smart model/i);
  assert.doesNotMatch(transcript, /The next step is one model-backed adapter/i);
  assert.match(captions, /00:01:20\.758/);
  assert.ok(audio.byteLength > 3_000_000);
});
