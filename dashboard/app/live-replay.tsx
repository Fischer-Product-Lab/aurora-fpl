"use client";

import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  buildReplaySnapshot,
  friendlyActor,
  nextReplayElapsed,
  replayCallout,
  replayDuration,
} from "./replay-model.mjs";

type ReplayEvent = {
  sequence: number;
  at_ms: number;
  kind: string;
  actor: string | null;
  task_id: string | null;
  message: string;
  metadata: [string, string][];
};

type ReplayTask = {
  task_id: string;
  role: string;
};

type ReplayEvidence = {
  evidence_id: string;
};

type ReplayResult = {
  run_id: string;
  started_at_ms: number;
  ended_at_ms: number;
  trace: ReplayEvent[];
  task_results: ReplayTask[];
  evidence: ReplayEvidence[];
};

type ReplayRun = {
  id: string;
  metadata: Record<string, unknown>;
  data: ReplayResult;
};

type ReplayTaskSnapshot = {
  taskId: string;
  role: string;
  state: string;
  visible: boolean;
  latestEvent: ReplayEvent | null;
};

type ReplaySnapshot = {
  elapsed: number;
  duration: number;
  progress: number;
  visibleEvents: ReplayEvent[];
  recentEvents: ReplayEvent[];
  visibleEvidence: ReplayEvidence[];
  tasks: ReplayTaskSnapshot[];
  currentEvent: ReplayEvent | null;
  nextEvent: ReplayEvent | null;
  activeTaskCount: number;
  decisionCount: number;
  runCompleted: boolean;
};

export type ExperienceMode = "explore" | "replay";
type ReplayPhase = "ready" | "playing" | "paused" | "complete";
type ReplaySpeed = 1 | 2 | 4;

const SPEEDS: ReplaySpeed[] = [1, 2, 4];

