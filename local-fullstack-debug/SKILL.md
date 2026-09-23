---
name: local-fullstack-debug
description: 在本地将 iot-service-unified 后端与 g0-android App（模拟器或真机）打通联调。当用户说"本地联调"、"前后端打通"、"App 连本地后端"、"模拟器连后端"、"local fullstack"、"debug frontend and backend"时触发。只要涉及 Android App 连接本地 iot-service 后端调试的场景就应使用本 Skill。
---

# local-fullstack-debug

将 Android App（g0-android）连接到本地运行的 iot-service-unified 后端，实现全栈联调。

## 前置条件

本 Skill 假设你已经具备：
- iot-service-unified 后端已在本地 `localhost:7777` 运行（参考 `iot-service-dev-setup` skill）
- g0-android 已构建出 APK（参考 `android-dev-setup` skill）
- Android 模拟器已创建或真机已连接

如果这些还没准备好，先分别完成对应 Skill 的流程。

## 核心概念

### Android 模拟器网络

模拟器有自己的虚拟网络，`localhost` 指向模拟器自身而非宿主机。要访问宿主机服务：

| 地址 | 含义 |
|------|------|
| `10.0.2.2` | 宿主机的 localhost |
| `10.0.2.1` | 宿主机的网关/路由器 |
| `10.0.2.15` | 模拟器自身 |
| `127.0.0.1` | 模拟器的 loopback |

所以 App 要访问本地后端 `localhost:7777`，必须使用 `http://10.0.2.2:7777`。

### Cleartext HTTP 安全策略

Android 9+ 默认禁止 HTTP 明文通信。g0-android 项目已在 `network_security_config.xml` 中白名单放行了 `10.0.2.2`、`localhost`、`127.0.0.1`：

```
Base/BaseUI/src/main/res/xml/network_security_config.xml
```

如果你使用真机，需要将宿主机的局域网 IP 也加入白名单。

## 执行流程

### Step 1: 启动后端

确认后端运行中：
```bash
curl -s http://localhost:7777/actuator/health
```

如果没有运行，使用 `iot-service-dev-setup` skill 启动。

### Step 2: 启动模拟器

```bash
# 列出可用的 AVD
$ANDROID_HOME/emulator/emulator -list-avds

# 启动（带可见窗口）
ANDROID_SDK_ROOT=$ANDROID_HOME \
$ANDROID_HOME/emulator/emulator -avd <AVD_NAME> -gpu swiftshader_indirect -no-audio &

# 等待模拟器就绪
adb wait-for-device
```

启用键盘输入（避免只能用虚拟键盘）：
```bash
# 修改 AVD 配置
AVD_DIR="$HOME/.android/avd/<AVD_NAME>.avd"
sed -i '' 's/hw.keyboard = no/hw.keyboard = yes/' "$AVD_DIR/config.ini"
# 需要重启模拟器生效
```

### Step 3: 安装 APK

```bash
# 确认设备已连接
adb devices

# 安装 APK（以 VicoHome Debug 为例）
adb install -r g0-android/app/build/outputs/apk/vicoHome/debug/app-vicoHome-debug.apk
```

### Step 4: 配置 App 指向本地后端

App 通过 SharedPreferences 中的 `key_local_base_url` 控制 API 地址。用 adb 写入：

```bash
# 设置 API 基础 URL 指向本地后端
adb shell "run-as com.addx.ai.debug sh -c 'cat > /data/data/com.addx.ai.debug/shared_prefs/addx_net_node.xml << EOF
<?xml version=\"1.0\" encoding=\"utf-8\" standalone=\"yes\" ?>
<map>
    <string name=\"key_local_base_url\">http://10.0.2.2:7777</string>
</map>
EOF'"

# 强制停止 App 使配置生效
adb shell am force-stop com.addx.ai.debug
```

说明：`com.addx.ai.debug` 是 VicoHome Debug 变体的包名。不同 flavor 的包名不同。

### Step 5: 清除代理（如果有）

某些开发工具（如 mitmproxy、Charles）会设置全局代理导致请求超时：

```bash
# 检查当前代理设置
adb shell settings get global http_proxy

# 清除代理
adb shell settings put global http_proxy :0
```

### Step 6: 配置国家节点（后端）

App 登录后会根据用户所在国家路由到不同节点。本地开发需要在 `application-dev-local.yml` 中配置所有节点指向本机：

```yaml
# 添加到 application-dev-local.yml 末尾
country:
  nodev1:
    defaultNode:
      vicoo: "http://10.0.2.2:7777"
      safemo: "http://10.0.2.2:7777"
    config:
      vicoo:
        US: "http://10.0.2.2:7777"
        CN: "http://10.0.2.2:7777"
        EU: "http://10.0.2.2:7777"
      safemo:
        US: "http://10.0.2.2:7777"
        CN: "http://10.0.2.2:7777"
        EU: "http://10.0.2.2:7777"
  node:
    defaultNode:
      vicoo: "http://10.0.2.2:7777"
      safemo: "http://10.0.2.2:7777"
    config:
      vicoo:
        US: "http://10.0.2.2:7777"
        CN: "http://10.0.2.2:7777"
        EU: "http://10.0.2.2:7777"
      safemo:
        US: "http://10.0.2.2:7777"
        CN: "http://10.0.2.2:7777"
        EU: "http://10.0.2.2:7777"

server-node:
  root-url:
    CN: "http://10.0.2.2:7777"
    US: "http://10.0.2.2:7777"
    EU: "http://10.0.2.2:7777"
```

