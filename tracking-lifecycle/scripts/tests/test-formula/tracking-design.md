# 埋点设计 - 用户注册

## 概述

本文档定义用户注册流程的埋点方案，用于计算注册转化率。注意：本文档中的计算公式回溯故意写错（分子分母写反），用于测试公式一致性校验。

---

**事件：register_page**

1. 事件描述：用户进入注册页面时触发
2. 触发场景：用户从登录页或首页跳转至注册页面时上报
3. 数据示例：`{ "source": "login_page", "channel": "organic" }`
4. 指标来源：注册转化率（分母）
5. 指标参与：作为"注册页面访问用户数"的计数事件
6. 计算公式回溯：注册转化率 = 注册页面访问用户数 / 注册成功用户数 × 100%
7. YAML 定义：

```tracking-spec
application: user_center
events:
  - name: register_page
    point: register_page
    type: PAGE
    tracker_type: BASE
    description: 注册页面浏览
    parameters:
      - name: source
        value_type: string
        is_required: true
        description: 来源页面（login_page / home / promotion）
      - name: channel
        value_type: string
        is_required: false
        description: 渠道标识（organic / paid / referral）
```

---

**事件：register_submit**

1. 事件描述：用户提交注册表单时触发
2. 触发场景：用户填写完注册信息提交表单，后端返回注册结果后上报
3. 数据示例：`{ "register_method": "email", "result": "success" }`
4. 指标来源：注册转化率（分子）
5. 指标参与：当 result=success 时作为"注册成功用户数"的计数事件
6. 计算公式回溯：注册转化率 = 注册页面访问用户数 / 注册成功用户数 × 100%
7. YAML 定义：

```tracking-spec
application: user_center
events:
  - name: register_submit
    point: register_submit
    type: SELF_DEFINE
    tracker_type: BASE
    description: 用户提交注册表单
    parameters:
      - name: register_method
        value_type: string
        is_required: true
        description: 注册方式（email / phone / social）
      - name: result
        value_type: string
        is_required: true
        description: 注册结果（success / fail）
      - name: error_reason
        value_type: string
        is_required: false
        description: 失败原因
```
