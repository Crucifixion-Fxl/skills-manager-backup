# 埋点设计 - 设备绑定

## 概述

本文档定义设备绑定流程的埋点方案，用于计算设备绑定成功率。

---

**事件：device_bind_start**

1. 事件描述：用户发起设备绑定请求时由后端服务触发
2. 触发场景：用户在 APP 扫描设备二维码后，后端收到绑定请求时立即上报
3. 数据示例：`{ "device_sn": "ABC123", "bind_method": "qr_scan" }`
4. 指标来源：设备绑定成功率（分母）
5. 指标参与：作为"绑定发起次数"的计数事件
6. 计算公式回溯：设备绑定成功率 = 绑定成功次数 / 绑定发起次数 × 100%
7. YAML 定义：

```tracking-spec
application: iot_device_service
events:
  - name: device_bind_start
    point: device_bind_start
    type: SELF_DEFINE
    tracker_type: BASE
    description: 用户发起设备绑定请求
    parameters:
      - name: device_sn
        value_type: string
        is_required: true
        description: 设备序列号
      - name: bind_method
        value_type: string
        is_required: true
        description: 绑定方式（qr_scan / manual_input）
```

---

**事件：device_bind_result**

1. 事件描述：设备绑定流程完成时由后端服务触发，记录绑定结果
2. 触发场景：后端完成设备绑定逻辑后，无论成功或失败均上报
3. 数据示例：`{ "device_sn": "ABC123", "bind_result": "success", "error_code": "" }`
4. 指标来源：设备绑定成功率（分子）
5. 指标参与：当 bind_result=success 时作为"绑定成功次数"的计数事件
6. 计算公式回溯：设备绑定成功率 = 绑定成功次数 / 绑定发起次数 × 100%
7. YAML 定义：

```tracking-spec
application: iot_device_service
events:
  - name: device_bind_result
    point: device_bind_result
    type: SELF_DEFINE
    tracker_type: BASE
    description: 设备绑定结果上报
    parameters:
      - name: device_sn
        value_type: string
        is_required: true
        description: 设备序列号
      - name: bind_result
        value_type: string
        is_required: true
        description: 绑定结果（success / fail）
      - name: error_code
        value_type: string
        is_required: false
        description: 失败时的错误码
```
