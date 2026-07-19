"use client";

import { useEffect, useMemo, useRef, useState } from "react";

type MetadataPairs = [string, string][];

type ArtifactDescriptor = {
  id: string;
  type: "run" | "report" | string;
  kind: string;
  path: string;
  title: string;
  summary: string;
  tags: string[];
  relationship_ids: string[];
  metadata: Record<string, unknown>;
};

type Story = {
  id: string;
  kind: string;
  title: string;
  summary: string;
  artifact_ids: string[];
  relationship_ids: string[];
  metadata: Record<string, unknown>;
};

type WalkthroughStep = {
  label: string;
  explanation: string;
};

type Manifest = {
  schema_version: string;
  type: string;
  kind: string;
  title: string;
  summary: string;
  default_artifact_id: string;
  artifacts: ArtifactDescriptor[];
  stories: Story[];
  metadata: Record<string, unknown>;
};

type TraceEvent = {
  sequence: number;
  at_ms: number;
  kind: string;
  actor: string | null;
  task_id: string | null;
  message: string;
  metadata: MetadataPairs;
};

type TaskResult = {
  task_id: string;
  role: string;
  status: string;
  attempts: number;
  started_at_ms: number | null;
  ended_at_ms: number;
  output: string | null;
  error: string | null;
  evidence_ids: string[];
  cost_units: number;
  tool_calls: number;
};

type EvidenceItem = {
  evidence_id: string;
  claim: string;
  source: string;
  task_id: string;
  role: string;
  observed_at_ms: number;
  value: string;
  confidence: number;
  parent_ids: string[];
  trusted: boolean;
  metadata: MetadataPairs;
};

type Score = Record<string, number>;

type SimulationResult = {
  run_id: string;
  strategy: string;
  status: string;
  started_at_ms: number;
  ended_at_ms: number;
  task_results: TaskResult[];
  evidence: EvidenceItem[];
  trace: TraceEvent[];
  attempts_used: number;
  cost_units_used: number;
  tool_calls_used: number;
  score: Score | null;
  metadata: MetadataPairs;
};

type RunMetrics = {
  confirmed_recovery_ms: number | null;
  verification_passes: number;
  max_concurrency: number;
  approval_violations: number;
  executed_action_ids: string[];
  diagnosis_causes: string[];
  injected_fault_names: string[];
  fault_target_task_ids: string[];
  fallback_task_ids: string[];
  terminal_fault_ids: string[];
  reassigned_fault_ids: string[];
  fallback_completed_fault_ids: string[];
  contained_fault_ids: string[];
  [key: string]: unknown;
};

type ArtifactEnvelope = {
  schema_version: string;
  id: string;
  type: string;
  kind: string;
  title: string;
  summary: string;
  tags: string[];
  metadata: Record<string, unknown>;
  data: SimulationResult | Record<string, unknown>;
  metrics?: RunMetrics;
};

type Panel =
  | "timeline"
  | "evidence"
  | "governance"
  | "verification"
  | "budget"
  | "trace";

const PANELS: { id: Panel; label: string }[] = [
  { id: "timeline", label: "Timeline" },
  { id: "evidence", label: "Evidence" },
  { id: "governance", label: "Approvals" },
  { id: "verification", label: "Health checks" },
  { id: "budget", label: "Resource limits" },
  { id: "trace", label: "Audit log" },
];

const KNOWN_EVENTS = new Set([
  "run_started",
  "run_completed",
  "goal_created",
  "task_queued",
  "task_started",
  "tool_attempt",
  "task_retry_scheduled",
  "retry_exhausted",
  "task_succeeded",
  "task_failed",
  "task_timed_out",
  "task_cancelled",
  "evidence_committed",
  "security_denied",
  "budget_exhausted",
  "budget_reservation_requested",
  "budget_reservation_granted",
  "budget_reservation_denied",
  "fallback_skipped",
  "synthesis_created",
  "plan_proposed",
  "self_review_completed",
  "fault_injected",
  "fault_detected",
  "fault_repaired",
  "task_reassigned",
  "fallback_completed",
  "fault_contained",
  "critique_rejected",
  "replan_created",
  "critique_accepted",
  "approval_requested",
  "approval_granted",
  "approval_denied",
  "action_executed",
  "action_denied",
  "verification_completed",
  "communication_published",
  "score_computed",
]);

const KEY_EVENTS = new Set([
  "synthesis_created",
  "plan_proposed",
  "fault_injected",
  "retry_exhausted",
  "task_failed",
  "fault_detected",
  "critique_rejected",
  "replan_created",
  "task_reassigned",
  "fallback_completed",
  "action_executed",
  "verification_completed",
  "fault_repaired",
  "fault_contained",
  "budget_exhausted",
  "budget_reservation_requested",
  "budget_reservation_granted",
  "budget_reservation_denied",
  "fallback_skipped",
  "approval_granted",
  "approval_denied",
  "communication_published",
  "run_completed",
]);

const artifactCache = new Map<string, Promise<ArtifactEnvelope>>();

function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function storyWalkthrough(story: Story) {
  const simpleExplanation = story.metadata.simple_explanation;
  const everydayAnalogy = story.metadata.everyday_analogy;
  const businessValue = story.metadata.business_value;
  const buildRationale = story.metadata.build_rationale;
  const whatToWatch = story.metadata.what_to_watch;
  const rawSteps = story.metadata.exact_steps;
  const steps: WalkthroughStep[] = Array.isArray(rawSteps)
    ? rawSteps.flatMap((candidate) => {
      if (!candidate || typeof candidate !== "object") return [];
      const label = "label" in candidate ? candidate.label : null;
      const explanation = "explanation" in candidate ? candidate.explanation : null;
      return typeof label === "string" && typeof explanation === "string"
        ? [{ label, explanation }]
        : [];
    })
    : [];

  if (
    typeof simpleExplanation !== "string"
    || typeof everydayAnalogy !== "string"
    || typeof businessValue !== "string"
    || typeof buildRationale !== "string"
    || typeof whatToWatch !== "string"
    || steps.length === 0
  ) return null;

  return {
    simpleExplanation,
    everydayAnalogy,
    businessValue,
    buildRationale,
    whatToWatch,
    steps,
  };
}

