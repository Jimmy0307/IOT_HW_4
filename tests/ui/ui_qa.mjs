// Browser QA for the Orchid dashboard. Drives the REAL stack through https://localhost:8443.
// TLS: the headless browser is given the lab CA's SPKI hash as its trust anchor (ORCHID_CA_SPKI);
// certificate verification is NOT disabled globally. Strict hostname verification of the endpoint is
// covered separately by gate G03 (Python ssl, CERT_REQUIRED + check_hostname).
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";
import { readFileSync, mkdirSync, writeFileSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..", "..");
const OUT = resolve(ROOT, "evidence", "generated", "ui");
const BASE = process.env.ORCHID_UI_BASE || "https://localhost:8443";
const SPKI = process.env.ORCHID_CA_SPKI;
mkdirSync(OUT, { recursive: true });

const creds = Object.fromEntries(
  readFileSync(resolve(ROOT, "secrets", "web_credentials.local.txt"), "utf8")
    .split(/\r?\n/).filter((l) => l && !l.startsWith("#")).map((l) => l.split("\t").slice(0, 2)),
);
const results = [];
const env = { ...process.env, MSYS_NO_PATHCONV: "1" };
const sh = (...args) => execFileSync("docker", args, { env, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"] });
const ctl = (container, method, path, body) =>
  sh("exec", container, "python", "-m", "orchid.tools.faultctl", method, path, ...(body ? [JSON.stringify(body)] : []));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function check(name, ok, detail = {}) {
  results.push({ check: name, result: ok ? "PASS" : "FAIL", detail, ts: new Date().toISOString() });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}`);
}

async function waitFor(fn, timeoutMs, stepMs = 1000) {
  const end = Date.now() + timeoutMs;
  let last;
  while (Date.now() < end) {
    try { last = await fn(); if (last) return last; } catch (e) { last = e; }
    await sleep(stepMs);
  }
  return null;
}

async function card(page, site) { return page.locator(`[data-testid="site-card-${site}"]`); }
async function text(loc) { return (await loc.innerText()).trim(); }

async function login(page, role) {
  await page.goto(BASE + "/");
  await page.getByTestId("login-username").fill(role);
  await page.getByTestId("login-password").fill(creds[role]);
  await page.getByTestId("login-submit").click();
  await page.getByTestId("site-card-site01").waitFor({ timeout: 20000 });
}

async function main() {
  if (!SPKI) throw new Error("ORCHID_CA_SPKI not set (run via scripts/verify_all.py or scripts/ui_qa.py)");
  const browser = await chromium.launch({ args: [`--ignore-certificate-errors-spki-list=${SPKI}`] });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
  const page = await ctx.newPage();
  let dialogs = 0;
  page.on("dialog", async (d) => { dialogs += 1; await d.dismiss(); });
  const consoleErrors = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text().slice(0, 200)); });

  try {
    // ---- login + six cards
    await page.goto(BASE + "/");
    await page.screenshot({ path: resolve(OUT, "00-login.png") });
    await login(page, "viewer");
    const cards = await page.locator('[data-testid^="site-card-"]').count();
    check("six site cards rendered", cards === 6, { cards });
    await sleep(2500);
    await page.screenshot({ path: resolve(OUT, "01-overview.png"), fullPage: true });

    // ---- telemetry changes over time (all 12 live readings on the overview)
    const snapshot = async () => (await page.locator('[data-testid="temp-value"], [data-testid="hum-value"]').allInnerTexts()).join("|");
    const t1 = await snapshot();
    const changed = await waitFor(async () => { const t2 = await snapshot(); return t2 !== t1 ? t2 : null; }, 20000);
    check("live telemetry changes", Boolean(changed), { before: t1, after: changed });

    // ---- warning colour: temperature high -> fans ON
    ctl("orchid-site02-sim", "POST", "/set", { metric: "temperature", value: 23 });
    const warm = await waitFor(async () => {
      const c = await card(page, "site02");
      const fan = await text(c.getByTestId("actuator-fan01"));
      const lvl = await c.getByTestId("temp-value").getAttribute("data-level");
      return fan.includes("ON") && lvl === "warning" ? { fan, lvl } : null;
    }, 45000);
    check("temperature high shows amber warning and fans ON", Boolean(warm), warm || {});
    await (await card(page, "site02")).screenshot({ path: resolve(OUT, "02-site02-warning-fans-on.png") });

    // ---- partial actuator failure -> DEGRADED (amber) + alert
    ctl("orchid-site06-controller", "POST", "/fault/actuator", { member: "fan02", fault: "STUCK_OFF" });
    ctl("orchid-site06-sim", "POST", "/set", { metric: "temperature", value: 23 });
    const degraded = await waitFor(async () => {
      const c = await card(page, "site06");
      const st = await text(c.getByTestId("site-status"));
      return st.includes("DEGRADED") ? { status: st, level: await c.getByTestId("site-status").getAttribute("data-level"),
        alerts: await text(c.getByTestId("alert-count")), fan02: await text(c.getByTestId("actuator-fan02")) } : null;
    }, 60000);
    check("partial actuator failure shows DEGRADED", Boolean(degraded) && degraded.level === "warning", degraded || {});
    await (await card(page, "site06")).screenshot({ path: resolve(OUT, "03-site06-partial-failure-degraded.png") });

    // ---- stale: freeze site05 controller (retained state must turn gray, not stay green)
    sh("pause", "orchid-site05-controller");
    const stale = await waitFor(async () => {
      const c = await card(page, "site05");
      const fan = await text(c.getByTestId("actuator-fan01"));
      const lvl = await c.getByTestId("actuator-fan01").getAttribute("data-level");
      return fan.includes("STALE") && lvl === "stale" ? { fan, lvl, status: await text(c.getByTestId("site-status")) } : null;
    }, 60000);
    check("frozen controller: retained state shown STALE (gray)", Boolean(stale), stale || {});
    await (await card(page, "site05")).screenshot({ path: resolve(OUT, "04-site05-stale.png") });
    sh("unpause", "orchid-site05-controller");

    // ---- offline: kill site04 controller -> LWT OFFLINE (red)
    sh("kill", "-s", "KILL", "orchid-site04-controller");
    const offline = await waitFor(async () => {
      const c = await card(page, "site04");
      const st = await text(c.getByTestId("site-status"));
      return st.includes("OFFLINE") ? { status: st, level: await c.getByTestId("site-status").getAttribute("data-level") } : null;
    }, 60000);
    check("killed controller shows OFFLINE (red)", Boolean(offline) && offline.level === "critical", offline || {});
    await page.screenshot({ path: resolve(OUT, "05-overview-offline-stale-degraded.png"), fullPage: true });
    sh("start", "orchid-site04-controller");

    // ---- stored XSS payload rendered as text on the security page
    const marker = `xss-${Date.now()}`;
    const evt = { event_id: marker, event_type: "DIAGNOSTIC",
      details: { message: `<script>alert('${marker}')</script><img src=x onerror=alert(1)>` } };
    sh("exec", "orchid-lab-probe", "python", "-m", "orchid.tools.probe", "pub", "--host", "site01-broker", "--user",
      "site01-temp01", "--topic", "orchid/v1/site01/events", "--payload", JSON.stringify(evt));
    await page.getByTestId("nav-system").click();
    await page.getByTestId("system-page").waitFor();
    const xss = await waitFor(async () => {
      const row = page.locator('[data-testid="events-table"]').getByText(`alert('${marker}')`, { exact: false });
      return (await row.count()) > 0 ? row.first() : null;
    }, 30000);
    const scriptsInTable = await page.locator('[data-testid="events-table"] script, [data-testid="events-table"] img').count();
    check("XSS payload shown as literal text, no script/img element, no dialog",
      Boolean(xss) && scriptsInTable === 0 && dialogs === 0, { found: Boolean(xss), scriptsInTable, dialogs });
    await page.screenshot({ path: resolve(OUT, "06-system-security-xss-as-text.png"), fullPage: true });
    const audit = await text(page.getByTestId("audit-integrity"));
    check("security page shows audit integrity PASS", audit.includes("PASS"), { audit });
    check("security page shows connected sites", (await text(page.getByTestId("sites-connected"))).length > 0);

    // ---- site detail + trend (viewer has no control panel)
    await page.getByTestId("nav-overview").click();
    await (await card(page, "site02")).click();
    await page.getByTestId("trend-chart").waitFor({ timeout: 15000 });
    check("viewer has no control panel", (await page.getByTestId("control-panel").count()) === 0);
    await sleep(2000);
    await page.screenshot({ path: resolve(OUT, "07-site02-detail-trend.png"), fullPage: true });

    // ---- operator control panel
    await page.getByTestId("nav-logout").click();
    await login(page, "operator");
    await (await card(page, "site01")).click();
    await page.getByTestId("control-panel").waitFor({ timeout: 15000 });
    await page.getByTestId("cmd-reason").fill("UI QA operator check");
    await page.getByTestId("cmd-dehum-on").click();
    const ack = await waitFor(async () => {
      const s = page.getByTestId("cmd-ack-status");
      return (await s.count()) && (await text(s)).includes("APPLIED") ? await text(s) : null;
    }, 30000);
    check("operator command ACK shown in UI", Boolean(ack), { ack });
    await page.screenshot({ path: resolve(OUT, "08-operator-control-ack.png"), fullPage: true });
    await page.getByTestId("mode-auto").click();
    await sleep(1500);
    check("no browser console errors", consoleErrors.length === 0, { consoleErrors });
  } finally {
    for (const [c, m, p, b] of [
      ["orchid-site02-sim", "POST", "/profile", {}], ["orchid-site06-sim", "POST", "/profile", {}],
      ["orchid-site06-controller", "POST", "/fault/clear", {}],
    ]) { try { ctl(c, m, p, b); } catch { /* best effort */ } }
    try { sh("unpause", "orchid-site05-controller"); } catch { /* not paused */ }
    try { sh("start", "orchid-site04-controller"); } catch { /* running */ }
    await browser.close();
  }
  const failed = results.filter((r) => r.result !== "PASS").length;
  writeFileSync(resolve(OUT, "ui_qa_result.json"),
    JSON.stringify({ base: BASE, tls_trust: "lab CA SPKI pinned for this browser instance only",
      generated: new Date().toISOString(), failed, results }, null, 2));
  process.exit(failed ? 1 : 0);
}

main().catch((e) => {
  writeFileSync(resolve(OUT, "ui_qa_result.json"), JSON.stringify({ error: String(e).slice(0, 500), results }, null, 2));
  console.error(e);
  process.exit(2);
});
