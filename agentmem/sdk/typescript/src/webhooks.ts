/**
 * Webhook receiver utilities for AgentMem.
 *
 * Every AgentMem webhook POST includes an `X-AgentMem-Signature` header of the
 * form `sha256=<hex>`.  Use `verifyWebhookSignature` to authenticate the
 * request before processing the payload.
 *
 * @example
 * // Express handler
 * import { verifyWebhookSignature, WebhookPayload } from "agentmem-sdk/webhooks";
 *
 * app.post("/webhooks/agentmem", express.raw({ type: "application/json" }), (req, res) => {
 *   const sig = req.headers["x-agentmem-signature"] as string;
 *   if (!verifyWebhookSignature(req.body, sig, process.env.AGENTMEM_WEBHOOK_SECRET!)) {
 *     return res.status(401).json({ error: "Invalid signature" });
 *   }
 *   const event: WebhookPayload = JSON.parse(req.body.toString());
 *   // handle event.event, event.data ...
 *   res.sendStatus(200);
 * });
 */

import { createHmac, timingSafeEqual } from "crypto";
import type { WebhookPayload, WebhookEventType } from "./types.js";

export type { WebhookPayload, WebhookEventType };

/**
 * Verify the HMAC-SHA256 signature on an incoming webhook.
 *
 * @param body    - Raw request body as a Buffer or UTF-8 string
 * @param header  - Value of the `X-AgentMem-Signature` header (e.g. `sha256=abc123…`)
 * @param secret  - The webhook secret returned when the endpoint was registered
 * @returns true if the signature is valid, false otherwise
 */
export function verifyWebhookSignature(
  body: Buffer | string,
  header: string,
  secret: string,
): boolean {
  if (!header.startsWith("sha256=")) return false;
  const expected = "sha256=" + createHmac("sha256", secret)
    .update(typeof body === "string" ? body : body)
    .digest("hex");
  try {
    return timingSafeEqual(Buffer.from(header), Buffer.from(expected));
  } catch {
    return false;
  }
}

/**
 * Parse and validate a raw webhook body into a typed payload.
 * Throws if the signature is invalid or the body is not valid JSON.
 *
 * @param body    - Raw request body as a Buffer or UTF-8 string
 * @param header  - Value of the `X-AgentMem-Signature` header
 * @param secret  - Webhook secret
 */
export function parseWebhookPayload<T = Record<string, unknown>>(
  body: Buffer | string,
  header: string,
  secret: string,
): WebhookPayload<T> {
  if (!verifyWebhookSignature(body, header, secret)) {
    throw new Error("AgentMem webhook signature verification failed");
  }
  const text = typeof body === "string" ? body : body.toString("utf8");
  return JSON.parse(text) as WebhookPayload<T>;
}