function studyLabel(story: Story) {
  const labels: Record<string, string> = {
    planning_omission: "Plan review test",
    permanent_worker_failure: "Backup continuity test",
    fallback_budget_exhaustion: "Resource-limit test",
  };
  return labels[String(story.metadata.study ?? "")] ?? "Standard response";
}

function runConditionLabel(run: ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics }) {
  const study = String(run.metadata.study ?? run.metadata.fault ?? "");
  const testCondition = String(run.metadata.study_arm ?? "baseline") === "fault";

  if (study === "planning_omission") {
    const reviewer = run.data.strategy === "specialists_with_critic";
    return `${testCondition ? "Step missing" : "Complete plan"} · Reviewer ${reviewer ? "on" : "off"}`;
  }
  if (study === "permanent_worker_failure") {
    const backup = run.data.strategy === "specialists_with_fallback";
    return `Specialist ${testCondition ? "unavailable" : "available"} · Backup ${backup ? "on" : "off"}`;
  }
  if (study === "fallback_budget_exhaustion") {
    const backup = run.data.strategy === "specialists_with_fallback";
    return `${testCondition ? "3-unit" : "4-unit"} limit · Backup ${backup ? "on" : "off"}`;
  }
  return run.title;
}

function milliseconds(value: number | null | undefined) {
  if (value === null || value === undefined) return "Not reached";
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}

function metadataValues(pairs: MetadataPairs, key: string) {
  return pairs.filter(([candidate]) => candidate === key).map(([, value]) => value);
}

function metadataLast(pairs: MetadataPairs, key: string) {
  return metadataValues(pairs, key).at(-1);
}

function scoreTotal(score: Score | null) {
  if (!score) return null;
  return Object.values(score).reduce((total, value) => total + value, 0);
}

function isRunEnvelope(value: ArtifactEnvelope): value is ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics } {
  return value.type === "run" && Array.isArray((value.data as SimulationResult).trace);
}

function isContained(run: ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics }) {
  return (run.metrics.contained_fault_ids?.length ?? 0) > 0;
}

function isRecovered(run: ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics }) {
  return run.metrics.confirmed_recovery_ms !== null && run.metrics.confirmed_recovery_ms !== undefined;
}

async function fetchArtifact(url: URL) {
  const key = url.toString();
  if (!artifactCache.has(key)) {
    artifactCache.set(
      key,
      fetch(key).then(async (response) => {
        if (!response.ok) throw new Error(`Artifact returned ${response.status}`);
        return (await response.json()) as ArtifactEnvelope;
      }),
    );
  }
  return artifactCache.get(key)!;
}

function outcomeLabel(run: ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics }) {
  if (isContained(run) && isRecovered(run)) return "Recovered via backup";
  if (isContained(run)) return "Contained";
  if (isRecovered(run)) return "Recovered";
  if (run.data.status === "failed" && run.metrics.approval_violations === 0) return "Safely degraded";
  return humanize(run.data.status);
}

function outcomeContext(run: ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics }) {
  if (isContained(run) && isRecovered(run)) return "A backup restored service while the original failure stayed isolated.";
  if (isContained(run)) return "The fault was isolated and the response stayed controlled.";
  if (isRecovered(run)) return "Two health checks confirmed normal service.";
  if (run.data.status === "failed" && run.metrics.approval_violations === 0) {
    return "The response stopped safely without confirming full recovery.";
  }
  return `The simulation ended with a ${humanize(run.data.status).toLowerCase()} result.`;
}

function outcomeTone(run: ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics }) {
  if (isRecovered(run)) return "recovered";
  if (isContained(run)) return "contained";
  return "degraded";
}

function eventTone(kind: string) {
  if (kind.includes("fault") || kind.includes("failed") || kind.includes("denied") || kind === "retry_exhausted") return "fault";
  if (kind.includes("contain") || kind.includes("repaired") || kind.includes("succeeded")) return "success";
  if (kind.includes("approval") || kind.includes("action")) return "governance";
  if (kind.includes("verification")) return "verification";
  if (kind.includes("retry") || kind.includes("budget")) return "warning";
  return "neutral";
}

type BusinessPhase =
  | "Investigate"
  | "Diagnose"
  | "Plan review"
  | "Resource check"
  | "Response"
  | "Health checks"
  | "Outcome";

const MILESTONE_LABELS: Record<string, string> = {
  synthesis_created: "Evidence combined into a diagnosis",
  plan_proposed: "Initial response plan prepared",
  fault_injected: "Test fault became active",
  retry_exhausted: "Automated retries ended",
  task_failed: "Investigation task could not finish",
  fault_detected: "System recognized the failure",
  critique_rejected: "Independent review found a gap",
  replan_created: "Response plan revised",
  task_reassigned: "Work moved to a backup agent",
  fallback_completed: "Backup investigation completed",
  action_executed: "Approved action carried out",
  verification_completed: "Service health checked",
  fault_repaired: "Fault repaired",
  fault_contained: "Fault contained",
  budget_exhausted: "Resource limit reached",
  budget_reservation_requested: "Backup resource limit checked",
  budget_reservation_granted: "Backup resources available",
  budget_reservation_denied: "Not enough resources for backup",
  fallback_skipped: "Backup work safely skipped",
  approval_granted: "Response approved",
  approval_denied: "Unsafe response blocked",
  communication_published: "Recovery update published",
  run_completed: "Simulation concluded",
};

function taskPurpose(taskId: string, role: string) {
  const purposes: Record<string, string> = {
    investigate_telemetry: "Checks system signals to locate when and where service reliability changed.",
    investigate_release: "Checks recent deployments for a change connected to the incident.",
    investigate_payments: "Measures customer payment impact and protects transaction integrity.",
    investigate_security: "Determines whether automated abuse or a security threat contributed.",
    broad_log_scan: "Searches a wider set of logs when focused checks may not be enough.",
  };
  return purposes[taskId] ?? `Collects ${humanize(role).toLowerCase()} evidence needed to understand the incident.`;
}

