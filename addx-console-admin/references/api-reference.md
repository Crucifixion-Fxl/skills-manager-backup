# A4x Console (console.addx.live) API Reference

> Auto-captured: 2026-03-03T02:10:41.686Z
> Base URL: `https://revenus-sharing-backend.addx.live`
> Total requests: 143 | Unique endpoints: 88

## Authentication

```http
POST /login
Content-Type: application/json

{"phone": "<username>", "password": "<password>"}
```

Returns `userToken`. Use header: `Authorization: <token>`

## Endpoints

### battery_cell/manage

#### `POST /battery_cell/manage/list`
Called 1x

Body:
```json
{
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":13,"batteryCellModel":"PY-21700-4600","batteryCellFactory":"河南鹏辉 ","batteryCellFactoryId":66,"createTime":"2026-02-04T05:43:46.000Z","updateTime":"2026-02-04T05:43:46.000Z","creator":"吴海森","updater":"吴海森"},{"id":12,"batteryCellModel":"YD-21700-6600","batteryCellFactory":"江西远东电池公司","batteryCellFactoryId":95,"createTime":"2026-02-04T05:27:28.000Z","updateTime":"2026-02-04T05:27:28.000Z","creator":"吴海森","updater":"吴海森"},{"id":11,"batteryCellModel":"PH-18650-
```

---

#### `GET /battery_cell/manage/selections`
Called 4x

Response (200):
```json
{"code":0,"msg":"","data":["BK-18650-3200","BK-18650-3350","DC-21700-6700","PH-18650-2200","PH-18650-2500","PY-21700-4600","TP-16340","TP-18650","TP-18650-2600","TP-21700","TP-26650","YD-21700-6600","YD-21700-6700"]}
```

---

### battery_package/manage

#### `POST /battery_package/manage/list`
Called 1x

Body:
```json
{
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"batteryPackModel":"PH9200","batteryCellModel":"PY-21700-4600","batteryCellNumber":2,"operator":"吴海森","updateTime":"2026-02-04T05:45:13.000Z","updateTimeUTCSecond":1770183913,"packFactories":["河南鹏辉 "],"supplierIds":"66"},{"batteryPackModel":"PH5000","batteryCellModel":"PH-18650-2500","batteryCellNumber":2,"operator":"吴海森","updateTime":"2026-02-04T05:42:27.000Z","updateTimeUTCSecond":1770183747,"packFactories":["河南鹏辉 "],"supplierIds":"66"},{"batteryPackModel":"
```

---

#### `GET /battery_package/manage/selections`
Called 2x

Response (200):
```json
{"code":0,"msg":"","data":["DEFAULT_BATTERY","XC9000","DT5000","CM6700","FXN4800","PX5000","TP5200","XT5200","PH5000","PB1_SS6211G1_SS621","test","XL4400","YT4400","PH4400","HD4500","XL4500","PH9200","YT4500","PB2_SS6221E1_SS622","JYX13500","JY13500","XL9000","XLYD9000","PB1A_SS6211G2_SS621","JX4500","GY13200","JY13350","JY13200","GD13200","FX4000","JY14400","PB2","PB2-SS121","XK4500","YT4501","GT5200","PB1B_SS6211G3_BW511_Lable","PB2_SS6221E1_SS622-F","PB1B_SS6211G3_BW511","PC1-XL","PC1-PH","PB
```

---

### battery_product_plan/list

#### `POST /battery_product_plan/list`
Called 1x

Body:
```json
{
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":3,"batteryPackModel":"XP4400","batteryCellBatchCode":"WT13579101","batteryPackBatchCode":"TESTTE00000001","createTime":"2026-01-28T09:38:20.000Z","updateTime":"2026-02-03T05:52:05.000Z","startNumber":1,"endNumber":5000,"seqLength":5,"startSn":"TESTTE0000000100001","endSn":"TESTTE0000000105000","planProductNumber":5000,"productNumber":0,"operator":"TEST01","packFactory":"测试用PACK厂"},{"id":2,"batteryPackModel":"XY4500","batteryCellBatchCode":"WT56765764","ba
```

---

### cloud/oem

#### `POST /cloud/oem/order/list`
Called 2x

Body:
```json
{
  "startDate": "",
  "endDate": "",
  "cuid": "",
  "pageIndex": 1,
  "pageSize": 10,
  "oemType": 1
}
```
```json
{
  "startDate": "",
  "endDate": "",
  "cuid": "",
  "pageIndex": 1,
  "pageSize": 10,
  "oemType": 2
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":null,"cuid":"CU0268","staticMonth":"2026-03","amount":71.50,"num":29,"extend":"","tenantId":"xsense","oemType":4,"currency":"$","summuryList":[{"productName":"SDK-基础版-用户维度","num":23,"amount":52.90},{"productName":"SDK-高级版-用户维度","num":6,"amount":18.60}],"customerName":"安室智能","expected":true},{"id":null,"cuid":"CU1725","staticMonth":"2026-03","amount":353.21,"num":169,"extend":"","tenantId":"tianherong","oemType":3,"currency":"$","summuryList":[{"productNam
```

---

#### `POST /cloud/oem/order/param/list`
Called 4x

Body:
```json
{
  "oemType": 1
}
```
```json
{
  "oemType": 2
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"CU0205":"维玺科技","CU1766":"广州癸橡科技有限公司","CU1736":"珺安科技","CU1603":"Provision-ISR","CU1341":"深圳市合创优速商贸有限公司","CU0274":"深圳市悦宝科技有限公司","CU0243":"Adesso Inc","CU0212":"homeguard","CU0245":"深圳捷美科技有限公司","CU1676":"i-alarm","CU1731":"浙江宇视系统技术有限公司","CU1830":"深圳比特微电子科技有限公司","CU0290":"烟台飞源商贸有限公司","CU0470":"B","CU0260":"积加创新"}}
```

---

### cloud/service

#### `POST /cloud/service/product/list`
Called 1x

Body:
```json
{
  "createDate": "",
  "lastModifyDate": "",
  "id": "",
  "cancel": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":4,"productName":"基础版","price":2.30,"cancel":0,"createTime":"2022-09-30","lastModifyTime":"2022-11-02 14:25:28","tierLevel":1,"oemType":1,"currency":"$"},{"id":5,"productName":"高级版","price":3.10,"cancel":0,"createTime":"2022-09-30","lastModifyTime":"2022-10-28 16:02:16","tierLevel":2,"oemType":1,"currency":"$"},{"id":6,"productName":"专业版","price":5.50,"cancel":0,"createTime":"2022-09-30","lastModifyTime":"2022-09-30 16:56:03","tierLevel":3,"oemType":1,"cur
```

---

#### `POST /cloud/service/product/param`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":{"productName":{"4":"基础版","5":"高级版","6":"专业版","10":"SDK-基础版","11":"SDK-高级版","12":"SDK-专业版","13":"SDK-天和荣","14":"单设备-HG","15":"四设备-HG","16":"单设备-monkey","17":"二设备-monkey","18":"SDK-基础版-用户维度","19":"SDK-高级版-用户维度","20":"SDK-专业版-用户维度"},"cancelList":[{"code":0,"name":"服务开始收费"},{"code":1,"name":"服务结束收费"}]}}
```

---

### code-rules/info

#### `GET /code-rules/info/list`
Called 1x

Query:
- `?name=&status=&startDate=&endDate=&pageIndex=1&pageSize=10`

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":27,"name":"测试用电池包SN编码规则","status":1,"createTime":"2026-01-29 06:05:07","updateTime":"2026-01-30 01:31:34"},{"id":26,"name":"KF126QSG规则Q3","status":1,"createTime":"2026-01-17 13:35:04","updateTime":"2026-01-17 13:37:11"},{"id":25,"name":"KF226QSG规则","status":1,"createTime":"2026-01-17 13:24:14","updateTime":"2026-01-17 13:24:20"},{"id":24,"name":"KF126QSG规则G3","status":1,"createTime":"2026-01-17 13:02:30","updateTime":"2026-01-17 13:03:43"},{"id":23,"name"
```

---

### customer/list

