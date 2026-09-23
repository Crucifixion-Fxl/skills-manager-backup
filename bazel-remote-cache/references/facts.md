# 当前部署事实表

> 域名和镜像根据 BuildBuddy 部署环境而定，其他团队部署时需替换为自己的值。

## 域名

| 用途 | 域名 | .bazelrc config |
|------|------|-----------------|
| CI 写缓存 | buildbuddy-sg-ci.addx.live | `--config=ci` |
| 开发者只读 | buildbuddy-sg-dev.addx.live | `--config=dev` |
| UI 查看结果 | buildbuddy-sg-ui.addx.live | `--bes_results_url` |

## CI 镜像

| 项 | 当前值 |
|----|--------|
| CI 构建镜像 | registry-harbor-sg.addx.live/firmware/embed-quality:v1.3.0 |
| Bazel 版本 | 8.5.0（通过 .bazelversion + Bazelisk） |

## CI 变量

| 变量名 | 说明 | 配置位置 |
|--------|------|----------|
| BUILDBUDDY_API_KEY | WAF header gate 门禁 | GitLab 项目 Settings → CI/CD → Variables |
