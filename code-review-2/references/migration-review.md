# Migration 文件审查参考

> 由 2026-05-11 naturehood `migration 020` column-drift bug 总结。本文档是 code-review skill 中 [Step 2 → 数据库 Migration 文件 — 专项检查](../SKILL.md) 一节的长版参考。

## 为什么需要专项检查

数据库 migration 跟普通代码不一样：

- **一次性 + 不可逆** — migration 在 prod 跑过就进 `schema_migrations` 表，永远不会再跑。当时写错了，**唯一补救是再写一条新 migration** 修旧的 damage。Code review 是 prod 上线前唯一的 catch 机会。
- **隐式时间耦合** — migration N 在 commit T1 写，可能在 commit T100 才 deploy 到 prod。期间 schema 和代码 mental model 已漂移，作者写时正确的 SQL 在 deploy 时可能错。
- **跨环境状态不一致** — staging / pre-prod / prod 的 `schema_migrations` 进度可能错位，"staging 验过了" 不等于 "prod 安全"。
- **金本位破坏** — 业务数据被 UPDATE / DELETE 后无 audit log，错了无法回溯。

业界主流答案：**migration 文件只做 schema 改动；数据 backfill 单独走 backfill job / 运维手动脚本**。

## 触发条件

变更 diff 包含：

- `**/migrations/*.up.sql` 或 `**/migrations/*.down.sql`
- 或新增 `schema_migrations` 表的访问代码

触发后按下面的 case 树审查。

## Case 树

### Case 1: 文件只含 ALTER TABLE / CREATE TABLE / CREATE INDEX，无 DML

✅ **通过门槛降低**，只需检查：
- 有 `information_schema` 列/索引存在性 guard（CI 双跑必需）
- 文件顶部有 why 注释
- 大表（>100k 行）有 online schema change 方案备注

### Case 2: 文件含 UPDATE（数据 backfill）

⚠️ **专项审查**。要求作者满足 schema-aware backfill 的 **4 条铁律**：

#### 铁律 1: Idempotent guard

WHERE 必须保证重跑不产生副作用：

```sql
-- ✅ 好：第二次跑 0 行匹配（已 set 过的不再 match）
UPDATE postcards SET tenant_id = 'kiwibit'
WHERE brand_id = 1 AND tenant_id IS NULL;

-- ❌ 坏：第二次会再次匹配同样的行，重复 UPDATE
UPDATE postcards SET tenant_id = 'kiwibit' WHERE brand_id = 1;

-- ❌ 坏：每次 deploy 都改写 status，业务数据无法可靠存活
UPDATE postcards SET status = 'pending' WHERE allow_community = 1 AND status = 'ready';
```

> 注：CI 的 `before_script` 用裸 mysql 跑一遍 + api-server 启动 golang-migrate 又跑一遍，同 CI job 内 migration 跑 2 次。无 guard 会让 CI 炸。prod 没这个特性，但 guard 仍是必须的（防御未来人工 replay + 跨环境一致）。

#### 铁律 2: No column drift

不能 UPDATE **被 split 过 / 即将被 split 的字段**。

检查方法 — grep 历史 migrations 看是否有过列拆分：

```bash
grep -i "split\|ADD COLUMN.*review_status\|MODIFY COLUMN" server/migrations/*.up.sql
```

如果当前 migration UPDATE 的列在过去某次 migration 里发生过 split，但作者**没**引用 split migration 的 HISTORICAL NOTE 解释为什么这次 UPDATE 仍然语义安全 —— flag 为 column-drift 风险。

#### 铁律 3: Bounded scope

WHERE 必须明确限定行范围：

```sql
-- ✅ 好：按 id range 或 NULL 反查
UPDATE explore_posts SET feeder_name = 'My Feeder'
WHERE feeder_name = '' AND id < 1000;

-- ❌ 坏：影响整张表，没有 scope
UPDATE explore_posts SET feeder_name = 'My Feeder' WHERE 1=1;
```

#### 铁律 4: Comment block

UPDATE 上方必须有注释写明：

```sql
-- WHY: 新加 tenant_id NOT NULL 之前，把历史行从 brand_id 桥接过来
-- GUARD: WHERE brand_id IN (1, 2) AND tenant_id IS NULL — 重跑无副作用
-- RECOVERY: 如果 UPDATE 中途 fail，手动 SELECT 跑剩余范围，再 UPDATE schema_migrations SET dirty=0
UPDATE postcards SET tenant_id = 'kiwibit'
WHERE brand_id = 1 AND tenant_id IS NULL;
```

