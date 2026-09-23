# buzz-broker：沙箱外的窄接口，给需要 docker／任意命令的 agent

[agent-sandbox.md](agent-sandbox.md)「已知风险与坑」第 5 条说了替代方案：*「把 Chrome 抓取放到沙箱外的一个窄接口后面（owner 侧的确定性服务／固定脚本，agent 只发 URL、取结果文件）」*。buzz-broker 是这个模式的通用实现，管的是 docker（testcontainers、compose、有头 Chrome 的 L3）而不是只有 Chrome：沙箱的 seccomp 过滤禁止子进程建 Unix socket，`docker` 客户端连不上任何守护进程；把 docker 组交给 agent 等于交出宿主机 root（见 agent-sandbox.md 第 4 条）。broker 让「沙箱内的 agent 能跑 docker」和「它依然不是 root」同时成立。

## 前提

只有开发类 agent（要 `docker`、testcontainers、有头 Chrome 的 L3、任意 shell 命令）才需要这层；纯答疑／分诊／只读类按 agent-sandbox.md 的通用基线即可，不需要 broker。

## 实现在哪里

服务端 [`scripts/broker.py`](../scripts/broker.py)（HTTP 服务：令牌认证、任务生命周期、每任务的 bwrap 隔离、上传校验、产物打包、审计日志）、管理 CLI [`scripts/broker_admin.py`](../scripts/broker_admin.py)（铸令牌／改名／撤销／轮换管理令牌／热加载配置）、客户端 [`scripts/buzz_job.py`](../scripts/buzz_job.py)（agent 在沙箱里用它提交任务）——这三个文件是**本文档一切「已验证事实」和安全边界表述的唯一依据**，测试在 [`tests/test_broker.py`](../tests/test_broker.py) 与 [`tests/test_admin.py`](../tests/test_admin.py)（含真实 bwrap 的隔离验证、变异测试确认过的安全逻辑）。这几个文件不含任何机器专属信息（本机的专用用户名 `buzz-svc`、端口、`/opt/buzz-toolchains` 路径都是部署时的配置，不是代码里的常量）。

**本机部署路径 `/opt/buzz-broker/src/` 是这三个文件的原样拷贝**，改动流程是：改这里的代码 → 跑 `python3 -m unittest discover -s tests` 全绿 → `cp scripts/{broker,broker_admin,buzz_job}.py /opt/buzz-broker/src/` → 用 `broker_admin.py restart` 让服务拉起新代码（管理 CLI 本身不需要重启，配置改动会热加载）。本机独有、不进这个仓库的东西：`/opt/buzz-broker/etc/config.json`（部署时的策略配置，含各 agent 令牌的 sha256，不含令牌明文）、每个 agent 的令牌文件、`setup/setup-buzz-svc.sh` 等一次性 sudo 安装脚本（建专用用户、装 rootless docker，机器相关，不是可移植代码）。

## 机制

```
agent（沙箱里）──HTTP 经显式代理──▶ 127.0.0.1:<port>  buzz-broker（专用用户，systemd --user 常驻）
                                        │  每个任务：解包工作区 tar → 套一层 bwrap（只看得到自己的工作区 + 只读工具链）
                                        │            → bash -c <命令>；systemd scope 限内存/进程数
                                        └─▶ rootless docker（专用用户名下，同一路径挂 docker.sock）
```

- **专用 Unix 用户**（不是每个 agent 一个；本机是 `buzz-svc`）：不在 `docker` 组、没有 sudo、环境里没有任何密钥。任务以它的身份跑，容器逃逸最多到这个用户，不是 jchen。
- **rootless docker**：装在这个专用用户名下，不是宿主机的 root docker；套接字只在它自己的 `/run/user/<uid>/docker.sock`。
- **broker 只监听 127.0.0.1**，每个 agent 一个令牌（配置里只存 sha256），按令牌认策略，不按 agent 名字——agent 改名只是改配置里的标签，令牌和令牌文件都不用换。
- **身份来自令牌，不来自 agent 名字**：铸令牌时生成一个不透明 id 做文件名（不含 agent 名），改名操作只改配置里的 label，文件路径不变。
- **每个任务再套一层 bwrap**：只看得到自己的工作区（同一路径在内外一致，任务里 `docker -v $PWD:...` 才能用）和只读工具链，看不到宿主机 home、看不到 broker 自己的密钥；systemd scope 限内存和进程数。
- **上传的工作区 tar 严格校验**：拒绝绝对路径、`..`、硬链接、设备、出界符号链接。日志、上传、产物、并发、队列都有上限。审计日志记录谁在何时跑了什么命令（只留前 200 字符）。
- **正在上传/解包的请求单独限流**：`app.q.qsize()` 只管已经排进任务队列的数量，管不到「正在写盘/解包」这个更早的阶段——`ThreadingHTTPServer` 会并行处理多个连接，低信任 agent 一次开多个大上传，能在真正排队之前就把磁盘和内存拖垮共享 broker。这里另有一个全局有界信号量（`max_concurrent_uploads`，服务启动时按配置固定大小，不随热加载改变），超了直接 429，不接受、不排队。

