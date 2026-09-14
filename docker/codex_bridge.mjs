import crypto from "node:crypto";
import http from "node:http";
import os from "node:os";
import fs from "node:fs/promises";
import path from "node:path";
import { spawn } from "node:child_process";

const token = process.env.LLM_WIKI_CODEX_BRIDGE_TOKEN || "";
const host = process.env.CODEX_BRIDGE_HOST || "0.0.0.0";
const port = Number(process.env.CODEX_BRIDGE_PORT || "8791");
const timeoutMs = Math.max(1000, Number(process.env.KOGWISTAR_MAINTENANCE_CODEX_TIMEOUT_SECONDS || "300") * 1000);
const maxBody = 256 * 1024;
const maxMessages = 64;
const maxMessageChars = 120000;
const maxSchemaChars = 64000;

if (!token) { console.error("LLM_WIKI_CODEX_BRIDGE_TOKEN must not be empty"); process.exit(78); }

function send(response, status, value) {
  const body = Buffer.from(JSON.stringify(value));
  response.writeHead(status, { "content-type": "application/json", "content-length": body.length });
  response.end(body);
}

function promptFor(messages, schema) {
  return "You are a bounded maintenance reasoning provider. Return only a JSON object matching the supplied response schema. Use only the supplied messages. Do not call tools, access files, access networks, use MCP, create jobs, or perform mutations. Do not include secrets or credentials.\n\nMESSAGES:\n" + JSON.stringify(messages) + "\n\nRESPONSE_SCHEMA:\n" + JSON.stringify(schema);
}

async function runCodex(payload) {
  const messages = payload.messages;
  const schema = payload.response_schema;
  if (!Array.isArray(messages) || messages.length < 1 || messages.length > maxMessages) throw new Error("messages must contain between 1 and 64 items");
  if (!schema || typeof schema !== "object" || Array.isArray(schema)) throw new Error("response_schema must be an object");
  if (JSON.stringify(schema).length > maxSchemaChars) throw new Error("response_schema exceeds the bridge limit");
  let total = 0;
  for (const message of messages) {
    if (!message || typeof message !== "object" || typeof message.content !== "string") throw new Error("each message must be an object with string content");
    total += message.content.length;
  }
  if (total > maxMessageChars) throw new Error("maintenance context exceeds the bridge limit");
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), "codex-bridge-"));
  const schemaPath = path.join(directory, "schema.json");
  const outputPath = path.join(directory, "answer.json");
  await fs.writeFile(schemaPath, JSON.stringify(schema), "utf8");
  const args = ["exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--config", "approval_policy=never", "--color", "never", "--json", "-o", outputPath, "--output-schema", schemaPath];
  const model = payload.model || process.env.KOGWISTAR_MAINTENANCE_CODEX_MODEL;
  if (model) args.push("--model", model);
  args.push("-");
  try {
    await new Promise((resolve, reject) => {
      const child = spawn(process.env.KOGWISTAR_CODEX_EXECUTABLE || "codex", args, { stdio: ["pipe", "pipe", "pipe"] });
      let output = ""; let error = "";
      const timer = setTimeout(() => { child.kill("SIGTERM"); reject(new Error("Codex request timed out")); }, timeoutMs);
      child.stdout.on("data", (chunk) => { output += chunk.toString(); });
      child.stderr.on("data", (chunk) => { error += chunk.toString(); });
      child.on("error", reject);
      child.on("close", (code) => { clearTimeout(timer); if (code !== 0) reject(new Error(`Codex CLI exited with status ${code}: ${error || output}`)); else resolve(output); });
      child.stdin.end(promptFor(messages, schema));
    });
    const raw = (await fs.readFile(outputPath, "utf8")).trim();
    const output = JSON.parse(raw);
    if (!output || typeof output !== "object" || Array.isArray(output)) throw new Error("Codex returned a non-object structured result");
    return { output, provider: "codex", model: model || "default" };
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
}

const server = http.createServer(async (request, response) => {
  if (request.method === "GET" && request.url === "/healthz") return send(response, 200, { ok: true, provider: "codex" });
  if (request.method !== "POST" || request.url !== "/v1/structured") return send(response, 404, { error: "not_found" });
  const expected = Buffer.from(`Bearer ${token}`);
  const supplied = Buffer.from(request.headers.authorization || "");
  if (supplied.length !== expected.length || !crypto.timingSafeEqual(supplied, expected)) return send(response, 401, { error: "unauthorized" });
  let size = 0; const chunks = [];
  request.on("data", (chunk) => { size += chunk.length; if (size <= maxBody) chunks.push(chunk); });
  request.on("end", async () => {
    try { if (size <= 0 || size > maxBody) throw new Error("request body exceeds the bridge limit"); send(response, 200, await runCodex(JSON.parse(Buffer.concat(chunks).toString("utf8")))); }
    catch (error) { const message = error instanceof Error ? error.message : String(error); send(response, message.includes("timed out") ? 504 : 400, { error: "invalid_request", message }); }
  });
});
server.listen(port, host, () => console.log(`Codex container bridge listening on ${host}:${port}`));
