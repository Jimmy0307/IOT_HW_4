#!/usr/bin/env node
// Static guard: fail (exit 1) if any file under src/ uses an HTML/code-injection sink.
// Device- and log-supplied text must only ever be rendered as React text nodes.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative, sep } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = fileURLToPath(new URL("..", import.meta.url));
const SRC = join(ROOT, "src");
const EXCLUDE = new Set(["src/__tests__/no-dangerous-html.test.ts"]);
const EXTENSIONS = /\.(tsx?|jsx?|mjs|cjs|html)$/;

const RULES = [
  { name: "dangerouslySetInnerHTML", re: /dangerouslySetInnerHTML/ },
  { name: "innerHTML", re: /\binnerHTML\b/ },
  { name: "outerHTML", re: /\bouterHTML\b/ },
  { name: "insertAdjacentHTML", re: /\binsertAdjacentHTML\b/ },
  { name: "document.write", re: /\bdocument\.write(ln)?\s*\(/ },
  { name: "eval(", re: /\beval\s*\(/ },
  { name: "new Function", re: /\bnew\s+Function\b/ },
];

function walk(dir) {
  return readdirSync(dir).flatMap((name) => {
    const full = join(dir, name);
    return statSync(full).isDirectory() ? walk(full) : [full];
  });
}

const files = walk(SRC).filter((f) => EXTENSIONS.test(f));
const hits = [];
for (const file of files) {
  const rel = relative(ROOT, file).split(sep).join("/");
  if (EXCLUDE.has(rel)) continue;
  readFileSync(file, "utf8")
    .split(/\r?\n/)
    .forEach((line, i) => {
      for (const rule of RULES) {
        if (rule.re.test(line)) hits.push(`${rel}:${i + 1}: forbidden pattern "${rule.name}"`);
      }
    });
}

if (hits.length > 0) {
  console.error(`lint:security FAILED - ${hits.length} hit(s):`);
  for (const h of hits) console.error(`  ${h}`);
  process.exit(1);
}
console.log(`lint:security OK - scanned ${files.length} files under src/, 0 dangerous patterns found.`);