使用 `10.0.2.2` 而非 `localhost`，因为这些 URL 会返回给 App，App 需要用模拟器地址访问。

如果不配置，登录会报 `国家配置不能为空` 错误，App 显示 "network error"。

### Step 7: 验证联调

1. 启动 App
2. 观察后端日志是否有请求进来
3. 尝试登录（使用 `iot-service-dev-setup` 中创建的测试用户）

验证命令：
```bash
# 监听后端日志中的登录请求
tail -f nohup.out | grep -i "login"

# 或通过 curl 模拟 App 请求
curl -s http://localhost:7777/account/login \
  -H "Content-Type: application/json" \
  -d '{"email":"test@vicohome.io","password":"9a931c55ac02bf216550c464b1992a30c522dfabf6cb31deada5c716bc13a263","authVersion":1}'
```

## 真机联调

如果使用真机而非模拟器，地址不同：

1. 获取宿主机局域网 IP：`ifconfig en0 | grep "inet "`
2. App 的 `key_local_base_url` 使用局域网 IP（如 `http://192.168.1.100:7777`）
3. 后端 `application-dev-local.yml` 中的国家节点也用局域网 IP
4. 在 `network_security_config.xml` 中添加局域网 IP 的 cleartext 许可
5. 确保宿主机防火墙允许 7777 端口入站

## 常见问题速查

| 问题 | 原因 | 解决 |
|------|------|------|
| App 显示 "network error" | 国家节点未配置 / 后端未启动 / 代理干扰 | 检查后端运行、配置国家节点、清除代理 |
| `国家配置不能为空` (后端日志) | `countryNodeV1Config.getConfig()` 返回 null | 在 `application-dev-local.yml` 添加国家节点配置 |
| 请求超时 | mitmproxy 等代理拦截 | `adb shell settings put global http_proxy :0` |
| `CLEARTEXT communication not permitted` | HTTP 被安全策略阻止 | 在 `network_security_config.xml` 添加目标 IP |
| App 仍连远程服务器 | SharedPreferences 未生效 | 强制停止 App 后重启 |
| 模拟器无法启动 | 系统镜像缺失 / AVD 配置错误 | `sdkmanager "system-images;android-XX;google_apis;arm64-v8a"` |
| `Broken AVD system path` | ANDROID_SDK_ROOT 未设置 | `export ANDROID_SDK_ROOT=$ANDROID_HOME` |
| 模拟器崩溃 (hanging thread) | GPU 加速问题 | 添加 `-gpu swiftshader_indirect -no-audio` 启动参数 |
| 登录返回 WRONG_PASSWORD | 密码哈希错误 | 参考 `iot-service-dev-setup` 的密码系统说明 |
| 登录要求邮箱验证码 | 设备不在信任列表 | 设置用户 `internal=1` 跳过 1FA |

## Examples

### Good — 使用 10.0.2.2 配置模拟器连接本地后端

```bash
# SharedPreferences 指向宿主机
adb shell "run-as com.addx.ai.debug sh -c 'cat > /data/data/com.addx.ai.debug/shared_prefs/addx_net_node.xml << EOF
<?xml version=\"1.0\" encoding=\"utf-8\" standalone=\"yes\" ?>
<map>
    <string name=\"key_local_base_url\">http://10.0.2.2:7777</string>
</map>
EOF'"

# 后端国家节点也用 10.0.2.2（App 会用返回的 URL 发请求）
country:
  nodev1:
    defaultNode:
      vicoo: "http://10.0.2.2:7777"
```

### Bad — 在模拟器中使用 localhost

```bash
# 错误：localhost 在模拟器中指向模拟器自身，不是宿主机
adb shell "run-as com.addx.ai.debug sh -c '... key_local_base_url=http://localhost:7777 ...'"
```

### Bad — 后端国家节点用 localhost

```yaml
# 错误：这些 URL 会返回给 App，App 用 localhost 访问不到宿主机
country:
  nodev1:
    defaultNode:
      vicoo: "http://localhost:7777"  # App 访问不到
```

### Good — 真机使用局域网 IP + adb reverse

```bash
# 方式一：adb reverse 端口转发
adb reverse tcp:7777 tcp:7777
# App 可以直接用 http://localhost:7777

# 方式二：使用局域网 IP
# App SharedPreferences: http://192.168.1.100:7777
# 后端国家节点也用: http://192.168.1.100:7777
```

## References

- [网络配置详解](references/network-guide.md) — 模拟器/真机网络架构、端口转发、HTTPS 配置