function milestonePhase(kind: string): BusinessPhase {
  if (["fault_injected", "retry_exhausted", "task_failed"].includes(kind)) return "Investigate";
  if (["synthesis_created", "fault_detected"].includes(kind)) return "Diagnose";
  if (["plan_proposed", "critique_rejected", "replan_created"].includes(kind)) return "Plan review";
  if ([
    "budget_exhausted",
    "budget_reservation_requested",
    "budget_reservation_granted",
    "budget_reservation_denied",
    "fallback_skipped",
  ].includes(kind)) return "Resource check";
  if (["task_reassigned", "fallback_completed", "approval_granted", "approval_denied", "action_executed"].includes(kind)) return "Response";
  if (kind === "verification_completed") return "Health checks";
  return "Outcome";
}

function isTimelineMilestone(event: TraceEvent) {
  return KEY_EVENTS.has(event.kind) || (!KNOWN_EVENTS.has(event.kind) && event.task_id === null);
}

function taskTerminalEvent(trace: TraceEvent[], task: TaskResult) {
  const exactKind = `task_${task.status}`;
  return [...trace].reverse().find((event) => event.task_id === task.task_id && event.kind === exactKind)
    ?? [...trace].reverse().find((event) => event.task_id === task.task_id && event.kind.startsWith("task_"))
    ?? null;
}

function MetricCard({ label, value, context }: { label: string; value: string; context: string }) {
  return (
    <div className="metric-card">
      <span>{label}</span>
      <strong>{value}</strong>
      <small>{context}</small>
    </div>
  );
}

const PORTFOLIO_TOUR_CUES = [
  { at: 0, time: "00:00", label: "Why Aurora exists" },
  { at: 8.46, time: "00:08", label: "How a run works" },
  { at: 33.76, time: "00:34", label: "What the tests proved" },
  { at: 53.6, time: "00:54", label: "How to inspect the proof" },
  { at: 60.62, time: "01:01", label: "Limits and takeaway" },
];

const PORTFOLIO_TOUR_AUDIO = "/media/aurora-portfolio-walkthrough.wav";

function PortfolioCaseStudy() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [activeCue, setActiveCue] = useState(0);
  const [audioSrc, setAudioSrc] = useState<string>();
  const [chapterReady, setChapterReady] = useState(false);

  useEffect(() => {
    let active = true;
    let objectUrl: string | undefined;

    async function prepareSeekableAudio() {
      try {
        const response = await fetch(PORTFOLIO_TOUR_AUDIO);
        if (!response.ok) throw new Error(`Narration request failed: ${response.status}`);
        objectUrl = URL.createObjectURL(await response.blob());
        if (!active) {
          URL.revokeObjectURL(objectUrl);
          return;
        }
        setAudioSrc(objectUrl);
        setChapterReady(true);
      } catch {
        if (active) setAudioSrc(PORTFOLIO_TOUR_AUDIO);
      }
    }

    void prepareSeekableAudio();
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, []);

  function syncCue(currentTime: number) {
    const nextCue = PORTFOLIO_TOUR_CUES.findLastIndex((cue) => currentTime >= cue.at);
    setActiveCue(Math.max(0, nextCue));
  }

  function playFrom(at: number) {
    if (!audioRef.current) return;
    audioRef.current.currentTime = at;
    void audioRef.current.play();
  }

  return (
    <section className="portfolio-case-study" aria-labelledby="portfolio-case-study-title">
      <div className="portfolio-intro">
        <span className="eyebrow">Portfolio case study</span>
        <h2 id="portfolio-case-study-title">A test lab for the machinery around AI agents.</h2>
        <p>Most agent demos show one successful answer. Aurora asks the harder question: what should the system do when a plan is incomplete, a specialist disappears, or a recovery step costs more than the available budget?</p>
      </div>

      <ol className="architecture-flow" aria-label="How the control plane works">
        {[
          ["01", "Investigate", "Specialists collect evidence"],
          ["02", "Combine", "A planner builds a response"],
          ["03", "Challenge", "A reviewer finds missing steps"],
          ["04", "Control", "Approval and budget gates decide"],
          ["05", "Verify", "Health checks prove the outcome"],
        ].map(([number, label, explanation]) => (
          <li key={number}><span>{number}</span><strong>{label}</strong><small>{explanation}</small></li>
        ))}
      </ol>

      <div className="portfolio-results" aria-label="Three matched-study results">
        <article><strong>30 / 30</strong><p>Incomplete plans rescued by independent review</p></article>
        <article><strong>30 / 30</strong><p>Unavailable-specialist runs recovered by one bounded backup</p></article>
        <article><strong>30 / 30</strong><p>Tight-budget runs stopped before dispatch with zero fallback spend</p></article>
      </div>

      <div className="narrated-tour">
        <div className="narrated-tour-heading">
          <div><span className="eyebrow">Quick orientation</span><h3>Listen to the 81-second walkthrough</h3></div>
          <p>Press play, then use the time markers to jump to the part you want to show. <a href="/media/aurora-portfolio-walkthrough.txt">Read the transcript.</a></p>
        </div>
        <audio
          controls
          onEnded={() => setActiveCue(0)}
          onTimeUpdate={(event) => syncCue(event.currentTarget.currentTime)}
          preload="metadata"
          ref={audioRef}
          aria-busy={!audioSrc}
          src={audioSrc}
        >
          <track default kind="captions" label="English" src="/media/aurora-portfolio-walkthrough.vtt" srcLang="en" />
        </audio>
        <ol className="tour-cues">
          {PORTFOLIO_TOUR_CUES.map((cue, index) => (
            <li key={cue.time} data-active={index === activeCue}>
              <button disabled={!chapterReady} onClick={() => playFrom(cue.at)} type="button"><time>{cue.time}</time><span>{cue.label}</span></button>
            </li>
          ))}
        </ol>
      </div>

      <details className="portfolio-notes">
        <summary>Limits and lessons learned</summary>
        <div>
          <p><strong>Limit.</strong> Aurora is deterministic and scripted. It demonstrates control-plane behavior under specific conditions, not general model reliability or production performance.</p>
          <p><strong>Lesson.</strong> Reliable agent systems need typed contracts, evidence provenance, bounded recovery, approval gates, and verification around the model—not confidence in a generated answer.</p>
          <p><strong>Extension.</strong> Aurora now includes one optional model-backed diagnosis behind the same evidence and validation contract. Approval and execution remain controlled.</p>
        </div>
      </details>
    </section>
  );
}

