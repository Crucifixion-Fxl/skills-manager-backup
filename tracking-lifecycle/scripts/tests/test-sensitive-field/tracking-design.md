# 埋点设计 - 敏感字段测试

**事件：login_page**

```tracking-spec
application: test_app
events:
  - name: login_page
    point: login_page
    type: PAGE
    tracker_type: BASE
    description: 登录页浏览
    parameters:
      - name: user_email
        value_type: string
        is_required: false
        description: 用户邮箱
      - name: source
        value_type: string
        is_required: false
        description: 来源
```
