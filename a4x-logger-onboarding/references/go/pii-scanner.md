# 业务代码裸 PII 扫描 + 自动改写

**目的**:全量 logic 迁移完后,**主动扫**业务代码里 message 模板的裸 PII,改成 `sensitive.FormatIDType` 包装 或 struct tag 路径。这是 skill 的**主动质量检查步**,不能漏。

## 扫描什么

business code 里的日志调用(`log.Info` / `log.Error` / `log.Warn` / `log.Debug`),**message 字符串**里有 `<PII-keyword><separator>%<verb>` 模式,且对应位置实参是**裸标量**(不是 struct field)。

**6 个 PII 关键字**(含变体,见 [fields-and-idtype.md](fields-and-idtype.md) 完整表):

| canonical | 变体 |
|---|---|
| `user_id` | `user_id` / `userId` / `userID` / `uid` / `user-id` |
| `email` | `email` / `mail` / `email_addr` / `emailAddr` |
| `device_sn` | `device_sn` / `serialNumber` / `serial_number` / `deviceSn` / `sn` |
| `device_mac` | `device_mac` / `mac` / `macAddr` / `mac_addr` |
| `ticket_id` | `ticket_id` / `ticketId` / `tid` / `ticket-id` |
| `user_sn` | `user_sn` / `userSn` / `userSerial` / `user-sn` |

## 扫描正则

Go 项目的扫描命令(用 `grep -rE`):

```bash
cd <service-root>
grep -rnE "log(x)?\.(Info|Error|Warn|Debug)(w|f)?\(.*\b(user_?[iI]d|userID|uid|email|mail|device_?[sS]n|serialNumber|serial_number|deviceSn|device_?[mM]ac|macAddr|ticket_?[iI]d|tid|user_?[sS]n|userSerial)[=:]?\s*%[vsd]" \
  --include='*.go' \
  -- internal/
```

分解:
- `log(x)?\.(Info|Error|Warn|Debug)(w|f)?\(` — 匹配 `log.Info(` / `logx.Infow(` / `l.logger.Errorf(` 等
- `.*\b<PII-keyword>` — message 前半段含 PII 关键字(word boundary 避免误匹配)
- `[=:]?\s*%[vsd]` — 关键字后面紧跟 `=%v` / `:%s` / ` %d` 之类

## 执行步骤

### 1. 跑扫描,产出 raw list

```bash
grep -rnE "<上面的正则>" --include='*.go' -- internal/ > /tmp/pii-hits.txt
wc -l /tmp/pii-hits.txt
```

### 2. 对每条 hit 做三段判定

读 hit 的源码位置,判断:

| 判定项 | 规则 | Action |
|---|---|---|
| 实参是 struct field(如 `req.UserID`) | struct 本身应该已经打 `sensitive:"user_id"` tag | **不改**,验证 struct tag 对就行 |
| 实参是裸标量(如 `uid`, `userID`, 函数返回值) | SDK 看到的是 int/string,反射失效 | **改**成 `sensitive.FormatIDType(sensitive.UserID, uid)` |
| 实参是 `nil` 或类型不确定 | 改动风险高,可能空指针 | **不改**,加 TODO 注释让开发 review |
| message 里的关键字只是上下文(如 `"checking user_id mapping"`),后面没跟 `%` 占位符 | 不是在打 PII 值 | **不改**(正则应该 filter 掉,但 review 时再确认) |

### 3. 按规则自动改写

**规则 A:裸 int/string → `FormatIDType`**

```go
// before
log.Info("login user_id=%d action=login", uid)
log.Error("send mail failed email=%s", err.Email, err)

// after
log.Info("login user_id=%v action=login", sensitive.FormatIDType(sensitive.UserID, uid))
log.Error("send mail failed email=%v", sensitive.FormatIDType(sensitive.Email, err.Email), err)
```

**注意**:`%d` / `%s` 要统一换成 `%v`(因为 FormatIDType 返回 string,类型 format verb 不匹配反而会打 `!(BADPREC)`)。

**规则 B:需要加 import**

改动文件里加:
```go
import "gitlab.addx.ai/CLOUD/a4x-logger-sdk/go/sensitive"
```

**规则 C:处理 `logx.Infow` + logx.Field**

```go
// before
logx.Infow("user_login", logx.Field("user_id", uid), logx.Field("ip", ip))

// after(整体改成 printf + FromContext)
l.logger.Info("user_login user_id=%v ip=%v",
    sensitive.FormatIDType(sensitive.UserID, uid),
    ip,
)
```

这条改动是 Step 5 (logic 迁移)要做的,PII 扫描阶段只需要把 `user_id` 包装上。

### 4. 产出 review report

每次扫描跑完,给 skill 用户一份 report:

```
PII 扫描报告(服务: <name>)
========================
扫到 N 处嫌疑位置:
  - 自动改写(高置信): M 处
    <file>:<line>  before → after  (列几条)
    ...
  - 人工 review(低置信): K 处
    <file>:<line>  理由:<struct field / nil / context mention>
    ...

改动 summary:
  - M 个文件加 sensitive import
  - M 行 log 调用改写
  - 建议单独 commit: "chore: wrap PII scalars with FormatIDType"
```

### 5. 验证改动没破坏测试

```bash
go build ./...
go test ./internal/logic/... -count=1
```

## skill 自主执行时的策略

Claude 在 skill 里跑这步时:

1. **先跑扫描**,把 raw hits 全量 `cat` 出来给自己看
2. **逐条 Read 对应文件**(别盲信 grep,要看源码判断实参类型)
3. **分类**:struct field(skip)/ 裸标量(auto-rewrite)/ 不确定(mark for human)
4. **auto-rewrite 批量改**:用 Edit 工具,一次改一个文件,每个改完 `go build` 立刻 verify
5. **给用户 report**:改了 M 处,标了 K 处需人工看
6. **单独 commit**:不要把 PII 改写塞进 logic 迁移 commit,独立 commit 更好 review

## 典型 false positive

**不是真 PII 的情况**,正则可能误报:

| 示例 | 为什么不是 PII |
|---|---|
| `log.Info("looking up user_id mapping %v", tableName)` | `user_id` 只是动词 + 名词描述,不是打值 |
| `log.Info("route=/api/users/%d", pathID)` | `/users/` 只是 URL 片段,`pathID` 不一定是 user_id(可能是 pagination) |
| `log.Debug("db_col=email_addr type=varchar")` | 打的是 schema 描述 |

**规则**:疑似但前后语义不清的,**先不改,加 TODO**,让业务开发自己 review。false positive 自动改反而搞坏语义。

## 扫描以外:IDE 防护

可选:在 `.editorconfig` / IDE pre-commit hook 加 lint 规则,阻止新增 `log.Info("user_id=%d", uid)` 这种裸 PII 模板。超出本 skill 范围,放在服务长期治理 ticket。
