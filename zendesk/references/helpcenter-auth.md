# Help Center 原生认证客户端

此路径与admin OAuth身份独立，不能静默互换或用一个tenant授权覆盖另一个。选择Help Center业务任务时才委派已有客户端；认证/获取方式正本在本文件，执行实现仍由业务helper维护。

该脚本通过 AWS Secrets Manager 在运行时获取 Zendesk API Token，凭证仅存在于脚本进程内存中，不出现在命令行参数、环境变量或标准输出中。

#### 前置条件

使用者需满足以下条件（公司统一配置，一次性设置）：

1. `~/.aws/credentials` 中配置有可 AssumeRole 到 `cs-tools-role` 的 profile
2. 设置环境变量 `AWS_PROFILE`（如 `cstools-dev`）
3. Python 环境中已安装 `boto3` 和 `requests`

业务调用：[zendesk-helpcenter](../../zendesk-helpcenter/SKILL.md)。验证只返回非敏感状态，模型不得读取或输出SecretString/Token。共享认证入口是web-access；其尚未实现此原生凭据后端时保持delegated，不能推断通用provider已接入。
