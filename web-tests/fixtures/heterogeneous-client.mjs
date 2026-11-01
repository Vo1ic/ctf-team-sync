import { BridgeClient } from "../../examples/jsonl-client.mjs";
import { randomUUID } from "node:crypto";

const bridge = new BridgeClient({
  command: process.env.TEAM_SYNC_PYTHON,
  args: ["-m", "team_sync.agent", "--state", process.env.TEST_STATE, "bridge"],
});
try {
  const hello = await bridge.call("protocol/hello", {
    protocol_versions: [1],
    client: {
      name: "custom-sdk",
      provider: "my-provider",
      model: "unlisted-local-model",
    },
  });
  await bridge.call("events/sync", { timeout: 0 });
  const batch = await bridge.call("context/read", { consumer: "node-session" });
  if (!JSON.stringify(batch).includes("Context from MCP"))
    throw new Error("Missing context");
  await bridge.call("context/ack", {
    consumer: "node-session",
    delivery_id: batch.delivery_id,
  });
  const action = (operation, args, resource_id = null) =>
    bridge.call("commands/execute", {
      operation,
      arguments: args,
      resource_id,
      idempotency_key: randomUUID(),
    });
  const task = await action(
    "task.claim",
    { expected_version: 1 },
    process.env.TEST_TASK,
  );
  const finding = await action("finding.create", {
    title: "Node result",
    body: "Result from a runtime without MCP",
    evidence: "Cross-client acceptance",
  });
  await action("message.send", {
    recipient_id: process.env.TEST_RECIPIENT,
    body: "Context from Node JSONL",
  });
  await action(
    "task.complete",
    { expected_version: task.version, result: finding.id },
    task.id,
  );
  console.log(
    JSON.stringify({ finding_id: finding.id, hello_model: hello.client.model }),
  );
} finally {
  bridge.close();
}