function humanize(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function formatClock(value: number) {
  const totalSeconds = Math.max(0, value) / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds - minutes * 60;
  return `${minutes}:${seconds.toFixed(1).padStart(4, "0")}`;
}

function replayEventTone(kind: string) {
  if (kind.includes("fault") || kind.includes("failed") || kind.includes("denied") || kind === "retry_exhausted") return "fault";
  if (kind.includes("contain") || kind.includes("repaired") || kind.includes("succeeded")) return "success";
  if (kind.includes("approval") || kind.includes("action")) return "governance";
  if (kind.includes("verification")) return "verification";
  if (kind.includes("retry") || kind.includes("budget")) return "warning";
  return "neutral";
}

function taskStateLabel(state: string) {
  const labels: Record<string, string> = {
    waiting: "Waiting",
    queued: "Assigned",
    working: "Working",
    retrying: "Retrying",
    succeeded: "Complete",
    failed: "Could not finish",
    "timed out": "Timed out",
    cancelled: "Stopped",
  };
  return labels[state] ?? humanize(state);
}

function taskActivity(task: ReplayTaskSnapshot) {
  if (!task.latestEvent) return "Waiting for an assignment.";
  if (task.latestEvent.message) return task.latestEvent.message;
  if (task.state === "working") return "Checking a bounded data source.";
  if (task.state === "queued") return "The coordinator assigned this investigation.";
  return humanize(task.latestEvent.kind);
}

export function LiveReplay({
  run,
  mode,
  onModeChange,
  onCompleteChange,
}: {
  run: ReplayRun;
  mode: ExperienceMode;
  onModeChange: (mode: ExperienceMode) => void;
  onCompleteChange: (complete: boolean) => void;
}) {
  const duration = replayDuration(run.data);
  const [phase, setPhase] = useState<ReplayPhase>("ready");
  const [elapsedMs, setElapsedMs] = useState(0);
  const [speed, setSpeed] = useState<ReplaySpeed>(4);
  const [hasStarted, setHasStarted] = useState(false);
  const elapsedRef = useRef(0);
  const completionReportedRef = useRef(false);

  useEffect(() => {
    if (mode !== "replay" || phase !== "playing") return;
    let animationFrame = 0;
    const anchorRealTime = performance.now();
    const anchorElapsed = elapsedRef.current;

    const advance = (now: number) => {
      const next = nextReplayElapsed(run.data, anchorElapsed, now - anchorRealTime, speed);
      elapsedRef.current = next;
      setElapsedMs(next);
      if (next >= duration) {
        setPhase("complete");
        const finalSnapshot = buildReplaySnapshot(run.data, next, true) as ReplaySnapshot;
        if (finalSnapshot.runCompleted && !completionReportedRef.current) {
          completionReportedRef.current = true;
          onCompleteChange(finalSnapshot.runCompleted);
        }
        return;
      }
      animationFrame = requestAnimationFrame(advance);
    };

    animationFrame = requestAnimationFrame(advance);
    return () => cancelAnimationFrame(animationFrame);
  }, [duration, mode, onCompleteChange, phase, run.data, speed]);

  const snapshot = useMemo(
    () => buildReplaySnapshot(run.data, elapsedMs, hasStarted) as ReplaySnapshot,
    [elapsedMs, hasStarted, run.data],
  );
  const study = String(run.metadata.study ?? run.metadata.fault ?? "");
  const callout = replayCallout(snapshot.currentEvent, study);
  const visibleTasks = snapshot.tasks.filter((task) => task.visible);
  const absoluteNow = run.data.started_at_ms + snapshot.elapsed;
  const nextEventWait = snapshot.nextEvent ? Math.max(0, snapshot.nextEvent.at_ms - absoluteNow) : 0;

  function resetCompletion() {
    completionReportedRef.current = false;
    onCompleteChange(false);
  }

  function startFresh() {
    elapsedRef.current = 0;
    setElapsedMs(0);
    setHasStarted(true);
    setPhase("playing");
    resetCompletion();
    onModeChange("replay");
  }

  function play() {
    if (phase === "complete") {
      startFresh();
      return;
    }
    setHasStarted(true);
    setPhase("playing");
  }

  function pause() {
    setPhase("paused");
  }

  function seek(nextElapsed: number) {
    const next = Math.min(duration, Math.max(0, nextElapsed));
    const soughtSnapshot = buildReplaySnapshot(run.data, next, true) as ReplaySnapshot;
    elapsedRef.current = next;
    setElapsedMs(next);
    setHasStarted(true);
    setPhase(soughtSnapshot.runCompleted ? "complete" : "paused");
    completionReportedRef.current = soughtSnapshot.runCompleted;
    onCompleteChange(soughtSnapshot.runCompleted);
  }

  function chooseExplore() {
    if (phase === "playing") setPhase("paused");
    onModeChange("explore");
  }

  const progressStyle = { width: `${snapshot.progress * 100}%` } as CSSProperties;

  return (
    <section className="live-replay" aria-labelledby="live-replay-title">
      <header className="replay-heading">
        <div>
          <span className="eyebrow">Recorded deterministic simulation</span>
          <h2 id="live-replay-title">See the agent team respond, one decision at a time.</h2>
          <p>Watch mode hides the ending and replays the existing audit trail. Explore mode opens the complete result immediately.</p>
        </div>
        <div className="experience-switch" aria-label="Choose how to view this run">
          <button aria-pressed={mode === "explore"} onClick={chooseExplore} type="button">Explore instantly</button>
          <button aria-pressed={mode === "replay"} onClick={() => mode === "replay" ? undefined : startFresh()} type="button">Watch replay</button>
        </div>
      </header>

      {mode === "explore" ? (
        <div className="replay-invitation">
          <div>
            <strong>Want the recruiter-friendly version?</strong>
            <p>Watch the specialists gather clues, recover from failures, request permission, and prove the result in about 17 seconds at 4× speed.</p>
          </div>
          <button className="replay-primary-action" onClick={startFresh} type="button">Start simulation replay</button>
        </div>
      ) : (
        <div className="replay-experience">
          <div className="replay-controls">
            <div className="replay-transport">
              <button className="replay-primary-action" onClick={phase === "playing" ? pause : play} type="button">
                {phase === "playing" ? "Pause" : phase === "complete" ? "Replay again" : "Play"}
              </button>
              <button onClick={startFresh} type="button">Restart</button>
              <span className={`replay-status ${phase}`}><i aria-hidden="true" />{phase === "playing" ? `Playing at ${speed}×` : phase === "complete" ? "Replay complete" : humanize(phase)}</span>
            </div>
            <fieldset className="replay-speed">
              <legend>Replay speed</legend>
              {SPEEDS.map((candidate) => (
                <button aria-pressed={speed === candidate} key={candidate} onClick={() => setSpeed(candidate)} type="button">{candidate}×</button>
              ))}
            </fieldset>
          </div>

          <div className="replay-scrubber">
            <div>
              <label htmlFor={`replay-progress-${run.id}`}>Simulation time</label>
              <span><strong>{formatClock(snapshot.elapsed)}</strong> / {formatClock(snapshot.duration)}</span>
            </div>
            <div className="replay-progress-track" aria-hidden="true"><span style={progressStyle} /></div>
            <input
              aria-label="Simulation replay position"
              aria-valuetext={`${formatClock(snapshot.elapsed)} of ${formatClock(snapshot.duration)}`}
              id={`replay-progress-${run.id}`}
              max={duration}
              min="0"
              onChange={(event) => seek(Number(event.currentTarget.value))}
              step="50"
              type="range"
              value={snapshot.elapsed}
            />
          </div>

          <div className="replay-stats" aria-label="Visible replay activity">
            <div><span>Agents active</span><strong>{snapshot.activeTaskCount}</strong></div>
            <div><span>Findings shared</span><strong>{snapshot.visibleEvidence.length}</strong></div>
            <div><span>Important steps</span><strong>{snapshot.decisionCount}</strong></div>
          </div>

          <div className="replay-stage">
            <section className="replay-agents" aria-labelledby="replay-agents-title">
              <div className="replay-section-title">
                <div><span className="eyebrow">Agent activity</span><h3 id="replay-agents-title">Who is doing what?</h3></div>
                <span>{visibleTasks.length} assigned</span>
              </div>
              <div className="control-plane-lane">
                <span className={`replay-node ${phase === "playing" ? "working" : ""}`} aria-hidden="true">A</span>
                <div><strong>Coordinator</strong><p>{snapshot.currentEvent ? `Following ${friendlyActor(snapshot.currentEvent.actor).toLowerCase()}'s latest update.` : "Waiting to divide and coordinate the work."}</p></div>
                <span>Control plane</span>
              </div>
              {visibleTasks.length ? (
                <ol className="replay-agent-list">
                  {visibleTasks.map((task) => (
                    <li data-state={task.state.replaceAll(" ", "-")} key={task.taskId}>
                      <span className={`replay-node ${task.state.replaceAll(" ", "-")}`} aria-hidden="true" />
                      <div><strong>{friendlyActor(task.role)}</strong><small>{humanize(task.taskId)}</small><p>{taskActivity(task)}</p></div>
                      <span className="replay-task-state">{taskStateLabel(task.state)}</span>
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="replay-empty">Press play and the coordinator will assign the first investigations.</p>
              )}
            </section>

            <aside className="replay-business-callout" aria-atomic="true" aria-live="polite">
              <span className="eyebrow">What is happening now?</span>
              <div className="callout-event-label"><i className={`event-inline-mark ${replayEventTone(snapshot.currentEvent?.kind ?? "")}`} aria-hidden="true" />{snapshot.currentEvent ? humanize(snapshot.currentEvent.kind) : "Ready"}</div>
              <h3>{callout.title}</h3>
              <dl>
                <div><dt>What happened</dt><dd>{callout.what}</dd></div>
                <div><dt>Why it matters</dt><dd>{callout.why}</dd></div>
              </dl>
              {nextEventWait >= 5000 && phase === "playing" && (
                <p className="replay-waiting">The clock is moving while the independent health checker waits for the next scheduled reading. Next update in {Math.ceil(nextEventWait / 1000)} simulated seconds.</p>
              )}
            </aside>
          </div>

          <section className="replay-event-stream" aria-labelledby="replay-event-stream-title">
            <div className="replay-section-title">
              <div><span className="eyebrow">Latest recorded activity</span><h3 id="replay-event-stream-title">The audit trail, as it arrives</h3></div>
              <span>{snapshot.visibleEvents.length} of {run.data.trace.length} events</span>
            </div>
            {snapshot.recentEvents.length ? (
              <ol>
                {snapshot.recentEvents.map((event) => (
                  <li key={event.sequence}>
                    <time>T+{formatClock(Math.max(0, event.at_ms - run.data.started_at_ms))}</time>
                    <span className={`event-inline-mark ${replayEventTone(event.kind)}`} aria-hidden="true" />
                    <div><strong>{friendlyActor(event.actor)}</strong><p>{event.message || humanize(event.kind)}</p></div>
                    <code>{humanize(event.kind)}</code>
                  </li>
                ))}
              </ol>
            ) : <p className="replay-empty">No events revealed yet.</p>}
          </section>

          <div className={`replay-outcome-gate ${snapshot.runCompleted ? "open" : "locked"}`}>
            <span aria-hidden="true">{snapshot.runCompleted ? "✓" : "?"}</span>
            <div>
              <strong>{snapshot.runCompleted ? "The full result is now unlocked" : "The ending is intentionally hidden"}</strong>
              <p>{snapshot.runCompleted ? "The final event arrived. The score, outcome, and detailed proof below are now visible." : "Finish the replay to reveal the outcome, or switch to Explore instantly if you want the answer now."}</p>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
