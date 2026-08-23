import type { Metadata } from "next";
import "./globals.css";

const vercelProductionHost = process.env.VERCEL_PROJECT_PRODUCTION_URL;
const publicSiteUrl = vercelProductionHost
  ? `https://${vercelProductionHost}`
  : "https://aurora-fpl.vercel.app";

export const metadata: Metadata = {
  metadataBase: new URL(publicSiteUrl),
  title: "Aurora | Fischer Product Lab",
  description:
    "When the specialist fails, is recovery still governed? A Fischer Product Lab teaching simulator for investigation, controlled decisions, bounded recovery, and proof.",
  openGraph: {
    title: "Aurora | Fischer Product Lab",
    description:
      "Investigate, decide, recover, and prove. A read-only Fischer Product Lab teaching simulator for governed agent recovery.",
    type: "website",
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
    title: "Aurora | Fischer Product Lab",
    description:
      "When the specialist fails, is recovery still governed? A read-only teaching simulator from Fischer Product Lab.",
    images: ["/og-fischer.png"],
  },
  icons: {
    icon: "/favicon.svg",
    shortcut: "/favicon.svg",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
