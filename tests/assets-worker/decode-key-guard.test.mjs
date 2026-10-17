// Behavioural guard test for the reconciled Cloudflare Worker entry point.
//
// hundredways.updates._reconcile_assets_worker_index rewrites the fork's Worker
// (scripts/cloudflare/assets-worker/index.js) to decode one path level behind
// decodeChannelKey and read release artifacts from R2.  This test imports the
// reconciled file named on the command line and drives the exported fetch
// handler with mocked R2 + KV bindings, so the engine proves the bytes it
// produces actually serve "%7e" and reject traversal -- not merely that a
// marker string is present.  It mirrors the fork's own
// scripts/cloudflare/assets-worker/test-index.mjs, run here against the
// reconcile's output.
//
// Usage: node tests/assets-worker/decode-key-guard.test.mjs <path-to-index.js>
import assert from "node:assert/strict";
import { pathToFileURL } from "node:url";

const workerPath = process.argv[2];
if (!workerPath) {
  console.error("usage: node decode-key-guard.test.mjs <path-to-index.js>");
  process.exit(2);
}
const worker = (await import(pathToFileURL(workerPath).href)).default;

const DEB = "releases/termux/canary/pool/n/nastech-agent_0.21.5~canary.20261014T170553Z-1_aarch64.deb";
const CHANNEL = "releases/channels/main.json";
const BOTH = "releases/stable/release-candidates.json";

function fakeEnv({ kv = {}, r2 = {} } = {}) {
  const lookups = { kv: [], r2: [] };
  return {
    lookups,
    env: {
      NASTECH_ASSETS: {
        get: (key) => {
          lookups.kv.push(key);
          return key in kv ? kv[key] : null;
        },
      },
      R2: {
        get: async (key) => {
          lookups.r2.push(key);
          return key in r2 ? { body: r2[key], httpMetadata: {} } : null;
        },
      },
    },
  };
}

async function request(path, env) {
  const response = await worker.fetch(
    new Request(`https://assets.invalid${path}`),
    env,
    {},
  );
  return { status: response.status, body: await response.text() };
}

// %7e (apt) and literal ~ both resolve the R2 object stored under "~".
for (const path of ["/" + DEB, "/" + DEB.replace("~", "%7e")]) {
  const { env } = fakeEnv({ r2: { [DEB]: "payload" } });
  const got = await request(path, env);
  assert.equal(got.status, 200, `${path} must resolve the R2 object`);
  assert.equal(got.body, "payload", `${path} must return the R2 object body`);
}

// A KV-only channel record still resolves (R2 miss falls back to KV).
{
  const { env } = fakeEnv({ kv: { [CHANNEL]: "{}" } });
  assert.equal((await request("/" + CHANNEL, env)).status, 200);
}

// R2 wins when the same key is in both stores.
{
  const { env } = fakeEnv({ kv: { [BOTH]: "kv-copy" }, r2: { [BOTH]: "r2-copy" } });
  assert.equal((await request("/" + BOTH, env)).body, "r2-copy");
}

// Absent objects 404; hostile escapes 400 and never touch either store.
assert.equal((await request("/releases/channels/absent.json", fakeEnv().env)).status, 404);
for (const path of [
  "/releases/a%2fb",
  "/releases/a%5cb",
  "/releases/%252e%252e/x",
  "/releases/bad%",
  "/releases/a/%2e%2e%2fsecret",
]) {
  const { env, lookups } = fakeEnv({ kv: { "releases/x": "kv" }, r2: { "releases/a": "r2" } });
  const got = await request(path, env);
  assert.equal(got.status, 400, `${path} must be rejected with 400`);
  assert.deepEqual(lookups, { kv: [], r2: [] }, `${path} must not query either store`);
}

console.log("reconciled worker: all guard assertions passed");
