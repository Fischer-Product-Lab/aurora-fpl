import { readdir, readFile } from "node:fs/promises";
import { extname, relative, resolve } from "node:path";
import { pathToFileURL } from "node:url";

const PUBLIC_ROOT = resolve(import.meta.dirname, "../public");
const TEXT_EXTENSIONS = new Set([
  ".css",
  ".html",
  ".js",
  ".json",
  ".md",
  ".svg",
  ".txt",
  ".vtt",
  ".xml",
]);

const RULES = [
  ["private key", /-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----/g],
  ["OpenAI-style API key", /\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}\b/g],
  ["GitHub token", /\bgh[opurs]_[A-Za-z0-9]{20,}\b/g],
  ["AWS access key", /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g],
  ["bearer token", /\bBearer\s+[A-Za-z0-9._~+/=-]{16,}\b/gi],
  ["credential assignment", /\b(?:api[_-]?key|secret|password|access[_-]?token|auth[_-]?token)\b\s*["']?\s*[:=]\s*["']?[A-Za-z0-9._~+/=-]{12,}/gi],
  ["email address", /\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b/gi],
  ["local user path", /(?:[A-Za-z]:\\Users\\[^\\\s]+|\/(?:Users|home)\/[^/\s]+)/g],
  ["US Social Security number", /\b\d{3}-\d{2}-\d{4}\b/g],
  ["IPv4 address", /\b(?:\d{1,3}\.){3}\d{1,3}\b/g],
];

async function textFiles(directory) {
  const files = [];
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = resolve(directory, entry.name);
    if (entry.isDirectory()) files.push(...await textFiles(path));
    else if (entry.isFile() && TEXT_EXTENSIONS.has(extname(entry.name).toLowerCase())) files.push(path);
  }
  return files;
}

export function scanText(text, file = "<memory>") {
  const findings = [];
  for (const [rule, pattern] of RULES) {
    for (const match of text.matchAll(new RegExp(pattern.source, pattern.flags))) {
      const line = text.slice(0, match.index).split("\n").length;
      findings.push({ file, line, rule });
    }
  }
  return findings;
}

export async function scanPublicArtifacts(root = PUBLIC_ROOT) {
  const findings = [];
  for (const file of await textFiles(root)) {
    findings.push(...scanText(await readFile(file, "utf8"), relative(root, file)));
  }
  return findings;
}

function formatFinding({ file, line, rule }) {
  return `${file}:${line} matched ${rule}`;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const findings = await scanPublicArtifacts();
  if (findings.length) {
    console.error("Public artifact scan failed. Review these locations without committing the matched values:");
    for (const finding of findings) console.error(`- ${formatFinding(finding)}`);
    process.exitCode = 1;
  } else {
    console.log("Public artifact scan passed.");
  }
}