缺任何一条 → reviewer 要求作者补全或拆出。

### Case 3: 文件含 INSERT mock / seed / 测试数据

🔴 **不通过**。Migration 永远不该塞 mock / seed 数据：

- Mock / seed 数据有时效性（团队/产品变了就过时）
- Migration 跑在 prod，mock 数据进 prod 会污染真业务数据
- 正确位置：`scripts/seed-*.sh` 或独立的运维操作包（如 `~/Downloads/preheat-*/`）

例外：固定字典数据（如行政区划、物种参考表）可以 INSERT，但需要：
- INSERT 用 `ON DUPLICATE KEY UPDATE` 或 `INSERT IGNORE` 保证幂等
- 文件顶部注释明确这是 system reference data 而非 mock

### Case 4: 文件含 DELETE

🔴 **几乎总是不通过**，除非：

- 是 DROP COLUMN / DROP TABLE 的配套清理
- 数据已确认无业务读取（DBA + senior eng 双确认）
- 文件顶部有完整的 recovery plan（如何从备份恢复）

否则要求作者改成应用层删除 + 软删除 + 配合 retention 策略。

### Case 5: 文件做 column rename / type change / NULL→NOT NULL

⚠️ **专项审查 — expand-and-contract 模式**：

- ❌ 单条 migration 直接 `ALTER COLUMN` 改 type 或加 NOT NULL，可能锁表 + breaking
- ✅ 拆成多步：
  1. Migration A：ADD 新列（NULL 或带 DEFAULT）
  2. 应用层双写新旧列
  3. Backfill job 把历史行的新列填齐
  4. 应用层切换读新列
  5. Migration B：DROP 旧列

reviewer 要求作者按上述模式拆，不接受一步到位。

### Case 6: 文件做 column split（一列拆两列，如 status → status + review_status）

⚠️ **高风险，特殊审查**：

- ✅ 拆分 migration 必须包含正向数据迁移（把旧值 walk 到新列）
- ✅ 文件顶部必须有 HISTORICAL NOTE：原列做什么用 → 现在分别是什么用 → 旧代码仍写到旧列怎么办
- ✅ **作者必须证明已审计所有 prior migrations 里 UPDATE 该列的代码**：是否有遗留 UPDATE 会重新污染拆分后的列？如果有，要么改 UPDATE 用新列，要么 HISTORICAL NOTE 注释删除（参考 naturehood `release/kbtv-master` commit `12358d1` 的 020 fix）
- ✅ 项目级 column drift 登记表（如 `server/CLAUDE.md` 的 column drift 表）必须更新

未做这些 → flag column-drift bug 风险。

## Case study: naturehood migration 020（2026-05-11）

