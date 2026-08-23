import type { NextConfig } from "next";

const contentSecurityPolicy = [
  "default-src 'self'",
  "base-uri 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "img-src 'self' data:",
  "font-src 'self' data:",
  "media-src 'self' blob:",
  "style-src 'self' 'unsafe-inline'",
  "script-src 'self' 'unsafe-inline'",
  "script-src-attr 'none'",
  "connect-src 'self'",
  "frame-src 'none'",
  "worker-src 'none'",
  "manifest-src 'none'",
  "upgrade-insecure-requests",
].join("; ");

const nextConfig: NextConfig = {
  async redirects() {
    return [
      {
        source: "/",
        has: [{ type: "query", key: "story" }],
        destination: "/demo",
        permanent: false,
      },
      {
        source: "/",
        has: [{ type: "query", key: "run" }],
        destination: "/demo",
        permanent: false,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/media/aurora-portfolio-walkthrough-297271fb.wav",
        headers: [
          {
            key: "Cache-Control",
            value: "public, max-age=31536000, immutable",
          },
        ],
      },
      ...["/media/:path*", "/data/:path*", "/fonts/:path*"].map(
        (source) => ({
          source,
          headers: [
            { key: "Cross-Origin-Resource-Policy", value: "same-origin" },
          ],
        }),
      ),
      {
        source: "/:path*",
        headers: [
          { key: "Content-Security-Policy", value: contentSecurityPolicy },
          { key: "Referrer-Policy", value: "no-referrer" },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Cross-Origin-Opener-Policy", value: "same-origin" },
          { key: "Strict-Transport-Security", value: "max-age=31536000" },
          {
            key: "Permissions-Policy",
            value: "camera=(), geolocation=(), microphone=()",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
