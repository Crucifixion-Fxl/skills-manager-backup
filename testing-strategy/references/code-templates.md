# 通用测试代码模板

> **读此文件的时机**：编写测试代码时查找对应技术栈的模板。

## 后端 Go 单元测试

```go
func TestServiceName_MethodName(t *testing.T) {
    // Arrange
    mockRepo := mocks.NewMockRepository(t)
    svc := NewService(mockRepo)

    // Act
    result, err := svc.Method(context.Background(), input)

    // Assert
    assert.NoError(t, err)
    assert.Equal(t, expected, result)
}
```

## 后端 Go 集成测试

```go
func TestIntegration_ServiceFlow(t *testing.T) {
    if testing.Short() { t.Skip("skipping integration test") }

    // Setup: Docker Compose / Testcontainers
    container := setupTestDB(t)
    defer container.Terminate(context.Background())

    // Test full flow
}
```

## 后端 Java/Spring Boot 集成测试

```java
@SpringBootTest
@ActiveProfiles({"test", "mock"})
class EvaluationIntegrationTest {
    @Autowired private MockMvc mockMvc;

    @Test
    void evaluate_noSubscription_returnsLockedTrue() throws Exception {
        // WireMock stub: Entitlement 返回空权益
        stubFor(get(urlPathMatching("/v1/features/.*"))
            .willReturn(okJson("{\"features\":[]}")));

        mockMvc.perform(get("/evaluate").param("userId", "10001"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$[0].locked").value(true));
    }
}
```

## Flutter Widget 测试

```dart
testWidgets('DeviceCard shows online status', (tester) async {
  await tester.pumpWidget(
    MaterialApp(home: DeviceCard(device: mockDevice)),
  );
  expect(find.text('Online'), findsOneWidget);
});
```

## Web (React) 组件测试

```typescript
import { render, screen } from '@testing-library/react';

test('DashboardCard renders metric value', () => {
  render(<DashboardCard value={42} label="Sensors" />);
  expect(screen.getByText('42')).toBeInTheDocument();
});
```

## 嵌入式 C++ GTest

```cpp
#include <gtest/gtest.h>
#include "<name>_cluster.h"

namespace iot::embedded::<name> {

class Mock<Name>HAL : public I<Name>HAL {
 public:
  void Set<Hardware>(type value) override { field_ = value; }
  type field_ = default_value;
};

TEST(<Name>ClusterTest, InitialState) { /* ... */ }
TEST(<Name>ClusterTest, BoundaryValue) { /* ... */ }

} // namespace
```

## Playwright E2E（通用）

```typescript
import { test, expect } from "@playwright/test";

test.describe("L3 E2E Flow", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(process.env.APP_URL!);
  });

  test("user login and dashboard load", async ({ page }) => {
    await page.fill('[data-testid="email"]', "test@example.com");
    await page.fill('[data-testid="password"]', "password");
    await page.click('[data-testid="login-btn"]');
    await expect(page.locator('[data-testid="dashboard"]')).toBeVisible();
  });
});
```

## Flutter Web + Playwright (Semantics Tree 黑盒 E2E)

> Flutter Web 通过 Semantics Tree 暴露无障碍 DOM 节点，Playwright 通过 ARIA role 定位元素，**无需修改生产代码**。

```typescript
// helpers/flutter.ts
export async function waitForFlutterReady(page: Page) {
  await page.waitForSelector('flutter-view, flt-glass-pane', { timeout: 30_000 });
}
export async function enableSemantics(page: Page) {
  const placeholder = page.locator('flt-semantics-placeholder');
  if (await placeholder.isVisible()) await placeholder.click();
}

// 用例：通过 ARIA role 定位 Flutter Widget
test('L3-FG-001 设备卡片显示锁定角标 @smoke', async ({ page }) => {
  await page.goto(`${BASE_URL}?scenario=new-user`);
  await waitForFlutterReady(page);
  await enableSemantics(page);
  await expect(page.locator('role=img').filter({ hasText: /locked/ })).toBeVisible();
});
```

Playwright 配置要点：`timeout: 60_000`（Flutter Web 冷启动慢）、`workers: 1`（不支持并行）、`webServer.command` 用 `flutter run -d web-server --web-port 5173 --web-renderer html`。

## Wasm Digital Twin 测试 (嵌入式专用)

```typescript
import { describe, it, expect, beforeAll } from "vitest";

describe("L2-2-LIGHT Digital Twin", () => {
  beforeAll(async () => {
    // 加载 scenario，等待 Wasm 设备就绪
  });

  it("L2-2-LIGHT-001 App 控制开灯", async () => {
    // 命令 → Wasm → HAL Callback → 断言
    bridge.invokeDeviceCommand(deviceId, clusterId, commandId);
    await waitFor(() => halState.ledOn === true);
  });
});
```

---

## CI/CD 通用流水线模板

```yaml
# .gitlab-ci.yml 通用结构
stages:
  - build
  - test-l1 # L1 + L2-1 (每次提交)
  - test-l2 # L2-2 集成 (Nightly / Merge)
  - test-e2e # L3 E2E (发布前)
  - quality # SonarQube / 覆盖率

test-l1:
  stage: test-l1
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    - if: $CI_COMMIT_BRANCH
  script:
    - go test ./... -short -count=1 -coverprofile=coverage.out  # 后端
    - flutter test                                                # APP
    - npm run test:unit                                           # WEB
    - bazel test //clusters/...:all //devices/...:all             # 嵌入式

test-l2:
  stage: test-l2
  rules:
    - if: $CI_PIPELINE_SOURCE == "schedule"
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
  script:
    - docker compose -f docker-compose.test.yml up -d
    - go test ./... -count=1 -run Integration

test-e2e:
  stage: test-e2e
  rules:
    - if: $CI_COMMIT_TAG
  script:
    - npx playwright test
```

---

## 嵌入式专用测试模式

### State-Wait Pattern (Digital Twin 场景)

```typescript
async function waitForHubStatus(key: string, value: string, timeout = 10000) {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Timeout")), timeout);
    const unsubscribe = bridge.onShadowDelta((delta) => {
      if (delta[key] === value) {
        clearTimeout(timer);
        unsubscribe();
        resolve(delta);
      }
    });
  });
}
```

### Forced Cycle Pattern (嵌入式状态重置)

```typescript
beforeEach(async () => {
  // 强制 ON → OFF 循环，确保设备发出重置通知
  await invokeCommand(deviceId, ON);
  await waitForState("on");
  await invokeCommand(deviceId, OFF);
  await waitForState("off");
});
```
