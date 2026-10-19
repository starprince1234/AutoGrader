import { chmodSync, writeFileSync } from "node:fs";

const runtimeEnvPath = process.argv[2];
const setupEnvPath = process.argv[3];
const deploymentTag = process.argv[4];
if (
  !runtimeEnvPath ||
  !setupEnvPath ||
  !deploymentTag ||
  !/^[a-f0-9]{40}$/i.test(deploymentTag)
) {
  throw new Error(
    "usage: node write-runtime-env.mjs <runtime-env-path> <setup-env-path> <git-sha>",
  );
}

function required(name) {
  const value = process.env[name];
  if (!value || /[\r\n]/.test(value)) {
    throw new Error(name + " is required and must be a single line");
  }
  return value;
}

function secretHex(name) {
  const value = required(name);
  if (!/^[a-f0-9]{64}$/i.test(value)) {
    throw new Error(name + " must be a 32-byte hexadecimal secret");
  }
  return value;
}

const postgresPassword = secretHex("POSTGRES_PASSWORD");
const b2Password = secretHex("B2_DB_PASSWORD");
const b3AppPassword = secretHex("B3_APP_DB_PASSWORD");
const b3InternalPassword = secretHex("B3_INTERNAL_DB_PASSWORD");
const b4Password = secretHex("B4_DB_PASSWORD");
const serviceToken = secretHex("B2_B3_SERVICE_TOKEN");
const postgres = "127.0.0.1:25432/autograder";
const natsUrl = "nats://127.0.0.1:25222";
const apiUrl = "http://127.0.0.1:18000";

const runtimeEntries = {
  B4_DATABASE_URL: "postgresql+psycopg://autograder_b4:" + b4Password + "@" + postgres,
  B4_NATS_URL: natsUrl,
  B4_ENV: "production",
  B4_JWT_ISSUER: "autograder",
  B4_JWT_AUDIENCE: "autograder-api",
  B4_JWKS_URL: apiUrl + "/api/v1/auth/jwks",
  B4_MODULE_ROOT: "/opt/autograder/current",
  B2_DATABASE_URL: "postgresql+psycopg://autograder_b2:" + b2Password + "@" + postgres,
  B2_NATS_URL: natsUrl,
  B2_ENV: "production",
  B2_ENVIRONMENT_TAG: deploymentTag,
  B2_ARTIFACT_ROOT: "/var/lib/autograder/artifacts",
  B2_WORKSPACE_ROOT: "/var/lib/autograder/workspace",
  B2_JWKS_PATH: "/var/lib/autograder/jwks/jwks.json",
  B2_B3_BASE_URL: apiUrl,
  B2_B3_AUTHORIZATION: "Bearer " + serviceToken,
  B3_DATABASE_URL:
    "postgresql+psycopg://autograder_b3_app:" + b3AppPassword + "@" + postgres,
  B3_DATABASE_PROTECTED_URL:
    "postgresql+psycopg://autograder_b3_internal:" + b3InternalPassword + "@" + postgres,
  B3_NATS_URL: natsUrl,
  B3_JWT_ISSUER: "autograder",
  B3_JWT_AUDIENCE: "autograder-api",
  B3_SERVICE_TOKEN: serviceToken,
  JWT_PRIVATE_KEY_FILE: "/var/lib/autograder/jwt/jwt-ed25519.pem",
  B4_REFRESH_COOKIE_SECURE: "true",
  AUTOGRADER_CORS_ORIGINS: "https://www.91idc.me",
};

const setupEntries = {
  POSTGRES_PASSWORD: postgresPassword,
  B2_DB_PASSWORD: b2Password,
  B3_APP_DB_PASSWORD: b3AppPassword,
  B3_INTERNAL_DB_PASSWORD: b3InternalPassword,
  B4_DB_PASSWORD: b4Password,
  PLATFORM_DATABASE_URL:
    "postgresql+psycopg://autograder_platform:" + postgresPassword + "@" + postgres,
  B4_ADMIN_DATABASE_URL:
    "postgresql+psycopg://autograder_platform:" + postgresPassword + "@" + postgres,
  POSTGRES_WAIT_SECONDS: "120",
};

function writeEnvFile(path, values) {
  const content = Object.entries(values)
    .map(([name, value]) => {
      const escaped = value.replace(/\\/g, "\\\\").replace(/"/g, '\\"');
      return name + '=\"' + escaped + '\"';
    })
    .join("\n") + "\n";
  writeFileSync(path, content, { encoding: "utf8", mode: 0o600 });
  chmodSync(path, 0o600);
}

writeEnvFile(runtimeEnvPath, runtimeEntries);
writeEnvFile(setupEnvPath, setupEntries);
