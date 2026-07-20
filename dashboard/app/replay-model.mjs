const TERMINAL_TASK_EVENTS = new Map([
  ["task_succeeded", "succeeded"],
  ["task_failed", "failed"],
  ["task_timed_out", "timed out"],
  ["task_cancelled", "cancelled"],
]);

const CALLOUT_EVENTS = new Set([
  "run_started",
  "goal_created",
  "task_queued",
  "task_started",
  "task_retry_scheduled",
  "task_succeeded",
  "task_failed",
  "task_timed_out",
  "task_cancelled",
  "evidence_committed",
  "security_denied",
  "synthesis_created",
  "plan_proposed",
  "self_review_completed",
  "fault_injected",
  "retry_exhausted",
  "fault_detected",
  "critique_rejected",
  "replan_created",
  "critique_accepted",
  "budget_reservation_requested",
  "budget_reservation_granted",
  "budget_reservation_denied",
  "budget_exhausted",
  "fallback_skipped",
  "task_reassigned",
  "fallback_completed",
  "approval_requested",
  "approval_denied",
  "approval_granted",
  "action_denied",
  "action_executed",
  "verification_completed",
  "fault_repaired",
  "fault_contained",
  "communication_published",
  "score_computed",
  "run_completed",
]);

const ACTOR_LABELS = {
  orchestrator: "Coordinator",
  telemetry: "Monitoring specialist",
  release: "Release specialist",
  payments: "Payments specialist",
  security: "Security specialist",
  generalist: "Backup helper",
  synthesizer: "Evidence coordinator",
  critic: "Independent reviewer",
  approver: "Safety gate",
  executor: "Action runner",
  verifier: "Health checker",
  communications: "Status reporter",
  system: "Simulation",
};

