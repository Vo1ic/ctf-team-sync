// A reference transport for custom runtimes. No model or provider SDK is required.
import { spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import { createInterface } from "node:readline";
import { pathToFileURL } from "node:url";

export class BridgeClient {
  constructor({ command, args = [], env = process.env, timeout = 35000 }) {
    this.timeout = timeout;
    this.pending = new Map();
    this.closed = false;
    this.child = spawn(command, args, {
      env,
      stdio: ["pipe", "pipe", "ignore"],
      shell: false,
    });
    this.lines = createInterface({ input: this.child.stdout });
    this.lines.on("line", (line) => {
      let reply;
      try {
        reply = JSON.parse(line);
      } catch {
        this.fail(new Error("Invalid bridge frame"));
        return;
      }
      const waiting = this.pending.get(reply.id);
      if (!waiting || reply.jsonrpc !== "2.0") return;
      this.pending.delete(reply.id);
      clearTimeout(waiting.timer);
      if (reply.error) {
        const error = new Error(reply.error.message);
        error.code = reply.error.code;
        error.data = reply.error.data;
        waiting.reject(error);
      } else waiting.resolve(reply.result);
    });
    this.child.on("error", () =>
      this.fail(new Error("Bridge process could not start")),
    );
    this.child.on("exit", () => this.fail(new Error("Bridge process exited")));
    this.child.stdin.on("error", () =>
      this.fail(new Error("Bridge input closed")),
    );
  }

  fail(error) {
    this.closed = true;
    for (const waiting of this.pending.values()) {
      clearTimeout(waiting.timer);
      waiting.reject(error);
    }
    this.pending.clear();
  }

  call(method, params) {
    if (this.closed)
      return Promise.reject(new Error("Bridge client is closed"));
    const id = randomUUID();
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error("Bridge response timed out"));
      }, this.timeout);
      this.pending.set(id, { resolve, reject, timer });
      this.child.stdin.write(
        JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n",
      );
    });
  }

  close() {
    this.fail(new Error("Bridge client closed"));
    this.lines.close();
    this.child.stdin.end();
    this.child.kill();
  }
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(process.argv[1]).href
) {
  const client = new BridgeClient({
    command: process.env.TEAM_SYNC_PYTHON || "python3",
    args: ["-m", "team_sync.agent", "bridge"],
  });
  try {
    await client.call("protocol/hello", {
      protocol_versions: [1],
      client: {
        name: "custom-node-runtime",
        ...(process.env.TEAM_SYNC_MODEL
          ? { model: process.env.TEAM_SYNC_MODEL }
          : {}),
      },
    });
    await client.call("events/sync", { timeout: 0 });
    const consumer = process.env.TEAM_SYNC_CONSUMER || "custom-node-runtime";
    const batch = await client.call("context/read", { consumer });
    console.log(JSON.stringify(batch, null, 2));
    // A real runtime inserts the batch into its model's context before acknowledging it.
    if (process.argv.includes("--ack") && batch.delivery_id) {
      await client.call("context/ack", {
        consumer,
        delivery_id: batch.delivery_id,
      });
    }
  } catch {
    console.error(
      "Team Sync example failed; check configuration and bridge status.",
    );
    process.exitCode = 1;
  } finally {
    client.close();
  }
}
