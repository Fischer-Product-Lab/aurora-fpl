import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import test from "node:test";
import {
  buildReplaySnapshot,
  nextReplayElapsed,
  replayCallout,
  replayDuration,
} from "../app/replay-model.mjs";

const runsRoot = new URL("../public/data/runs/", import.meta.url);

async function loadRun(name) {
  return JSON.parse(await readFile(new URL(name, runsRoot), "utf8"));
}

test("every exported run has a deterministic replay contract", async () => {
  const names = (await readdir(runsRoot)).filter((name) => name.endsWith(".json"));
  assert.ok(names.length >= 13);

  for (const name of names) {
    const run = await loadRun(name);
    const trace = run.data.trace;
    assert.ok(trace.length > 0, `${name} has a trace`);
    assert.ok(trace[0].at_ms >= run.data.started_at_ms, `${name} starts inside its run window`);
    assert.ok(trace.at(-1).at_ms <= run.data.ended_at_ms, `${name} ends inside its run window`);
    assert.equal(trace.at(-1).kind, "run_completed", `${name} ends with run_completed`);
    for (let index = 1; index < trace.length; index += 1) {
      assert.ok(trace[index].at_ms >= trace[index - 1].at_ms, `${name} timestamps never move backward`);
      assert.ok(trace[index].sequence > trace[index - 1].sequence, `${name} sequences strictly increase`);
    }
  }
});

test("replay boundaries reveal tied events together and in sequence order", async () => {
  const run = (await loadRun("worker-fault-with-fallback.json")).data;
  assert.equal(buildReplaySnapshot(run, 0, false).visibleEvents.length, 0);
  assert.deepEqual(buildReplaySnapshot(run, 0, true).visibleEvents.map((event) => event.sequence), [0, 1]);
  assert.deepEqual(buildReplaySnapshot(run, 499, true).visibleEvents.map((event) => event.sequence), [0, 1]);

  const atFiveHundred = buildReplaySnapshot(run, 500, true).visibleEvents;
  assert.deepEqual(atFiveHundred.slice(-12).map((event) => event.sequence), Array.from({ length: 12 }, (_, index) => index + 2));
  assert.ok(atFiveHundred.every((event, index) => index === 0 || event.sequence > atFiveHundred[index - 1].sequence));

  const afterEnd = buildReplaySnapshot(run, replayDuration(run) + 10_000, true);
  assert.equal(afterEnd.elapsed, replayDuration(run));
  assert.equal(afterEnd.visibleEvents.length, run.trace.length);
  assert.equal(afterEnd.runCompleted, true);
});

test("task, evidence, and outcome state comes only from revealed events", async () => {
  const run = (await loadRun("worker-fault-with-fallback.json")).data;
  const stateOf = (snapshot, taskId) => snapshot.tasks.find((task) => task.taskId === taskId);

  assert.equal(buildReplaySnapshot(run, 1200, true).visibleEvidence.length, 0, "observations remain hidden before commit");
  assert.equal(buildReplaySnapshot(run, 1246, true).visibleEvidence.length, 2);
  assert.equal(stateOf(buildReplaySnapshot(run, 500, true), "investigate_telemetry").state, "working");
  assert.equal(stateOf(buildReplaySnapshot(run, 1283, true), "investigate_telemetry").state, "retrying");
  assert.equal(stateOf(buildReplaySnapshot(run, 1783, true), "investigate_telemetry").state, "working");
  assert.equal(stateOf(buildReplaySnapshot(run, 2552, true), "investigate_telemetry").state, "failed");
  assert.equal(stateOf(buildReplaySnapshot(run, 2652, true), "broad_log_scan").state, "working");
  assert.equal(buildReplaySnapshot(run, replayDuration(run) - 1, true).runCompleted, false);
  assert.equal(buildReplaySnapshot(run, replayDuration(run), true).runCompleted, true);

  const control = (await loadRun("clean-full-orchestration.json")).data;
  const cancelled = stateOf(buildReplaySnapshot(control, 2652, true), "broad_log_scan");
  assert.equal(cancelled.state, "cancelled");
  assert.ok(!control.trace.some((event) => event.task_id === "broad_log_scan" && event.kind === "task_started"));
});

test("clock speed changes only how quickly the same virtual snapshot is reached", async () => {
  const run = (await loadRun("planning-fault-with-critic.json")).data;
  assert.equal(nextReplayElapsed(run, 1_000, 2_000, 1), 3_000);
  assert.equal(nextReplayElapsed(run, 1_000, 1_000, 2), 3_000);
  assert.equal(nextReplayElapsed(run, 1_000, 500, 4), 3_000);
  assert.equal(nextReplayElapsed(run, 0, 100_000, 4), replayDuration(run));
  assert.equal(nextReplayElapsed(run, 2_000, -500, 4), 2_000);
});

test("important and unknown events always receive plain-English business copy", () => {
  const representativeKinds = [
    "fault_injected",
    "task_retry_scheduled",
    "budget_reservation_denied",
    "budget_reservation_granted",
    "approval_denied",
    "approval_granted",
    "action_executed",
    "verification_completed",
    "run_completed",
    "future_event_type",
  ];

  for (const kind of representativeKinds) {
    const callout = replayCallout({
      sequence: 1,
      at_ms: 1,
      kind,
      actor: "system",
      task_id: null,
      message: kind === "run_completed" ? "incident recovered" : "A recorded update.",
      metadata: kind === "verification_completed" ? [["slo_passed", "true"]] : [],
    }, "fallback_budget_exhaustion");
    assert.ok(callout.title.length > 3, `${kind} has a title`);
    assert.ok(callout.what.length > 15, `${kind} explains what happened`);
    assert.ok(callout.why.length > 15, `${kind} explains why it matters`);
  }
});
