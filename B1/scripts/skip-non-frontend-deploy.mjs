import { spawnSync } from "node:child_process";

const previous = process.env.VERCEL_GIT_PREVIOUS_SHA;
const current = process.env.VERCEL_GIT_COMMIT_SHA;

// Build the first deployment and fail open when Vercel does not provide both revisions.
if (!previous || !current) process.exit(1);

const root = spawnSync("git", ["rev-parse", "--show-toplevel"], {
  encoding: "utf8",
  stdio: ["ignore", "pipe", "ignore"],
});
if (root.status !== 0) process.exit(1);

// The Vercel project root is B1. Resolve the monorepo root so shared contracts can
// trigger this project while documentation-only changes remain skipped.
const changed = spawnSync(
  "git",
  [
    "diff",
    "--quiet",
    previous,
    current,
    "--",
    "B1/src",
    "B1/scripts",
    "B1/public",
    "B1/index.html",
    "B1/contracts.lock",
    "B1/package.json",
    "B1/pnpm-lock.yaml",
    "B1/tsconfig.json",
    "B1/tsconfig.app.json",
    "B1/tsconfig.node.json",
    "B1/vite.config.ts",
    "B1/vercel.json",
    "B1/.env.production",
    "MAIN/contracts",
  ],
  { cwd: root.stdout.trim(), stdio: "ignore" },
);

// Vercel skips when this command exits 0. Any B1 change or git error builds instead.
process.exit(changed.status === 0 ? 0 : 1);
