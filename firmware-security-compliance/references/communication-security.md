# 通信协议安全设计

> 本文档是 SKILL.md 的详细补充，适用于 A4x 联网嵌入式设备；摄像头专属流程只在对应产品形态适用。具体代码检查另见 [配网与本地访问代码审查](device-access-review.md)。

## 目录

- [TLS/DTLS 配置](#tlsdtls-配置)
- [WebSocket 安全 (WSS)](#websocket-安全-wss)
- [HTTP/HTTPS 安全](#httphttps-安全)
- [WebRTC 音视频安全](#webrtc-音视频安全)
- [BLE 安全](#ble-安全)
- [WiFi 安全](#wifi-安全)
- [视频流加密](#视频流加密)
- [CoAP 安全](#coap-安全)
- [协议选型决策树](#协议选型决策树)
- [证书管理](#证书管理)

---

## TLS/DTLS 配置

### 版本要求

| 协议 | 最低版本 | 推荐版本 | 说明 |
|------|----------|----------|------|
| TLS | 1.2 | 1.3 | 1.0/1.1 已废弃 (RFC 8996)，禁止使用 |
| DTLS | 1.2 | 1.2 | DTLS 1.3 标准化中，mbedTLS 支持有限 |

### 密码套件优先级

按优先级从高到低排列，仅列出允许使用的套件：

**TLS 1.3**（支持时优先）：
```
TLS_AES_256_GCM_SHA384
TLS_AES_128_GCM_SHA256
TLS_AES_128_CCM_SHA256        // 受限设备
TLS_CHACHA20_POLY1305_SHA256  // 无 AES HW 加速时的替代
```

**TLS 1.2**：
```
TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256    // ECDSA 证书，首选
TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384    // 高安全
TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256      // RSA 证书兼容
TLS_ECDHE_ECDSA_WITH_AES_128_CCM           // 受限设备
TLS_ECDHE_ECDSA_WITH_AES_128_CCM_8         // 极受限设备 (8 字节 tag)
```

**禁止使用的套件**：
- 包含 RC4、DES、3DES 的任何套件
- 包含 MD5、SHA-1 (作为 MAC) 的任何套件
- 包含 EXPORT 的任何套件
- 包含 NULL 加密的任何套件
- 非 PFS 套件（无 ECDHE/DHE 的 RSA 密钥交换）

### mbedTLS TLS 配置示例

```c
/* 密码套件配置 */
static const int ciphersuites[] = {
    MBEDTLS_TLS_ECDHE_ECDSA_WITH_AES_128_GCM_SHA256,
    MBEDTLS_TLS_ECDHE_ECDSA_WITH_AES_256_GCM_SHA384,
    MBEDTLS_TLS_ECDHE_RSA_WITH_AES_128_GCM_SHA256,
    0  // 终止符
};
mbedtls_ssl_conf_ciphersuites(&conf, ciphersuites);

/* 最低 TLS 版本 */
mbedtls_ssl_conf_min_tls_version(&conf, MBEDTLS_SSL_VERSION_TLS1_2);

/* 证书验证：生产环境必须启用 */
mbedtls_ssl_conf_authmode(&conf, MBEDTLS_SSL_VERIFY_REQUIRED);
// 禁止使用 MBEDTLS_SSL_VERIFY_NONE（红线）
```

### 证书 Pinning

A4x 设备使用两级 pinning 策略：

1. **中级 CA Pinning**（默认）：pin 签发设备证书的中级 CA 公钥
   - 优点：CA 续签不影响设备
   - 缺点：CA 被撤销时需要 OTA 更新 pin

2. **服务器证书 Pinning**（高安全场景）：pin 服务器叶证书公钥
   - 优点：最强绑定
   - 缺点：服务器换证书时必须先 OTA 更新 pin

**Pin 更新机制**：通过 OTA 固件更新下发新的 pin 集合，保持新旧 pin 并存至少一个更新周期。

### 会话恢复

受限设备应启用 TLS 会话恢复以减少握手开销：

- **Session Tickets**（推荐）：无服务端状态，节省约 3KB RAM/连接
- **Session ID**：需服务端维护缓存，适用于连接少的场景

```c
mbedtls_ssl_conf_session_tickets(&conf, MBEDTLS_SSL_SESSION_TICKETS_ENABLED);
```

### mTLS（双向认证）

设备到云管理通道**必须**使用 mTLS：

- 设备证书在工厂产线预置，私钥存入安全存储
- 证书格式：X.509 v3，ECC P-256 密钥
- 证书链：Device Cert → Intermediate CA → Root CA
- 云端通过设备证书的 CN 或 SAN 识别设备身份

---

## WebSocket 安全 (WSS)

A4x 设备使用 WebSocket 作为主要的**设备-云端双向通信协议**（命令下发、状态上报、信令交换）。

### 传输层

| 要求 | 说明 |
|------|------|
| WSS (TLS 1.2+) | 所有 WebSocket 连接必须使用 WSS，**禁止** WS 明文连接 |
| 服务器证书验证 | 必须验证服务器证书链，禁止跳过验证 |
| 密码套件 | 遵循上述 TLS 密码套件优先级 |

### 认证与鉴权

- **连接认证**：WSS 握手时通过 URL 参数或 HTTP Header 携带设备 token
- **Token 安全**：token 由云端签发，有时效性（建议 <= 24 小时），过期后重新获取
- **禁止硬编码 token**：token 必须动态获取，不可编译进固件
- **设备身份绑定**：云端校验 token 对应的 Device ID，防止伪装

### 消息安全

- **默认**：WSS (TLS) 已提供传输加密，消息体无需额外加密
- **信令通道**：WebSocket 同时承载 WebRTC 信令（SDP offer/answer、ICE candidate），信令中不含媒体密钥（密钥通过 DTLS 独立协商）
- **禁止在消息中明文传输**：设备密钥、用户密码、精确地理位置
- **消息格式校验**：设备端必须严格校验收到的消息格式（JSON schema），防止注入攻击

### 连接管理

- **心跳**：WebSocket ping/pong 间隔 30-60 秒，保持连接活跃
- **断线重连**：使用指数退避 + 随机抖动（防止雷群效应）
  - 初始延迟：1s + random(0, 1s)
  - 最大延迟：300s
  - 退避因子：2x
- **并发连接限制**：同一设备 ID 同时只允许一个 WSS 连接，新连接踢掉旧连接
- **Origin 校验**：服务端校验 WebSocket 握手的 Origin header（防止跨站 WebSocket 劫持）

### 与 HTTP 的分工

| 通信类型 | 协议 | 说明 |
|----------|------|------|
| 双向实时通信（命令/状态/信令） | WSS | 长连接，低延迟 |
| 单次请求（OTA 下载、配置拉取、日志上传） | HTTPS | 请求-响应模式 |
| 音视频 | WebRTC | DTLS-SRTP，由 WSS 交换信令 |

---

## HTTP/HTTPS 安全

A4x 设备使用 HTTPS 进行**单次请求类通信**（OTA 下载、配置拉取、认证、日志上传等）。

### 基本要求

| 要求 | 说明 |
|------|------|
| HTTPS (TLS 1.2+) | 所有 HTTP 通信必须使用 HTTPS，**禁止**明文 HTTP |
| 证书验证 | 必须验证服务器证书，禁止 `MBEDTLS_SSL_VERIFY_NONE` |
| 密码套件 | 遵循上述 TLS 密码套件优先级 |

### 认证

- **设备认证**：HTTPS 请求通过 Authorization header 携带设备 token（Bearer token）
- **Token 获取**：设备首次注册或 token 过期时，通过安全的认证接口获取新 token
- **禁止在 URL query string 中传递 token**（会被日志和 CDN 缓存记录）

### API 安全

- **输入校验**：严格校验所有 API 响应的 JSON 格式和字段类型
- **超时设置**：HTTP 请求超时 30 秒（OTA 下载除外），防止长时间阻塞
- **重试策略**：仅对 5xx 和网络错误重试，使用指数退避，4xx 不重试
- **防重放**：关键操作（OTA 触发、配置变更）使用 nonce 或时间戳防重放

---

## WebRTC 音视频安全

A4x 设备使用 WebRTC 进行**实时音视频传输**（P2P 直连或云端中继）。

### 安全架构

```
App/浏览器                    A4x 摄像头
    │                            │
    │←── WSS 信令通道 ──→│  (SDP/ICE 交换)
    │                            │
    │←── DTLS 握手 ────→│  (密钥协商)
    │                            │
    │←── SRTP 加密流 ──→│  (音视频数据)
```

### DTLS 要求

- **必须启用 DTLS**：WebRTC 标准要求 DTLS 用于密钥协商，禁止禁用
- DTLS 版本：最低 1.2
- 密码套件：与 TLS 密码套件策略一致
- 证书：使用自签名证书 + fingerprint 验证（通过 WSS 信令通道交换 fingerprint）

### SRTP 加密

- **加密**：AES-128-CTR（SRTP 标准默认）
- **认证**：HMAC-SHA-1-80（SRTP 标准，此处 SHA-1 的 HMAC 用法仍被认为安全）
- **密钥来源**：通过 DTLS-SRTP 握手导出，不手动设置密钥
- **密钥轮换**：每 2^31 个 SRTP 包或 1 小时（以先到为准）
- **禁止**：使用未加密的 RTP/RTCP

### ICE/TURN 安全

- **TURN**（中继场景）：
  - 必须使用 TURNS (TURN over TLS)，禁止明文 TURN
  - TURN 凭证有时效性，由云端动态签发
  - 中继服务器只转发加密后的 SRTP 数据，无法解密
- **ICE candidate 过滤**：设备不暴露内网 IP 到公网信令（使用 mDNS candidate 或仅 relay candidate）

### 信令安全

- WebRTC 信令（SDP offer/answer、ICE candidate）通过 WSS 加密通道交换
- SDP 中不包含媒体解密密钥（密钥通过 DTLS 独立协商）
- 信令消息需校验 Device ID，防止信令注入/伪造

---

## BLE 安全

### 安全等级要求

| Security Mode | Level | 加密 | 认证 | A4x 要求 |
|---------------|-------|------|------|----------|
| Mode 1, Level 1 | 无安全 | 否 | 否 | **禁止** |
| Mode 1, Level 2 | 未认证加密 | 是 | 否 | 不推荐 |
| Mode 1, Level 3 | 认证加密 | 是 | 是 | **最低要求** |
| Mode 1, Level 4 | 认证 LE SC | 是 | 是 (P-256) | 推荐 |

### 配对方式

| 设备类型 | 配对方式 | 说明 |
|----------|----------|------|
| 有屏幕的摄像头 | Numeric Comparison | 用户确认 6 位数字匹配 |
| 无屏幕摄像头 | Passkey Entry | 用户在 App 输入设备标签上的 PIN |
| 设置后丢弃 | OOB (Out-of-Band) | 通过 QR 码传递配对信息 |

### BLE 版本要求

- **BLE 4.2+** 必须（LE Secure Connections with ECDH P-256）
- BLE 4.0/4.1 的 LE Legacy Pairing 使用临时密钥 (TK) 交换，已被证明可暴力破解
- A4x 新产品必须支持 BLE 5.0+

### A4x BLE 使用模式

若产品 BLE 仅用于**设备配网/初始设置**，设置完成后应关闭或进入不可连接状态。其他产品确需持续发现或控制时，明确业务用途、认证与广播隐私要求，不直接套用摄像头的关闭流程。仅用于配网时的流程：

1. 用户打开 App → 扫描 BLE 广播 → 配对（Level 3+）
2. 通过 BLE 加密通道传输 WiFi 凭证
3. 设备连上 WiFi 后 → 关闭 BLE 广播（减少攻击面）
4. 后续通信全部走 WiFi + WSS/HTTPS + WebRTC

### 广播安全

- **禁止**在 BLE 广播包中包含 PII（设备 SN、MAC 地址映射、用户信息）
- 使用 Random Resolvable Private Address (RPA)，每 15 分钟轮换
- 广播内容仅包含：设备类型代码 + 配网状态标志

---

## WiFi 安全

### 加密协议要求

| 协议 | A4x 状态 | 说明 |
|------|----------|------|
| WPA3-SAE | **优先** | 抗离线字典攻击，前向保密 |
| WPA2-AES (CCMP) | 最低要求 | 兼容旧路由器 |
| WPA2-TKIP | **禁止** | 已知攻击 (Beck-Tews) |
| WEP | **禁止** | 已完全破解 |
| Open (无加密) | **禁止** | 任何场景均不允许 |

### PMF（Protected Management Frames）

- WPA3：PMF **强制**（802.11w mandatory）
- WPA2：PMF **推荐**启用（capable / optional）
- PMF 防止 deauth 攻击（攻击者伪造 deauthentication 帧断开设备连接）

### 安全配网

WiFi 凭证传输是设备最脆弱的环节之一：

**推荐方式**（优先级从高到低）：

1. **BLE 加密通道**（A4x 默认）：
   - BLE Level 3+ 配对后传输 WiFi SSID/密码
   - 端到端加密，不经过任何中间节点

2. **SoftAP + HTTPS**：
   - 设备开启 SoftAP → 用户手机连接 → 通过 HTTPS 传输凭证
   - 必须使用自签名证书 + App 端 pinning
   - SoftAP 密码必须设备唯一且不可预测：用 CSPRNG 生成，或由受保护的设备秘密经标准 KDF 派生；公开 SN 只能作为上下文，不能单独作为秘密来源

3. **禁止的方式**：
   - SmartConfig / ESP-Touch（明文广播 WiFi 密码到空气中）
   - 未加密的 HTTP 配网
   - 固定 SoftAP 密码

### Enterprise WiFi (802.1X)

面向企业客户部署时需支持：
- EAP-TLS：使用设备证书认证（与 mTLS 复用同一证书）
- 禁止 EAP-MD5（无服务器认证 + MD5）
- 禁止 LEAP（已知弱点）

---

## 视频流加密

### 架构模式

A4x 摄像头视频流有三种传输场景，安全方案不同：

| 场景 | 协议 | 加密方式 | 密钥管理 |
|------|------|----------|----------|
| P2P 实时观看 | WebRTC / P2P | SRTP (AES-128-CTR) | DTLS 密钥交换 |
| 云端中继观看 | Relay Server | SRTP + E2E 加密 | 设备-App 端密钥协商，云端不持有密钥 |
| 云录像存储 | HTTPS 上传 | AES-128-CTR per-segment | HKDF 派生存储密钥 |
| HLS/DASH 回放 | HTTPS | AES-128-CBC (标准兼容) | 密钥通过 DRM 或安全接口下发 |

### SRTP（实时流）

- 底层加密：AES-128-CTR（SRTP 标准默认）
- 认证：HMAC-SHA-1-80（SRTP 标准，此处 SHA-1 的 HMAC 用法仍被认为安全）
- 密钥交换：通过 DTLS-SRTP 握手协商
- 密钥轮换：每 2^31 个 SRTP 包或 1 小时（以先到为准）

### E2E 零知识架构

视频经过云端中继/存储时，云服务器**不持有解密密钥**：

```
摄像头 → [AES-CTR 加密] → 云端存储/中继 → [密文转发] → App → [AES-CTR 解密]
                ↑                                              ↑
          设备端密钥                                      App 端密钥
          (HKDF 派生)                                   (密钥协商获取)
```

密钥协商流程：
1. 设备和 App 通过 ECDH P-256 协商共享秘密
2. HKDF 从共享秘密派生视频加密密钥
3. 协商过程可通过云端信令通道中继（密文），但云端无法获取共享秘密
4. 密钥每次会话更新

### 云录像存储加密

- 视频分段（每段 10-30 秒）独立加密
- 加密算法：AES-128-CTR（支持随机访问播放）
- 每段密钥通过 HKDF 从主密钥 + 段序号派生
- 主密钥存储在设备安全存储中，通过安全通道同步到用户的 App

### 密钥轮换策略

| 密钥类型 | 轮换周期 | 触发条件 |
|----------|----------|----------|
| 视频流会话密钥 | 1 小时 | 时间到达或数据量 > 1GB |
| SRTP 主密钥 | 每次 DTLS 重协商 | DTLS 会话超时或手动触发 |
| 云存储分段密钥 | 每段 | 每个视频段使用不同派生密钥 |
| E2E 主密钥 | 每次配对 | 设备重新绑定用户时 |

---

## CoAP 安全

### 适用场景

CoAP 在 A4x 产品中用于：
- LAN 内设备发现与控制（Matter 兼容）
- 低功耗设备的简短命令交互

### 安全模式

| 模式 | 安全机制 | A4x 使用场景 |
|------|----------|-------------|
| NoSec | 无 | **禁止** |
| PSK | DTLS 1.2 + PSK | 受限设备，预共享密钥由 HKDF 派生 |
| Certificate | DTLS 1.2 + 证书 | 能力较强的设备，复用设备证书 |
| OSCORE | 对象级加密 | 极受限设备，不需要 DTLS 传输层 |

### DTLS 配置

CoAP over DTLS 遵循与 TLS 相同的密码套件策略，额外注意：
- DTLS 重传超时：初始 2 秒，最大 60 秒
- DTLS 会话保持：CoAP 观察模式需要长会话
- 连接 ID 扩展：支持 NAT 穿越场景的会话保持

---

## 协议选型决策树

```
通信场景
├── 设备 ↔ 云端
│   ├── 双向实时通道（命令/状态/信令） → WSS (WebSocket over TLS 1.2+)
│   ├── 单次请求（OTA/配置/认证） → HTTPS (TLS 1.2+)
│   ├── 实时音视频 → WebRTC (DTLS-SRTP)，信令通过 WSS 交换
│   ├── 视频录像上传 → HTTPS (TLS 1.2+)
│   └── OTA 固件下载 → HTTPS (TLS 1.2+) + 签名验证
│
├── 设备 ↔ App (P2P)
│   ├── 实时音视频 → WebRTC (DTLS-SRTP)
│   ├── 设备控制 → WebRTC DataChannel
│   └── 视频回放 → HTTPS (TLS 1.2+)
│
├── 设备 ↔ App (配网)
│   ├── 默认 → BLE LE Secure Connections (Level 3+)
│   └── 备选 → SoftAP + HTTPS (自签证书 + App pinning)
│
├── 设备 ↔ 设备 (LAN)
│   ├── Matter 兼容 → CoAP over DTLS (CASE/PASE)
│   └── 发现 → mDNS (不含敏感信息)
│
└── 设备 ↔ 紧急服务 (Noonlight 等)
    └── HTTPS (TLS 1.2+) + E2E 加密（地址/电话零明文）
```

每条通信链路的安全要求：
- **机密性**：传输加密（TLS/DTLS/SRTP）
- **完整性**：AEAD 或 HMAC
- **身份认证**：mTLS / 设备证书 / PSK
- **前向保密**：ECDHE 密钥交换
- **重放保护**：序号 / nonce / 时间戳

---

## 证书管理

### 设备证书体系

```
Root CA (A4x PKI)
├── Intermediate CA (设备签发)
│   ├── Device Cert (Camera Model A, SN: xxx)
│   ├── Device Cert (Camera Model B, SN: yyy)
│   └── ...
└── Intermediate CA (服务器签发)
    ├── Server Cert (wss.addx.live)
    ├── Server Cert (api.addx.live)
    └── ...
```

### 工厂产线证书预置

1. HSM 生成设备密钥对（ECC P-256）
2. CSR 签发设备证书（CN = Device ID, SAN = Device SN）
3. 私钥写入设备安全存储（eFuse/SE/TEE）
4. 证书 + CA 链写入设备 Flash（证书是公开信息，无需加密存储）

### 证书更新

- 设备证书有效期：5-10 年
- 通过 OTA 固件更新可更新 CA 证书和 pin 集合
- 设备证书续签：通过 EST (Enrollment over Secure Transport) 协议或自定义续签 API
- 证书吊销：通过 OCSP Stapling 或 CRL（设备端定期检查）

### CA 证书存储

设备 Flash 中存储：
- Root CA 证书（验证服务器证书链）
- 中级 CA 证书（设备证书链的一部分）
- 设备自身证书
- Pin 集合（当前 + 备份）

所有 CA 证书在固件编译时嵌入，通过 OTA 或 BLE 通道可更新。

### CA Root 证书安全规范

#### 信任锚最小化

- **只嵌入业务需要的私有 CA**：设备只需信任 A4x 自有 PKI 的 Root CA，禁止嵌入全量公共 CA 包（如 Mozilla CA Bundle）
- 全量公共 CA 包的风险：任何被信任的 CA 均可签发任意域名证书，一个 CA 被入侵即可中间人攻击所有设备
- 如需连接第三方服务（如 AWS IoT），单独添加该服务的 Root CA，不引入整个公共 CA 包

#### 证书链完整验证

TLS 握手必须完整验证证书链，缺一不可：

| 验证项 | 说明 | 不通过处理 |
|--------|------|------------|
| 链完整性 | Root CA → Intermediate → Leaf，每一级签名有效 | 断开连接 |
| 有效期 | 每级证书的 notBefore/notAfter | 见"过期应急"章节 |
| 域名匹配 | Leaf 证书的 CN 或 SAN 与连接目标域名匹配 | 断开连接 |
| 密钥用途 | Leaf 证书包含 serverAuth 扩展用途 | 断开连接 |
| 吊销状态 | OCSP Stapling（优先）或本地 CRL 缓存 | 见"吊销检查"章节 |

**禁止**在生产固件中设置 `MBEDTLS_SSL_VERIFY_NONE`（红线 RL-11）。

#### 证书过期应急 — 仓储长期存放场景

IoT 设备生产后可能在仓库存放数月甚至数年未联网。首次上电时，固件内嵌的 Intermediate CA 或服务器证书可能已过期，导致 TLS 握手失败，设备无法连接云端获取更新。

**有效期设计原则**：

| 证书类型 | 推荐有效期 | 原因 |
|----------|-----------|------|
| Root CA | >= 20 年 | 信任锚，更换成本极高 |
| Intermediate CA | >= 10 年 | 覆盖产品全生命周期 + 仓储期 |
| 服务器证书 | 1-2 年 | 行业惯例，由云端自动续签 |
| 设备证书 | >= 10 年 | 与产品生命周期对齐 |

**首次上电证书过期的恢复流程**：

```
设备首次上电
    │
    ├── 尝试 TLS 连接云端
    │   ├── 成功 → 正常流程（NTP 校时 → OTA 更新证书）
    │   └── 失败（证书过期）→ 进入 BLE 配网模式
    │
    └── BLE 配网阶段（App 连接设备）
        │
        ├── 1. BLE Level 3+ 配对（认证加密通道）
        ├── 2. App 从云端获取最新 CA 证书包（App 侧验证包的签名）
        ├── 3. App 通过 BLE 加密通道下发证书包到设备
        ├── 4. 设备验证证书包签名（使用固件内嵌的 Root CA 或预置的更新签名公钥）
        ├── 5. 设备写入新证书到 Flash
        └── 6. 重试 TLS 连接 → 成功
```

**BLE 证书更新的安全要求**：
- 证书包必须由 A4x 服务端签名（ECDSA P-256），设备端验证签名后才写入
- BLE 通道必须已完成 Level 3+ 配对（防止中间人注入伪造证书）
- 证书包格式包含版本号，设备只接受版本号递增的更新（防回滚）
- 写入前保留旧证书备份，写入失败可回退

**时钟信任问题**：
- 设备首次上电没有可信时钟（RTC 未校准），无法准确判断证书是否"真的"过期
- 策略：首次连接时**暂时放宽有效期检查**（仅对 Root CA 的有效期不做严格校验，但仍验证签名链和域名），连接成功后立即通过 NTP 校时，后续连接恢复严格有效期检查
- 此放宽仅限首次连接（设备 NVS 中标记"已校时"），已校时的设备必须严格检查

#### CA Root 更新机制

| 更新通道 | 优先级 | 适用场景 |
|----------|--------|----------|
| OTA 固件更新 | 主通道 | 设备已联网，随固件一起更新 CA 证书 |
| BLE 安全通道 | 备选通道 | 设备无法联网（证书过期/网络不可达） |
| 工厂重刷 | 最后手段 | 大批量退货设备 |

**OTA 更新 CA 证书的安全要求**：
- CA 证书包含在签名的 OTA 固件镜像中，随固件一起验证
- 不单独通过 HTTP API 更新 CA 证书（避免绕过签名验证）

#### 证书存储完整性

- CA 证书虽然是公开信息，但在设备端作为**信任锚**，被篡改等同于信任被劫持
- Flash 中的 CA 证书区域应有完整性校验（SHA-256 摘要存储在独立位置或 Secure Boot 保护的分区）
- 检测到完整性校验失败时，拒绝 TLS 连接并进入安全恢复模式（BLE 证书更新）

#### 证书吊销检查

| 方式 | 优先级 | 说明 |
|------|--------|------|
| OCSP Stapling | 优先 | 服务端在 TLS 握手中附带 OCSP 响应，设备无需额外网络请求 |
| 本地 CRL 缓存 | 备选 | 通过 OTA 定期更新 CRL，适用于无法做 OCSP 的场景 |
| 在线 OCSP 查询 | 不推荐 | 增加连接延迟，受限设备网络开销大 |

对于受限设备，如果 OCSP Stapling 不可用且 CRL 获取不现实，可以依赖**短有效期 + 证书 Pinning** 作为替代方案——pin 失效时通过 OTA 更新 pin 集合。