## agent 怎么用

沙箱里 `127.0.0.1` 是沙箱自己的空回环，宿主机服务只能经沙箱的 HTTP/SOCKS 代理到达；`NO_PROXY` 又会让客户端绕开代理，所以客户端必须检测到代理就显式走代理并清空 `NO_PROXY`（与 agent-sandbox 通用基线之外、给沙箱内进程连宿主机服务的固定做法一致）。

- 令牌文件在该 agent 自己的 `~/`（不在共享目录），沙箱设置里要给它加**精确路径** `allowRead`（父目录 `~/` 已 `denyRead`，通配符在 `~/` 下不生效，见 agent-sandbox.md「基线还要放行的五项只读工具」同样的坑）。
- `BUZZ_BROKER_TOKEN_FILE` 走沙箱 `settings.json` 的顶层 `env` 注入（已验证会注入 Bash 子进程），不用改 agent 自己的 `.env`、不用重启 agent 进程。
- 客户端把当前目录打包上传（默认排除 `.git`／`.claude`／自己的产物目录），在任务里执行，实时回显日志，退出码与命令一致；产物按 glob 下载回来。

## 已验证的事实

- feishu-bridge 的 L1（纯逻辑）、L2（testcontainers 起真实 Postgres）经 broker 稳定全绿。
- L3（relay + Postgres + Redis 容器、有头 Chrome 走完整绑定流程、外部 CLI 工具）子集通过；**L3 全套有间歇失败**，见下「已知的坑」。
- 4 个开发类 agent（cwd 不是仓根、仓在 cwd 子目录）套沙箱 + 发令牌后，经沙箱代理调用 broker 全链路跑通容器：沙箱 → 代理 → broker → 专用用户的 rootless docker。直连 `docker.sock` 仍被拒（符合预期，只能走 broker，这是设计而不是缺口）。
- 这层不改变 agent-sandbox.md 的结论：沙箱本身依然挡住直接的 docker/Chrome Unix socket；broker 是沙箱之外单独运行的窄服务，不是把 socket 重新打开给沙箱。

## 已知的坑

- **任务里 `CI` 不要预设为 true**：不少仓库的测试支撑（如 testcontainers 封装）看见 `CI` 就拒绝起容器，误以为在 CI runner 里没有 docker。要用就由任务自己显式设。
- **`.git` 不在打包范围内**：需要版本号／脏树状态的目标（如生成测试报告）要从外部传参数，不能指望目标自己读 `git rev-parse`。
- **容器网络端口发布在 rootless 下偶发冲突**：docker 挑选宿主端口的游标可能撞上宿主机别的服务正在监听的端口，rootful docker 遇到冲突会换端口重试，rootless 的端口驱动没有这个重试，报 `RootlessKit PortManager.AddPort(): listen … bind: address already in use`。失败用例单独重跑或整批重跑通常就过；这是已知的稳定性限制，不是任务本身的缺陷，重跑前不要去改被测代码。
- **`/run` 是任务自己的 tmpfs**，会盖住 `/etc/resolv.conf` 的目标（多数发行版是指向 `/run/systemd/resolve` 的符号链接），任务里 DNS 会整体失效，除非单独把该目录只读挂进去。
- 需要浏览器时，Chrome 的安装目录要作为只读路径显式挂进任务（不在默认工具链范围内）。
- Go 模块缓存等共享依赖只读挂载：任务能建能测，但新增依赖需要维护者重新同步缓存，任务自己装不进去。

## 残余风险（设计固有，不是缺陷；给令牌前要知道）

1. **同一专用用户下的任务之间不是互相隔离的**：一个任务能经 docker 挂载读到其它*正在运行*任务的工作区，以及已完成任务的日志/产物。工作区在任务结束时立即删除，缩小窗口但不是零窗口。**所以给这层令牌的 agent 要当作共享一个信任域**：不要把持有不同机密的 agent 都接进来，任务里不要打包上传 `.env` 之类文件（该用环境变量传的用环境变量传）。
2. **任务和它起的容器共用宿主网络**：能连本机 `127.0.0.1` 上的其它服务。这是 testcontainers 之类工具连得到自己起的容器映射端口的前提，不能整个禁掉；如果不接受，就不要把这层开给需要连接本机服务的 agent。
3. **令牌等于该 agent 在这层里的全部能力**：令牌文件在沙箱内被精确路径放行，其它沙箱设置（allowRead/allowWrite/deny）该按 agent-sandbox.md 的通用基线照常收紧，broker 不替代那层。

## 与 agent-sandbox.md 的关系（更新）

agent-sandbox.md「推广指引」把开发类 agent 列为「不能一刀切」，「开发类 agent 的额外注意」一节把 docker 列为不可用（分析结论，未实测）。**现状**：docker 本身依然不可用（沙箱设计如此，见上一节最后一条），但已经有经过验证的窄路径可以给需要它的 agent；套上 buzz-broker 之后，`git push`／`~/.gitconfig`／`/tmp` 那几条注意事项仍然适用，不受影响。开发类 agent 的推广顺序、逐个 agent 的取舍仍按各自评估，不因这层存在而默认全部开通。
