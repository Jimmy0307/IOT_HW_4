import { describe, expect, it } from "vitest";

// Every source file under src/, loaded as raw text at test time by Vite.
const sources = import.meta.glob("/src/**/*.{ts,tsx,js,jsx,mjs,cjs,html}", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

const SELF = "/src/__tests__/no-dangerous-html.test.ts";

// Patterns are assembled from fragments so the list itself never matches a grep.
const FORBIDDEN: Array<{ name: string; re: RegExp }> = [
  { name: "dangerously" + "SetInnerHTML", re: new RegExp("dangerously" + "SetInnerHTML") },
  { name: "inner" + "HTML", re: new RegExp("\\binner" + "HTML\\b") },
  { name: "ev" + "al(", re: new RegExp("\\bev" + "al\\s*\\(") },
  { name: "new " + "Function", re: new RegExp("\\bnew\\s+" + "Function\\b") },
];

describe("source tree has no HTML/code injection sinks", () => {
  const files = Object.keys(sources).filter((f) => f !== SELF);

  it("scans a non-trivial number of files", () => {
    expect(files.length).toBeGreaterThan(15);
    expect(files.some((f) => f.endsWith(".tsx"))).toBe(true);
  });

  it.each(FORBIDDEN.map((p) => [p.name, p.re] as const))("no file uses %s", (_name, re) => {
    const hits = files.flatMap((file) =>
      sources[file]
        .split(/\r?\n/)
        .map((line, i) => ({ file, line: i + 1, text: line }))
        .filter((l) => re.test(l.text)),
    );
    expect(hits.map((h) => `${h.file}:${h.line}`)).toEqual([]);
  });
});
