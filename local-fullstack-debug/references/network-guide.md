# 本地联调网络配置详解

## 目录

1. [Android 模拟器网络架构](#android-模拟器网络架构)
2. [App 的 API 路由机制](#app-的-api-路由机制)
3. [后端国家节点配置](#后端国家节点配置)
4. [HTTPS 与安全策略](#https-与安全策略)
5. [端口转发](#端口转发)
6. [网络调试技巧](#网络调试技巧)

---

## Android 模拟器网络架构

Android 模拟器运行在一个隔离的虚拟网络中，通过 NAT 访问宿主机网络。

```
┌──────────────────────┐     ┌──────────────────────┐
│   Android Emulator   │     │     Host Machine      │
│                      │     │                       │
│  App (com.addx.ai)   │     │  iot-service :7777    │
│       │               │     │       ▲               │
│       ▼               │     │       │               │
│  http://10.0.2.2:7777 ├────►│  localhost:7777      │
│                      │ NAT │                       │
│  127.0.0.1 = 模拟器  │     │  127.0.0.1 = 宿主机  │
│  10.0.2.2  = 宿主机  │     │                       │
│  10.0.2.15 = 模拟器  │     │  Docker:              │
│                      │     │   MySQL  :3306        │
│                      │     │   Redis  :6379        │
│                      │     │   Kafka  :9092        │
└──────────────────────┘     └──────────────────────┘
```

### 关键 IP 映射

| 模拟器内地址 | 实际指向 |
|-------------|---------|
| `10.0.2.2` | 宿主机 localhost |
| `10.0.2.1` | 虚拟路由器/DNS |
| `10.0.2.3` | DNS 服务器 |
| `10.0.2.15` | 模拟器自身的网络接口 |
| `127.0.0.1` | 模拟器自身的 loopback |

## App 的 API 路由机制

### SharedPreferences 配置

g0-android App 使用 `SharedPreferences` 文件 `addx_net_node.xml` 存储 API 基础 URL：

```xml
<!-- /data/data/com.addx.ai.debug/shared_prefs/addx_net_node.xml -->
<?xml version="1.0" encoding="utf-8" standalone="yes" ?>
<map>
    <string name="key_local_base_url">http://10.0.2.2:7777</string>
</map>
```

设置方式：
```bash
# 通过 adb 写入 SharedPreferences
adb shell "run-as com.addx.ai.debug sh -c 'cat > /data/data/com.addx.ai.debug/shared_prefs/addx_net_node.xml << EOF
<?xml version=\"1.0\" encoding=\"utf-8\" standalone=\"yes\" ?>
<map>
    <string name=\"key_local_base_url\">http://10.0.2.2:7777</string>
</map>
EOF'"

# 修改后必须强制停止 App 使配置生效
adb shell am force-stop com.addx.ai.debug
```

### 不同 Build Variant 的包名

| Flavor | Build Type | 包名 |
|--------|-----------|------|
| vicoHome | debug | `com.addx.ai.debug` |
| vicoHome | release | `com.addx.ai` |
| safemo | debug | `com.addx.safemo.debug` |
| safemo | release | `com.addx.safemo` |

### 请求路由流程

```
App 启动
  │
  ├─ 读取 SharedPreferences key_local_base_url
  │   └─ 如果有值 → 使用该 URL 作为 API base
  │   └─ 如果为空 → 使用 build.conf 中配置的远程服务器
  │
  ├─ 登录请求 POST {base_url}/account/login
  │
  └─ 登录成功后，服务端返回 node 信息
      └─ 后续请求使用服务端返回的 node URL
```

这就是为什么后端的国家节点配置也必须返回 `10.0.2.2` 地址——登录成功后 App 会使用服务端返回的节点 URL。

## 后端国家节点配置

### CountryNodeV1Config

`@ConfigurationProperties(prefix = "country.nodev1")` 读取 YAML 配置。如果未配置，`getConfig()` 返回 null，导致登录时报错 `国家配置不能为空`。

### 完整配置模板

```yaml
# application-dev-local.yml
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

### 真机配置

真机使用宿主机局域网 IP 替代 `10.0.2.2`：

```yaml
country:
  nodev1:
    defaultNode:
      vicoo: "http://192.168.1.100:7777"  # 替换为实际 IP
      # ...
```

获取宿主机 IP：
```bash
# macOS
ipconfig getifaddr en0

# Linux
hostname -I | awk '{print $1}'
```

## HTTPS 与安全策略

### network_security_config.xml

位置：`g0-android/Base/BaseUI/src/main/res/xml/network_security_config.xml`

项目已配置允许以下地址的 HTTP 明文通信：
- `10.0.2.2`（模拟器访问宿主机）
- `localhost`
- `127.0.0.1`

如需添加新地址（如真机的局域网 IP）：
```xml
<network-security-config>
    <domain-config cleartextTrafficPermitted="true">
        <domain includeSubdomains="true">10.0.2.2</domain>
        <domain includeSubdomains="true">localhost</domain>
        <domain includeSubdomains="true">127.0.0.1</domain>
        <domain includeSubdomains="true">192.168.1.100</domain> <!-- 新增 -->
    </domain-config>
</network-security-config>
```

修改后需要重新构建 APK。

## 端口转发

### adb reverse（真机推荐）

将真机端口转发到宿主机：
```bash
# 将真机的 7777 端口转发到宿主机的 7777
adb reverse tcp:7777 tcp:7777
```

这样真机上的 `localhost:7777` 就能直接访问宿主机的后端服务，不需要修改 `network_security_config.xml`。

```bash
# 查看所有转发规则
adb reverse --list

# 移除转发
adb reverse --remove tcp:7777

# 移除所有转发
adb reverse --remove-all
```

### adb forward（较少使用）

将宿主机端口转发到设备：
```bash
# 将宿主机的 8080 转发到设备的 7777
adb forward tcp:8080 tcp:7777
```

## 网络调试技巧

### 1. 从模拟器测试后端连通性

```bash
# 进入模拟器 shell
adb shell

# 测试后端是否可达
curl http://10.0.2.2:7777/actuator/health
# 如果 curl 不可用
wget -q -O - http://10.0.2.2:7777/actuator/health
```

### 2. 检查代理干扰

```bash
# 查看当前代理
adb shell settings get global http_proxy

# 清除代理
adb shell settings put global http_proxy :0
```

### 3. 后端日志过滤

```bash
# 只看登录相关日志
grep -i "login\|Login\|account" <log_file>

# 看 HTTP 请求
grep "Received request" <log_file>
```

### 4. 抓包分析

如果需要查看 App 与后端之间的通信：

```bash
# 使用 tcpdump（在模拟器中）
adb shell tcpdump -i any -s 0 -w /sdcard/capture.pcap host 10.0.2.2
adb pull /sdcard/capture.pcap
# 用 Wireshark 分析 capture.pcap
```

### 5. 常用 ADB 命令

```bash
# 查看所有连接的设备
adb devices

# 安装 APK
adb install -r path/to/app.apk

# 卸载 App
adb uninstall com.addx.ai.debug

# 查看 App 日志
adb logcat | grep -i "addx\|vicoo"

# 查看 App 的 SharedPreferences
adb shell "run-as com.addx.ai.debug cat /data/data/com.addx.ai.debug/shared_prefs/addx_net_node.xml"

# 清除 App 数据
adb shell pm clear com.addx.ai.debug
```