完整 thread 见 [MR !377](https://gitlab.addx.ai/applications/naturehood/-/merge_requests/377) 描述 + 020 文件内 HISTORICAL NOTE。

### 时间线

1. **2025-Q4** — `postcards.status` 是混合字段：既存 `ready/processing/failed`（视频拷贝 lifecycle），又存 `pending/approved/rejected`（审核状态）
2. 写了 `012_postcard_review.sql`：

   ```sql
   UPDATE postcards SET status='pending'
   WHERE allow_community = 1 AND status = 'ready';
   ```

   **当时正确** — 把社区分享塞进 admin 审核队列。
3. **后来** `014_postcard_split_status_review.up.sql` 发现混用是 bug，强制拆分：
   - `status` 只回到视频拷贝 lifecycle
   - 新建 `review_status` 列承担审核

   14 自带正向回填：`status='pending' → status='ready', review_status='pending'`
4. 012 在后续 MR 时 renumber 到 018，再到 020，但**文件内 UPDATE 没跟着改**
5. 020 在 14 之后跑：把刚拆出去的 'pending' **重新塞回 status 列**，污染视频拷贝 lifecycle 字段（'pending' 不是合法 video-copy 值）

### 后果

- staging 没炸出来：feedLogic / hub 主要看 review_status，status 污染对用户不可见
- 但语义已坏：将来读 status 的代码会看到 garbage
- prod 还没 deploy，避免了灾难，但 lesson 已经学到

### 怎么应该被 catch 住

- ❌ 没被 catch：14 拆分时没审计 prior migrations 里 UPDATE 该列的代码（Case 6 铁律 3）
- ❌ 没被 catch：012 → 020 renumber 时没重新 review 文件内容是否仍正确（Case 2 / Case 6）
- ✅ 本规范加了 Case 6 专项审查 + column drift 表 + PR checklist

### 修复

- 020 文件内 UPDATE 替换成多行 HISTORICAL NOTE 注释（commit `12358d1` on `release/kbtv-master`）
- staging recovery SQL：

  ```sql
  UPDATE postcards
  SET status = 'ready'
  WHERE status = 'pending'
    AND allow_community = 1
    AND video_url IS NOT NULL AND video_url != '';
  ```

- 规范沉淀到项目 `server/CLAUDE.md` + `docs/architecture/server/migration-rules.md` + 本 skill reference

## 项目级规范模板（推荐每个项目都建）

每个 Go 项目应在 `server/CLAUDE.md` 或类似位置有：

1. 黄金规则段（schema-only, no business data UPDATE in migrations）
2. 4 条铁律段（idempotent guard / no column drift / bounded scope / comment block）
3. Column drift 登记表（被拆/重命名/改语义的列）
4. PR checklist
5. 大表 ALTER 警告 + 阈值

参考实现：
- naturehood: [`server/CLAUDE.md > 数据库迁移`](https://gitlab.addx.ai/applications/naturehood/-/blob/master/server/CLAUDE.md) + [`docs/architecture/server/migration-rules.md`](https://gitlab.addx.ai/applications/naturehood/-/blob/master/docs/architecture/server/migration-rules.md)

## golang-migrate 部署原理速查（reviewer 常问）

### schema_migrations 表

```sql
CREATE TABLE schema_migrations (
  version BIGINT NOT NULL PRIMARY KEY,
  dirty   TINYINT(1) NOT NULL
);
```

**只保留一行**：当前应用到的最高版本号 + 是否处于 "脏" 状态。

### 部署时做什么

1. 查 `schema_migrations` 拿当前 version V
2. 扫 embed.FS 找所有 `version > V` 的 `.up.sql`，按版本号升序
3. 每个新文件：执行 SQL → 成功则 `version=N, dirty=0`；失败则 `dirty=1`，**拒绝再跑任何 migration**

### 每个 migration 在 prod 上跑几次

**严格 1 次**。后续 deploy 看到 version 已在 schema_migrations 就 skip 整个文件 —— 包括其中的 UPDATE。

> 这就是为什么 schema-aware backfill 必须 idempotent —— 不是 prod 重跑（不会），是 CI 重跑（双跑特性）+ 防御未来人工 replay。

### CI 双跑特性

CI 的 `before_script` 用裸 `mysql < f.up.sql` 跑一遍 + api-server 启动 golang-migrate 再跑一遍 = 同一 CI job 内每个 migration 跑 **2 次**。所有 ALTER 必须有 `information_schema` guard，UPDATE 必须有 WHERE guard。

### dirty 状态

如果 migration 中途 fail：
- `schema_migrations` 标 `dirty=1`
- 下次 deploy refuse 跑任何 migration
- 必须人工 `UPDATE schema_migrations SET dirty=0` + 手动恢复数据，才能继续

reviewer 应在 migration PR 中要求作者描述 "中途 fail 的人工恢复步骤"。

## 演进路径（项目成熟度阶段）

随项目规模升级 migration 工程实践：

| 触发条件 | 升级动作 |
|:---|:---|
| 单表 > 100k 行 | DDL 必须走 [gh-ost](https://github.com/github/gh-ost) 或 [pt-online-schema-change](https://docs.percona.com/percona-toolkit/pt-online-schema-change.html) |
| migration 频次 > 每周 1 条 | 设立 DBA review gate；migration MR 必须 DBA + senior eng 双签 |
| 团队 > 5 人 | 考虑迁移到 [Atlas](https://atlasgo.io/) 等 declarative schema 工具 |
| prod 切多 region / 多 cluster | 部署前必须在 prod replica 跑一次 migration dry-run，看耗时 / 锁等待 |

reviewer 视项目成熟度阶段决定哪些升级建议是 "必须" 哪些是 "建议"。
