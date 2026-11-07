import test from "node:test";
import assert from "node:assert/strict";
import { imapEnvEntries, intakeEnvEntries } from "./mail-env.mjs";

test("IMAP challenges use the authenticated directory without a fixed assignment", () => {
  const env = {
    IMAP_HOST: "imap.example.test", IMAP_USER: "inbox@example.test", IMAP_PASS: "test",
    IMAP_TLS_MODE: "tls", B2_B3_SERVICE_TOKEN: "test-token", B2_IDENTITY_URL: "",
  };
  const identity = intakeEnvEntries(env);
  const imap = imapEnvEntries({ ...env, ...identity });
  assert.equal(identity.B2_IDENTITY_URL, "http://127.0.0.1:18000");
  assert.equal(identity.B2_IDENTITY_AUTHORIZATION, "Bearer test-token");
  assert.equal(identity.B2_IMAP_TARGET_JSON, undefined);
  assert.equal(imap.B2_IMAP_HOST, "imap.example.test");
  assert.equal(imap.B2_IMAP_USE_SSL, "true");
});

test("IMAP without a service token is rejected", () => {
  assert.throws(() => intakeEnvEntries({ IMAP_HOST: "imap.example.test" }), /service token/);
});