#### `POST /customer/list`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":[{"id":861,"name":"深圳市枭泽电子科技有限公司","abbreviation":"枭泽电子","code":"CU0640"},{"id":860,"name":"Ronlight Health LTD","abbreviation":"Ronlight","code":"CU0639"},{"id":859,"name":"深圳市浩方科技有限公司","abbreviation":"浩方科技","code":"CU0638"},{"id":858,"name":"CENOVA BİLİŞİM TEKNOLOJİLERİ İTH. İHR. ve TİC. LTD.","abbreviation":"CENOVA","code":"CU0637"},{"id":857,"name":"深圳涌潮智创科技有限公司","abbreviation":"涌潮智","code":"CU0636"},{"id":856,"name":"眾欣贸易有限公司","abbreviation":"眾欣","code":"CU0635"},{"
```

---

### customer/model

#### `POST /customer/model/list`
Called 1x

Body:
```json
{
  "createDate": "",
  "lastModifyDate": "",
  "modelNo": "",
  "customerId": "",
  "pageSize": 10,
  "pageIndex": 1
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":0,"customerId":861,"createTime":null,"lastModifyTime":null,"cuid":"CU0640","customerName":"深圳市枭泽电子科技有限公司","modelNos":null,"createTimeStr":"","lastModifyTimeStr":"","modelManufacturerDOList":null,"supportManufacturerList":null},{"id":0,"customerId":860,"createTime":null,"lastModifyTime":null,"cuid":"CU0639","customerName":"Ronlight Health LTD","modelNos":null,"createTimeStr":"","lastModifyTimeStr":"","modelManufacturerDOList":null,"supportManufacturerList"
```

---

### customer/policy

#### `GET /customer/policy/list`
Called 1x

Query:
- `?userId=389&tenantId=&iotHostDomain=`

Response (200):
```json
{"code":1,"msg":"can not get policy list without query tenantId","data":null}
```

---

### customer/policycustomer

#### `GET /customer/policycustomer/list`
Called 1x

Query:
- `?userId=389&cuid=&tenantId=&pageIndex=1&pageSize=10`

Response (200):
```json
{"code":0,"msg":"","data":{"total":19,"list":[{"cuid":"CU0205","tenantIds":"dzees,dzeesHome","appNames":"dzees,dzeeshome","customerName":"维玺科技","templateCount":122,"policyCount":68},{"cuid":"CU0212","tenantIds":"homeguardsmart","appNames":"homeguardsmart","customerName":"homeguard","templateCount":122,"policyCount":68},{"cuid":"CU0243","tenantIds":"cyberviewplus","appNames":"CyberView Plus","customerName":"Adesso Inc","templateCount":122,"policyCount":68},{"cuid":"CU0245","tenantIds":"soliompro"
```

---

### customer/policytemplate

#### `GET /customer/policytemplate/list`
Called 1x

Query:
- `?userId=389`

Response (200):
```json
{"code":0,"msg":"","data":{"total":139,"list":[{"templateId":"012ee0eabf71b570413d682c9a18f3b7","templateName":"智能服务协议","templateCode":"awareness-service-agreement","templateTitle":null,"templateContent":"<html lang=\"en\"  class=\"w-e-text-container\"><head><meta charset=\"UTF-8\"><meta http-equiv=\"X-UA-Compatible\" content=\"IE=edge\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\"><link href=\"https://unpkg.com/@wangeditor/editor@latest/dist/css/style.css\" rel=\"st
```

---

### customer/register

#### `POST /customer/register/model/list`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":[{"id":6,"modelNo":"CG1JA","displayModelNo":null,"modelType":1,"sourceId":1,"categoryId":1,"iconUrl":"https://addx-device-config.s3.amazonaws.com/battery/2023/1683603282_G1.png","smallIconUrl":"https://addx-device-config.s3.amazonaws.com/battery/2023/1683603283_G1-1.png","status":3,"iotReleaseStatus":3,"releaseModelType":1,"releaseSourceId":1,"pullRequestUrl":"","createTime":"","lastModifyTime":"2024-03-25 16:34:34","versionTime":0,"componentChoiceVersionTime":0,"compon
```

---

### device/app

#### `POST /device/app/model/list`
Called 1x

Body:
```json
{
  "modelType": "",
  "modelNo": "",
  "categoryId": "",
  "releaseStatus": "",
  "event": "",
  "freeTierId": "",
  "magicpix": "",
  "promotionPeriod": "",
  "createDate": "",
  "lastModifyDate": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":1941,"modelNo":"CG625F-TN2","displayModelNo":null,"modelType":1,"sourceId":1698,"categoryId":1,"iconUrl":"https://addx-device-config.s3.amazonaws.com/modelIcon/1764678184_1764678184CG625F-TN2.png","smallIconUrl":"https://addx-device-config.s3.amazonaws.com/modelIcon/1764678188_1764678188CG625F-TN2.png","status":3,"iotReleaseStatus":3,"releaseModelType":1,"releaseSourceId":1698,"pullRequestUrl":"https://bitbucket-internal.addx.live/projects/PROD/repos/test
```

---

### device/category

#### `POST /device/category/list`
Called 1x

Body:
```json
{
  "categoryId": "",
  "componentGroupId": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":23,"categoryName":"整机(SKU 定义）","releaseCategoryName":"整机(SKU 定义）","categoryCode":"Complete_machine_SKU_definition","createTime":"2026-02-06 11:09:11","lastModifyTime":"2026-02-06 13:58:20","releaseStatus":4,"versionTime":1770357388,"prRequest":"https://bitbucket-internal.addx.live/projects/PROD/repos/test-item-config/pull-requests/8246","releaseVersionTime":1770357388,"componentName":"喂鸟器半成品","releaseStatusStr":"已发布","modelComponentGroupDOList":null,"mode
```

---

#### `POST /device/category/param/list`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":{"modelCategoryList":[{"id":1,"categoryName":"摄像机-后会改为低功耗Wi-Fi摄像机","categoryCode":"1"},{"id":2,"categoryName":"门铃","categoryCode":"2"},{"id":3,"categoryName":"Sub-G室内叮咚","categoryCode":"3"},{"id":4,"categoryName":"基站","categoryCode":"4"},{"id":6,"categoryName":"电池","categoryCode":"5"},{"id":7,"categoryName":"摄像机套装","categoryCode":"camera_kit"},{"id":8,"categoryName":"太阳能板","categoryCode":"SP"},{"id":9,"categoryName":"低功耗4G摇头摄像机","categoryCode":"battery_4g_ptz_cam"},{"id
```

---

### device/model

#### `POST /device/model/battery/list`
Called 1x

Body:
```json
{
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":66,"batteryCode":"XP4400","voltameterType":"CW2017","battery":4400,"url":"https://addx-device-config.s3.amazonaws.com/battery/2025/1765780468_XP4400_CW2017.txt","releaseBatteryCode":"XP4400","releaseVoltameterType":"CW2017","releaseBattery":4400,"releaseUrl":"https://addx-device-config.s3.amazonaws.com/battery/2025/1765780468_XP4400_CW2017.txt","releaseStatus":3,"linkUrl":"https://bitbucket-internal.addx.live/projects/PROD/repos/test-item-config/pull-requ
```

---

#### `POST /device/model/list`
Called 1x

Body:
```json
{
  "createDate": "",
  "lastModifyDate": "",
  "releaseStatus": "",
  "manufacturerId": "",
  "modelType": "",
  "modelNo": "",
  "categoryId": "",
  "component": "",
  "displayModelNo": "",
  "customerCode": "",
  "relationModel": "",
  "param": "",
  "materialNo": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":1941,"modelNo":"CG625F-TN2","displayModelNo":"CG6M","modelType":1,"sourceId":1698,"categoryId":1,"iconUrl":"https://addx-device-config.s3.amazonaws.com/modelIcon/1764678184_1764678184CG625F-TN2.png","smallIconUrl":"https://addx-device-config.s3.amazonaws.com/modelIcon/1764678188_1764678188CG625F-TN2.png","status":3,"iotReleaseStatus":3,"releaseModelType":1,"releaseSourceId":1698,"pullRequestUrl":"https://bitbucket-internal.addx.live/projects/PROD/repos/te
```

---

#### `POST /device/model/listByType`
Called 1x

Body:
```json
{
  "modelType": 0
}
```

Response (200):
```json
{"code":0,"msg":"","data":[{"modelNo":"CG1","modelType":0},{"modelNo":"CG121","modelType":0},{"modelNo":"CG721","modelType":0},{"modelNo":"CG110-A","modelType":0},{"modelNo":"CG122","modelType":0},{"modelNo":"CB0","modelType":0},{"modelNo":"CG521","modelType":0},{"modelNo":"CG621","modelType":0},{"modelNo":"CG621-W","modelType":0},{"modelNo":"CB120B","modelType":0},{"modelNo":"CB320","modelType":0},{"modelNo":"CG522","modelType":0},{"modelNo":"CG121-A","modelType":0},{"modelNo":"CG410-A","modelT
```

---

### device/release

#### `GET /device/release/release/tasks/-1`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":13366,"branchName":"testItem/20250822115536","evn":1,"modelNo":"1367","userId":278,"type":9,"keyId":0,"releaseStatus":0,"createUserName":"吴海森","createTimestamp":"2025-08-22 03:55:36","createTimestampMillis":1755834936000,"prUrl":"","itemName":"CG625-BD2-TNBD"},{"id":13373,"branchName":"testItem/20250823123910","evn":1,"modelNo":"1722","userId":278,"type":9,"keyId":0,"releaseStatus":0,"createUserName":"吴海森","createTimestamp":"2025-08-23 04:39:11","createTi
```

---

### device/sim

#### `POST /device/sim/list`
Called 1x

Body:
```json
{
  "manufacturerId": "",
  "relationCuid": "",
  "iccid": "",
  "imei": "",
  "activateStatus": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"iccid":"89430101522291369282","userSn":"AICSD2G7DXB0028","serialNumber":"603463d0ebf93ee009db0581a0b15e75","productionTime":1712057132,"relationManufacturerId":32,"relationCuid":null,"imei":"867507060184831","activateStatus":null,"activateTime":null},{"iccid":"89430101522291369282","userSn":"AICSD2G7DXB0082","serialNumber":"721ecc524b2cbf8a65c20ef99e1d3d67","productionTime":1714018186,"relationManufacturerId":32,"relationCuid":null,"imei":"867507060184393","a
```

---

### devide/coefficient

#### `POST /devide/coefficient/cuid/list`
Called 1x

Body:
```json
{
  "name": "",
  "type": "",
  "whetherCashBack": "",
  "configStatus": "",
  "customerChannelList": [],
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":0,"cuid":"CU0640","manufacturerId":null,"type":null,"coefficient":null,"whetherCashBack":null,"cashBack":null,"shareAmount":null,"mdate":null,"fromMonth":null,"db3ActiveValidityPeriodStart":null,"db3ActiveValidityPeriodEnd":null,"db3ActivationReward":null,"db3Coefficient":null,"isPoweredIncentiveEnabled":null,"poweredIncentiveAmount":null,"poweredIncentiveStartDate":null,"poweredIncentiveEndDate":null,"currency":null,"registerRegionRestrictionEnable":null
```

---

#### `POST /devide/coefficient/manufacturer/list`
Called 1x

Body:
```json
{
  "name": "",
  "type": "",
  "whetherCashBack": "",
  "configStatus": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":0,"cuid":null,"manufacturerId":1,"type":null,"coefficient":null,"whetherCashBack":null,"cashBack":null,"shareAmount":null,"mdate":null,"fromMonth":null,"db3ActiveValidityPeriodStart":null,"db3ActiveValidityPeriodEnd":null,"db3ActivationReward":null,"db3Coefficient":null,"isPoweredIncentiveEnabled":null,"poweredIncentiveAmount":null,"poweredIncentiveStartDate":null,"poweredIncentiveEndDate":null,"currency":null,"registerRegionRestrictionEnable":null,"addit
```

---

### devide/cost

#### `POST /devide/cost/param/list`
Called 1x

Body:
```json
{
  "yearList": [
    "2026"
  ],
  "devideType": 0
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"devideCostDOList":[{"id":49,"staticYear":"2026","staticMonth":"2026-01","monthIndex":1,"cost":190000.00,"releaseStatus":2,"coefficient":null,"devideType":0},{"id":50,"staticYear":"2026","staticMonth":"2026-02","monthIndex":2,"cost":0.00,"releaseStatus":2,"coefficient":null,"devideType":0},{"id":51,"staticYear":"2026","staticMonth":"2026-03","monthIndex":3,"cost":0.00,"releaseStatus":2,"coefficient":null,"devideType":0},{"id":52,"staticYear":"2026","staticMonth":"2026-
```

---

### devide/sim

#### `POST /devide/sim/coefficient/list`
Called 1x

Body:
```json
{
  "customerType": 0,
  "cuid": "",
  "manufacturerId": "",
  "statisticsMode": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":19,"cuid":"CU1424","customerName":"长视(广州)有限公司","manufacturerId":0,"manufacturerName":null,"type":1,"coefficient":38,"shareAmount":0.000000,"validStartDate":1742515200,"validEndDate":1773964800,"validStatus":2,"devideSimActivationIncentive":{"id":6,"activationIncentiveValidStartDate":"2025-12-09","activationIncentiveValidEndDate":"2026-12-08","activationIncentiveAmount":6.020000},"createTime":"2025-03-22 12:10:16","updateTime":"2025-03-25 04:40:41","fromMo
```

---

### factory_integration/manage

#### `GET /factory_integration/manage/integrationType/selection`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":[{"code":0,"name":"文件上传"},{"code":1,"name":"接口调用"}]}
```

---

#### `POST /factory_integration/manage/list`
Called 1x

Body:
```json
{
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":2,"manufacturerId":88,"manufacturerName":"测试用电芯厂","productType":0,"productTypeName":"电芯","integrationType":0,"integrationTypeName":"文件上传","createTime":"2026-01-28T08:21:19.000Z","updateTime":"2026-01-28T08:21:19.000Z","operator":"张源盛"},{"id":1,"manufacturerId":87,"manufacturerName":"测试用PACK厂","productType":1,"productTypeName":"电池包","integrationType":0,"integrationTypeName":"文件上传","createTime":"2026-01-28T08:08:52.000Z","updateTime":"2026-01-28T08:08:52.00
```

---

#### `GET /factory_integration/manage/productType/selection`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":[{"code":0,"name":"电芯"},{"code":1,"name":"电池包"}]}
```

---

### factory/devices

#### `POST /factory/devices/cuid`
Called 1x

Body:
```json
{
  "manufactureTimeStart": 1770048000,
  "manufactureTimeEnd": 1772553599,
  "pageSize": 10,
  "pageIndex": 1
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[],"total":0,"checkDownloadResult":null,"totalModelNos":["CG721","CM1C","CG621C","CM1A","CB227C","CB061D1","KG125A1","KG125C","DB121A1","CK160C-BD","KG125D","CL060C","CL060D","CB227TC","12","CB160","CG121C","CM2C","CM2A","CG631-BD","CG121D","HB181SC","DB121B1","801DA1","CK127SC-BD","CG625-BD","CG121-A","CG623G-BD","CB260C","SS121B","CB260D","SS121A","CG410-A","CG630-W","CX124","CG623H1","CB060D1","CG625-BD2","CG625-BD1","CG1","DB121A","CG3","CB140-A","CG2","CG4"
```

---

### factory/firmware

#### `GET /factory/firmware/build-platform/list`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":[{"id":18,"buildPlatform":"AK3918AV100N-AIC8800D40L"},{"id":13,"buildPlatform":"AK3918AV100N-AIC8800DL"},{"id":15,"buildPlatform":"AK3918AV100N-ATBM6012B-X"},{"id":21,"buildPlatform":"AK3918AV100N-ATBM6132"},{"id":11,"buildPlatform":"AK3918AV100N-RTL8188"},{"id":27,"buildPlatform":"AK3918EV300L-ATBM6012B-X"},{"id":28,"buildPlatform":"AK3918EV300L-ATBM6132"},{"id":42,"buildPlatform":"EFR32MG2-HDM8A512B"},{"id":44,"buildPlatform":"EFR32MG2-NDP115A0"},{"id":43,"buildPlatfo
```

---

#### `GET /factory/firmware/list`
Called 1x

Query:
- `?modelNo=&firmwareId=&pageIndex=1&pageSize=10`

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"modelNo":"0.17.0","firmwareId":"0.17.0"},{"modelNo":"BX150","firmwareId":"1.3.0"},{"modelNo":"CB0","firmwareId":"0.5.0"},{"modelNo":"CB027C","firmwareId":"1.14.0"},{"modelNo":"CB027TC","firmwareId":"1.14.0"},{"modelNo":"CB040B","firmwareId":"0.4.1"},{"modelNo":"CB040B1","firmwareId":"0.4.1"},{"modelNo":"CB040C","firmwareId":"0.13.0"},{"modelNo":"CB060","firmwareId":"1.1.2"},{"modelNo":"CB060C","firmwareId":"1.12.15"}],"total":173}}
```

---

### factory/test-item-result

#### `POST /factory/test-item-result/export-info`
Called 1x

Body:
```json
{
  "componentModelNos": "",
  "createTimestampEnd": "",
  "createTimestampStart": "",
  "derivationModelNos": "",
  "displayModelNos": "",
  "manufacturerIds": "",
  "modelNos": "",
  "produceArtId": ""
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"total":null,"checkDownloadResult":null,"totalManufacturerIds":[0,64,65,4,5,6,10,12,27,30,32,33,34,35,37,39,40,41,42,43,44,45,46,47,48,49,50,51,52,53,57,58,59,61],"totalManufacturerId2NameMap":{"64":"越南律笙实业有限公司","65":"珠海美安宝电子科技有限公司","4":"深圳市延创兴电子有限公司","5":"深圳中安讯视科技有限公司","6":"深圳市盈润佳电子有限公司","10":"江西佳信捷智能装备有限公司","12":"深圳市将帅科技有限公司","27":"东莞市明宏凯实业有限公司","30":"深圳富特科数字科技有限公司","32":"深圳研发自用","33":"深圳市诚安电子有限公司","34":"深圳市赛腾智能科技有限公司","35":"深圳市微视科智能技术有限公司","37":"深圳市天诺安防有限公司","39":"深
```

---

### filecenter/file

#### `POST /filecenter/file/list`
Called 1x

Body:
```json
{
  "fileTypeCode": "IQ_file",
  "fileName": ""
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"files":[{"fileId":"example-file","fileTypeCode":"IQ_file","fileName":"example.iq","available":true,"updateTime":"2024-11-30 04:44:24","url":"https://example.invalid/fileCenter/IQ_file/example"}]}}
```

---

### filecenter/type

#### `POST /filecenter/type/list`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":{"fileTypes":[{"code":"IQ_file","name":"IQ文件（弃用）"},{"code":"iqFile","name":"IQ文件"},{"code":"watermark","name":"录像水印"}]}}
```

---

### iot-tool/apiTasks

#### `GET /iot-tool/apiTasks`
Called 1x

Response (200):
```json
{"code":0,"data":[{"cron":"1 30 * * * ?","nodeName":"prod-eu","path":"/work/broadcast","lastExecutedBy":"revenueSharing:ac9dee53-f6d3-463c-9d19-c4a3c3528ba3","method":"POST","createTime":1743386600179,"callCount":1,"active":false,"locked":false,"taskId":"3d24c3edb3894b51b30613b607794245"},{"cron":"1 30 * * * ?","nodeName":"prod-us","path":"/work/broadcast","lastExecutedBy":"revenueSharing:205a8c41-00e7-400c-a310-57b6eb1b2d49","method":"POST","createTime":1741847381679,"callCount":8,"active":fals
```

---

### iot-tool/invokeMarketingApi

#### `POST /iot-tool/invokeMarketingApi`
Called 2x

Query:
- `?accountId=paas_owned&tenantId=paas_owned&nodeName=prod-us&path=%2Finner-api%2Fcreative%2FgetSlots%3Fpage%3D1%26pageSize%3D10&method=POST`
- `?accountId=paas_owned&tenantId=paas_owned&nodeName=prod-us&path=%2Finner-api%2Fcreative%2FgetCreatives%3Fpage%3D1%26pageSize%3D10&method=POST`

Body:
```json
{}
```

Response (200):
```json
{"result":0,"msg":"Success","data":{"total":2,"list":[{"id":2,"name":"home_promo_popup","layouts":"multi_media_container,product_picker,feedback_list","description":"首页通用运营弹窗位","createTime":1732246557,"updateTime":1732246557},{"id":1,"name":"home_promo_banner","layouts":"multi_media_container,product_picker,feedback_list","description":"首页通用运营位","createTime":1732244831,"updateTime":1732244831}]}}
```

---

### iot-tool/invokePaasApi

#### `POST /iot-tool/invokePaasApi`
Called 4x

Query:
- `?accountId=paas_owned&tenantId=paas_owned&nodeName=prod-us&path=%2Finner-api%2Fcreative%2FgetSlots%3Fpage%3D1%26pageSize%3D10&method=POST`
- `?accountId=paas_owned&tenantId=paas_owned&nodeName=staging-us&path=%2Fwork%2FqueryNetTestTargetServers&method=GET`
- `?accountId=paas_owned&tenantId=paas_owned&nodeName=staging-cn&path=%2Fwork%2FqueryProductConfig&method=GET`

Body:
```json
{}
```

Response (200):
```json
{"result":0,"msg":"Success","data":{"total":7,"list":[{"id":7,"name":"home_no_1_protection","layouts":"video_image,image_carousel","description":"","createTime":1767950477,"updateTime":1767950477},{"id":6,"name":"after_home_promo_bird","layouts":"video_image,image_carousel","description":"","createTime":1735289261,"updateTime":1735289261},{"id":5,"name":"after_home_promo","layouts":"video_image,image_carousel","description":"营销跳转至的购买页","createTime":1732247450,"updateTime":1732247450},{"id":4,"na
```

---

### iot-tool/listExchangeCodeDisplay

#### `POST /iot-tool/listExchangeCodeDisplay`
Called 1x

Body:
```json
{
  "page": 1,
  "pageSize": 10,
  "deviceCategory": "",
  "modelNo": "",
  "lastModifyDate": ""
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"total":23,"list":[{"id":26,"uuid":"4c5e4290-ddbf-4274-81ca-5bcf40446b88","deviceCategory":"14","modelNo":null,"createTime":"2026-02-09T06:27:12.000+00:00","updateTime":"2026-02-09T06:27:12.000+00:00","productName":"喂鸟器-单设备-永久","tierId":1004,"codeNum":{"totalNum":80,"availableNum":80,"usedNum":0,"disabledNum":0},"modelNoList":["KF226-P7G1P2"]},{"id":25,"uuid":"58d4d3a4-9188-412f-a10d-864a594e1e9e","deviceCategory":"14","modelNo":null,"createTime":"2026-01-15T08:50:13.0
```

---

### iot-tool/queryAllProducts

#### `POST /iot-tool/queryAllProducts`
Called 1x

Body:
```json
{
  "nodeName": "staging-cn"
}
```

Response (200):
```json
{"code":0,"data":[{"rollingDays":15,"tierType":0,"level":1,"subscriptionGroupId":"","subject":"基础版-1个月","tierServiceType":0,"keyId":0,"storage":3221225472,"body":"基础版-1个月","type":0,"subscriptionPeriod":0,"cdate":"2019-09-17T21:55:35.000+00:00","tierId":1,"size":3,"month":1,"mdate":"2019-09-17T21:55:37.000+00:00","price":1800,"tenantId":"vicoo","currency":0,"id":20000,"showInTier":1,"status":0},{"rollingDays":15,"tierType":0,"level":1,"subscriptionGroupId":"","subject":"基础版-3个月","tierServiceType"
```

---

### iot/manage

#### `POST /iot/manage/serve/node`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":[{"evn":"Prod","nodeList":[{"node":"prod-us","name":"美国节点","host":"https://api-us.addx.live"},{"node":"prod-eu","name":"欧洲节点","host":"https://api-eu.addx.live"},{"node":"prod-cn","name":"中国节点","host":"https://api.addx.live"}]},{"evn":"Pre","nodeList":[{"node":"pre-us","name":"美国节点","host":"https://api-pre-us.addx.live"},{"node":"pre-eu","name":"欧洲节点","host":"https://api-pre-eu.addx.live"},{"node":"pre-cn","name":"中国节点","host":"https://api-pre.addx.live"}]},{"evn":"Stage
```

---

### iot/user

#### `POST /iot/user/subscription/param`
Called 1x

Body:
```json
{}
```

Response (200):
```json
{"code":0,"msg":"","data":{"serverMap":{"pre":["pre-cn","pre-eu","pre-us"],"prod":["prod-cn","prod-eu","prod-us"],"staging":["staging-cn","staging-eu","staging-us"]}}}
```

---

#### `POST /iot/user/vip/param`
Called 1x

Body:
```json
{}
```

Response (200):
```json
{"code":0,"msg":"","data":{"tierServiceTypeMap":{"0":"云服务套餐","1":"4G套餐"},"serverMap":{"pre":["pre-cn","pre-eu","pre-us"],"prod":["prod-cn","prod-eu","prod-us"],"staging":["staging-cn","staging-eu","staging-us"]}}}
```

---

### login

#### `POST /login`
Called 1x

Body:
```json
{
  "password": "<password>",
  "phone": "jchen"
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"userId":389,"userToken":"2bd27bf8172b4669ad83f24fc6a7d5dc","phone":"","email":"jchen@a4x.io","ldapCn":"jchen","userName":"陈敬敏","type":88,"customerType":-1,"cuid":"","manufacturerId":0,"companyName":"A4X","isServerUser":true,"userPermission":{"lastUpdateTime":1772503583024,"enablePageIds":["ModelDetail","partsGroupManagement","testItemLogs","testItemManagement","production","testItemDetail","CheckPartGroupDetail","product","editTestItem","partsManagement","CheckPartDet
```

---

### manage/list

#### `GET /manage/list/manufacturer/param`
Called 6x

Response (200):
```json
{"code":0,"msg":"","data":[{"id":1,"name":"addx","code":"addx","type":0},{"id":2,"name":"深圳市罡扇广联电子科技有限公司","code":"CU1291","type":0},{"id":3,"name":"深圳市达创威视科技有限公司","code":"CU1311","type":0},{"id":4,"name":"深圳市延创兴电子有限公司","code":"CU1307","type":0},{"id":5,"name":"深圳中安讯视科技有限公司","code":"CU1304","type":0},{"id":6,"name":"深圳市盈润佳电子有限公司","code":"CU1139","type":0},{"id":7,"name":"深圳市俊明视电子科技有限公司","code":"CU1150","type":0},{"id":8,"name":"深圳市兴未来智能科技有限公司","code":"CU1362","type":0},{"id":9,"name":"深圳市沃安电子有限公司
```

---

#### `POST /manage/list/param`
Called 7x

Body:
```json
{
  "typeList": [
    "releaseStatus",
    "component",
    "manufacturer",
    "modelType",
    "deviceCategory",
    "customerChannel",
    "deviceModel",
    "releaseModelStatus"
  ]
}
```
```json
{
  "typeList": [
    "releaseStatus",
    "modelType",
    "deviceCategory",
    "releaseModelStatus"
  ]
}
```
```json
{
  "typeList": [
    "releaseStatus",
    "component",
    "manufacturer",
    "componentGroup",
    "deviceCategory"
  ]
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"部件名称":[{"code":1,"name":"测试数据"},{"code":2,"name":"无指示灯"},{"code":3,"name":"T31ZL"},{"code":4,"name":"850nm红外灯"},{"code":5,"name":"测试电机1"},{"code":6,"name":"CM1A"},{"code":7,"name":"CM1A1"},{"code":8,"name":"CM1C"},{"code":9,"name":"CM2C"},{"code":10,"name":"CM2A"},{"code":11,"name":"CM2A1"},{"code":12,"name":"测试数据1"},{"code":13,"name":"24BYJ28"},{"code":14,"name":"24BYJ48"},{"code":15,"name":"格科微GC2083"},{"code":16,"name":"格科微GC2063"},{"code":17,"name":"晶相F37"},{"code
```

---

### matter/cdCert

#### `POST /matter/cdCert/list`
Called 1x

Body:
```json
{
  "vid": "",
  "pid": "",
  "type": null,
  "modelNoLike": ""
}
```

Response (200):
```json
{"code":0,"msg":"","data":[{"id":2,"vid":"140D","pid":"0001","cdCertification":"MIIBszCCAVqgAwIBAgIIRdrzneR6oI8wCgYIKoZIzj0EAwIwKzEpMCcGA1UEAwwgTWF0dGVyIFRlc3QgQ0QgU2lnbmluZyBBdXRob3JpdHkwIBcNMjEwNjI4MTQyMzQzWhgPOTk5OTEyMzEyMzU5NTlaMCsxKTAnBgNVBAMMIE1hdHRlciBUZXN0IENEIFNpZ25pbmcgQXV0aG9yaXR5MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEPDmJIkUrVcrzicJb0bykZWlSzLkOiGkkmthHRlMBTL+V1oeWXgNrUhxRA35rjO3vyh60QEZpT6CIgu7WUZ3suqNmMGQwEgYDVR0TAQH/BAgwBgEB/wIBATAOBgNVHQ8BAf8EBAMCAQYwHQYDVR0OBBYEFGL6gjNZrPqplj4c+hQK
```

---

#### `POST /matter/cdCert/params`
Called 1x

Body:
```json
{}
```

Response (200):
```json
{"code":0,"msg":"","data":{"modelNoList":{"0":["SS0111W1"],"1":["SS011"]},"vidList":["1506","140D"],"pidList":["1011","0001"]}}
```

---

### matter/queryModelDacRemainDetail

#### `POST /matter/queryModelDacRemainDetail`
Called 1x

Body:
```json
{
  "modelNoPtn": "",
  "page": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"page":1,"pageSize":10,"total":3,"list":[{"modelNo":"SS011","expectNum":4580,"generateNum":4580,"allocateNum":3427,"remainGenerateNum":1153,"alertRule":{"modelNo":null,"minRemainNum":500,"alertUserIds":[264],"alertEmails":[],"enable":true,"operationUserId":264,"alertUserIdsJson":"[264]","alertEmailsJson":"[]"},"isAlert":false},{"modelNo":"CS-HB1","expectNum":230,"generateNum":230,"allocateNum":162,"remainGenerateNum":68,"alertRule":{"modelNo":null,"minRemainNum":0,"ale
```

---

### matter/queryModelTypeAndModelNos

#### `POST /matter/queryModelTypeAndModelNos`
Called 1x

Body:
```json
{}
```

Response (200):
```json
{"code":0,"msg":"","data":{"0":["SS0111W1","SS0111W2"],"1":["SS011","CS-HB1","KS-HB1"]}}
```

---

### model/component

#### `POST /model/component/group/list`
Called 1x

Body:
```json
{
  "componentGroup": "",
  "categoryId": "",
  "modelComponentGroupParam": "",
  "relatedWorkstation": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":67,"componentGroupName":"喂鸟器半成品","componentGroupCode":"semi_birdfeeder","paramNum":0,"createTime":"2026-02-04 19:08:51","lastModifyTime":"2026-02-04 19:10:54","releaseStatus":4,"userId":297,"remark":"","versionTime":1770203331,"releaseComponentGroupName":"喂鸟器半成品","releaseRemark":"","storeVersion":null,"prRequest":"","releaseVersionTime":1770203331,"iconId":5,"businessType":0,"relatedBusiness":0,"releaseBusinessType":0,"releaseRelatedBusiness":0,"categoryS
```

---

#### `POST /model/component/group/list/param`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":{"modelComponentGroupDOList":[{"id":1,"componentGroupName":"套装门铃","componentGroupCode":"Set_Doorbell","paramNum":1,"createTime":"2023-02-23 17:01:02","lastModifyTime":"2023-03-28 14:24:55","releaseStatus":1,"userId":287,"remark":"别动，别删","versionTime":1677465440,"releaseComponentGroupName":"套装门铃","releaseRemark":"","storeVersion":null,"prRequest":"http://192.168.31.7:7990/projects/PROD/repos/test-item-config/pull-requests/597","releaseVersionTime":1677465440,"iconId":6,"
```

---

#### `POST /model/component/info`
Called 1x

Body:
```json
{
  "id": ""
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"id":null,"componentName":null,"componentGroupName":null,"componentCode":null,"componentGroupId":null,"modelTypeName":null,"modelTypeIdList":null,"remark":null,"paramIds":null,"releaseStatus":null,"groupParamDOList":null,"modelComponentGroupDOList":[{"id":1,"componentGroupName":"套装门铃","componentGroupCode":"Set_Doorbell","paramNum":1,"createTime":"2023-02-23 17:01:02","lastModifyTime":"2023-03-28 14:24:55","releaseStatus":1,"userId":287,"remark":"别动，别删","versionTime":16
```

---

#### `POST /model/component/list`
Called 1x

Body:
```json
{
  "component": "",
  "modelType": "",
  "componentGroup": "",
  "param": "",
  "supplierId": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":277,"componentName":"CGA25-A4X","componentCode":"CGA25-A4X","componentGroupId":33,"paramNum":0,"createTime":"2026-02-09 14:07:36","lastModifyTime":"2026-02-12 10:59:48","releaseStatus":4,"remark":"","versionTime":1770617256,"releaseComponentName":"CGA25-A4X","releaseRemark":"","storeVersion":null,"prRequest":"https://bitbucket-internal.addx.live/projects/PROD/repos/test-item-config/pull-requests/8249","releaseVersionTime":1770617256,"uniqCodeRuleIds":"","
```

---

#### `POST /model/component/list/param`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":{"modelComponentGroupDOList":[{"id":1,"componentGroupName":"套装门铃","componentGroupCode":"Set_Doorbell","paramNum":1,"createTime":"2023-02-23 17:01:02","lastModifyTime":"2023-03-28 14:24:55","releaseStatus":1,"userId":287,"remark":"别动，别删","versionTime":1677465440,"releaseComponentGroupName":"套装门铃","releaseRemark":"","storeVersion":null,"prRequest":"http://192.168.31.7:7990/projects/PROD/repos/test-item-config/pull-requests/597","releaseVersionTime":1677465440,"iconId":6,"
```

---

#### `POST /model/component/param/master/list`
Called 1x

Body:
```json
{
  "releaseStatus": "",
  "componentId": "",
  "categoryId": "",
  "componentGroupId": "",
  "createDate": "",
  "lastModifyDate": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[],"total":0}}
```

---

### pack_factory/manage

#### `GET /pack_factory/manage/getPackFactories`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":[{"key":1,"value":"addx"},{"key":53,"value":"广东信隆新能源有限公司"},{"key":87,"value":"测试用PACK厂"},{"key":89,"value":"测试用PACK厂A"}]}
```

---

#### `POST /pack_factory/manage/list`
Called 1x

Body:
```json
{
  "packFactoryId": 1
}
```

Response (200):
```json
{"code":0,"msg":"","data":[{"batteryCellModelFactoryId":90,"batteryCellModelFactoryName":"测试用电芯厂A","batteryCellModels":["TP-16340","TP-26650"],"selectedBatteryCellModels":["TP-16340","TP-26650"]}]}
```

---

### pack_warehousing/manage

#### `POST /pack_warehousing/manage/list`
Called 1x

Body:
```json
{
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":1,"batteryCellModel":"TP-18650","batteryCellBatchCode":"WT12345678","batteryCellFactoryName":"测试用电芯厂","batteryCellFactoryId":88,"createTime":"2026-01-28T02:09:19.000Z","updateTime":"2026-01-30T06:34:02.000Z","warehousingNumber":10000,"remainBatteryCellNum":0,"operator":"张源盛","packFactoryName":"测试用PACK厂","packFactoryId":87},{"id":3,"batteryCellModel":"TP-16340","batteryCellBatchCode":"WT12527572","batteryCellFactoryName":"测试用电芯厂A","batteryCellFactoryId":90
```

---

### permission/query_client_role

#### `POST /permission/query_client_role`
Called 2x

Body:
```json
{
  "headers": {
    "Content-Type": "application/json;",
    "Accept": "*/*"
  }
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"roles":[{"roleId":0,"roleName":"渠道客户","type":0,"customerType":0,"rolePages":[{"roleId":0,"pageId":"Cloud","pageName":"","operations":[],"parameters":{}},{"roleId":0,"pageId":"sharing","pageName":"","operations":[],"parameters":{}}],"cdate":"2022-12-15 10:57:45","mdate":"2024-01-19 16:35:46","orgId":null},{"roleId":1,"roleName":"工厂客户","type":0,"customerType":1,"rolePages":[{"roleId":1,"pageId":"Cloud","pageName":"","operations":[],"parameters":{}},{"roleId":1,"pageId":
```

---

### permission/query_server_org

#### `POST /permission/query_server_org`
Called 2x

Body:
```json
{
  "headers": {
    "Content-Type": "application/json;",
    "Accept": "*/*"
  }
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"orgId":"a4x","orgName":"A4X","parentOrgId":null,"adminUserId":null,"orgRoles":null,"users":[],"orgs":[{"orgId":"a4xorg_admin","orgName":"行政部","parentOrgId":"a4x","adminUserId":null,"orgRoles":null,"users":[{"userId":246,"ldapCn":"hdong","userName":"董涵","phone":"","email":"hdong@addx.ai","status":0,"customerType":-1,"userRoles":null,"cdate":"2023-02-24 16:21:00","mdate":"2025-10-24 17:21:23"},{"userId":379,"ldapCn":"wzhao","userName":"赵雯","phone":"","email":"wzhao@a4x.
```

---

### permission/query_server_org_detail

#### `POST /permission/query_server_org_detail`
Called 1x

Body:
```json
{
  "orgId": "a4xorg_admin"
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"orgId":"a4xorg_admin","orgName":"行政部","parentOrgId":"a4x","adminUserId":null,"orgRoles":[],"users":null,"orgs":null,"rolePages":[],"operations":[],"cdate":"2023-02-24 16:21:01","mdate":"2023-02-24 16:21:01"}}
```

---

### permission/query_server_role

#### `POST /permission/query_server_role`
Called 1x

Body:
```json
{
  "headers": {
    "Content-Type": "application/json;",
    "Accept": "*/*"
  }
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"roles":[{"roleId":2,"roleName":"CodecProfile配置","type":1,"customerType":-1,"rolePages":[{"roleId":2,"pageId":"tempTools","pageName":"","operations":[],"parameters":{}},{"roleId":2,"pageId":"RecordingCodecConfig","pageName":"","operations":[],"parameters":{}}],"cdate":"2022-12-15 10:57:45","mdate":"2025-09-26 14:45:03","orgId":null},{"roleId":3,"roleName":"TPM","type":1,"customerType":-1,"rolePages":[{"roleId":3,"pageId":"ModelDetail","pageName":"","operations":[],"par
```

---

### produce-plan/bind-art

#### `POST /produce-plan/bind-art/manage/list`
Called 1x

Query:
- `?produceArtIds=&modelNoCategoryIds=&modelNoLike=&status=&pageSize=10&page=1`

Body:
```json
{}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"modelNo":"CB027C","artPlanId":11,"modelNoCategoryName":"PCBA型号","categoryName":"-","statusName":"未配置","status":1,"produceArtCount":0,"totalProduceArtCount":1},{"modelNo":"CB127C","artPlanId":11,"modelNoCategoryName":"PCBA型号","categoryName":"-","statusName":"未配置","status":1,"produceArtCount":0,"totalProduceArtCount":1},{"modelNo":"CG121JA","artPlanId":11,"modelNoCategoryName":"整机型号","categoryName":"-","statusName":"未配置","status":1,"produceArtCount":0,"totalPro
```

---

### produce/art

#### `POST /produce/art/all-produce-art-ability`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":8,"name":"软件升级","createTimestamp":"2023-07-11T19:29:41.000+00:00","updateTimestamp":"2023-07-11T19:29:41.000+00:00"},{"id":1,"name":"测试","createTimestamp":"2023-07-11T19:29:40.000+00:00","updateTimestamp":"2023-07-11T19:29:40.000+00:00"},{"id":2,"name":"打印","createTimestamp":"2023-07-11T19:29:40.000+00:00","updateTimestamp":"2023-07-11T19:29:40.000+00:00"},{"id":3,"name":"登记","createTimestamp":"2023-07-11T19:29:40.000+00:00","updateTimestamp":"2023-07-11T
```

---

#### `POST /produce/art/query-produce-art-list`
Called 5x

Query:
- `?abilityId=1`
- `?abilityId=4`
- `?abilityId=&artNameLike=`

Body:
```json
{}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":111,"name":"镜头模组绑定","artAbilityIdList":",4,1,","displayModelTypeList":null,"ext":"{\"code\":4}","code":4,"needPassCheck":0,"publishStatus":1,"createTimestamp":"2026-03-02T08:29:30.000+00:00","updateTimestamp":"2026-03-02T08:29:30.000+00:00","inProduceCodeType":2},{"id":110,"name":"整机气密性工站","artAbilityIdList":",1,","displayModelTypeList":null,"ext":"{\"code\":1}","code":1,"needPassCheck":0,"publishStatus":1,"createTimestamp":"2026-02-26T09:28:05.000+00:00"
```

---

### produce/model-plan

#### `POST /produce/model-plan/query-manufacturer-list`
Called 1x

Query:
- `?manufacturerId=&modelNoCategory=&modelNoLike=&producePlanLike=&page=1&pageSize=10`

Body:
```json
{}
```

Response (200):
```json
{"code":0,"msg":"","data":{"total":78,"list":[{"manufacturerId":1,"modelNoCount":143,"totalModelNoCount":181},{"manufacturerId":2,"modelNoCount":2,"totalModelNoCount":5},{"manufacturerId":3,"modelNoCount":5,"totalModelNoCount":14},{"manufacturerId":4,"modelNoCount":2,"totalModelNoCount":5},{"manufacturerId":5,"modelNoCount":3,"totalModelNoCount":5},{"manufacturerId":6,"modelNoCount":1,"totalModelNoCount":10},{"manufacturerId":7,"modelNoCount":1,"totalModelNoCount":1},{"manufacturerId":8,"modelNo
```

---

### produce/plan

#### `POST /produce/plan/query-default-produce-plan-list`
Called 1x

Query:
- `?manufacturerId=&modelNoCategory=&modelNoLike=&producePlanLike=&page=1&pageSize=10`

Body:
```json
{}
```

Response (200):
```json
{"code":0,"msg":"","data":{"total":59,"list":[{"id":69,"name":"整机测试工艺（SKU定义）","modelNoCount":2,"manufacturerCount":1,"totalManufacturerCount":0,"publishStatus":1},{"id":68,"name":"喂鸟器半成品工艺方案","modelNoCount":3,"manufacturerCount":1,"totalManufacturerCount":0,"publishStatus":1},{"id":67,"name":"方案产品基站测试方案","modelNoCount":1,"manufacturerCount":2,"totalManufacturerCount":0,"publishStatus":1},{"id":66,"name":"SS121SP1太阳能整机测试方案","modelNoCount":1,"manufacturerCount":3,"totalManufacturerCount":0,"publis
```

---

### register/list

#### `POST /register/list`
Called 2x

Body:
```json
{
  "startDate": "",
  "endDate": "",
  "customerName": "",
  "customerType": 0,
  "summaryPeriod": 1,
  "pageSize": 10,
  "pageIndex": 1
}
```
```json
{
  "startDate": "",
  "endDate": "",
  "customerName": "",
  "customerType": 1,
  "summaryPeriod": 1,
  "pageSize": 10,
  "pageIndex": 1
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":null,"cuid":"CU1814","staticDay":null,"num":11176,"staticMonth":"2026-02","staticYear":null,"realTime":0,"customerName":"深圳市爱伯乐科技有限公司"},{"id":null,"cuid":"CU0292","staticDay":null,"num":19363,"staticMonth":"2026-02","staticYear":null,"realTime":0,"customerName":"拓普视讯"},{"id":null,"cuid":"CU0263","staticDay":null,"num":120,"staticMonth":"2026-02","staticYear":null,"realTime":0,"customerName":"深圳前海启晋网络科技有限公司"},{"id":null,"cuid":"CU0465","staticDay":null,"nu
```

---

### revenue/devide

#### `GET /revenue/devide/cost/header/info`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":{"incomeAmount":22353904.25,"revenueShareAmount":3262543.78,"additionAmount":150917.760307,"proportion":null,"payNum":7317381}}
```

---

#### `GET /revenue/devide/cost/list`
Called 1x

Query:
- `?startMonth=2026-01&endMonth=2026-12&customerName=&pageIndex=1&pageSize=10`

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"cuid":"CU0210","month":"2026-02","payNum":79935,"incomeAmount":355777.31,"revenueShareAmount":61501.40,"additionAmount":20500.47,"proportion":null,"deviceDevideAmount":null,"simDevice":null,"db3Device":null,"otherDevice":{"incomeAmount":355777.31,"revenueShareAmount":61501.40,"additionAmount":20500.47,"proportion":"17.29%","payNum":79935}},{"cuid":"CU0248","month":"2026-02","payNum":4441,"incomeAmount":19214.37,"revenueShareAmount":1131.97,"additionAmount":nu
```

---

### revenue/incomeStatement

#### `POST /revenue/incomeStatement`
Called 1x

Body:
```json
{
  "serveNoList": [
    "CN",
    "EU",
    "US"
  ],
  "startDate": "2026-01",
  "staticType": 0,
  "tenantIdList": [
    "vicoo",
    "guard"
  ],
  "endDate": "2026-03",
  "pageIndex": 1,
  "pageSize": 10,
  "platformType": 0
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[],"total":0,"tierNum":{"基础版":0,"高级版":0,"专业版":0}}}
```

---

### revenue/list

#### `POST /revenue/list`
Called 4x

Body:
```json
{
  "devideType": 0,
  "startDate": "",
  "endDate": "",
  "pageSize": 10,
  "pageIndex": 1
}
```
```json
{
  "customerType": 0,
  "devideType": 0,
  "startDate": "",
  "endDate": "",
  "customerName": "",
  "pageIndex": 1,
  "pageSize": 10
}
```
```json
{
  "customerType": 1,
  "devideType": 0,
  "startDate": "",
  "endDate": "",
  "customerName": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"devideMonth":3,"devideCoefficient":null,"payCount":null,"expectedAmount":null,"devideList":[],"devideListCount":0,"totalAmount":null,"cashBackTotalAmount":0,"totalPayCount":0,"totalPayDeviceNumCount":0,"totalActiveNum":0,"thirtyDayActiveNum":0,"incomeTotalAmount":0,"devideCostTotalAmount":0,"db3DevideTotalAmount":null,"db3ValidityCount":0,"db3ValidityTotalAmount":0,"additionShow":1}}
```

---

### revenue/sim

#### `POST /revenue/sim/list`
Called 1x

Body:
```json
{
  "customerType": 0,
  "cuid": "",
  "manufacturerId": "",
  "startMonth": "",
  "endMonth": "",
  "configStatus": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"totalAmount":79887.10,"totalIncomeAmount":214362.87,"totalPayNum":16291.60,"totalDeviceNum":21966,"totalActiveNum":0,"totalActiveNumInThirtyDay":0,"totalTraffic":0,"list":[{"id":null,"month":"2026-02","cuid":"CU0309","manufacturerId":null,"customerName":"安科创新（深圳）有限公司","manufacturerName":null,"amount":214.22,"revenueAmount":214.220000,"activationIncentiveRevenueAmount":0.000000,"activationIncentiveDeviceNum":0,"incomeAmount":565.39,"payNum":48.33,"deviceNum":59,"active
```

---

### test/item

#### `POST /test/item/list`
Called 1x

Body:
```json
{
  "createDate": "",
  "lastModifyDate": "",
  "itemLike": "",
  "paramLike": "",
  "produceArtId": "",
  "pageIndex": 1,
  "pageSize": 10
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":234,"itemCode":"PrintCodeTest","itemName":"条码打印","remark":"","createTime":"2026-02-27 14:48:48","lastModifyTime":"2026-02-27 19:26:17","paramNames":"类型（0：pcba条码，1：mac)）","testItemParamDOList":null,"testItemThresholdTypeList":null,"logDOList":null,"produceArtRelationDOList":[{"id":1087,"testItemId":234,"produceArtId":110,"createTimestamp":"2026-02-27T06:48:49.000+00:00"}]},{"id":233,"itemCode":"AirtightTest","itemName":"气密性测试","remark":"","createTime":"202
```

---

#### `POST /test/item/manage/list/new`
Called 1x

Query:
- `?produceArtIds=&modelNoCategory=0&categoryId=&modelNoLike=&itemLike=&paramLike=&status=&pageSize=10&page=1`

Body:
```json
{}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":1913,"modelNo":"KF451","categoryId":11,"status":1,"createTime":"2026-01-14 10:33:43","lastModifyTime":"2026-03-02 20:06:35","branchName":null,"prUrl":"","modelType":0,"batteryId":null,"categoryName":"喂鸟器","sourceDeviceName":null,"statusName":"待发布","manageManufacturerList":null,"commectionName":"VAN,USB","itemNum":36,"logDOList":null,"produceArtCount":4,"totalProduceArtCount":61},{"id":1905,"modelNo":"KF351","categoryId":11,"status":1,"createTime":"2026-01
```

---

#### `POST /test/item/manage/param`
Called 1x

Body:
```json
{
  "modelType": 0
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"modelList":{"513":"BX150","1":"CG1","514":"CK160A1","2":"CG121","3":"CG721","4":"CG110-A","5":"CG122","14":"CG621","527":"CG623G","15":"CG621-W","528":"CG623H","16":"CB120B","529":"CQ123B","1043":"CG625A3","23":"CG522","537":"CG623G-BD","541":"CG623G1","542":"CG623H1","1056":"KG125A1","32":"CG121-A","1058":"CQ425A2","1060":"CL060C","548":"CG121D","1061":"CL060D","1574":"CG625E","552":"PB1","1578":"CG628-BD","1579":"CG628","556":"CK160C","559":"CX124A","51":"CG410-A","
```

---

### user-sn/records

#### `GET /user-sn/records`
Called 1x

Query:
- `?pageIndex=1&pageSize=10`

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":6271,"manufacturerId":32,"modelNo":"KF351","quantity":10,"orderNo":"zhou-test","subOrderNo":"1","status":1,"createTime":"2026-02-10 11:46:22","updateTime":"2026-02-10 11:46:22","usedStatus":{"availableCount":10,"disabledCount":0,"usedCount":0}},{"id":6270,"manufacturerId":84,"modelNo":"KG125","quantity":1408,"orderNo":"20260206-xmy","subOrderNo":"1","status":1,"createTime":"2026-02-06 19:29:35","updateTime":"2026-02-06 19:29:35","usedStatus":{"availableCo
```

---

### user/cuid

#### `POST /user/cuid/list`
Called 11x

Response (200):
```json
{"code":0,"msg":"","data":[{"id":861,"name":"深圳市枭泽电子科技有限公司","abbreviation":"枭泽电子","code":"CU0640"},{"id":860,"name":"Ronlight Health LTD","abbreviation":"Ronlight","code":"CU0639"},{"id":859,"name":"深圳市浩方科技有限公司","abbreviation":"浩方科技","code":"CU0638"},{"id":858,"name":"CENOVA BİLİŞİM TEKNOLOJİLERİ İTH. İHR. ve TİC. LTD.","abbreviation":"CENOVA","code":"CU0637"},{"id":857,"name":"深圳涌潮智创科技有限公司","abbreviation":"涌潮智","code":"CU0636"},{"id":856,"name":"眾欣贸易有限公司","abbreviation":"眾欣","code":"CU0635"},{"
```

---

### user/list

#### `POST /user/list`
Called 1x

Body:
```json
{
  "userName": "",
  "name": "",
  "roleIds": [],
  "type": "",
  "pageSize": 10,
  "pageIndex": 1,
  "createTime": "",
  "lastModifyDate": "",
  "status": ""
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":50,"userName":"董梦凡","email":"mdong@addx.ai","phone":"18800410164","statusStr":null,"customerName":"香港积加","manufacturerName":null,"type":8,"typeStr":"渠道客户,客服,4G分成","createTimeStr":"2022-05-16 13:24:04","lastModifyDateStr":"2025-06-19 17:14:43","cuid":"CU0262","manufacturerId":null,"customerType":0,"status":1,"isServerUser":null,"userPermission":null},{"id":53,"userName":"渠道客户0","email":"18810260000@163.com","phone":"18810260000","statusStr":null,"customerN
```

---

### user/manufacturer

#### `POST /user/manufacturer/list`
Called 13x

Response (200):
```json
{"code":0,"msg":"","data":[{"id":1,"name":"addx","code":"addx","type":0},{"id":2,"name":"深圳市罡扇广联电子科技有限公司","code":"CU1291","type":0},{"id":3,"name":"深圳市达创威视科技有限公司","code":"CU1311","type":0},{"id":4,"name":"深圳市延创兴电子有限公司","code":"CU1307","type":0},{"id":5,"name":"深圳中安讯视科技有限公司","code":"CU1304","type":0},{"id":6,"name":"深圳市盈润佳电子有限公司","code":"CU1139","type":0},{"id":7,"name":"深圳市俊明视电子科技有限公司","code":"CU1150","type":0},{"id":8,"name":"深圳市兴未来智能科技有限公司","code":"CU1362","type":0},{"id":9,"name":"深圳市沃安电子有限公司
```

---

### user/tenantId

#### `POST /user/tenantId/list`
Called 1x

Query:
- `?cuid=`

Response (200):
```json
{"code":0,"msg":"","data":[{"id":2,"tenantId":"hthome","appCustomer":"CU1731","appName":"HT home camera","oemType":1,"iotHostDomain":"hthomecamera.live"},{"id":3,"tenantId":"shenmou","appCustomer":"CU1830","appName":"神眸智品","oemType":1,"iotHostDomain":"superacme.com"},{"id":4,"tenantId":"dzees","appCustomer":"CU0205","appName":"dzees","oemType":1,"iotHostDomain":"dzeesja.com"},{"id":5,"tenantId":"dzeesHome","appCustomer":"CU0205","appName":"dzeeshome","oemType":1,"iotHostDomain":"dzeesja.com"},{"
```

---

### user/ticket

#### `POST /user/ticket/countryList`
Called 1x

Body:
```json
{}
```

Response (200):
```json
{"code":0,"msg":"","data":[{"shortName":"AF","enName":"Afghanistan","cnName":"阿富汗"},{"shortName":"AL","enName":"Albania","cnName":"阿尔巴尼亚"},{"shortName":"DZ","enName":"Algeria","cnName":"阿尔及利亚"},{"shortName":"AD","enName":"Andorra","cnName":"安道尔"},{"shortName":"AO","enName":"Angola","cnName":"安哥拉"},{"shortName":"AG","enName":"Antigua and Barbuda","cnName":"安提瓜和巴布达"},{"shortName":"AR","enName":"Argentina","cnName":"阿根廷"},{"shortName":"AM","enName":"Armenia","cnName":"亚美尼亚"},{"shortName":"AU","enNa
```

---

#### `POST /user/ticket/list`
Called 1x

Body:
```json
{
  "keywords": "",
  "cuid": "",
  "platformTicketId": "",
  "ticketStatus": "",
  "lastCommentUser": "",
  "countryShortName": "",
  "producModel": "",
  "pageIndex": 1,
  "pageSize": 10,
  "filedConditionMap": {}
}
```

Response (200):
```json
{"code":0,"msg":"","data":{"total":12583,"list":[{"ticketId":"2080451","parentTicketId":null,"ticketTitle":"Camera only capturing images, not recordings","ticketBrefJson":"{\"organizationId\":null,\"createdAt\":1772482987000,\"requesterId\":55632211803545,\"submitterId\":55632211803545,\"groupId\":10961470096281,\"recipient\":\"support@vicohome.io\",\"assigneeId\":10999509826969,\"url\":\"https://addxai.zendesk.com/api/v2/tickets/2080451.json\",\"via\":{\"channel\":\"api\",\"source\":{\"from\":{
```

---

#### `GET /user/ticket/zendesk/custom_fields`
Called 1x

Response (200):
```json
{"code":0,"msg":"","data":[{"id":360030181953,"url":"https://addxai.zendesk.com/api/v2/ticket_fields/360030181953.json","type":"subject","title":"标题","description":"","position":0,"active":true,"required":false,"tag":null,"removable":false,"raw_title":"标题","raw_description":"","collapsed_for_agents":false,"regexp_for_validation":null,"title_in_portal":"Title","raw_title_in_portal":"Title","visible_in_portal":true,"editable_in_portal":true,"required_in_portal":true,"created_at":"2019-12-10T05:21:
```

---

### worker/list

#### `GET /worker/list`
Called 1x

Query:
- `?companyCode=&companyName=&pageIndex=1&pageSize=10`

Response (200):
```json
{"code":0,"msg":"","data":{"list":[{"id":1,"manufacturerId":1,"email":"admin@addx.ai","pswd":"SZ4EMHS4","roleId":1,"companyName":"addx","companyCode":"addx","type":0,"status":0,"typeName":"工厂厂测"},{"id":2,"manufacturerId":1,"email":"worker@addx.ai","pswd":"12345678","roleId":2,"companyName":"addx","companyCode":"addx","type":0,"status":0,"typeName":"工厂厂测"},{"id":3,"manufacturerId":0,"email":"demo","pswd":"CHZM8LHD","roleId":2,"companyName":"demo","companyCode":"demo","type":0,"status":0,"typeName
```

---
