// Standalone HTTP-only client. Runs in Node Alpine without Python or MCP.
import { createHash, randomUUID } from "node:crypto";

const root = `${process.env.TEAM_SYNC_URL}/api/v1/projects/${process.env.TEAM_SYNC_PROJECT_ID}`;
async function request(path, { method = "GET", body } = {}) {
  const response = await fetch(root + path, {
    method,
    headers: {
      Authorization: `Bearer ${process.env.TEAM_SYNC_TOKEN}`,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  });
  if (!response.ok)
    throw new Error(`HTTP acceptance failed: ${response.status}`);
  return path.endsWith("/content")
    ? new Uint8Array(await response.arrayBuffer())
    : response.json();
}
const command = (operation, arguments_, resource_id = null) =>
  request("/commands", {
    method: "POST",
    body: {
      operation,
      arguments: arguments_,
      resource_id,
      idempotency_key: randomUUID(),
    },
  });

const artifact = (await request("/collections/artifacts")).items.find(
  (item) => item.id === process.env.ACCEPTANCE_ARTIFACT,
);
if (!artifact) throw new Error("Missing shared artifact");
const bytes = await request(`/artifacts/${artifact.id}/content`);
if (createHash("sha256").update(bytes).digest("hex") !== artifact.sha256)
  throw new Error("Artifact integrity mismatch");
const task = await command(
  "task.claim",
  { expected_version: 1 },
  process.env.ACCEPTANCE_TASK,
);
const finding = await command("finding.create", {
  title: "HTTP runtime verified the shared artifact",
  kind: "result",
  body: "A standalone Node runtime downloaded the file and verified its SHA-256.",
  evidence: `artifact:${artifact.id}`,
  task_id: task.id,
});
await command(
  "task.complete",
  { expected_version: task.version, result: finding.id },
  task.id,
);
const messages = (await request("/inbox")).items;
const incoming = messages.find(
  (item) => item.id === process.env.ACCEPTANCE_MESSAGE,
);
if (!incoming) throw new Error("Missing addressed message");
await command("message.ack", { stage: "processed" }, incoming.id);
await command("message.send", {
  recipient_id: process.env.ACCEPTANCE_PARTNER,
  body: "HTTP-only runtime completed verification and published its evidence.",
});
console.log(
  JSON.stringify({
    runtime: process.version,
    platform: process.platform,
    artifact_integrity: "passed",
    task_completed: "passed",
  }),
);
