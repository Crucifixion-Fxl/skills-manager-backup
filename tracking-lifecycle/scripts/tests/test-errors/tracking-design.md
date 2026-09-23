# 埋点设计 - 商品详情（含错误）

## 概述

本文档故意包含多处错误，用于测试 validator 的错误检测能力。

---

**事件：product_detail_page**

1. 事件描述：用户浏览商品详情页时触发
2. 触发场景：用户从商品列表进入商品详情页面时上报
3. 数据示例：`{ "product_id": "P001", "source": "search" }`
4. 指标来源：商品详情页转化率（分母）
5. 指标参与：作为"详情页浏览次数"的计数事件
7. YAML 定义：

```tracking-spec
application: ecommerce_app
events:
  - name: product_detail_page
    point: product_detail_page
    type: PAGE
    tracker_type: BASE
    description: 商品详情页浏览
    parameters:
      - name: product_id
        value_type: string
        is_required: true
        description: 商品唯一标识
```

---

**事件：product_info_module**

1. 事件描述：商品信息模块曝光时触发
2. 触发场景：商品详情页加载后，商品信息区域展示时上报
3. 数据示例：`{ "product_id": "P001", "price": 99.9 }`
4. 指标来源：商品详情页转化率
5. 指标参与：模块级曝光容器
7. YAML 定义：

```tracking-spec
application: ecommerce_app
events:
  - name: product_info_module
    point: product_info_module
    type: MODULE
    tracker_type: EXP
    parent_page: product_detail_page
    description: 商品信息模块曝光
    parameters:
      - name: product_id
        value_type: string
        is_required: true
        description: 商品唯一标识
      - name: price
        value_type: float
        is_required: true
        description: 商品价格
```

---

**事件：add_cart_btn**

1. 事件描述：用户点击加入购物车按钮时触发
2. 触发场景：用户在商品详情页点击"加入购物车"按钮时上报
3. 数据示例：`{ "product_id": "P001", "quantity": 1 }`
4. 指标来源：商品详情页转化率（分子）
5. 指标参与：作为"加购次数"的计数事件
7. YAML 定义：

```tracking-spec
application: ecommerce_app
events:
  - name: add_cart_btn
    point: product_detail_page
    type: COMPONENT
    tracker_type: EXP
    parent_page: product_detail_page
    description: 用户点击加入购物车按钮
    parameters:
      - name: product_id
        value_type: string
        is_required: true
        description: 商品唯一标识
      - name: quantity
        value_type: integer
        is_required: true
        description: 加购数量
```
