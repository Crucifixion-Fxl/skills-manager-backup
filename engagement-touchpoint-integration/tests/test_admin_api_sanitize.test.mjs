/**
 * Regression tests for credential redaction in admin_api.mjs output.
 *
 * The CLI writes the Admin API response body to stdout as JSON. Documents
 * (references/admin-api-auth.md) forbid printing cookies, OAuth codes, and
 * tokens anywhere. These tests pin sanitizeValue()/sanitizeText(): every
 * credential key and embedded credential-looking string must leave the
 * helper as [REDACTED], while business fields (voucher/promo `code`,
 * `token_type`, `cmsSlug`, ...) stay readable.
 *
 * Run: node --test 'skills/engagement-touchpoint-integration/tests/*.test.mjs'
 * (Node 22+ glob form; bare directory args fail on Node 23+ with MODULE_NOT_FOUND.)
 */

import test from "node:test";
import assert from "node:assert/strict";
import {
  apiPathAllowed,
  renderWritePreview,
  sanitizeText,
  sanitizeValue,
} from "../scripts/admin_api.mjs";

const SAMPLE_TOKEN = "eyJhbGciOiJIUzI1NiJ9.abc.def-123_~";

test("redacts every credential key at any nesting depth", () => {
  const body = {
    data: {
      access_token: SAMPLE_TOKEN,
      refresh_token: SAMPLE_TOKEN,
      id_token: SAMPLE_TOKEN,
      token: SAMPLE_TOKEN,
      secret: "hunter2",
      password: "hunter2",
      api_key: "sk-test-123",
      nested: [
        { authorization: `Bearer ${SAMPLE_TOKEN}` },
        { cookie: "admin_session=xyz; Path=/" },
      ],
    },
  };
  const out = JSON.stringify(sanitizeValue(body));
  assert.equal(out.includes(SAMPLE_TOKEN), false);
  assert.equal(out.includes("hunter2"), false);
  assert.equal(out.includes("sk-test-123"), false);
  assert.equal(out.includes("admin_session=xyz"), false);
});

test("credential keys are matched case-insensitively and in header case", () => {
  const body = {
    Authorization: `Bearer ${SAMPLE_TOKEN}`,
    "Set-Cookie": "admin_session=xyz",
    ADMIN_SESSION: "xyz",
  };
  const out = JSON.stringify(sanitizeValue(body));
  assert.equal(out.includes(SAMPLE_TOKEN), false);
  assert.equal(out.includes("admin_session=xyz"), false);
  assert.equal(out.includes("xyz"), false);
});

test("redacts the whole value when a credential key holds an object", () => {
  const out = sanitizeValue({ token: { value: SAMPLE_TOKEN, nested: true } });
  assert.deepEqual(out, { token: "[REDACTED]" });
});

test("preserves business fields operators and agents must read", () => {
  const body = {
    code: "PROMO_1007",
    voucherCode: "legacy_fl_voucher_1001",
    cmsSlug: "playback-ad-fl-3d",
    token_type: "Bearer",
    expires_in: 28800,
    items: [{ variantKey: "r0_experience" }],
  };
  assert.deepEqual(sanitizeValue(body), body);
});

test("sanitizes credential-looking substrings inside string leaves", () => {
  const out = sanitizeValue({
    detail: `upstream said Bearer ${SAMPLE_TOKEN} and admin_session=Leaked; retry`,
    raw: "cookie: admin_session=Leaked",
  });
  assert.equal(out.detail.includes(SAMPLE_TOKEN), false);
  assert.equal(out.detail.includes("Leaked"), false);
  assert.equal(out.raw.includes("Leaked"), false);
  assert.equal(out.detail.includes("retry"), true);
});

test("passes through non-JSON bodies and primitives unchanged in structure", () => {
  assert.equal(sanitizeValue(42), 42);
  assert.equal(sanitizeValue(true), true);
  assert.equal(sanitizeValue(null), null);
  const text = `plain text with Bearer ${SAMPLE_TOKEN}`;
  assert.equal(sanitizeValue(text).includes(SAMPLE_TOKEN), false);
  assert.ok(sanitizeValue(text).startsWith("plain text with Bearer [REDACTED]"));
});

test("sanitizeText keeps non-credential text intact", () => {
  assert.equal(sanitizeText("touchpoint vh_home_banner created"), "touchpoint vh_home_banner created");
});

test("stdout envelope built from sanitizeValue leaks no credentials", () => {
  // Mirrors the exact envelope main() writes to process.stdout.
  const responseBody = {
    ok: true,
    result: { user: { email: "hmei@a4x.io" }, access_token: SAMPLE_TOKEN },
  };
  const envelope = JSON.stringify({
    ok: true,
    status: 200,
    method: "GET",
    path: "/api/touchpoints",
    operator: "hmei@a4x.io",
    body: sanitizeValue(responseBody),
  });
  assert.equal(envelope.includes(SAMPLE_TOKEN), false);
  assert.equal(envelope.includes("access_token\":\"[REDACTED]\""), true);
});

