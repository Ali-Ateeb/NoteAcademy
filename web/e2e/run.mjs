/**
 * Build the app in fixture mode, serve it, run the smoke test, tear it down.
 *
 *   npm run e2e
 *
 * The smoke test is written against the seed fixtures (src/lib/data/seed.ts):
 * a 12-question paper, a three-question "dynamics" drill, fixed review-queue
 * items. Pointed at a build that picked up .env.local it exercises live data
 * instead -- where nothing it expects is true, and where its review-queue
 * section would approve a real question. So this script is the only intended
 * way to run it: every Supabase and solver variable is blanked for the build
 * (an empty value in the environment beats .env.local, and NEXT_PUBLIC_ ones
 * are inlined at build time, so it has to be blank *here*, not at `next start`).
 *
 * It builds into its own directory (`.next-e2e`) so it can run while
 * `npm run dev` is open: both write to the build directory, and sharing one
 * kills the dev server with "Cannot find module './611.js'".
 */

import { spawn, spawnSync } from "node:child_process";
import { rmSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";

const PORT = process.env.E2E_PORT ?? "3210";
const DIST = ".next-e2e";
const root = path.resolve(import.meta.dirname, "..");
const next = createRequire(import.meta.url).resolve("next/dist/bin/next");

const env = {
  ...process.env,
  NEXT_DIST_DIR: DIST,
  NEXT_PUBLIC_SUPABASE_URL: "",
  NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY: "",
  SUPABASE_SERVICE_ROLE_KEY: "",
  GOOGLE_API_KEY: "",
};

function run(args, options = {}) {
  return spawnSync(process.execPath, args, { cwd: root, env, stdio: "inherit", ...options });
}

console.log("Building in fixture mode (no database)...");
const build = run([next, "build"]);
if (build.status !== 0) process.exit(build.status ?? 1);

const server = spawn(process.execPath, [next, "start", "-p", PORT], {
  cwd: root,
  env,
  stdio: "ignore",
});

function stopServer() {
  if (server.exitCode !== null) return;
  // A plain kill() leaves Next's child listening on the port on Windows.
  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(server.pid), "/T", "/F"], { stdio: "ignore" });
  } else {
    server.kill();
  }
}
process.on("exit", stopServer);
process.on("SIGINT", () => process.exit(130));

const base = `http://127.0.0.1:${PORT}`;
let up = false;
for (let attempt = 0; attempt < 60 && !up; attempt++) {
  try {
    up = (await fetch(base)).ok;
  } catch {
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
}
if (!up) {
  console.error(`The server did not come up on ${base}.`);
  process.exit(1);
}

const smoke = run([path.join(root, "e2e", "smoke.mjs")], {
  env: { ...env, BASE_URL: base },
});

stopServer();
rmSync(path.join(root, DIST), { recursive: true, force: true });
process.exit(smoke.status ?? 1);
