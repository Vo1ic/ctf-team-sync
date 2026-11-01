import assert from "node:assert/strict";
import { test } from "node:test";
import {
  accessLink,
  readAccessLink,
  connection,
} from "../src/team_sync/web/access.js";
const origin = "http://localhost:8000";
const project_id = "00000000-0000-4000-8000-000000000001";
const token = "synthetic-test-credential-only";
test("capability link round-trips a project connection entirely in the fragment", () => {
  const url = new URL(accessLink(origin, project_id, token));
  assert.equal(url.search, "");
  assert.equal(url.pathname, "/");
  assert.deepEqual(readAccessLink(url.hash, origin), { project_id, token });
  assert.equal(readAccessLink("#project=" + project_id, origin), null);
});
test("connection import refuses a different server and malformed credentials", () => {
  assert.throws(() =>
    connection({ url: "https://other.example", project_id, token }, origin),
  );
  assert.throws(() =>
    connection(
      { url: origin, project_id, token, schema_version: true },
      origin,
    ),
  );
  assert.throws(() =>
    connection({ url: origin, project_id, token: "bad\ncredential" }, origin),
  );
  assert.throws(() =>
    connection(
      { url: "http://user:password@localhost:8000", project_id, token },
      origin,
    ),
  );
  assert.deepEqual(
    connection({ url: origin, project_id, token, schema_version: 1 }, origin),
    { project_id, token },
  );
});
test("malformed join links fail without reflecting credential input", () => {
  const input = "#join=not+a/valid=encoded-secret";
  assert.throws(
    () => readAccessLink(input, origin),
    (error) => !error.message.includes("encoded-secret"),
  );
  assert.throws(() => readAccessLink("#join=" + "a".repeat(5000), origin));
});
test("browser cookie client omits bearer headers and uses same-origin requests", async () => {
  const { ApiClient } = await import("../src/team_sync/web/api.js");
  const api = new ApiClient(project_id, "", {
    fetcher: async (_url, options) => {
      assert.equal(options.credentials, "same-origin");
      assert.equal(options.headers.Authorization, undefined);
      assert.equal(options.headers["X-Team-Sync-Browser"], "1");
      return new Response(JSON.stringify({ ok: true }));
    },
  });
  assert.deepEqual(await api.command("task.create", { title: "A" }), {
    ok: true,
  });
  api.close();
});
