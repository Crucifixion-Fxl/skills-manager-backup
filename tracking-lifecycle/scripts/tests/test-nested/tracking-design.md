# 埋点设计 - 视频播放

## 概述

本文档定义视频播放器页面的埋点方案，覆盖 PAGE → MODULE → COMPONENT 三层 SPM 结构，用于计算视频播放完成率。

---

**事件：player_page**

1. 事件描述：用户进入视频播放器页面时触发
2. 触发场景：用户从视频列表或推荐流跳转至播放器页面时上报
3. 数据示例：`{ "video_id": "V20240101", "source": "recommend_feed" }`
4. 指标来源：视频播放完成率（页面级容器）
5. 指标参与：作为 SPM 链的根节点，提供页面维度的数据切面
6. 计算公式回溯：视频播放完成率 = 播放完成次数 / 播放开始次数 × 100%
7. YAML 定义：

```tracking-spec
application: video_app
events:
  - name: player_page
    point: player_page
    type: PAGE
    tracker_type: BASE
    description: 视频播放器页面浏览
    parameters:
      - name: video_id
        value_type: string
        is_required: true
        description: 视频唯一标识
      - name: source
        value_type: string
        is_required: false
        description: 来源页面（recommend_feed / search / history）
```

---

**事件：video_module**

1. 事件描述：视频播放区域模块曝光时触发
2. 触发场景：播放器页面渲染完成后，视频播放区域模块出现在屏幕中时上报
3. 数据示例：`{ "video_id": "V20240101", "video_duration": 120 }`
4. 指标来源：视频播放完成率（模块级容器）
5. 指标参与：作为播放行为的模块级承载，连接页面和播放按钮组件
6. 计算公式回溯：视频播放完成率 = 播放完成次数 / 播放开始次数 × 100%
7. YAML 定义：

```tracking-spec
application: video_app
events:
  - name: video_module
    point: video_module
    type: MODULE
    tracker_type: EXP
    parent_page: player_page
    description: 视频播放区域模块曝光
    parameters:
      - name: video_id
        value_type: string
        is_required: true
        description: 视频唯一标识
      - name: video_duration
        value_type: integer
        is_required: true
        description: 视频总时长（秒）
```

---

**事件：play_btn**

1. 事件描述：用户点击播放按钮时触发
2. 触发场景：用户在视频播放区域点击播放/暂停按钮时上报
3. 数据示例：`{ "video_id": "V20240101", "action": "play", "current_position": 0 }`
4. 指标来源：视频播放完成率（分子计数来源）
5. 指标参与：当 action=play 时作为"播放开始次数"的计数事件
6. 计算公式回溯：视频播放完成率 = 播放完成次数 / 播放开始次数 × 100%
7. YAML 定义：

```tracking-spec
application: video_app
events:
  - name: play_btn
    point: play_btn
    type: COMPONENT
    tracker_type: CLK
    parent_page: player_page
    parent_module: video_module
    description: 播放按钮点击
    parameters:
      - name: video_id
        value_type: string
        is_required: true
        description: 视频唯一标识
      - name: action
        value_type: string
        is_required: true
        description: 操作类型（play / pause）
      - name: current_position
        value_type: integer
        is_required: false
        description: 当前播放进度（秒）
```