test("write preview stderr output redacts credential values, keeps business fields", () => {
  // renderWritePreview is the exact function main() passes to
  // process.stderr.write before a confirmed write.
  const payload = {
    touchpoint: { cmsSlug: "playback-ad-fl-3d", code: "PROMO_1007" },
    authorization: `Bearer ${SAMPLE_TOKEN}`,
    admin_session: "xyz-secret-session",
    note: "copied cookie: admin_session=Leaked",
  };
  const preview = renderWritePreview(
    "prod",
    "POST",
    "/api/touchpoints",
    payload,
  );
  assert.ok(preview.startsWith("WRITE PREVIEW: prod POST /api/touchpoints\n"));
  assert.equal(preview.includes(SAMPLE_TOKEN), false);
  assert.equal(preview.includes("xyz-secret-session"), false);
  assert.equal(preview.includes("Leaked"), false);
  assert.equal(preview.includes("PROMO_1007"), true);
  assert.equal(preview.includes("playback-ad-fl-3d"), true);
  assert.equal(preview.includes("[REDACTED]"), true);
});

test("write preview renders a redacted null body for bodyless writes", () => {
  const preview = renderWritePreview(
    "prod",
    "DELETE",
    "/api/touchpoints/123",
    undefined,
  );
  assert.ok(
    preview.startsWith("WRITE PREVIEW: prod DELETE /api/touchpoints/123\n"),
  );
  assert.ok(preview.endsWith("null\n"));
});

test("string leaves redact every credential key in key=value form", () => {
  const pairs = [
    "refresh_token=rt-xyz",
    "id_token=id-xyz",
    "token=op-xyz",
    "secret=sh-xyz",
    "password=pw-xyz",
    "api_key=ak-xyz",
    "set-cookie=sc-xyz",
  ];
  for (const pair of pairs) {
    const out = sanitizeValue(`upstream echoed ${pair} and failed`);
    assert.equal(out.includes("xyz"), false, pair);
    assert.equal(out.includes("[REDACTED]"), true, pair);
    assert.equal(out.includes("upstream echoed"), true, pair);
    assert.equal(out.includes("and failed"), true, pair);
  }
});

test("non-JSON plain-text response bodies lose embedded credentials", () => {
  // fetchWithTimeout falls back to the raw response text when JSON.parse
  // fails; sanitizeValue routes strings through sanitizeText.
  const text =
    'gateway log: refresh_token=rt-abc&trace=1 next line "access_token":"eyJabc" done';
  const out = sanitizeValue(text);
  assert.equal(out.includes("rt-abc"), false);
  assert.equal(out.includes("eyJabc"), false);
  assert.equal(out.includes("trace=1"), true);
  assert.equal(out.includes("gateway log:"), true);
});

test("JSON embedded in text keeps non-credential neighbors readable", () => {
  const out = sanitizeValue('{"refresh_token":"eyJz","user":1}');
  assert.equal(out.includes("eyJz"), false);
  assert.equal(out.includes("user"), true);
  const business = sanitizeValue("code=PROMO_1007 token_type: Bearer");
  assert.equal(business, "code=PROMO_1007 token_type: Bearer");
});

test("authorization prose form redacts the rest of the line", () => {
  const out = sanitizeValue("authorization: Bearer eyJq.zw trailing detail");
  assert.equal(out.includes("eyJq"), false);
  assert.equal(out.includes("trailing"), false);
  assert.ok(out.startsWith("authorization: [REDACTED]"));
});

test("Bearer redaction covers the full token charset including + / =", () => {
  // exchangeGrant accepts any non-empty access_token string, so opaque
  // tokens may contain base64 characters beyond [A-Za-z0-9._~-].
  const token = "YEL3x+/=9sLm.~_z-";
  const out = sanitizeValue(`upstream echoed Bearer ${token} then failed`);
  assert.equal(out.includes(token), false);
  assert.equal(out.includes("YEL3x"), false);
  assert.ok(out.includes("Bearer [REDACTED]"));
  assert.ok(out.endsWith("then failed"));
});

test("API allowlist permits touchpoint CRUD/lookup and publish-order creation", () => {
  assert.equal(apiPathAllowed("GET", "/api/touchpoints"), true);
  assert.equal(
    apiPathAllowed("GET", "/api/touchpoints/home_page_device_badge"),
    true,
  );
  assert.equal(
    apiPathAllowed(
      "POST",
      "/api/touchpoints/vh_home_banner/experience-variants",
    ),
    true,
  );
  assert.equal(
    apiPathAllowed("DELETE", "/api/touchpoints/vh_home_banner/fatigue-variants/9"),
    true,
  );
  assert.equal(apiPathAllowed("POST", "/api/publish-orders"), true);
  assert.equal(apiPathAllowed("GET", "/api/publish-orders/42"), true);
});

test("API allowlist hard-rejects production-promotion paths regardless of flags", () => {
  const denied = [
    ["POST", "/api/publish-orders/42/approve-prod"],
    ["POST", "/api/publish-orders/42/refresh-approval"],
    ["POST", "/api/publish-orders/42/rollback"],
    ["POST", "/api/publish-orders/42"],
    ["PATCH", "/api/publish-orders/42"],
    ["DELETE", "/api/publish-orders/42"],
    ["POST", "/api/publish-orders/approve"],
    ["POST", "/api/audit/export"],
    ["GET", "/api/admin/session"],
    ["POST", "/api/auth/cli/exchange"],
  ];
  for (const [method, pathname] of denied) {
    assert.equal(
      apiPathAllowed(method, pathname),
      false,
      `${method} ${pathname} must not be callable with the CLI credential`,
    );
  }
});

test("API allowlist rejects near-miss paths under other prefixes", () => {
  // Path-prefix attacks must not slip through a startsWith("/api/") check.
  assert.equal(apiPathAllowed("POST", "/api/touchpoints-evil"), false);
  assert.equal(apiPathAllowed("POST", "/api/publish-orders-suite"), false);
  assert.equal(apiPathAllowed("GET", "/api/users"), false);
});
