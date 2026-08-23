import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Aurora Run Explorer | Fischer Product Lab",
  description:
    "A Fischer Product Lab demonstration of deterministic agent orchestration, evidence, governance, failures, and recovery.",
  openGraph: {
    title: "Aurora Run Explorer | Fischer Product Lab",
    description:
      "A Fischer Product Lab trace-driven demonstration of agent orchestration, controlled faults, governance, and recovery.",
    images: [
      {
        url: "/og-fischer.png",
        width: 1536,
        height: 1024,
        alt: "Aurora Run Explorer in the Fischer Product Lab visual system, with parallel agent lanes, evidence, verification, and governed recovery",
      },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Aurora Run Explorer | Fischer Product Lab",
    description:
      "A Fischer Product Lab demonstration of how an orchestrated agent team handles evidence, budgets, faults, and governed recovery.",
    images: ["/og-fischer.png"],
  },
};

export default function DemoLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return children;
}
