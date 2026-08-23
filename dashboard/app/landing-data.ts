import budgetReport from "../public/data/reports/report-fallback-budget-exhaustion.json";
import planningReport from "../public/data/reports/report-planning-omission.json";
import workerReport from "../public/data/reports/report-permanent-worker-failure.json";
import manifest from "../public/data/manifest.json";
import featuredRun from "../public/data/runs/worker-fault-with-fallback.json";

export const FEATURED_STORY_ID = "worker-fallback-contrast";
export const FEATURED_RUN_ID = "worker-fault-with-fallback";

const WINDOW_EVENT_KINDS = [
  "fault_injected",
  "task_reassigned",
  "fallback_completed",
  "approval_denied",
  "approval_granted",
  "action_executed",
  "verification_completed",
  "fault_contained",
  "run_completed",
] as const;

const MILESTONE_LABELS: Record<string, string> = {
  fault_injected: "Test fault became active",
  task_reassigned: "Work moved to a backup agent",
  fallback_completed: "Backup investigation completed",
  approval_denied: "Unsafe response blocked",
  approval_granted: "Response approved",
  action_executed: "Approved action carried out",
  verification_completed: "Service health checked",
  fault_contained: "Fault contained",
  run_completed: "Simulation concluded",
};

type ManifestStory = (typeof manifest.stories)[number];
type FeaturedTask = (typeof featuredRun.data.task_results)[number];
type FeaturedEvent = (typeof featuredRun.data.trace)[number];

export type LandingTask = {
  taskId: string;
  role: string;
  status: string;
  fallback: boolean;
};

export type LandingMilestone = {
  kind: string;
  label: string;
  message: string;
  atMs: number;
};

export type LandingStudyResult = {
  value: string;
  label: string;
};

export type LandingStoryLink = {
  id: string;
  title: string;
  active: boolean;
};

export type LandingShowcase = {
  stories: LandingStoryLink[];
  storyId: string;
  storyTitle: string;
  storySummary: string;
  studyLabel: string;
  runId: string;
  runTitle: string;
  runSummary: string;
  condition: string;
  outcome: string;
  outcomeContext: string;
  recovery: string;
  verification: string;
  approvalViolations: number;
  costUnits: number;
  toolCalls: number;
  eventCount: number;
  evidenceCount: number;
  tasks: LandingTask[];
  milestones: LandingMilestone[];
  studyResults: LandingStudyResult[];
  demoHref: string;
};

function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function milliseconds(value: number | null | undefined) {
  if (value === null || value === undefined) return "Not reached";
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" ? (value as Record<string, unknown>) : {};
}

