import Link from "next/link";
import { ProductWindow } from "./product-window";
import type { LandingShowcase } from "./landing-data";

const DECISION_STEPS = [
  ["01", "Investigate", "Specialists collect evidence"],
  ["02", "Combine", "A planner builds a response"],
  ["03", "Challenge", "A reviewer finds missing steps"],
  ["04", "Control", "Approval and budget gates decide"],
  ["05", "Verify", "Health checks prove the outcome"],
] as const;

export function LandingPage({ showcase }: { showcase: LandingShowcase }) {
  return (
    <div className="marketing">
      <div className="marketing-grain" aria-hidden="true" />

      <div className="marketing-frame">
        <header className="marketing-nav">
          <Link className="marketing-brand" href="/">
            <span>Aurora</span>
            <small>Fischer Product Lab</small>
          </Link>
          <Link className="marketing-nav-link" href="/demo">Open the demo</Link>
        </header>

        <main className="marketing-main">
          <section className="marketing-hero">
            <p className="marketing-type-meta marketing-rise">
              Portfolio demonstration · read-only · teaching simulator
            </p>
            <h1 className="marketing-type-display marketing-rise marketing-rise-delay-1">
              When the specialist fails, is recovery still governed?
            </h1>
            <p className="marketing-type-body marketing-rise marketing-rise-delay-2">
              Investigate the failure. Make a controlled decision. Recover within
              bounds. Prove the result. Aurora is a read-only synthetic teaching
              simulator, not a production responder.
            </p>
            <div className="marketing-ctas marketing-rise marketing-rise-delay-3">
              <Link className="marketing-cta-primary" href="/demo">Open the demo</Link>
              <a className="marketing-cta-secondary" href="#how-it-decides">See how it decides</a>
            </div>
          </section>

          <section
            className="marketing-stage marketing-rise marketing-rise-delay-4"
            aria-label="Aurora product"
          >
            <div className="marketing-light" aria-hidden="true" />
            <Link className="marketing-product-link" href={showcase.demoHref}>
              <span className="sr-only">
                Open the specialist-failure run in the live demo
              </span>
              <ProductWindow showcase={showcase} />
            </Link>
          </section>

          <section className="marketing-decides" id="how-it-decides">
            <p className="marketing-type-meta">How it decides</p>
            <h2 className="marketing-type-title">
              Investigate, decide, recover, and prove — with the original failure still on the record.
            </h2>
            <ol>
              {DECISION_STEPS.map(([n, label, copy]) => (
                <li key={n}>
                  <span>{n}</span>
                  <strong>{label}</strong>
                  <p>{copy}</p>
                </li>
              ))}
            </ol>
          </section>
        </main>

        <footer className="marketing-footer">
          <p>
            Fischer Product Lab · Aurora Agent Orchestration Lab · deterministic
            studies on synthetic data
          </p>
        </footer>
      </div>
    </div>
  );
}