function words(value) {
  return String(value ?? "")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function metadataLast(metadata, key) {
  return [...(metadata ?? [])].reverse().find(([candidate]) => candidate === key)?.[1];
}

export function replayDuration(result) {
  return Math.max(0, result.ended_at_ms - result.started_at_ms);
}

export function clampReplayElapsed(result, elapsedMs) {
  return Math.min(replayDuration(result), Math.max(0, Number(elapsedMs) || 0));
}

export function nextReplayElapsed(result, elapsedMs, wallDeltaMs, speed) {
  return clampReplayElapsed(
    result,
    clampReplayElapsed(result, elapsedMs) + Math.max(0, wallDeltaMs) * Math.max(0, speed),
  );
}

function taskStateFromEvents(events) {
  let state = "waiting";
  for (const event of events) {
    if (event.kind === "task_queued") state = "queued";
    if (event.kind === "task_started" || event.kind === "tool_attempt") state = "working";
    if (event.kind === "task_retry_scheduled") state = "retrying";
    if (TERMINAL_TASK_EVENTS.has(event.kind)) state = TERMINAL_TASK_EVENTS.get(event.kind);
  }
  return state;
}

export function buildReplaySnapshot(result, elapsedMs, hasStarted = true) {
  const elapsed = clampReplayElapsed(result, elapsedMs);
  const cutoff = result.started_at_ms + elapsed;
  const orderedEvents = [...result.trace].sort((left, right) => (
    left.at_ms - right.at_ms || left.sequence - right.sequence
  ));
  const visibleEvents = hasStarted
    ? orderedEvents.filter((event) => event.at_ms <= cutoff)
    : [];
  const visibleEvidenceIds = new Set(
    visibleEvents
      .filter((event) => event.kind === "evidence_committed")
      .map((event) => metadataLast(event.metadata, "evidence_id"))
      .filter(Boolean),
  );
  const visibleEvidence = result.evidence.filter((item) => visibleEvidenceIds.has(item.evidence_id));
  const tasks = result.task_results.map((task) => {
    const events = visibleEvents.filter((event) => event.task_id === task.task_id);
    return {
      taskId: task.task_id,
      role: task.role,
      state: taskStateFromEvents(events),
      visible: events.length > 0,
      latestEvent: events.at(-1) ?? null,
    };
  });
  const calloutEvents = visibleEvents.filter((event) => CALLOUT_EVENTS.has(event.kind));
  const currentEvent = calloutEvents.at(-1) ?? visibleEvents.at(-1) ?? null;
  const nextEvent = orderedEvents.find((event) => event.at_ms > cutoff) ?? null;
  const runCompleted = visibleEvents.some((event) => event.kind === "run_completed");

  return {
    elapsed,
    duration: replayDuration(result),
    progress: replayDuration(result) === 0 ? 1 : elapsed / replayDuration(result),
    visibleEvents,
    recentEvents: visibleEvents.slice(-5),
    visibleEvidence,
    tasks,
    currentEvent,
    nextEvent,
    activeTaskCount: tasks.filter((task) => task.state === "working" || task.state === "retrying").length,
    decisionCount: calloutEvents.filter((event) => ![
      "run_started",
      "goal_created",
      "task_queued",
      "task_started",
      "evidence_committed",
    ].includes(event.kind)).length,
    runCompleted,
  };
}

export function friendlyActor(actor) {
  return ACTOR_LABELS[actor] ?? words(actor || "system");
}

function injectedFaultCopy(study) {
  if (study === "planning_omission") {
    return {
      what: "For this test, one necessary fix was removed from the plan.",
      why: "This shows whether an independent reviewer catches the mistake.",
    };
  }
  if (study === "permanent_worker_failure") {
    return {
      what: "For this test, the monitoring specialist remains unavailable after its final try.",
      why: "This shows whether one limited backup can keep the response moving.",
    };
  }
  if (study === "fallback_budget_exhaustion") {
    return {
      what: "For this test, the backup limit was reduced from four effort units to three.",
      why: "This shows whether the system refuses work it cannot afford to finish.",
    };
  }
  return {
    what: "The planned test problem is now active.",
    why: "A repeatable challenge lets us compare responses fairly.",
  };
}

export function replayCallout(event, study = "") {
  if (!event) {
    return {
      title: "Ready to begin",
      what: "Press play to watch the recorded simulation unfold one decision at a time.",
      why: "The result stays hidden until the final health check, just as it would during the run.",
    };
  }

  const task = event.task_id ? words(event.task_id) : "the assigned work";
  const message = event.message || `${words(event.kind)} was recorded.`;
  const copy = {
    run_started: ["The practice incident has begun.", "We can now watch the agent team respond from alert to final check."],
    goal_created: ["The team agreed on a safe finish line.", "Every helper is working toward the same business outcome."],
    task_queued: ["A focused job was assigned to a specialist.", "The coordinator turns one large problem into smaller checks."],
    task_started: [`${task} is now being investigated.`, "Several specialists can investigate at the same time."],
    tool_attempt: ["A specialist is checking a data source.", "The answer will be based on evidence, not a guess."],
    task_retry_scheduled: ["The first check hit a temporary problem. One retry is planned.", "Small hiccups are handled, but retries stay limited."],
    task_succeeded: [`${task} is complete.`, "Its findings can now support the response decision."],
    evidence_committed: [message, "The finding is saved so later decisions can point back to specific proof."],
    security_denied: ["A suspicious instruction hidden inside a log was ignored.", "Outside text is treated as data, never as permission to break the rules."],
    retry_exhausted: ["The specialist used its allowed retry and still could not finish.", "The system stops retrying instead of wasting time forever."],
    task_failed: ["This investigation could not be completed.", "The missing information stays visible and cannot be quietly ignored."],
    task_timed_out: ["This investigation took too long and was stopped.", "One slow helper cannot hold the entire response open forever."],
    task_cancelled: ["This job was stopped before more work was done.", "The system avoids work that is no longer needed, safe, or allowed."],
    fault_detected: ["The coordinator recognized the test problem.", "Once noticed, it can choose a safe next step and record the decision."],
    synthesis_created: ["The trusted findings were joined into one explanation.", "The response considers the whole picture, not just one clue."],
    plan_proposed: ["A first response plan is ready.", "The team has a proposal, but nothing has changed yet."],
    self_review_completed: ["The plan passed its own basic checks.", "The format and rules look right, but no separate reviewer challenged the logic."],
    critique_rejected: ["The independent reviewer found a missing piece.", "The incomplete plan is stopped before it can become an incomplete fix."],
    replan_created: ["The coordinator repaired the response plan.", "The missing action is restored before approval."],
    critique_accepted: ["The independent reviewer passed the plan.", "The plan uses evidence, addresses the problem, and includes safety checks."],
    budget_reservation_requested: ["The coordinator checked whether the whole backup job fits the limit.", "It asks before spending or assigning more work."],
    budget_reservation_granted: ["The full backup cost fits and has been reserved.", "The backup can finish without going over the agreed limit."],
    budget_reservation_denied: ["The backup needs more effort than the limit allows.", "No backup work starts and no backup budget is spent."],
    budget_exhausted: ["The resource limit has been reached.", "The system stops extra work instead of quietly overspending."],
    fallback_skipped: ["The backup job was not started.", "The refusal happened before assignment, tool use, or spending."],
    task_reassigned: ["One backup helper took over the unfinished investigation.", "Work can continue without pretending the original specialist recovered."],
    fallback_completed: ["The backup helper recovered the missing information.", "The coordinator can now make a complete, evidence-based decision."],
    approval_requested: [`The plan asked permission to perform this action: ${message}`, "Planning is kept separate from changing the system."],
    approval_denied: ["This proposed action was blocked.", "It broke policy or lacked permission, so nothing changed."],
    approval_granted: ["This exact action received one-time permission.", "Only a narrow, approved change is allowed to run."],
    action_denied: ["The blocked action did not run.", "No approval means no system change."],
    action_executed: [message, "The approved change was carried out and remains traceable."],
    fault_repaired: ["The missing plan step was restored and recovery was confirmed.", "Independent review prevented a partial fix from being mistaken for success."],
    fault_contained: ["The service recovered even though the original specialist is still unavailable.", "The backup limited the damage without hiding the original failure."],
    communication_published: ["A plain-language status update was published.", "People are told what the checks proved, not what the team merely hoped."],
    score_computed: ["The run received a transparent performance score.", "Different approaches can be compared using the same rules."],
  }[event.kind];

  if (event.kind === "fault_injected") {
    const injected = injectedFaultCopy(study);
    return { title: "The test challenge appears", ...injected };
  }

  if (event.kind === "verification_completed") {
    const passed = metadataLast(event.metadata, "slo_passed") === "true";
    return {
      title: passed ? "A health check passed" : "The service still needs work",
      what: passed ? "A health check says the service is working." : "A health check says the service is still struggling.",
      why: passed ? "Recovery is confirmed with measurement, not assumed." : "The system reports incomplete recovery honestly.",
    };
  }

  if (event.kind === "run_completed") {
    const recovered = /recover/i.test(message);
    return {
      title: "Simulation complete",
      what: recovered ? "The final checks confirmed that the service recovered." : "The system stopped without claiming a recovery it could not prove.",
      why: recovered ? "The response ended with verified recovery." : "Unsafe or unsupported work did not continue.",
    };
  }

  if (copy) {
    return {
      title: words(event.kind),
      what: copy[0],
      why: copy[1],
    };
  }

  return {
    title: "Another recorded step",
    what: message,
    why: "It remains visible so a reviewer can see the complete history.",
  };
}
