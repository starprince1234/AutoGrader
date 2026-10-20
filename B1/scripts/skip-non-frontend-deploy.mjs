import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const previous = process.env.VERCEL_GIT_PREVIOUS_SHA;
const current = process.env.VERCEL_GIT_COMMIT_SHA;

// Build if Vercel cannot give us an unambiguous revision range. In particular,
// equal revisions must not hide a push that changes the production bundle.
const fullSha = /^[a-f0-9]{40}$/i;
if (!fullSha.test(previous ?? "") || !fullSha.test(current ?? "") || previous === current) {
  process.exit(1);
}

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const root = spawnSync("git", ["rev-parse", "--show-toplevel"], {
  cwd: projectRoot,
  encoding: "utf8",
  stdio: ["ignore", "pipe", "ignore"],
});
if (root.status !== 0) process.exit(1);
const repositoryRoot = root.stdout.trim();

// Resolve paths from the actual Git root. Vercel's Root Directory may itself be
// the checkout root or a subdirectory in a monorepo.
const relativeProjectRoot = path.relative(repositoryRoot, projectRoot);
const contractsRoot = path.resolve(projectRoot, "../MAIN/contracts");
const relativeContractsRoot = path.relative(repositoryRoot, contractsRoot);
if (
  relativeProjectRoot.startsWith(`..${path.sep}`) ||
  relativeProjectRoot === ".." ||
  relativeContractsRoot.startsWith(`..${path.sep}`) ||
  relativeContractsRoot === ".."
) {
  process.exit(1);
}
const gitPath = (base, file = "") =>
  path.posix.join(base.split(path.sep).join("/"), file);
const projectPrefix = relativeProjectRoot === "" ? "" : gitPath(relativeProjectRoot);

// Shared contracts must trigger the SPA because its client types are generated from them.
const changed = spawnSync(
  "git",
  [
    "diff",
    "--quiet",
    previous,
    current,
    "--",
    gitPath(projectPrefix, "src"),
    gitPath(projectPrefix, "scripts"),
    gitPath(projectPrefix, "public"),
    gitPath(projectPrefix, "index.html"),
    gitPath(projectPrefix, "contracts.lock"),
    gitPath(projectPrefix, "package.json"),
    gitPath(projectPrefix, "pnpm-lock.yaml"),
    gitPath(projectPrefix, "tsconfig.json"),
    gitPath(projectPrefix, "tsconfig.app.json"),
    gitPath(projectPrefix, "tsconfig.node.json"),
    gitPath(projectPrefix, "vite.config.ts"),
    gitPath(projectPrefix, "vercel.json"),
    gitPath(projectPrefix, ".env.production"),
    gitPath(path.relative(repositoryRoot, contractsRoot)),
  ],
  { cwd: repositoryRoot, stdio: "ignore" },
);

// Vercel skips when this command exits 0. Any B1 change or git error builds instead.
process.exit(changed.status === 0 ? 0 : 1);
