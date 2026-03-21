/**
 * LLM Provider using @anthropic-ai/claude-agent-sdk query() function.
 *
 * Converts SDK stream events into the SSE format expected by
 * the claude-to-im bridge conversation engine.
 */

import fs from "node:fs";
import { execSync } from "node:child_process";
import { query } from "@anthropic-ai/claude-agent-sdk";
import type {
  SDKMessage,
  PermissionResult,
} from "@anthropic-ai/claude-agent-sdk";
import type {
  LLMProvider,
  StreamChatParams,
} from "claude-to-im/src/lib/bridge/host.js";
import type { PendingPermissions } from "./permission-gateway.js";

function sseEvent(type: string, data: unknown): string {
  const payload = typeof data === "string" ? data : JSON.stringify(data);
  return `data: ${JSON.stringify({ type, data: payload })}\n`;
}

// ── Environment isolation ──

/** Env vars always passed through to the CLI subprocess. */
const ENV_WHITELIST = new Set([
  "PATH",
  "HOME",
  "USER",
  "LOGNAME",
  "SHELL",
  "LANG",
  "LC_ALL",
  "LC_CTYPE",
  "TMPDIR",
  "TEMP",
  "TMP",
  "TERM",
  "COLORTERM",
  "NODE_PATH",
  "NODE_EXTRA_CA_CERTS",
  "XDG_CONFIG_HOME",
  "XDG_DATA_HOME",
  "XDG_CACHE_HOME",
  "SSH_AUTH_SOCK",
]);

/** Prefixes that are always stripped (even in inherit mode). */
const ENV_ALWAYS_STRIP = ["CLAUDECODE"];

/**
 * Build a clean env for the CLI subprocess.
 *
 * CTI_ENV_ISOLATION (default "strict"):
 *   "strict"  — only whitelist + CTI_* + ANTHROPIC_* from config.env
 *   "inherit" — full parent env minus CLAUDECODE
 */
export function buildSubprocessEnv(): Record<string, string> {
  const mode = process.env.CTI_ENV_ISOLATION || "strict";
  const out: Record<string, string> = {};

  if (mode === "inherit") {
    // Pass everything except always-stripped vars
    for (const [k, v] of Object.entries(process.env)) {
      if (v === undefined) continue;
      if (ENV_ALWAYS_STRIP.includes(k)) continue;
      out[k] = v;
    }
  } else {
    // Strict: whitelist only
    for (const [k, v] of Object.entries(process.env)) {
      if (v === undefined) continue;
      if (ENV_WHITELIST.has(k)) {
        out[k] = v;
        continue;
      }
      // Pass through CTI_* so skill config is available
      if (k.startsWith("CTI_")) {
        out[k] = v;
        continue;
      }
    }
    // ANTHROPIC_* should come from config.env, not parent process.
    // Only pass them if CTI_ANTHROPIC_PASSTHROUGH is explicitly set.
    if (process.env.CTI_ANTHROPIC_PASSTHROUGH === "true") {
      for (const [k, v] of Object.entries(process.env)) {
        if (v !== undefined && k.startsWith("ANTHROPIC_")) out[k] = v;
      }
    }
  }

  return out;
}

// ── Claude CLI path resolution ──

