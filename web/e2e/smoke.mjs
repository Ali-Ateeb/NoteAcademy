/**
 * Browser smoke test.
 *
 * Covers the behaviour that unit tests cannot reach and that breaks silently:
 * the timer actually ticking, a half-finished paper surviving a reload, marking
 * against the answer key, and the dashboard reading the attempt log. The resume
 * path in particular is the one students notice when it regresses.
 *
 *   npm run build && npm run start -- -p 3210
 *   node e2e/smoke.mjs                       # override with BASE_URL
 *
 * Exits non-zero if any check fails.
 */

import { chromium } from "playwright";

const BASE = process.env.BASE_URL ?? "http://localhost:3210";
const EXECUTABLE = process.env.CHROMIUM_PATH ?? undefined;

let failures = 0;
function check(label, ok) {
  if (!ok) failures += 1;
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}`);
}

const browser = await chromium.launch(
  EXECUTABLE ? { executablePath: EXECUTABLE } : {},
);
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

const jsErrors = [];
page.on("pageerror", (e) => jsErrors.push(`pageerror: ${e.message}`));
page.on("console", (m) => {
  if (m.type() === "error") jsErrors.push(`console: ${m.text()}`);
});

// ---- landing ----
await page.goto(BASE, { waitUntil: "networkidle" });
check(
  "landing renders its headline",
  (await page.locator("h1").innerText()).includes("Past papers"),
);

// ---- the arena ----
await page.goto(`${BASE}/practice/physics-5054-2019-may-june-p12`, {
  waitUntil: "networkidle",
});
await page.waitForSelector("[role=radiogroup]");
check("arena opens on question 1", (await page.getByText("Question 1 / 12").count()) > 0);

const clock = () => page.locator("span.tabular-nums").first().innerText();
check(`timer starts at 15:00 (${await clock()})`, (await clock()) === "15:00");

// Answer by keyboard, the way someone under time pressure does.
await page.keyboard.press("b"); // q1: correct
check(
  "a keypress selects an option",
  (await page.locator('[aria-checked="true"]').count()) === 1,
);
await page.keyboard.press("ArrowRight");
await page.keyboard.press("c"); // q2: correct
await page.keyboard.press("f"); // flag it
await page.keyboard.press("ArrowRight");
await page.keyboard.press("a"); // q3: deliberately wrong
check("three answers recorded", (await page.getByText("3 of 12 answered").count()) > 0);

// The clock must genuinely tick while the student is interacting. It once did
// not: the interval was torn down and recreated on every state change.
await page.waitForTimeout(2200);
check(`clock advances during use (${await clock()})`, (await clock()) !== "15:00");

// ---- resume ----
await page.reload({ waitUntil: "networkidle" });
await page.waitForSelector("[role=radiogroup]");
check(
  "resumes after a reload",
  (await page.getByText("Resumed where you left off").count()) > 0,
);
check("answers survived the reload", (await page.getByText("3 of 12 answered").count()) > 0);
check(`clock was not reset by the reload (${await clock()})`, (await clock()) !== "15:00");

// ---- marking ----
await page.getByRole("button", { name: "Submit paper" }).click();
await page.waitForSelector("text=time spent");
const score = (await page.locator("span.font-serif").first().innerText()).replace(/\s+/g, "");
check(`marked 2 of the 3 answered correct (${score})`, score.startsWith("2"));
check("mark scheme revealed only after submitting", (await page.getByText("Mark scheme").count()) > 0);
check("examiner report revealed", (await page.getByText("Examiner report").count()) > 0);

// ---- dashboard ----
await page.goto(`${BASE}/dashboard`, { waitUntil: "networkidle" });
await page.waitForSelector("text=Questions attempted");
const accuracies = await page.locator("a[href^='/topics/'] span.tabular-nums").allInnerTexts();
check(
  `topics ordered worst-first (${accuracies.join(", ")})`,
  accuracies.length > 1 && parseInt(accuracies[0]) <= parseInt(accuracies[1]),
);

// ---- split viewer ----
await page.goto(`${BASE}/papers/physics-5054-2019-may-june-p22`, { waitUntil: "networkidle" });
check("viewer shows the question paper pane", (await page.getByText("Question Paper").count()) > 0);
await page.keyboard.press("e");
check(
  "E switches the second pane to the examiner report",
  (await page.getByRole("heading", { name: "Examiner Report" }).count()) > 0,
);

// ---- topic drill ----
// Untimed by design, and it must not share storage with a half-finished paper.
await page.goto(`${BASE}/topics/physics-5054/dynamics/practice`, { waitUntil: "networkidle" });
await page.waitForSelector("[role=radiogroup]");
check("drill opens with the topic's questions", (await page.getByText("Question 1 / 3").count()) > 0);
check(
  "drill shows no countdown",
  (await page.locator("span.tabular-nums").filter({ hasText: /^\d\d:\d\d$/ }).count()) === 0,
);
check(
  "drill submit reads as marking, not sitting a paper",
  (await page.getByRole("button", { name: "Mark answers" }).count()) > 0,
);
await page.keyboard.press("b"); // dynamics q6: correct
await page.getByRole("button", { name: "Mark answers" }).click();
await page.waitForSelector("text=time spent");
check("drill marks answers", (await page.getByText("Mark scheme").count()) > 0);

// The paper session must have survived the drill — separate keys, separate state.
await page.goto(`${BASE}/practice/physics-5054-2019-may-june-p12`, { waitUntil: "networkidle" });
await page.waitForSelector("body");
check(
  "the drill did not clobber the in-progress paper",
  (await page.getByText("time spent").count()) > 0 ||
    (await page.getByText("of 12 answered").count()) > 0,
);

// ---- review queue ----
await page.goto(`${BASE}/admin/review`, { waitUntil: "networkidle" });
await page.waitForSelector("text=Pending");
check("review queue lists pending questions", (await page.getByText("Pending").count()) > 0);
check(
  // The *topic* confidence. It used to assert "41% confident", the extraction
  // confidence — which varies in these fixtures and is 1.0 on every real
  // geometrically segmented question, so the check passed here while the live
  // queue showed "100% confident" on all 1238 rows and sorted by a constant.
  "queue is ordered worst-topic-confidence first",
  (await page.getByText("topic 55%").count()) > 0,
);
check(
  "the reason it was held back is shown",
  (await page.getByText("Label not found in the page text").count()) > 0,
);

const pendingBefore = parseInt(
  await page.locator("div").filter({ hasText: /^\d+Pending$/ }).first().innerText(),
);
await page.keyboard.press("a"); // approve
await page.waitForTimeout(200);
const pendingAfter = parseInt(
  await page.locator("div").filter({ hasText: /^\d+Pending$/ }).first().innerText(),
);
check(`approving removes an item from pending (${pendingBefore} -> ${pendingAfter})`, pendingAfter === pendingBefore - 1);

await page.keyboard.press("u"); // undo
await page.waitForTimeout(200);
const pendingUndone = parseInt(
  await page.locator("div").filter({ hasText: /^\d+Pending$/ }).first().innerText(),
);
check(`undo restores it (${pendingUndone})`, pendingUndone === pendingBefore);

// ---- theme and layout ----
await page.goto(BASE, { waitUntil: "networkidle" });
await page.getByLabel("Toggle colour theme").click();
const theme = await page.evaluate(() => document.documentElement.getAttribute("data-theme"));
check(`theme toggle sets an explicit theme (${theme})`, theme === "dark" || theme === "light");

await page.setViewportSize({ width: 375, height: 800 });
await page.goto(`${BASE}/subjects/physics-5054`, { waitUntil: "networkidle" });
const overflow = await page.evaluate(
  () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
);
check(`no sideways scroll at 375px (overflow ${overflow}px)`, overflow <= 0);

check(`no JS errors${jsErrors.length ? `:\n  ${jsErrors.join("\n  ")}` : ""}`, jsErrors.length === 0);

await browser.close();
console.log(failures === 0 ? "\nAll checks passed." : `\n${failures} check(s) failed.`);
process.exit(failures === 0 ? 0 : 1);
