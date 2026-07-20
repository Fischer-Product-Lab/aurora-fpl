import { access, cp, mkdir, rm, writeFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { Plugin } from "vite";

async function exists(path: string): Promise<boolean> {
  try {
    await access(path);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") {
      return false;
    }
    throw error;
  }
}

// Packages Sites metadata and migrations after Vite finishes compiling.
export function sites(): Plugin {
  let root = process.cwd();

  return {
    name: "sites",
    apply: "build",
    configResolved(config) {
      root = config.root;
    },
    async closeBundle() {
      const outputDirectory = resolve(root, "dist", ".openai");
      const hostingConfig = resolve(root, ".openai", "hosting.json");
      const drizzleSource = resolve(root, "drizzle");

      await rm(outputDirectory, { recursive: true, force: true });
      await mkdir(outputDirectory, { recursive: true });

      if (await exists(hostingConfig)) {
        await cp(hostingConfig, resolve(outputDirectory, "hosting.json"));
      }
      if (await exists(drizzleSource)) {
        await cp(drizzleSource, resolve(outputDirectory, "drizzle"), {
          recursive: true,
        });
      }

      await writeFile(
        resolve(root, "dist", "client", "_headers"),
        [
          "# Cache content-hashed assets immutably",
          "/assets/*",
          "  Cache-Control: public, max-age=31536000, immutable",
          "  X-Content-Type-Options: nosniff",
          "",
          "# Security and cache hardening for direct static-asset responses",
          "/*",
          "  Strict-Transport-Security: max-age=31536000",
          "  X-Content-Type-Options: nosniff",
          "  Referrer-Policy: no-referrer",
          "  Permissions-Policy: camera=(), geolocation=(), microphone=()",
          "",
          "/media/*",
          "  Cross-Origin-Resource-Policy: same-origin",
          "",
          "/media/aurora-portfolio-walkthrough-297271fb.wav",
          "  Cache-Control: public, max-age=31536000, immutable",
          "",
          "/data/*",
          "  Cross-Origin-Resource-Policy: same-origin",
          "",
          "/fonts/*",
          "  Cross-Origin-Resource-Policy: same-origin",
          "",
        ].join("\n"),
        "utf8",
      );
    },
  };
}