function isExecutable(p: string): boolean {
  try {
    fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/**
 * Resolve the path to the `claude` CLI executable.
 * Priority: CTI_CLAUDE_CODE_EXECUTABLE env → command -v claude → common install paths.
 */
export function resolveClaudeCliPath(): string | undefined {
  // 1. Explicit env var
  const fromEnv = process.env.CTI_CLAUDE_CODE_EXECUTABLE;
  if (fromEnv && isExecutable(fromEnv)) return fromEnv;

  // 2. command -v claude (respects PATH)
  try {
    const resolved = execSync("command -v claude", {
      encoding: "utf-8",
      timeout: 3000,
    }).trim();
    if (resolved && isExecutable(resolved)) return resolved;
  } catch {
    // not found in PATH
  }

  // 3. Common install locations
  const candidates = [
    "/usr/local/bin/claude",
    "/opt/homebrew/bin/claude",
    `${process.env.HOME}/.npm-global/bin/claude`,
    `${process.env.HOME}/.local/bin/claude`,
  ];
  for (const p of candidates) {
    if (isExecutable(p)) return p;
  }

  return undefined;
}

export class SDKLLMProvider implements LLMProvider {
  private cliPath: string | undefined;

  constructor(
    private pendingPerms: PendingPermissions,
    cliPath?: string,
  ) {
    this.cliPath = cliPath;
  }

  streamChat(params: StreamChatParams): ReadableStream<string> {
    const pendingPerms = this.pendingPerms;
    const cliPath = this.cliPath;

    return new ReadableStream({
      start(controller) {
        (async () => {
          try {
            const cleanEnv = buildSubprocessEnv();

            const permissionMode =
              (params.permissionMode as
                | "default"
                | "acceptEdits"
                | "plan"
                | "bypassPermissions") || undefined;
            const queryOptions: Record<string, unknown> = {
              cwd: params.workingDirectory,
              model: params.model,
              resume: params.sdkSessionId || undefined,
              abortController: params.abortController,
              permissionMode,
              includePartialMessages: true,
              env: cleanEnv,
              settingSources: ["user", "project"],
              allowedTools: ["Skill", "Read", "Write", "Bash"],
              canUseTool: async (
                toolName: string,
                input: Record<string, unknown>,
                opts,
              ): Promise<PermissionResult> => {
                // In bypassPermissions mode, allow all tool calls without user confirmation
                if (permissionMode === "bypassPermissions") {
                  return { behavior: "allow" as const };
                }

                // Emit permission_request SSE event for the bridge
                controller.enqueue(
                  sseEvent("permission_request", {
                    permissionRequestId: opts.toolUseID,
                    toolName,
                    toolInput: input,
                    suggestions: opts.suggestions || [],
                  }),
                );

                // Block until IM user responds
                const result = await pendingPerms.waitFor(opts.toolUseID);

                if (result.behavior === "allow") {
                  return { behavior: "allow" as const };
                }
                return {
                  behavior: "deny" as const,
                  message: result.message || "Denied by user",
                };
              },
            };
            if (cliPath) {
              queryOptions.pathToClaudeCodeExecutable = cliPath;
            }

            // Helper to run query with given options
            const runQuery = async (opts: Record<string, unknown>) => {
              const q = query({
                prompt: params.prompt,
                options: opts as Parameters<typeof query>[0]["options"],
              });
              for await (const msg of q) {
                handleMessage(msg, controller);
              }
            };

            try {
              await runQuery(queryOptions);
            } catch (firstErr) {
              const errMsg =
                firstErr instanceof Error ? firstErr.message : String(firstErr);
              // If resume failed (stale session), retry without resume
              if (
                queryOptions.resume &&
                (errMsg.includes("No conversation found") ||
                  errMsg.includes("exited with code 1"))
              ) {
                console.warn(
                  "[llm-provider] Resume failed, retrying without session:",
                  errMsg,
                );
                controller.enqueue(
                  sseEvent("text", "⚠️ Session expired, starting fresh...\n\n"),
                );
                delete queryOptions.resume;
                await runQuery(queryOptions);
              } else {
                throw firstErr;
              }
            }

            controller.close();
          } catch (err) {
            const message = err instanceof Error ? err.message : String(err);
            console.error(
              "[llm-provider] SDK query error:",
              err instanceof Error ? err.stack || err.message : err,
            );
            controller.enqueue(sseEvent("error", message));
            controller.close();
          }
        })();
      },
    });
  }
}

function handleMessage(
  msg: SDKMessage,
  controller: ReadableStreamDefaultController<string>,
): void {
  switch (msg.type) {
    case "stream_event": {
      const event = msg.event;
      if (
        event.type === "content_block_delta" &&
        event.delta.type === "text_delta"
      ) {
        // Emit delta text — the bridge accumulates on its side
        controller.enqueue(sseEvent("text", event.delta.text));
      }
      if (
        event.type === "content_block_start" &&
        event.content_block.type === "tool_use"
      ) {
        controller.enqueue(
          sseEvent("tool_use", {
            id: event.content_block.id,
            name: event.content_block.name,
            input: {},
          }),
        );
      }
      break;
    }

    case "assistant": {
      // Full assistant message — extract content blocks
      // Text deltas are already handled by stream_event; this handles
      // any tool_use blocks not caught by partial streaming.
      if (msg.message?.content) {
        for (const block of msg.message.content) {
          if (block.type === "tool_use") {
            controller.enqueue(
              sseEvent("tool_use", {
                id: block.id,
                name: block.name,
                input: block.input,
              }),
            );
          }
        }
      }
      break;
    }

    case "user": {
      // User messages contain tool_result blocks from completed tool calls
      const content = msg.message?.content;
      if (Array.isArray(content)) {
        for (const block of content) {
          if (
            typeof block === "object" &&
            block !== null &&
            "type" in block &&
            block.type === "tool_result"
          ) {
            const rb = block as {
              tool_use_id: string;
              content?: unknown;
              is_error?: boolean;
            };
            const text =
              typeof rb.content === "string"
                ? rb.content
                : JSON.stringify(rb.content ?? "");
            controller.enqueue(
              sseEvent("tool_result", {
                tool_use_id: rb.tool_use_id,
                content: text,
                is_error: rb.is_error || false,
              }),
            );
          }
        }
      }
      break;
    }

    case "result": {
      if (msg.subtype === "success") {
        controller.enqueue(
          sseEvent("result", {
            session_id: msg.session_id,
            is_error: msg.is_error,
            usage: {
              input_tokens: msg.usage.input_tokens,
              output_tokens: msg.usage.output_tokens,
              cache_read_input_tokens: msg.usage.cache_read_input_tokens ?? 0,
              cache_creation_input_tokens:
                msg.usage.cache_creation_input_tokens ?? 0,
              cost_usd: msg.total_cost_usd,
            },
          }),
        );
      } else {
        // Error result
        const errors =
          "errors" in msg && Array.isArray(msg.errors)
            ? msg.errors.join("; ")
            : "Unknown error";
        controller.enqueue(sseEvent("error", errors));
      }
      break;
    }

    case "system": {
      if (msg.subtype === "init") {
        controller.enqueue(
          sseEvent("status", {
            session_id: msg.session_id,
            model: msg.model,
          }),
        );
      }
      break;
    }

    default:
      // Ignore other message types (auth_status, task_notification, etc.)
      break;
  }
}
