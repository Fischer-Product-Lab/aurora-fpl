import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Aurora Run Explorer | Fischer Product Lab",
  description:
    "A Fischer Product Lab demonstration of deterministic agent orchestration, evidence, governance, failures, and recovery.",
  openGraph: {
    title: "Aurora Run Explorer | Fischer Product Lab",
    description:
      "A Fischer Product Lab trace-driven demonstration of agent orchestration, controlled faults, governance, and recovery.",
  },
  twitter: {
    title: "Aurora Run Explorer | Fischer Product Lab",
    description:
      "A Fischer Product Lab demonstration of how an orchestrated agent team handles evidence, budgets, faults, and governed recovery.",
  },
};

export default function DemoLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return children;
}
