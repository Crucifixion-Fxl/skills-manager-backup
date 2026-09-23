# 密码算法选型指南

> 本文档是 SKILL.md 的详细补充，针对 A4x IoT 摄像头固件的密码算法选型提供深度指导。

## 目录

- [AES 模式选型矩阵](#aes-模式选型矩阵)
- [哈希算法对比](#哈希算法对比)
- [密钥派生函数](#密钥派生函数)
- [硬件密码加速](#硬件密码加速)
- [随机数生成](#随机数生成)
- [TLS 密码库选型](#tls-密码库选型)
- [Nonce 与 IV 管理](#nonce-与-iv-管理)
- [密钥安全生命周期](#密钥安全生命周期)
- [代码模式参考](#代码模式参考)

---

## AES 模式选型矩阵

### 模式对比总览

| 特性 | GCM | CCM | CTR | CBC | ECB |
|------|-----|-----|-----|-----|-----|
| 认证（AEAD） | 是 | 是 | 否 | 否 | 否 |
| 可并行加密 | 是 | 否 | 是 | 否 | 是 |
| 可并行解密 | 是 | 否 | 是 | 是 | 是 |
| 支持 seek/随机访问 | 是 | 否 | 是 | 否 | 否（但无意义） |
| mbedTLS RAM 占用 (ctx) | ~8 KB | ~4 KB | ~1 KB | ~1 KB | ~1 KB |
| 需要 padding | 否 | 否 | 否 | 是 (PKCS7) | 是 (PKCS7) |
| ARM crypto ext 加速 | 是 (GHASH) | 间接 | 是 | 是 | 是 |
| A4x 使用状态 | **主选** | **受限设备主选** | **视频流** | **遗留** | **禁止** |

### A4x 场景选型指南

#### AES-GCM：认证加密首选

适用场景：BLE 安全通道、WiFi 配网数据、配置文件加密、TLS 内部密码套件

选择 GCM 的条件：
- SOC 有 AES 硬件加速（ARM Cortex-A with crypto extensions）
- RAM 充足（>= 64KB 可用于密码操作）
- 需要同时保证机密性和完整性（AEAD）

GCM 注意事项：
- nonce **必须** 12 字节，不可复用（同一密钥下 nonce 重复 = 密钥泄露）
- GCM 的 GHASH 依赖查找表，在无 HW 加速的 MIPS 上性能差（约为 CCM 的 60%）
- 最大加密数据量：单个 nonce 下不超过 2^39 - 256 bit（约 64GB，实际不是瓶颈）

#### AES-CCM：受限设备首选

适用场景：资源受限 MCU、Matter/Thread/Zigbee 协议栈、BLE LE Secure Connections

选择 CCM 的条件：
- SOC 无 GCM 硬件加速（MIPS、部分 RISC-V）
- RAM 紧张（<32KB 可用）
- 协议规范要求（Matter 强制 AES-128-CCM）

CCM 特点：
- 基于 CBC-MAC + CTR 组合，纯 AES 操作，不需要额外的 GHASH
- 在纯软件实现中比 GCM 更快（尤其在无 GHASH 硬件的平台）
- nonce 长度可变（7-13 字节），推荐 13 字节以最大化计数器空间
- 缺点：不可并行加密，大数据块性能不如 CTR

#### AES-CTR：流加密场景

适用场景：视频流加密、大文件加密、需要 seek 的加密存储

选择 CTR 的条件：
- 需要流式加密（边加密边传输）
- 需要随机访问能力（视频回放 seek）
- 加密性能是首要考虑

CTR 注意事项：
- CTR 模式**不提供认证**，必须配合独立 HMAC 或在 TLS/DTLS 会话内使用
- 如果单独使用 CTR + HMAC，必须使用 Encrypt-then-MAC 模式
- 计数器溢出 = 密钥流重复 = 安全灾难，16 字节计数器块中至少保留 8 字节给计数器

#### CBC：仅限遗留兼容

新设计**禁止**使用 CBC 模式。遗留系统维护时：
- **必须**配合 HMAC-SHA-256 使用 Encrypt-then-MAC 模式
- **必须**使用 PKCS7 padding
- **必须**使用随机 IV（不可用固定 IV 或计数器）
- 迁移计划：所有 CBC 使用点应在下一个大版本迁移到 GCM/CCM

#### ECB：绝对禁止

ECB 将相同明文块映射为相同密文块，对结构化数据（尤其是图像/视频）会完整保留原始数据模式。这不是理论风险——对摄像头产品是实际安全灾难。

唯一例外：AES-ECB 作为其他模式的底层原语（如实现 AES-CTR 时内部调用 ECB 加密计数器块），但这是库的实现细节，应用层不得直接使用 ECB API。

---

## 哈希算法对比

### 算法选择矩阵

| 算法 | 摘要长度 | A4x 状态 | 适用场景 | 性能参考 (ARM Cortex-A53) |
|------|----------|----------|----------|---------------------------|
| SHA-256 | 256 bit | **最低要求** | 固件完整性、HMAC、HKDF | ~200 MB/s (HW), ~80 MB/s (SW) |
| SHA-384 | 384 bit | 允许 | 需要更高安全边际 | ~150 MB/s (基于 SHA-512) |
| SHA-512 | 512 bit | 允许 | 大数据完整性校验 | ~150 MB/s (64-bit 优化) |
| SHA-3-256 | 256 bit | 推荐（有 HW 时） | 后量子准备、多样化 | ~100 MB/s (SW), HW 视实现 |
| SHA-1 | 160 bit | **禁止** | - | - |
| MD5 | 128 bit | **禁止** | - | - |

### 选型指导

**SHA-256 为默认选择**，覆盖绝大多数场景：
- 固件镜像完整性校验
- HMAC-SHA-256 消息认证
- HKDF-SHA-256 密钥派生
- TLS 握手中的 PRF

**SHA-3 在以下场景考虑**：
- SOC 提供 SHA-3 硬件加速
- 安全策略要求算法多样化（避免单点依赖 SHA-2 系列）
- 面向未来的后量子安全考虑

**SHA-512 在以下场景使用**：
- 运行在 64-bit ARM 平台上（SHA-512 在 64-bit 实现上比 SHA-256 更快）
- 需要处理大量数据的完整性校验

### HMAC 构造规则

- 密钥长度 >= 散列输出长度（HMAC-SHA-256 密钥 >= 256 bit / 32 字节）
- 密钥由 HKDF 或 TRNG 生成，禁止使用用户密码直接作为 HMAC 密钥
- Encrypt-then-MAC：先加密后 MAC，MAC 覆盖密文 + IV/nonce + AAD

---

## 密钥派生函数

### HKDF-SHA-256（主选）

A4x 设备端密钥派生统一使用 HKDF (HMAC-based Key Derivation Function, RFC 5869)。

**HKDF 两阶段模型**：

```
Extract:  PRK = HMAC-SHA-256(salt, IKM)
Expand:   OKM = HMAC-SHA-256(PRK, info || 0x01) || HMAC-SHA-256(PRK, T(1) || info || 0x02) || ...
```

**A4x 密钥层级设计**：

```
工厂写入 eFuse
    └── Device Root Key (256 bit, 设备唯一)
         ├── HKDF(salt=固定盐, info="mqtt-tls-v1")     → MQTT TLS 预共享密钥
         ├── HKDF(salt=会话随机数, info="video-stream-v1") → 视频流会话密钥
         ├── HKDF(salt=固定盐, info="ble-setup-v1")     → BLE 配网会话密钥
         ├── HKDF(salt=固定盐, info="config-enc-v1")    → 配置文件加密密钥
         └── HKDF(salt=固定盐, info="ota-verify-v1")    → OTA 签名验证辅助密钥
```

**info 字段规范**：
- 格式：`{用途}-{协议}-v{版本号}`
- 用途标识可以升级密钥而不影响其他通道
- 版本号用于密钥轮换：v1 → v2 时，两个版本短暂共存后切换

**salt 使用规则**：
- 会话密钥：salt = TRNG 生成的随机数（每次会话不同）
- 长期密钥：salt = 固定值（可以是全零或预定义常量，HKDF 规范允许）
- salt 不是秘密，可以明文传输

### PBKDF2（仅限特殊场景）

PBKDF2 仅用于从人类可记忆的密码/PIN 派生密钥，在设备端的使用场景极为有限：
- AP 模式配网时用户输入 PIN 的场景
- 本地 Web 管理界面的密码认证（如有）

参数要求：迭代次数 >= 100,000，salt >= 16 字节随机数

---

## 硬件密码加速

### MIPS 平台（Ingenic T-series）

典型特征：
- 通常**无**专用密码硬件加速
- 部分型号有 DMA 引擎可加速数据搬运
- CPU 主频 800MHz-1.5GHz

推荐配置：
- 优先使用 AES-CCM（纯 AES 操作，无需 GHASH 查找表）
- 视频流使用 AES-128-CTR（可并行，CPU 利用率高）
- SHA-256 软件实现，考虑 MIPS 优化的汇编实现
- mbedTLS 配置：禁用 `MBEDTLS_AESNI_C`，禁用 `MBEDTLS_AESCE_C`

### ARM Cortex-A 平台（Rockchip/Amlogic）

典型特征：
- ARMv8 Crypto Extensions (CE)：硬件 AES、SHA-1/SHA-256、PMULL (GCM)
- 部分 SOC 有独立 Crypto Engine（通过 /dev/crypto 或 AF_ALG 访问）
- RAM 充裕 (128MB-512MB)

推荐配置：
- 优先使用 AES-GCM（GHASH 有硬件加速）
- 启用 mbedTLS 的 ARM CE 支持
- mbedTLS 配置：启用 `MBEDTLS_AESCE_C`，启用 `MBEDTLS_SHA256_USE_ARMV8_A_CRYPTO_IF_PRESENT`
- 如果 SOC 有独立 Crypto Engine，通过 PSA Crypto Driver 接口对接（v4.0）或 `MBEDTLS_AES_ALT`（v3.x）

### RISC-V 平台（Bouffalo/ESP-C 系列）

典型特征：
- 部分支持 Zkn (RISC-V Crypto) 扩展
- RAM 通常较小（32KB - 8MB）
- 常用于 WiFi/BLE 子系统

推荐配置：
- 有 Zkn 扩展：使用 AES-GCM 或 AES-CCM（视 RAM 和 SDK 支持）
- 无 Zkn 扩展：使用 AES-128-CCM（省 RAM） + AES-128-CTR（流加密）
- SHA-256 软件实现
- mbedTLS 轻量配置：只启用必要的密码套件，禁用未使用的算法

### mbedTLS v4.0 配置（PSA Crypto）

mbedTLS v4.0 将密码算法配置从 `mbedtls_config.h` 迁移到 `psa/crypto_config.h`，使用 `PSA_WANT_*` 宏替代旧的 `MBEDTLS_*_C` 宏。TLS/X.509 配置仍在 `mbedtls_config.h`。

```c
/* === psa/crypto_config.h — 密码算法选择 === */

/* 所有平台必须启用 */
#define PSA_WANT_KEY_TYPE_AES           1
#define PSA_WANT_ALG_GCM                1  // 或 PSA_WANT_ALG_CCM（受限平台）
#define PSA_WANT_ALG_SHA_256            1
#define PSA_WANT_ALG_HKDF               1
#define PSA_WANT_ALG_ECDSA              1  // OTA 签名验证
#define PSA_WANT_ECC_SECP_R1_256        1  // P-256 曲线
#define PSA_WANT_ALG_CTR                1  // 视频流 AES-CTR

/* 禁止启用 */
// #define PSA_WANT_ALG_MD5             // MD5 禁止
// #define PSA_WANT_ALG_SHA_1           // SHA-1 禁止
// DES/3DES: v4.0 已完全移除，无需配置

/* 受限平台精简（RAM < 64KB） */
#undef PSA_WANT_ALG_GCM                // 用 CCM 替代
#define PSA_WANT_ALG_CCM                1
#undef PSA_WANT_ALG_RSA_PKCS1V15_SIGN  // 只用 ECDSA，不用 RSA

/* === mbedtls_config.h — 硬件加速（仍在此文件） === */

/* ARM 平台启用 */
#define MBEDTLS_AESCE_C                 // ARMv8 Crypto Extensions
#define MBEDTLS_SHA256_USE_ARMV8_A_CRYPTO_IF_PRESENT
```

> **v3.x → v4.0 迁移注意**：旧代码中的 `MBEDTLS_AES_C`、`MBEDTLS_GCM_C`、`MBEDTLS_SHA256_C` 等宏已移除。如果项目仍在 v3.x，使用旧宏；迁移到 v4.0 时必须改用 `PSA_WANT_*`。

---

## 随机数生成

### 规则

1. **密码操作必须使用 TRNG（硬件真随机数生成器）**
   - 所有 nonce、IV、salt、临时密钥的生成必须基于 TRNG
   - `rand()`、`srand(time(NULL))` **绝对禁止**用于密码操作（RL-07）

2. **mbedTLS 熵源配置**
   - **v4.0**：实现 `MBEDTLS_PSA_DRIVER_GET_ENTROPY` 回调，从 SOC TRNG 寄存器读取
   - **v3.x**：定义 `MBEDTLS_ENTROPY_HARDWARE_ALT` 并实现 `mbedtls_hardware_poll()`

3. **启动时初始化**
   - **v4.0 必须**：首次密码操作前调用 `psa_crypto_init()`，内部自动完成熵池初始化
   - **v3.x**：通过 `mbedtls_ctr_drbg_seed()` 手动初始化 CTR-DRBG

4. **TRNG 健康检测**
   - 生产固件应包含 TRNG 输出的基本健康检查（重复检测、单调性检测）
   - 参考 NIST SP 800-90B 的在线健康测试

### 无 TRNG 的应急方案

部分极低成本 SOC 可能无 TRNG。此时：
- 收集多个低熵源并混合：ADC 噪声、CPU 时钟抖动、中断时间差
- 通过 SHA-256 压缩混合后的熵源
- **必须**在安全审查报告中标注为风险项（G# 缺口）
- **必须**有向带 TRNG 的 SOC 迁移的计划

---

## TLS 密码库选型

| 库 | 适用场景 | 优势 | 劣势 |
|----|----------|------|------|
| **mbedTLS** | A4x 默认选择 | 轻量、模块化、ARM CE 支持好 | 无 FIPS 认证 |
| **wolfSSL** | 需要 FIPS 140-2/3 | 有 FIPS 认证模块 | 商业授权、体积稍大 |
| **OpenSSL** | Linux SOC (>64MB RAM) | 功能最全、生态最好 | 体积大、API 复杂、不适合受限设备 |

**决策流程**：
1. 默认选择 mbedTLS
2. 如果客户明确要求 FIPS 认证 → wolfSSL FIPS 模块
3. 如果 SOC 运行完整 Linux 且 RAM > 64MB → 可选 OpenSSL（但 mbedTLS 仍可用）

---

## Nonce 与 IV 管理

### GCM Nonce（12 字节）

**推荐构造方式**：

```
| 4 bytes: Device UID (固定) | 8 bytes: Monotonic Counter (递增) |
```

- Device UID：从 eFuse 或设备证书中提取的唯一标识
- Counter：64-bit 单调递增计数器，断电后从 NVS (Non-Volatile Storage) 恢复
- **关键**：同一密钥下 nonce 绝对不可重复，counter 值必须持久化

### CCM Nonce（13 字节推荐）

```
| 4 bytes: Device UID | 1 byte: Channel ID | 8 bytes: Counter |
```

- Channel ID：区分不同通信通道（0x01=MQTT, 0x02=BLE, 0x03=Video）
- 13 字节 nonce 提供最大的计数器空间

### CTR IV（16 字节）

```
| 4 bytes: Device UID | 4 bytes: Session ID | 8 bytes: Block Counter |
```

- Session ID：每次会话由 TRNG 生成
- Block Counter：从 0 开始，每加密一个 AES 块递增

### Counter 持久化要求

- Counter 必须存储在 NVS/Flash 中，断电不丢失
- 每次启动从 NVS 读取并加上安全余量（+1000），防止异常断电导致 counter 回退
- 写入频率优化：可以每 N 次加密写入一次（N=100），异常断电最多浪费 N 个 counter 值

---

## 密钥安全生命周期

### 生成

- 设备唯一密钥在工厂产线通过 HSM 生成并烧录 eFuse
- 会话密钥通过 HKDF 从设备密钥 + 随机 salt 派生
- 临时密钥（如 DH 交换的临时私钥）由 TRNG 生成

### 存储

- 见 SKILL.md 的密钥存储决策树
- 密钥在 RAM 中的存储位置应尽量集中，便于统一清零
- 禁止将密钥写入日志、debug 输出、core dump

### 使用

- 同一密钥用于单一目的（不可同时用于加密和签名）
- 密钥使用计数：GCM 单个密钥加密不超过 2^32 条消息（实际由 nonce 空间决定）

### 轮换

- 视频流会话密钥：每 1 小时或 1GB 数据轮换（以先到为准）
- MQTT TLS 会话：依靠 TLS 会话机制自动轮换
- 设备根密钥：不轮换（eFuse 一次性写入），通过 HKDF info 字段版本化实现逻辑轮换

### 销毁

- 会话密钥使用后立即调用 `mbedtls_platform_zeroize()` 清零
- 不使用 `memset()` 清零密钥（编译器可能优化掉）
- 进程退出或模块卸载前清零所有密钥材料

---

## 代码模式参考

### AES-128-GCM 完整示例（PSA Crypto API，mbedTLS v4.0+）

```c
#include "psa/crypto.h"
#include "mbedtls/platform_util.h"

int encrypt_gcm(const uint8_t *key, size_t key_len,
                const uint8_t *nonce, size_t nonce_len,
                const uint8_t *aad, size_t aad_len,
                const uint8_t *input, size_t input_len,
                uint8_t *output, size_t output_buf_size,
                size_t *output_len)
{
    psa_status_t status;
    psa_key_id_t key_id;
    psa_key_attributes_t attr = PSA_KEY_ATTRIBUTES_INIT;

    psa_set_key_type(&attr, PSA_KEY_TYPE_AES);
    psa_set_key_bits(&attr, key_len * 8);
    psa_set_key_usage_flags(&attr, PSA_KEY_USAGE_ENCRYPT);
    psa_set_key_algorithm(&attr, PSA_ALG_GCM);

    status = psa_import_key(&attr, key, key_len, &key_id);
    if (status != PSA_SUCCESS) return (int)status;

    status = psa_aead_encrypt(key_id, PSA_ALG_GCM,
        nonce, nonce_len,
        aad, aad_len,
        input, input_len,
        output, output_buf_size, output_len);  // output = ciphertext + 16 字节 tag

    psa_destroy_key(key_id);  // 销毁密钥句柄
    return (int)status;
}

// 调用前必须执行一次：psa_crypto_init();
```

### HKDF 密钥派生示例（PSA Crypto API，mbedTLS v4.0+）

```c
#include "psa/crypto.h"
#include "mbedtls/platform_util.h"

int derive_session_key(const uint8_t *device_key, size_t dk_len,
                       const uint8_t *salt, size_t salt_len,
                       const char *info, size_t info_len,
                       uint8_t *out_key, size_t out_len)
{
    psa_status_t status;
    psa_key_id_t base_key;
    psa_key_attributes_t attr = PSA_KEY_ATTRIBUTES_INIT;

    psa_set_key_type(&attr, PSA_KEY_TYPE_DERIVE);
    psa_set_key_usage_flags(&attr, PSA_KEY_USAGE_DERIVE);
    psa_set_key_algorithm(&attr, PSA_ALG_HKDF(PSA_ALG_SHA_256));

    status = psa_import_key(&attr, device_key, dk_len, &base_key);
    if (status != PSA_SUCCESS) return (int)status;

    psa_key_derivation_operation_t op = PSA_KEY_DERIVATION_OPERATION_INIT;
    psa_key_derivation_setup(&op, PSA_ALG_HKDF(PSA_ALG_SHA_256));
    psa_key_derivation_input_bytes(&op, PSA_KEY_DERIVATION_INPUT_SALT,
        salt, salt_len);
    psa_key_derivation_input_key(&op, PSA_KEY_DERIVATION_INPUT_SECRET,
        base_key);
    psa_key_derivation_input_bytes(&op, PSA_KEY_DERIVATION_INPUT_INFO,
        (const uint8_t *)info, info_len);

    status = psa_key_derivation_output_bytes(&op, out_key, out_len);
    psa_key_derivation_abort(&op);
    psa_destroy_key(base_key);
    return (int)status;
}

// 使用示例
uint8_t session_key[16];
derive_session_key(efuse_key, 16, trng_salt, 16,
                   "video-stream-v1", 15, session_key, 16);

// ... 使用 session_key ...

// 用完立即清零
mbedtls_platform_zeroize(session_key, sizeof(session_key));
```

### Nonce 管理示例

```c
typedef struct {
    uint8_t device_uid[4];
    uint64_t counter;
    uint32_t nvs_handle;
} nonce_manager_t;

int nonce_init(nonce_manager_t *nm, const uint8_t uid[4], uint32_t nvs_handle)
{
    memcpy(nm->device_uid, uid, 4);
    nm->nvs_handle = nvs_handle;

    // 从 NVS 恢复 counter + 安全余量
    uint64_t saved_counter;
    if (nvs_read_u64(nvs_handle, "nonce_ctr", &saved_counter) == 0) {
        nm->counter = saved_counter + 1000;  // 安全余量
    } else {
        nm->counter = 0;
    }
    // 立即持久化新起点
    nvs_write_u64(nvs_handle, "nonce_ctr", nm->counter);
    return 0;
}

int nonce_generate(nonce_manager_t *nm, uint8_t nonce[12])
{
    memcpy(nonce, nm->device_uid, 4);
    // 大端序写入 counter
    uint64_t c = nm->counter++;
    for (int i = 7; i >= 0; i--) {
        nonce[4 + i] = (uint8_t)(c & 0xFF);
        c >>= 8;
    }

    // 每 100 次持久化一次 counter
    if (nm->counter % 100 == 0) {
        nvs_write_u64(nm->nvs_handle, "nonce_ctr", nm->counter);
    }
    return 0;
}
```