function SimpleWalkthrough({ story }: { story: Story }) {
  const walkthrough = storyWalkthrough(story);
  if (!walkthrough) return null;
  const headingId = `simple-walkthrough-${story.id}`;

  return (
    <section className="simple-walkthrough" aria-labelledby={headingId}>
      <header className="walkthrough-heading">
        <div>
          <span className="eyebrow">Plain-English demo guide</span>
          <h2 id={headingId}>What exactly happens in this demo?</h2>
        </div>
        <p>{walkthrough.simpleExplanation}</p>
      </header>

      <div className="walkthrough-analogy">
        <strong>Picture it like this</strong>
        <p>{walkthrough.everydayAnalogy}</p>
      </div>

      <div className="walkthrough-watch">
        <strong>When you show this demo</strong>
        <p>{walkthrough.whatToWatch}</p>
      </div>

      <ol className="walkthrough-steps" aria-label="What happens, in order">
        {walkthrough.steps.map((step, index) => (
          <li key={`${step.label}-${index}`}>
            <span className="walkthrough-step-number" aria-hidden="true">{index + 1}</span>
            <div>
              <strong>{step.label}</strong>
              <p>{step.explanation}</p>
            </div>
          </li>
        ))}
      </ol>

      <dl className="walkthrough-takeaways">
        <div>
          <dt>The value it demonstrates</dt>
          <dd>{walkthrough.businessValue}</dd>
        </div>
        <div>
          <dt>Why we built this demo</dt>
          <dd>{walkthrough.buildRationale}</dd>
        </div>
      </dl>
    </section>
  );
}

function ComparisonStrip({
  runs,
  selectedId,
  onSelect,
}: {
  runs: (ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics })[];
  selectedId: string;
  onSelect: (id: string) => void;
}) {
  return (
    <div className="comparison-strip" aria-label="Runs in this story">
      {runs.map((run) => (
        <button
          className="comparison-run"
          data-selected={run.id === selectedId}
          key={run.id}
          onClick={() => onSelect(run.id)}
          type="button"
        >
          <span className={`status-dot ${outcomeTone(run)}`} aria-hidden="true" />
          <span className="comparison-copy">
            <strong>{runConditionLabel(run)}</strong>
          </span>
          <span className={`outcome-chip ${outcomeTone(run)}`}>{outcomeLabel(run)}</span>
        </button>
      ))}
    </div>
  );
}

function EventInspector({ event, onClose }: { event: TraceEvent; onClose: () => void }) {
  const known = KNOWN_EVENTS.has(event.kind);
  return (
    <aside className="event-inspector" aria-label="Selected event details">
      <div className="section-heading compact">
        <div>
          <span className="eyebrow">Event #{event.sequence}</span>
          <h3>{humanize(event.kind)}</h3>
        </div>
        <button className="text-button" onClick={onClose} type="button">Close</button>
      </div>
      {!known && <span className="unknown-badge">Unknown event type</span>}
      <p>{event.message || "No event message."}</p>
      <dl className="inspector-facts">
        <div><dt>Time</dt><dd>{milliseconds(event.at_ms)}</dd></div>
        <div><dt>Actor</dt><dd>{event.actor ? humanize(event.actor) : "System"}</dd></div>
        <div><dt>Task</dt><dd><code>{event.task_id ?? "—"}</code></dd></div>
      </dl>
      <div className="metadata-list" aria-label="Event metadata">
        {event.metadata.length ? event.metadata.map(([key, value], index) => (
          <div key={`${key}-${index}`}><code>{key}</code><span>{value}</span></div>
        )) : <p className="empty-copy">No metadata attached.</p>}
      </div>
    </aside>
  );
}