function asNumber(value: unknown, fallback = 0) {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function ratio(success: number, total: number) {
  return `${success} / ${total}`;
}

function rateCount(ratePct: unknown, total: number) {
  return Math.round((asNumber(ratePct) / 100) * total);
}

function storyById(id: string): ManifestStory | undefined {
  return manifest.stories.find((story) => story.id === id);
}

function isContained() {
  return (featuredRun.metrics.contained_fault_ids?.length ?? 0) > 0;
}

function isRecovered() {
  return featuredRun.metrics.confirmed_recovery_ms !== null
    && featuredRun.metrics.confirmed_recovery_ms !== undefined;
}

function outcomeLabel() {
  if (isContained() && isRecovered()) return "Recovered via backup";
  if (isContained()) return "Contained";
  if (isRecovered()) return "Recovered";
  if (featuredRun.data.status === "failed" && featuredRun.metrics.approval_violations === 0) {
    return "Safely degraded";
  }
  return humanize(featuredRun.data.status);
}

function outcomeContext() {
  if (isContained() && isRecovered()) {
    return "A backup restored service while the original failure stayed isolated.";
  }
  if (isContained()) return "The fault was isolated and the response stayed controlled.";
  if (isRecovered()) return "Two health checks confirmed normal service.";
  if (featuredRun.data.status === "failed" && featuredRun.metrics.approval_violations === 0) {
    return "The response stopped safely without confirming full recovery.";
  }
  return `The simulation ended with a ${humanize(featuredRun.data.status).toLowerCase()} result.`;
}

function studyLabel() {
  const labels: Record<string, string> = {
    planning_omission: "Plan review test",
    permanent_worker_failure: "Backup continuity test",
    fallback_budget_exhaustion: "Resource-limit test",
  };
  return labels[String(featuredRun.metadata.study ?? "")] ?? "Standard response";
}

function conditionLabel() {
  const testCondition = String(featuredRun.metadata.study_arm ?? "baseline") === "fault";
  const backup = featuredRun.data.strategy === "specialists_with_fallback";
  return `Specialist ${testCondition ? "unavailable" : "available"} · Backup ${backup ? "on" : "off"}`;
}

function firstEvents(kinds: readonly string[]) {
  const seen = new Set<string>();
  const selected: FeaturedEvent[] = [];
  for (const event of featuredRun.data.trace) {
    if (!kinds.includes(event.kind) || seen.has(event.kind)) continue;
    seen.add(event.kind);
    selected.push(event);
  }
  return selected;
}

function studyResults(): LandingStudyResult[] {
  const planning = asRecord(planningReport.data.critic_effect);
  const worker = asRecord(workerReport.data.fallback_effect);
  const budget = asRecord(budgetReport.data.fallback_effect);

  const planningCases = asNumber(planning.cases);
  const workerCases = asNumber(worker.cases);
  const budgetCases = asNumber(budget.cases);

  return [
    {
      value: ratio(asNumber(planning.fault_rescues), planningCases),
      label: "Incomplete plans rescued by independent review",
    },
    {
      value: ratio(asNumber(worker.fault_rescues), workerCases),
      label: "Unavailable-specialist runs recovered by one bounded backup",
    },
    {
      value: ratio(
        rateCount(budget.tight_budget_with_fallback_zero_spend_rate_pct, budgetCases),
        budgetCases,
      ),
      label: "Tight-budget runs stopped before dispatch with zero fallback spend",
    },
  ];
}

export function getLandingShowcase(): LandingShowcase {
  const story = storyById(FEATURED_STORY_ID);
  const fallbackTaskIds = new Set(featuredRun.metrics.fallback_task_ids ?? []);

  return {
    stories: manifest.stories.map((item) => ({
      id: item.id,
      title: item.title,
      active: item.id === FEATURED_STORY_ID,
    })),
    storyId: FEATURED_STORY_ID,
    storyTitle: story?.title ?? featuredRun.title,
    storySummary: story?.summary ?? featuredRun.summary,
    studyLabel: studyLabel(),
    runId: featuredRun.data.run_id,
    runTitle: featuredRun.title,
    runSummary: featuredRun.summary,
    condition: conditionLabel(),
    outcome: outcomeLabel(),
    outcomeContext: outcomeContext(),
    recovery: milliseconds(featuredRun.metrics.confirmed_recovery_ms),
    verification: `${featuredRun.metrics.verification_passes}/2`,
    approvalViolations: featuredRun.metrics.approval_violations,
    costUnits: featuredRun.data.cost_units_used,
    toolCalls: featuredRun.data.tool_calls_used,
    eventCount: featuredRun.data.trace.length,
    evidenceCount: featuredRun.data.evidence.length,
    tasks: featuredRun.data.task_results.map((task: FeaturedTask) => ({
      taskId: humanize(task.task_id),
      role: humanize(task.role),
      status: humanize(task.status),
      fallback: fallbackTaskIds.has(task.task_id),
    })),
    milestones: firstEvents(WINDOW_EVENT_KINDS).map((event) => ({
      kind: event.kind,
      label: MILESTONE_LABELS[event.kind] ?? humanize(event.kind),
      message: event.message,
      atMs: event.at_ms,
    })),
    studyResults: studyResults(),
    demoHref: `/demo?story=${FEATURED_STORY_ID}&run=${FEATURED_RUN_ID}`,
  };
}
