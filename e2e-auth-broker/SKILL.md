---
name: e2e-auth-broker
description: Use when a project needs L3/L4 E2E tests, smoke tests, or local test clients to call staging APIs behind APISIX using real user authentication from user-center. Triggers include golf-app/golf-backend E2E, staging-us backend tests that need Authorization Bearer tokens, test persona setup, avoiding APISIX/auth bypass, or wiring app/web/API tests to the user-center E2E session broker.
---

# E2E Auth Broker

## Description

Use this skill to connect automated test clients to staging APIs through the real user-center authentication path. The target pattern is: test runner requests a short-lived real user token from user-center's E2E session broker, then calls the protected service through APISIX with `Authorization: Bearer <accessToken>`.

This skill is for ToC services that sit behind APISIX and rely on user-center tokens, such as golf-backend, naturehood, subscription-center, or other app-facing services.

## Rules

- Keep L3 black-box: do not bypass APISIX, do not direct-call backend pods, and do not spoof `X-User-ID` or `X-Tenant-ID`.
- Never put user passwords, user access tokens, refresh tokens, Zitadel PATs, or broker tokens in source code.
- Store the broker token in CI/Vault/local secret files only.
- Use only configured test personas. Do not mint tokens for real customer accounts.
- Treat the broker response as secret. Do not log `accessToken` or `refreshToken`.
- Use this only for local/dev/test/ci/staging. Production broker enablement is a security bug.
- Cache returned tokens only in memory for the current test run. If `expiresIn` is near expiry, request a fresh broker session.

## Workflow

1. Find the staging APISIX base URL used by the real client. Use that URL for all protected API calls.
2. Find the user-center broker URL that exposes `/internal/e2e/sessions`. It may be the same APISIX domain under a user-center path.
3. Add environment variables to the test runner:
   - `USER_CENTER_E2E_BROKER_URL`
   - `USER_CENTER_E2E_BROKER_TOKEN`
   - `E2E_TENANT_ID`
   - `E2E_PERSONA`
   - `E2E_BUNDLE`
4. Implement a small test helper that calls the broker once per worker or scenario and injects the returned `accessToken` into the app/web/API client.
5. Add a smoke assertion that a protected endpoint returns 200 through APISIX and that the response belongs to the requested test persona/tenant.

## Integration Contract

Call user-center:

```http
POST /internal/e2e/sessions
Authorization: Bearer <broker-token>
Content-Type: application/json
```

Request body:

```json
{
  "tenantId": "golf",
  "persona": "golf-smoke",
  "bundle": "com.addx.golf",
  "deviceId": "l3-worker-1",
  "deviceName": "golf-app L3",
  "deviceModel": "ios-simulator",
  "deviceOS": "iOS",
  "purpose": "L3 smoke"
}
```

Expected response:

```json
{
  "userId": 1500001001,
  "tenantId": "golf",
  "persona": "golf-smoke",
  "accessToken": "<real access token>",
  "refreshToken": "<real refresh token>",
  "expiresIn": 900
}
```

Then call the target API through APISIX:

```http
Authorization: Bearer <accessToken>
```

## App Client Patterns

For Flutter/RN/Appium:

- Prefer a test-only launch argument, deep link, or platform channel that injects the bearer token into the same auth storage abstraction used by normal login.
- Keep this injection behind a test build flag or test harness entrypoint.
- Do not add a production UI button or hidden gesture to mint tokens.

For web/Playwright:

- Use a setup project or fixture to fetch the broker token and populate the app's auth storage before tests.
- Keep generated auth state under ignored test output directories.

For backend/API smoke tests:

- Fetch the broker token in test setup.
- Attach `Authorization: Bearer <accessToken>` to every APISIX request.
- Do not call protected backends directly.

## user-center Config Reminder

The user-center side must define broker tokens and personas:

```yaml
E2EAuth:
  Enabled: true
  Environment: staging-us
  BrokerTokens:
    - Name: golf-app-ci
      Token: ${GOLF_APP_E2E_BROKER_TOKEN}
      AllowedTenants: [golf]
      AllowedPersonas: [golf-smoke]
      AllowedBundles: [com.addx.golf]
  Personas:
    - Key: golf-smoke
      TenantID: golf
      UserID: 1500001001
      Email: golf-smoke-e2e@example.invalid
```

If a consumer gets 401, check the broker token. If it gets 403, check environment, tenant/persona/bundle allowlists, and persona configuration.

## Examples

### Bad

```text
For L3 tests, call golf-backend pod IP directly and set X-User-ID: 1500001001.
```

This bypasses APISIX and user-center authentication, so it is not an L3 test of the deployed boundary.

```text
Put a staging user's refresh token in CI variables and reuse it for all tests.
```

This creates an unmanaged long-lived credential and makes revocation/audit unclear.

### Good

```text
Test setup calls user-center /internal/e2e/sessions for persona golf-smoke, then calls the staging APISIX URL with Authorization: Bearer <accessToken>.
```

This keeps the deployed auth boundary real while avoiding UI login automation and user passwords in tests.