function Timeline({
  run,
  selectedEvent,
  onSelectEvent,
}: {
  run: ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics };
  selectedEvent: TraceEvent | null;
  onSelectEvent: (event: TraceEvent | null) => void;
}) {
  const result = run.data;
  const runDuration = Math.max(1, result.ended_at_ms - result.started_at_ms);
  const taskMoments = result.task_results.flatMap((task) => (
    task.started_at_ms === null ? [task.ended_at_ms] : [task.started_at_ms, task.ended_at_ms]
  ));
  const taskWindowStart = taskMoments.length ? Math.min(...taskMoments) : result.started_at_ms;
  const taskWindowEnd = taskMoments.length ? Math.max(...taskMoments) : result.ended_at_ms;
  const taskWindowDuration = Math.max(1, taskWindowEnd - taskWindowStart);
  const taskPosition = (atMs: number) => `${Math.min(100, Math.max(0, ((atMs - taskWindowStart) / taskWindowDuration) * 100))}%`;
  const milestones = result.trace.filter(isTimelineMilestone);
  const axisTicks = [0, 0.25, 0.5, 0.75, 1];

  return (
    <section className="explorer-section">
      <div className="section-heading">
        <div>
          <span className="eyebrow">Simulated incident · {milliseconds(runDuration)}</span>
          <h2>Investigation work and decisions</h2>
        </div>
        <span className="section-note">{run.metrics.max_concurrency} tasks ran at once</span>
      </div>
      <div className="timeline-subheading">
        <div>
          <span className="eyebrow">Phase 1 · Investigate</span>
          <h3>Active investigation period</h3>
          <p>Task bars use only the active investigation window, so short parallel work remains readable.</p>
        </div>
        <span className="timeline-window">Simulated time {milliseconds(taskWindowStart)}–{milliseconds(taskWindowEnd)}</span>
      </div>
      <div className="timeline-shell">
        <div className="task-time-axis" aria-hidden="true">
          <span className="axis-caption">Task and purpose</span>
          <div className="axis-track">
            {axisTicks.map((tick, index) => (
              <span
                data-edge={index === 0 ? "start" : index === axisTicks.length - 1 ? "end" : undefined}
                key={tick}
                style={{ left: `${tick * 100}%` }}
              >{milliseconds(taskWindowStart + (taskWindowDuration * tick))}</span>
            ))}
          </div>
        </div>
        {result.task_results.map((task) => {
          const started = task.started_at_ms !== null;
          const start = task.started_at_ms ?? task.ended_at_ms;
          const taskDuration = started ? Math.max(0, task.ended_at_ms - start) : null;
          const width = taskDuration === null ? 0 : (taskDuration / taskWindowDuration) * 100;
          const attempts = result.trace.filter((event) => event.kind === "tool_attempt" && event.task_id === task.task_id);
          const terminal = taskTerminalEvent(result.trace, task);
          return (
            <div className="task-lane" key={task.task_id}>
              <div className="lane-label">
                <div className="task-title-row">
                  <strong>{humanize(task.task_id)}</strong>
                  <span className={`task-status ${task.status}`}>{humanize(task.status)}</span>
                </div>
                <p>{taskPurpose(task.task_id, task.role)}</p>
                <div className="task-facts">
                  <span>{humanize(task.role)}</span>
                  <span>{task.attempts} attempt{task.attempts === 1 ? "" : "s"}</span>
                  <span>{taskDuration === null ? "Not started" : `${milliseconds(taskDuration)} active`}</span>
                </div>
              </div>
              <div className="lane-track">
                {started ? (
                  <button
                    aria-label={`${humanize(task.task_id)} ${humanize(task.status)} from ${milliseconds(start)} to ${milliseconds(task.ended_at_ms)}, ${milliseconds(taskDuration)}`}
                    className={`task-span ${task.status}`}
                    data-selected={selectedEvent?.sequence === terminal?.sequence}
                    onClick={() => onSelectEvent(terminal)}
                    style={{ left: taskPosition(start), width: `${width}%` }}
                    type="button"
                  >
                    <span className="sr-only">Open terminal event</span>
                    {attempts.map((attempt) => (
                      <i
                        aria-hidden="true"
                        key={attempt.sequence}
                        style={{
                          left: `${Math.min(100, Math.max(0, ((attempt.at_ms - start) / Math.max(1, taskDuration ?? 0)) * 100))}%`,
                        }}
                      />
                    ))}
                  </button>
                ) : (
                  <button
                    aria-label={`${humanize(task.task_id)} never started and was ${humanize(task.status)} at ${milliseconds(task.ended_at_ms)}`}
                    className={`task-terminal-marker ${task.status}`}
                    data-selected={selectedEvent?.sequence === terminal?.sequence}
                    onClick={() => onSelectEvent(terminal)}
                    style={{ left: taskPosition(task.ended_at_ms) }}
                    type="button"
                  ><span className="sr-only">Open terminal event</span></button>
                )}
              </div>
            </div>
          );
        })}
      </div>
      <div className="timeline-subheading milestone-heading">
        <div>
          <span className="eyebrow">Full-run sequence</span>
          <h3>Business milestones</h3>
          <p>Decisions are shown in order with exact elapsed times, including later health checks and the final outcome.</p>
        </div>
        <span className="timeline-window">{milestones.length} recorded milestones</span>
      </div>
      <ol className="milestone-sequence" aria-label="Control-plane milestones in chronological order">
        {milestones.map((event, index) => {
          const phase = milestonePhase(event.kind);
          const label = MILESTONE_LABELS[event.kind] ?? humanize(event.kind);
          const elapsed = Math.max(0, event.at_ms - result.started_at_ms);
          return (
            <li key={event.sequence}>
              <span className="milestone-order" aria-hidden="true">{String(index + 1).padStart(2, "0")}</span>
              <span className="milestone-phase">{phase}</span>
              <time dateTime={`PT${elapsed / 1000}S`}>T+{milliseconds(elapsed)}</time>
              <button
                aria-label={`${phase}: ${label}, ${milliseconds(elapsed)} elapsed`}
                className="milestone-button"
                data-selected={selectedEvent?.sequence === event.sequence}
                onClick={() => onSelectEvent(event)}
                type="button"
              >
                <span className={`event-inline-mark ${eventTone(event.kind)}`} aria-hidden="true" />
                <strong>{label}</strong>
              </button>
            </li>
          );
        })}
      </ol>
      {selectedEvent && <EventInspector event={selectedEvent} onClose={() => onSelectEvent(null)} />}
    </section>
  );
}

function EvidencePanel({ run }: { run: ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics } }) {
  const byTask = new Map<string, EvidenceItem[]>();
  run.data.evidence.forEach((item) => byTask.set(item.task_id, [...(byTask.get(item.task_id) ?? []), item]));
  return (
    <section className="explorer-section">
      <div className="section-heading">
        <div><span className="eyebrow">Append-only shared state</span><h2>Evidence lineage</h2></div>
        <span className="section-note">{run.data.evidence.length} observations · {run.data.evidence.filter((item) => item.trusted).length} trusted</span>
      </div>
      <div className="lineage-flow">
        <div className="lineage-stage"><span>Tasks</span><strong>{byTask.size}</strong></div>
        <div className="lineage-arrow" aria-hidden="true">→</div>
        <div className="lineage-stage"><span>Evidence</span><strong>{run.data.evidence.length}</strong></div>
        <div className="lineage-arrow" aria-hidden="true">→</div>
        <div className="lineage-stage"><span>Diagnosis</span><strong>{run.metrics.diagnosis_causes.length}</strong></div>
        <div className="lineage-arrow" aria-hidden="true">→</div>
        <div className="lineage-stage"><span>Executed actions</span><strong>{run.metrics.executed_action_ids.length}</strong></div>
      </div>
      <div className="evidence-grid">
        {Array.from(byTask.entries()).map(([taskId, items]) => (
          <article className="evidence-group" key={taskId}>
            <header><div><span className="eyebrow">{humanize(items[0]?.role ?? "system")}</span><h3>{humanize(taskId)}</h3></div><span>{items.length}</span></header>
            {items.map((item) => {
              const tags = (metadataLast(item.metadata, "tags") ?? "").split(",").filter(Boolean);
              const original = metadataLast(item.metadata, "original_evidence_id");
              return (
                <div className="evidence-item" key={item.evidence_id}>
                  <div><code>{item.evidence_id}</code><span className={`trust-mark ${item.trusted ? "trusted" : "untrusted"}`}>{item.trusted ? "Trusted" : "Untrusted"}</span></div>
                  <p>{item.claim}</p>
                  {original && <small>Re-observed from <code>{original}</code></small>}
                  <div className="tag-row">{tags.map((tag) => <span key={tag}>{humanize(tag)}</span>)}</div>
                </div>
              );
            })}
          </article>
        ))}
      </div>
    </section>
  );
}

