# TKE Pod Identity → COS：Python 接入示例

适用于 `workflows/add-tencent-cos.md` 的 SDK 适配环节，不负责开桶或建角色。
示例适用版本：Python 3.12、`cos-python-sdk-v5==1.9.44`、
`tencentcloud-sdk-python-common==3.1.181`。其他版本或 S3 兼容客户端需核对各自接口并验证。

## 凭据与客户端

`DefaultTkeOIDCRoleArnProvider().get_credentials()` 返回的 provider 会读取
`TKE_ROLE_ARN`、`TKE_REGION`、`TKE_PROVIDER_ID` 和 `TKE_WEB_IDENTITY_TOKEN_FILE`。
其 `get_credential_info()` 返回 SecretId / SecretKey / Token 三元组，并检查刷新条件。
不要仅在启动时取一次后永久放入 COSClient：COSClient 持有的是当时的凭据快照，
业务需在操作或生成签名前从 provider 取当前凭据，并更新/重建 client。
高并发应用需自行实现有界缓存、连接复用、刷新失败处理及测试，下面只是接线示例。

```python
from tencentcloud.common.credential import DefaultTkeOIDCRoleArnProvider
from qcloud_cos import CosConfig, CosS3Client

identity = DefaultTkeOIDCRoleArnProvider().get_credentials()


def current_cos(region):
    secret_id, secret_key, token = identity.get_credential_info()
    client = CosS3Client(CosConfig(
        Region=region, SecretId=secret_id, SecretKey=secret_key,
        Token=token, Scheme="https",
    ))
    return client, token
```

## 预签名必须显式携带 Session Token

上述版本 `get_presigned_url()` 不自动把 `CosConfig.Token` 放入 URL。
普通 SDK 请求通过 `CosConfig.Token` 传递临时凭据；预签名 URL 则需将
`x-cos-security-token` 显式放入 `Params`，上传和下载均适用。

```python
client, token = current_cos(region)
# ttl_seconds 必须经过应用检查，小于当前 STS 凭据剩余有效期并预留时钟余量。
url = client.get_presigned_url(
    Bucket=bucket, Key=object_key, Method="PUT", Expired=ttl_seconds,
    Params={"x-cos-security-token": token},
)
# 将 URL 仅交给已授权调用者；不要打印、记录或提交这个 URL。
```

GET 同样需要这个参数。COS CAM 对象资源中的 `uid/<AppId>` 使用 APPID，
不是 CAM 信任 ARN 中的账号 UIN；按 [COS 授权策略](https://cloud.tencent.com/document/product/436/31923)
核对，避免两个账号标识混用。

## 接入验收

验证身份注入、STS 换证、授权范围内读写删、越权及错误 SA 拒绝，
并检查预签名上传/下载和过期拒绝。
除主动调用 provider 的 `refresh()` 外，还需验证自然到期刷新和 kubelet 投影 token
轮换后的持续访问。按业务实际使用补充 S3 兼容客户端、浏览器 CORS 和 multipart 验证。

参考：[TKE OIDC](https://cloud.tencent.com/document/product/457/137141)、
[COS 临时凭据](https://cloud.tencent.com/document/product/436/68283)。
