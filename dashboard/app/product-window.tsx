import type { LandingShowcase } from "./landing-data";

function milliseconds(value: number) {
  if (value < 1000) return `${value} ms`;
  return `${(value / 1000).toFixed(2)} s`;
}

export function ProductWindow({ showcase }: { showcase: LandingShowcase }) {
  return (
    <div className="product-window">
      <div className="product-window-chrome">
        <div className="product-window-dots" aria-hidden="true">
          <span />
          <span />
          <span />
        </div>
        <div className="product-window-address">
          aurora · demo / {showcase.storyId}
        </div>
      </div>

      <div className="product-window-body">
        <aside className="product-window-rail" aria-hidden="true">
          <div className="product-window-brand">
            <span>A</span>
            <div>
              <strong>Aurora</strong>
              <small>Run Explorer</small>
            </div>
          </div>
          <p>Choose a demo</p>
          <ol>
            {showcase.stories.map((item, index) => (
              <li key={item.id} data-active={item.active}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <em>{item.title}</em>
              </li>
            ))}
          </ol>
        </aside>

        <div className="product-window-main">
          <header className="product-window-hero">
            <div>
              <p className="product-window-meta">{showcase.studyLabel}</p>
              <h3>{showcase.runTitle}</h3>
              <p>{showcase.runSummary}</p>
            </div>
            <div className="product-window-status">
              <span className="product-window-chip">{showcase.outcome}</span>
              <small>{showcase.condition}</small>
            </div>
          </header>

          <dl className="product-window-metrics">
            <div>
              <dt>Recovery confirmed</dt>
              <dd>{showcase.recovery}</dd>
            </div>
            <div>
              <dt>Health checks</dt>
              <dd>{showcase.verification}</dd>
            </div>
            <div>
              <dt>Policy violations</dt>
              <dd>{showcase.approvalViolations}</dd>
            </div>
            <div>
              <dt>Usage</dt>
              <dd>{showcase.costUnits} units · {showcase.toolCalls} calls</dd>
            </div>
          </dl>

          <div className="product-window-split">
            <section>
              <p className="product-window-meta">Investigation tasks</p>
              <ul className="product-window-tasks">
                {showcase.tasks.map((task) => (
                  <li key={task.taskId} data-status={task.status.toLowerCase()}>
                    <span className="product-window-task-mark" aria-hidden="true" />
                    <div>
                      <strong>{task.taskId}</strong>
                      <small>{task.role}{task.fallback ? " · bounded backup" : ""}</small>
                    </div>
                    <em>{task.status}</em>
                  </li>
                ))}
              </ul>
            </section>

            <section>
              <p className="product-window-meta">Governed sequence</p>
              <ol className="product-window-milestones">
                {showcase.milestones.map((event) => (
                  <li key={`${event.kind}-${event.atMs}`}>
                    <time>{milliseconds(event.atMs)}</time>
                    <div>
                      <strong>{event.label}</strong>
                      <small>{event.message}</small>
                    </div>
                  </li>
                ))}
              </ol>
            </section>
          </div>
        </div>
      </div>

      <div className="product-window-proof" aria-label="Matched-study results from showcase reports">
        {showcase.studyResults.map((result) => (
          <article key={result.label}>
            <strong>{result.value}</strong>
            <p>{result.label}</p>
          </article>
        ))}
      </div>
    </div>
  );
}