function GovernancePanel({ run }: { run: ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics } }) {
  const actionIds = Array.from(new Set(run.data.trace.map((event) => metadataLast(event.metadata, "action_id")).filter((value): value is string => Boolean(value))));
  return (
    <section className="explorer-section">
      <div className="section-heading">
        <div><span className="eyebrow">Capability-gated execution</span><h2>Approval and action chains</h2></div>
        <span className="section-note">{run.metrics.approval_violations} policy violations</span>
      </div>
      <div className="governance-list">
        {actionIds.map((actionId) => {
          const events = run.data.trace.filter((event) => metadataLast(event.metadata, "action_id") === actionId);
          const requested = events.find((event) => event.kind === "approval_requested");
          const decision = events.find((event) => event.kind === "approval_granted" || event.kind === "approval_denied");
          const execution = events.find((event) => event.kind === "action_executed" || event.kind === "action_denied");
          const status = execution?.kind === "action_executed" ? "executed" : decision?.kind === "approval_denied" ? "denied" : "not executed";
          return (
            <article className="governance-chain" key={actionId}>
              <div className="governance-action"><span className={`status-dot ${status === "executed" ? "recovered" : "degraded"}`} /><div><h3>{humanize(actionId)}</h3><p>{requested?.message ?? "Governed request"}</p></div><span className={`outcome-chip ${status === "executed" ? "recovered" : "degraded"}`}>{humanize(status)}</span></div>
              <ol>
                <li data-complete={Boolean(requested)}><span>1</span><div><strong>Requested</strong><small>{requested ? `${metadataLast(requested.metadata, "risk") ?? "policy"} risk` : "Not observed"}</small></div></li>
                <li data-complete={Boolean(decision)}><span>2</span><div><strong>{decision ? humanize(decision.kind) : "Decision missing"}</strong><small>{decision?.message ?? "No capability issued"}</small></div></li>
                <li data-complete={execution?.kind === "action_executed"}><span>3</span><div><strong>{execution ? humanize(execution.kind) : "No execution"}</strong><small>{execution?.message ?? "State unchanged"}</small></div></li>
              </ol>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function VerificationPanel({ run }: { run: ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics } }) {
  const checks = run.data.trace.filter((event) => event.kind === "verification_completed");
  const objectives = [
    ["checkout_success", "Checkout success", ">= 90%"],
    ["payment_timeout_rate", "Payment timeout rate", "< 5%"],
    ["duplicate_authorization_rate", "Duplicate authorization rate", "< 0.1%"],
    ["oversold_seats", "Oversold seats", "= 0"],
  ];
  return (
    <section className="explorer-section">
      <div className="section-heading"><div><span className="eyebrow">Independent numeric checks</span><h2>Recovery verification</h2></div><span className="section-note">{run.metrics.verification_passes}/2 valid passes</span></div>
      <div className="verification-grid">
        {checks.map((event) => {
          const passed = metadataLast(event.metadata, "slo_passed") === "true";
          return (
            <article className="verification-card" key={event.sequence}>
              <header><div><span className="eyebrow">Pass {metadataLast(event.metadata, "pass") ?? "?"}</span><h3>{passed ? "Objectives satisfied" : "Service remains degraded"}</h3></div><span className={`outcome-chip ${passed ? "recovered" : "degraded"}`}>{passed ? "Passed" : "Failed"}</span></header>
              <dl>{objectives.map(([key, label, threshold]) => <div key={key}><dt>{label}<small>{threshold}</small></dt><dd>{metadataLast(event.metadata, key) ?? "—"}</dd></div>)}</dl>
            </article>
          );
        })}
      </div>
    </section>
  );
}

function BudgetPanel({ run }: { run: ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics } }) {
  const maxCost = Math.max(1, ...run.data.task_results.map((task) => task.cost_units));
  const request = run.data.trace.find((event) => event.kind === "budget_reservation_requested");
  const decision = run.data.trace.find((event) => event.kind === "budget_reservation_granted" || event.kind === "budget_reservation_denied");
  const skipped = run.data.trace.find((event) => event.kind === "fallback_skipped");
  const reassigned = run.data.trace.find((event) => event.kind === "task_reassigned" && (!request || event.at_ms >= request.at_ms));
  const refused = run.data.trace.filter((event) => event.kind === "budget_exhausted" || event.kind === "budget_reservation_denied");
  const budgetMetadata = (run.metadata.budget_limits ?? {}) as Record<string, unknown>;
  const isBudgetStudy = run.metadata.fault === "fallback_budget_exhaustion" || run.metadata.study === "fallback_budget_exhaustion";
  const granted = decision?.kind === "budget_reservation_granted";
  const ledgerBefore = decision ? metadataLast(decision.metadata, "ledger_before_cost_units") : undefined;
  const ledgerAfter = decision ? metadataLast(decision.metadata, "ledger_after_cost_units") : undefined;
  const decisionLabel = granted ? "Exact-fit granted" : decision ? "Denied before dispatch" : "Not requested";
  const reservationFlow = [request, decision, skipped ?? reassigned].filter((event): event is TraceEvent => Boolean(event));
  return (
    <section className="explorer-section">
      <div className="section-heading"><div><span className="eyebrow">Bounded orchestration</span><h2>Usage and budget decisions</h2></div><span className="section-note">{decision ? decisionLabel : refused.length ? `${refused.length} refused request${refused.length === 1 ? "" : "s"}` : "Within exported usage"}</span></div>
      <div className="budget-summary">
        <MetricCard label="Attempts" value={String(run.data.attempts_used)} context={budgetMetadata.max_attempts ? `Limit ${budgetMetadata.max_attempts}` : "Across tasks and agent turns"} />
        <MetricCard label="Cost units" value={String(run.data.cost_units_used)} context={budgetMetadata.max_cost_units ? `Limit ${budgetMetadata.max_cost_units}` : "Deterministic accounting"} />
        <MetricCard label="Tool calls" value={String(run.data.tool_calls_used)} context={budgetMetadata.max_tool_calls ? `Limit ${budgetMetadata.max_tool_calls}` : "Externally bounded work"} />
      </div>
      {request && decision && (
        <article className="reservation-decision" data-decision={granted ? "granted" : "denied"}>
          <header>
            <div><span className="eyebrow">Fallback admission guard</span><h3>Atomic scoped reservation</h3></div>
            <span className={`outcome-chip ${granted ? "recovered" : "degraded"}`}>{decisionLabel}</span>
          </header>
          <div className="reservation-vectors">
            <div><span>Requested</span><strong>{metadataLast(request.metadata, "requested_cost_units") ?? "?"} cost</strong><small>{metadataLast(request.metadata, "requested_attempts") ?? "?"} attempt · {metadataLast(request.metadata, "requested_tool_calls") ?? "?"} call</small></div>
            <div><span>Capacity</span><strong>{metadataLast(request.metadata, "capacity_cost_units") ?? "?"} cost</strong><small>{metadataLast(request.metadata, "capacity_attempts") ?? "?"} attempt · {metadataLast(request.metadata, "capacity_tool_calls") ?? "?"} call</small></div>
            <div><span>Deficit</span><strong>{metadataLast(request.metadata, "deficit_cost_units") ?? "0"} cost</strong><small>Binding: {humanize(metadataLast(request.metadata, "binding_dimensions") ?? "none")}</small></div>
            <div><span>Scoped use</span><strong>{metadataLast(decision.metadata, "scoped_usage_cost_units") ?? (granted ? metadataLast(request.metadata, "requested_cost_units") : "0")} cost</strong><small>{metadataLast(decision.metadata, "scoped_usage_attempts") ?? (granted ? "1" : "0")} attempt · {metadataLast(decision.metadata, "scoped_usage_tool_calls") ?? (granted ? "1" : "0")} call</small></div>
          </div>
          <ol className="reservation-flow">
            {reservationFlow.map((event, index) => <li key={event.sequence}><span>{index + 1}</span><div><strong>{humanize(event.kind)}</strong><small>{event.message}</small></div><time>{milliseconds(event.at_ms)}</time></li>)}
          </ol>
          <p className="atomic-proof">
            {granted
              ? "The exact-fit request was admitted as one unit before the fallback was assigned."
              : `The ledger stayed at ${ledgerBefore ?? "?"} cost before and ${ledgerAfter ?? "?"} after denial; the skipped fallback recorded zero scoped spend.`}
          </p>
        </article>
      )}
      {!request && isBudgetStudy && (
        <div className="reservation-empty"><strong>No fallback admission request</strong><p>This negative-control strategy does not enable reassignment, so the budget intervention remained dormant after the shared worker failure.</p></div>
      )}
      <div className="task-usage-list">
        {run.data.task_results.map((task) => <div className="task-usage" key={task.task_id}><div><strong>{humanize(task.task_id)}</strong><span>{task.cost_units} cost · {task.tool_calls} call{task.tool_calls === 1 ? "" : "s"}</span></div><div className="usage-track"><span style={{ width: `${(task.cost_units / maxCost) * 100}%` }} /></div></div>)}
      </div>
      {refused.length > 0 && <div className="budget-events"><h3>Refused work</h3>{refused.map((event) => <div key={event.sequence}><span className="status-dot degraded" /><div><strong>{event.task_id ? humanize(event.task_id) : "Budget ledger"}</strong><p>{event.message}</p></div><time>{milliseconds(event.at_ms)}</time></div>)}</div>}
      {!Object.keys(budgetMetadata).length && !request && !isBudgetStudy && <p className="panel-footnote">This artifact exports deterministic usage but no configured limit snapshot.</p>}
    </section>
  );
}

function TracePanel({ run, onSelectEvent }: { run: ArtifactEnvelope & { data: SimulationResult; metrics: RunMetrics }; onSelectEvent: (event: TraceEvent) => void }) {
  return (
    <section className="explorer-section">
      <div className="section-heading"><div><span className="eyebrow">Immutable audit record</span><h2>Raw event trace</h2></div><span className="section-note">{run.data.trace.length} events</span></div>
      <div className="trace-table-wrap"><table className="trace-table"><thead><tr><th>Seq</th><th>Time</th><th>Event</th><th>Actor / task</th><th>Message</th></tr></thead><tbody>{run.data.trace.map((event) => <tr key={event.sequence}><td>{event.sequence}</td><td>{milliseconds(event.at_ms)}</td><td><button className="trace-event-button" onClick={() => onSelectEvent(event)} type="button"><span className={`event-inline-mark ${eventTone(event.kind)}`} />{humanize(event.kind)}{!KNOWN_EVENTS.has(event.kind) && <small>Unknown</small>}</button></td><td>{humanize(event.actor ?? "system")}<small>{event.task_id ? humanize(event.task_id) : "—"}</small></td><td>{event.message || "—"}</td></tr>)}</tbody></table></div>
    </section>
  );
}

export default function Home() {
  const [manifest, setManifest] = useState<Manifest | null>(null);
  const [manifestUrl, setManifestUrl] = useState<URL | null>(null);
  const [storyId, setStoryId] = useState("");
  const [artifacts, setArtifacts] = useState<ArtifactEnvelope[]>([]);
  const [selectedArtifactId, setSelectedArtifactId] = useState("");
  const [panel, setPanel] = useState<Panel>("timeline");
  const [selectedEvent, setSelectedEvent] = useState<TraceEvent | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const url = new URL("data/manifest.json", document.baseURI);
    fetch(url)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Manifest returned ${response.status}`);
        const loaded = (await response.json()) as Manifest;
        if (!loaded.schema_version.startsWith("1.")) throw new Error(`Unsupported manifest version ${loaded.schema_version}`);
        const requested = new URLSearchParams(window.location.search).get("story");
        const initialStory = loaded.stories.find((story) => story.id === requested) ?? loaded.stories[0];
        setManifest(loaded);
        setManifestUrl(url);
        setStoryId(initialStory?.id ?? "");
      })
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Unable to load showcase manifest"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!manifest || !manifestUrl || !storyId) return;
    const story = manifest.stories.find((candidate) => candidate.id === storyId);
    if (!story) return;
    const refs = story.artifact_ids.map((id) => manifest.artifacts.find((artifact) => artifact.id === id)).filter((value): value is ArtifactDescriptor => Boolean(value));
    let cancelled = false;
    queueMicrotask(() => !cancelled && setLoading(true));
    Promise.all(refs.map((ref) => fetchArtifact(new URL(ref.path, manifestUrl))))
      .then((loaded) => {
        if (cancelled) return;
        const runs = loaded.filter(isRunEnvelope);
        setArtifacts(loaded);
        const requestedRun = new URLSearchParams(window.location.search).get("run");
        const initial = runs.find((run) => run.id === requestedRun) ?? runs.at(-1);
        setSelectedArtifactId(initial?.id ?? "");
        setSelectedEvent(null);
      })
      .catch((caught: unknown) => !cancelled && setError(caught instanceof Error ? caught.message : "Unable to load story artifacts"))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [manifest, manifestUrl, storyId]);

  const story = manifest?.stories.find((candidate) => candidate.id === storyId) ?? null;
  const runs = useMemo(() => artifacts.filter(isRunEnvelope), [artifacts]);
  const selectedRun = runs.find((run) => run.id === selectedArtifactId) ?? runs[0] ?? null;

  function updateLocation(nextStory: string, nextRun?: string) {
    const url = new URL(window.location.href);
    url.searchParams.set("story", nextStory);
    if (nextRun) url.searchParams.set("run", nextRun); else url.searchParams.delete("run");
    window.history.replaceState({}, "", url);
  }

  function chooseStory(nextStory: string) {
    setStoryId(nextStory);
    updateLocation(nextStory);
  }

  function chooseRun(nextRun: string) {
    setSelectedArtifactId(nextRun);
    setSelectedEvent(null);
    updateLocation(storyId, nextRun);
  }

  if (loading && !manifest) return <main className="loading-state" aria-live="polite"><span className="loading-mark" /><p>Assembling the incident dossier…</p></main>;
  if (error) return <main className="error-state"><span className="eyebrow">Data could not be loaded</span><h1>Aurora Run Explorer</h1><p>{error}</p><p>Generate the showcase bundle, then reload this page.</p></main>;
  if (!manifest || !story || !selectedRun) return <main className="error-state"><h1>Aurora Run Explorer</h1><p>No curated run is available.</p></main>;

  const totalScore = scoreTotal(selectedRun.data.score);

  return (
    <main className="app-shell">
      <a className="skip-link" href="#explorer-content">Skip to run details</a>
      <header className="app-header">
        <div className="brand-block"><span className="brand-mark" aria-hidden="true">A</span><div><strong>Aurora Run Explorer</strong><span className="eyebrow">Fischer Product Lab · Agent orchestration</span></div></div>
        <div className="header-proof"><span>Deterministic</span><span>Trace-derived</span><span>Schema v{manifest.schema_version}</span></div>
      </header>

      <div className="workspace">
        <nav className="story-rail" aria-label="Curated simulation stories">
          <div><span className="eyebrow">Choose a demo</span><h2>Pick a scenario. See what happened. Then inspect the proof.</h2></div>
          {manifest.stories.map((item, index) => <button aria-current={item.id === storyId ? "page" : undefined} key={item.id} onClick={() => chooseStory(item.id)} type="button"><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{item.title}</strong><small>{item.summary}</small></div></button>)}
          <div className="rail-footnote"><span className="status-dot recovered" /><p>All displayed metrics are reconstructed from immutable events and results.</p></div>
        </nav>

        <div className="explorer-content" id="explorer-content">
          <PortfolioCaseStudy />

          <section className="story-hero">
            <div className="hero-copy"><span className="eyebrow">{studyLabel(story)}</span><h1>{story.title}</h1><p>{story.summary}</p></div>
            <div className="hero-status"><span className={`outcome-chip large ${outcomeTone(selectedRun)}`}>{outcomeLabel(selectedRun)}</span><code>{selectedRun.data.run_id}</code></div>
          </section>

          <SimpleWalkthrough story={story} />

          <ComparisonStrip runs={runs} selectedId={selectedRun.id} onSelect={chooseRun} />

          <section className="metric-grid" aria-label="Selected run summary">
            <MetricCard label="Outcome" value={outcomeLabel(selectedRun)} context={outcomeContext(selectedRun)} />
            <MetricCard label="Recovery confirmed" value={milliseconds(selectedRun.metrics.confirmed_recovery_ms)} context={`${selectedRun.metrics.verification_passes}/2 health checks passed`} />
            <MetricCard label="Simulation score" value={`${totalScore?.toFixed(1) ?? "—"} / 100`} context={`${selectedRun.data.cost_units_used} effort units · ${selectedRun.data.tool_calls_used} external calls`} />
          </section>

          <nav className="panel-tabs" aria-label="Run detail views">{PANELS.map((item) => <button aria-current={panel === item.id ? "page" : undefined} key={item.id} onClick={() => { setPanel(item.id); setSelectedEvent(null); }} type="button">{item.label}</button>)}</nav>

          {loading && <div className="story-loading" aria-live="polite">Loading story artifacts…</div>}
          {!loading && panel === "timeline" && <Timeline run={selectedRun} selectedEvent={selectedEvent} onSelectEvent={setSelectedEvent} />}
          {!loading && panel === "evidence" && <EvidencePanel run={selectedRun} />}
          {!loading && panel === "governance" && <GovernancePanel run={selectedRun} />}
          {!loading && panel === "verification" && <VerificationPanel run={selectedRun} />}
          {!loading && panel === "budget" && <BudgetPanel run={selectedRun} />}
          {!loading && panel === "trace" && <TracePanel run={selectedRun} onSelectEvent={setSelectedEvent} />}
          {!loading && panel === "trace" && selectedEvent && <EventInspector event={selectedEvent} onClose={() => setSelectedEvent(null)} />}

          <footer className="app-footer"><div><strong>Aurora Agent Orchestration Lab</strong><span>A Fischer Product Lab experiment.</span><span>Deterministic studies, plus one optional model-backed diagnosis behind the same controls.</span></div><div><span>{selectedRun.data.trace.length} events</span><span>{selectedRun.data.evidence.length} evidence items</span><span>{selectedRun.data.task_results.length} tasks</span></div></footer>
        </div>
      </div>
      <p className="sr-only" aria-live="polite">Showing {story.title}: {selectedRun.title}, outcome {outcomeLabel(selectedRun)}.</p>
    </main>
  );
}
