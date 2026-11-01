import assert from "node:assert/strict";
import { test } from "node:test";
import { ApiClient, ApiError } from "../src/team_sync/web/api.js";

const response = (value) =>
  new Response(JSON.stringify(value), {
    headers: { "Content-Type": "application/json" },
  });

test("uncertain retries keep their command key; a new confirmed operation gets a new key", async () => {
  const bodies = [];
  const client = new ApiClient("project", "private-token", {
    fetcher: async (url, options) => {
      assert.equal(url, "/api/v1/projects/project/commands");
      assert.equal(options.headers.Authorization, "Bearer private-token");
      assert.equal(options.cache, "no-store");
      bodies.push(JSON.parse(options.body));
      if (bodies.length === 1)
        throw new TypeError("Response lost after commit");
      return response({ id: "task-1" });
    },
  });
  await assert.rejects(
    client.command("task.create", { title: "One", description: "Context" }),
    (error) => error.uncertain,
  );
  await client.command("task.create", { description: "Context", title: "One" });
  await client.command("task.create", { title: "One", description: "Context" });
  assert.equal(bodies[0].idempotency_key, bodies[1].idempotency_key);
  assert.notEqual(bodies[1].idempotency_key, bodies[2].idempotency_key);
  client.close();
  assert.equal(client.token, "");
});

test("HTTP 503 preserves retry identity; definitive validation failures clear it", async () => {
  const keys = [];
  const client = new ApiClient("project", "token", {
    fetcher: async (_url, options) => {
      keys.push(JSON.parse(options.body).idempotency_key);
      return new Response(JSON.stringify({ code: "version_conflict" }), {
        status: keys.length === 1 ? 503 : 409,
      });
    },
  });
  await assert.rejects(
    client.command("task.claim", { expected_version: 1 }, "task"),
    (error) => error.uncertain,
  );
  await assert.rejects(
    client.command("task.claim", { expected_version: 1 }, "task"),
    (error) => error.status === 409 && !error.uncertain,
  );
  await assert.rejects(
    client.command("task.claim", { expected_version: 1 }, "task"),
  );
  assert.equal(keys[0], keys[1]);
  assert.notEqual(keys[1], keys[2]);
});

test("collection pagination reads beyond the first 500 items", async () => {
  const pages = [];
  const client = new ApiClient("project", "token", {
    fetcher: async (url) => {
      const offset = Number(
        new URL(url, "http://localhost").searchParams.get("offset"),
      );
      pages.push(offset);
      return response({
        items: Array.from({ length: offset === 0 ? 500 : 3 }, (_, index) => ({
          id: offset + index,
        })),
      });
    },
  });
  assert.equal((await client.all("collections/tasks")).length, 503);
  assert.deepEqual(pages, [0, 500]);
});

test("artifact download refuses bytes with a different SHA-256", async () => {
  const client = new ApiClient("project", "token", {
    fetcher: async () => new Response("modified"),
  });
  await assert.rejects(
    client.download({ id: "artifact", sha256: "0".repeat(64) }),
    (error) => error instanceof ApiError && error.message.includes("сума"),
  );
});

test("closing a session aborts its outstanding read", async () => {
  const client = new ApiClient("project", "token", {
    fetcher: (_url, options) =>
      new Promise((_, reject) => {
        options.signal.addEventListener("abort", () =>
          reject(new DOMException("Aborted", "AbortError")),
        );
      }),
  });
  const waiting = client.get("events/wait");
  client.close();
  await assert.rejects(waiting, (error) => error.name === "AbortError");
});
