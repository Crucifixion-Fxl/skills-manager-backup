#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { spawnSync } = require("node:child_process");

function fail(message) {
  console.error(`ERROR: code-review result submission failed: ${message}`);
  process.exit(2);
}

if (process.argv.length !== 2) fail("arguments are not accepted");
const workspace = process.env.CODE_REVIEW_WORKSPACE || "";
const executionId = process.env.CODE_REVIEW_EXECUTION_ID || "";
if (!path.isAbsolute(workspace)) fail("CODE_REVIEW_WORKSPACE must be an absolute path");
if (!/^[A-Za-z0-9._:-]{1,200}$/.test(executionId)) fail("CODE_REVIEW_EXECUTION_ID is invalid");

let raw = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  raw += chunk;
  if (Buffer.byteLength(raw, "utf8") > 1024 * 1024) fail("payload exceeds 1 MiB");
});
process.stdin.on("end", () => {
  let payload;
  try {
    payload = JSON.parse(raw);
  } catch (error) {
    fail(`payload is not valid JSON (${error.message})`);
  }

  fs.mkdirSync(workspace, { recursive: true, mode: 0o700 });
  const canonical = `${JSON.stringify(payload, null, 2)}\n`;
  const digest = crypto.createHash("sha256").update(canonical).digest("hex");
  const resultPath = path.join(workspace, "review-result.json");
  const receiptPath = path.join(workspace, "review-result.receipt.json");

  if (fs.existsSync(resultPath) || fs.existsSync(receiptPath)) {
    try {
      const receipt = JSON.parse(fs.readFileSync(receiptPath, "utf8"));
      if (
        receipt.execution_id === executionId &&
        receipt.sha256 === digest &&
        fs.readFileSync(resultPath, "utf8") === canonical
      ) {
        console.log(`review_result_submitted=true execution_id=${executionId} sha256=${digest} idempotent=true`);
        return;
      }
    } catch (_error) {
      // A malformed prior receipt is replaced only after the new payload passes schema validation.
    }
  }

  const nonce = `${process.pid}-${crypto.randomBytes(8).toString("hex")}`;
  const candidatePath = path.join(workspace, `.review-result.${nonce}.json`);
  const previewPath = path.join(workspace, `.review-result.${nonce}.md`);
  const receiptCandidate = path.join(workspace, `.review-result.${nonce}.receipt.json`);
  let validationError = "";
  try {
    fs.writeFileSync(candidatePath, canonical, { encoding: "utf8", flag: "wx", mode: 0o600 });
    const validator = path.join(__dirname, "render_review_result.js");
    const validation = spawnSync(process.execPath, [validator, candidatePath, previewPath], {
      encoding: "utf8",
      env: {},
      timeout: 10_000,
    });
    if (validation.status !== 0) {
      validationError = (validation.stderr || "schema validation failed").trim();
    } else {
      fs.writeFileSync(
        receiptCandidate,
        `${JSON.stringify({ schema_version: "1.0", execution_id: executionId, sha256: digest }, null, 2)}\n`,
        { encoding: "utf8", flag: "wx", mode: 0o600 },
      );
      fs.renameSync(candidatePath, resultPath);
      fs.renameSync(receiptCandidate, receiptPath);
    }
  } finally {
    for (const temporary of [candidatePath, previewPath, receiptCandidate]) {
      try {
        fs.unlinkSync(temporary);
      } catch (error) {
        if (error.code !== "ENOENT") throw error;
      }
    }
  }
  if (validationError) fail(validationError);
  console.log(`review_result_submitted=true execution_id=${executionId} sha256=${digest} idempotent=false`);
});
