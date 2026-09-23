# Client 包 + 测试

> **何时读**：写 HTTP client 或单测时。

## 包分层

```
internal/clients/<name>/
  client.go               # 通用：do(method, path, body, out) + 签名 + envelope 解析
  <resource>.go           # 资源特定：Create / List / Get / Update / Delete
  client_test.go          # 单测：签名算法、错误类型化
  smoke_test.go           # 打真实环境，需要 env var 才跑
```

**包独立** — 不依赖 K8s。这样既能本地 `go test` 快速验证 HTTP/签名逻辑，又能让控制器测试只关心"业务流转"。

## client.go 标准封装

参考 [`internal/clients/ninedata/client.go`](https://gitlab.addx.ai/DEV/provider-ninedata/-/blob/main/internal/clients/ninedata/client.go)：

```go
package ninedata

import (
    "bytes"
    "context"
    "crypto/sha256"
    "encoding/hex"
    "encoding/json"
    "fmt"
    "io"
    "net/http"
    "strings"
    "time"
)

const DefaultTimeout = 30 * time.Second

type Credentials struct {
    AccessKeyID     string `json:"access_key_id"`
    AccessKeySecret string `json:"access_key_secret"`
}

type Client struct {
    Endpoint   string
    Creds      Credentials
    HTTPClient *http.Client
    Now        func() time.Time   // 暴露给测试冻结时间
}

func New(endpoint string, creds Credentials) *Client {
    return &Client{
        Endpoint:   strings.TrimRight(endpoint, "/"),
        Creds:      creds,
        HTTPClient: &http.Client{Timeout: DefaultTimeout},
        Now:        time.Now,
    }
}

// Sign 暴露为顶层函数，方便确定性单测
func Sign(apiPath, accessKeySecret, timestamp string) string {
    h := sha256.Sum256([]byte(apiPath + "/" + accessKeySecret + "&" + timestamp))
    return hex.EncodeToString(h[:])
}

func FormatTimestamp(t time.Time) string {
    return t.UTC().Format("2006-01-02T15:04:05Z")
}

// APIError：caller 可 type assert 做条件处理（404 当 ResourceExists=false 等）
type APIError struct {
    HTTPStatus int
    RequestID  string
    ErrorCode  string
    Message    string
}

func (e *APIError) Error() string {
    return fmt.Sprintf("ninedata %s: %s (requestId=%s, http=%d)",
        e.ErrorCode, e.Message, e.RequestID, e.HTTPStatus)
}

// envelope：统一响应壳，省得每个资源都解一次
type envelope struct {
    Success   bool            `json:"success"`
    ErrorCode string          `json:"errorCode"`
    Message   string          `json:"message"`
    RequestID string          `json:"requestId"`
    Data      json.RawMessage `json:"data"`
}

// do 是所有资源 CRUD 的底层入口：签名 + 调用 + envelope 解析
func (c *Client) do(ctx context.Context, method, apiPath, query string, body, out any) error {
    var bodyReader io.Reader
    if body != nil {
        b, err := json.Marshal(body)
        if err != nil { return fmt.Errorf("marshal: %w", err) }
        bodyReader = bytes.NewReader(b)
    }

    u := c.Endpoint + apiPath
    if query != "" { u += "?" + query }

    req, err := http.NewRequestWithContext(ctx, method, u, bodyReader)
    if err != nil { return fmt.Errorf("new request: %w", err) }

    ts := FormatTimestamp(c.Now())
    req.Header.Set("access-key-id", c.Creds.AccessKeyID)
    req.Header.Set("signature", Sign(apiPath, c.Creds.AccessKeySecret, ts))
    req.Header.Set("timestamp", ts)
    if body != nil { req.Header.Set("Content-Type", "application/json") }

    resp, err := c.HTTPClient.Do(req)
    if err != nil { return fmt.Errorf("http: %w", err) }
    defer resp.Body.Close()

    raw, err := io.ReadAll(resp.Body)
    if err != nil { return fmt.Errorf("read: %w", err) }

    var env envelope
    if err := json.Unmarshal(raw, &env); err != nil {
        return fmt.Errorf("unmarshal envelope (http=%d, body=%s): %w", resp.StatusCode, truncate(raw, 200), err)
    }
    if !env.Success {
        return &APIError{
            HTTPStatus: resp.StatusCode,
            RequestID:  env.RequestID,
            ErrorCode:  env.ErrorCode,
            Message:    env.Message,
        }
    }
    if out != nil && len(env.Data) > 0 {
        if err := json.Unmarshal(env.Data, out); err != nil {
            return fmt.Errorf("unmarshal data: %w", err)
        }
    }
    return nil
}
```

## 资源 CRUD 文件

```go
// datasource.go
type CreateDataSourceRequest struct {
    Name string `json:"name"`
    // ...
}

type createDataSourceResponse struct {
    DatasourceID string `json:"datasourceId"`
}

func (c *Client) CreateDataSource(ctx context.Context, req *CreateDataSourceRequest) (string, error) {
    var resp createDataSourceResponse
    if err := c.do(ctx, http.MethodPost, "/openapi/v1/datasource/create", "", req, &resp); err != nil {
        return "", err
    }
    if resp.DatasourceID == "" {
        return "", fmt.Errorf("empty datasourceId for %q", req.Name)
    }
    return resp.DatasourceID, nil
}

// 兜底"按名查"用于 adopt
func (c *Client) GetDataSourceByName(ctx context.Context, name string) (*DataSource, error) {
    return c.findDataSource(ctx, func(d *DataSource) bool { return d.Name == name })
}

// 翻页查全表 — NineData 不暴露 single-get
func (c *Client) findDataSource(ctx context.Context, match func(*DataSource) bool) (*DataSource, error) {
    const pageSize = 100
    for page := 1; ; page++ {
        q := url.Values{}
        q.Set("current", strconv.Itoa(page))
        q.Set("pageSize", strconv.Itoa(pageSize))

        var items []DataSource
        if err := c.do(ctx, http.MethodGet, "/openapi/v1/datasource/list", q.Encode(), nil, &items); err != nil {
            return nil, err
        }
        for i := range items {
            if match(&items[i]) {
                return &items[i], nil
            }
        }
        if len(items) < pageSize {
            return nil, nil  // 不存在 → (nil, nil)
        }
    }
}
```

## 单测 1：签名确定性

```go
func TestSign(t *testing.T) {
    got := Sign("/openapi/v1/datasource/list", "secret", "2026-04-20T12:34:56Z")
    want := "abc...预先算好的 hex"
    if got != want { t.Errorf("Sign mismatch: got %q want %q", got, want) }
}
```

## 单测 2：错误类型化

```go
func TestAPIError(t *testing.T) {
    server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        w.WriteHeader(http.StatusBadRequest)
        json.NewEncoder(w).Encode(map[string]any{
            "success": false,
            "errorCode": "INVALID_PARAM",
            "message": "name too long",
            "requestId": "req-123",
        })
    }))
    defer server.Close()

    c := New(server.URL, Credentials{"ak", "sk"})
    err := c.do(context.Background(), "POST", "/x", "", map[string]string{}, nil)

    var apiErr *APIError
    if !errors.As(err, &apiErr) { t.Fatal("expected *APIError") }
    if apiErr.ErrorCode != "INVALID_PARAM" { t.Errorf("got %s", apiErr.ErrorCode) }
}
```

## 控制器测试：用 httptest mock 远端，不要 mock crossplane-runtime

```go
// internal/controller/datasource/datasource_test.go
func TestObserve_AdoptByName(t *testing.T) {
    server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
        // mock list 返回 1 条同名记录
        json.NewEncoder(w).Encode(map[string]any{
            "success": true,
            "data": []map[string]any{
                {"datasourceId": "ds-existing", "name": "my-ds"},
            },
        })
    }))
    defer server.Close()

    cr := &v1alpha1.DataSource{
        ObjectMeta: metav1.ObjectMeta{Name: "my-cr", Namespace: "default"},
        Spec: v1alpha1.DataSourceSpec{
            ForProvider: v1alpha1.DataSourceParameters{Name: "my-ds"},
        },
    }
    // 注意：cr 没有 external-name annotation

    ext := newExternal(server)  // helper：装好 fake K8s client + mock service
    obs, err := ext.Observe(context.Background(), cr)
    if err != nil { t.Fatal(err) }

    if !obs.ResourceExists { t.Error("should adopt existing resource") }
    if meta.GetExternalName(cr) != "ds-existing" {
        t.Error("should pin external-name")
    }
}
```

## smoke test：打真实环境

```go
//go:build smoke

func TestSmoke_DataSourceCRUD(t *testing.T) {
    if os.Getenv("NINEDATA_SMOKE") != "1" { t.Skip() }
    endpoint := os.Getenv("NINEDATA_ENDPOINT")
    ak := os.Getenv("NINEDATA_AK")
    sk := os.Getenv("NINEDATA_SK")
    // ...
}
```

跑法：

```bash
NINEDATA_SMOKE=1 NINEDATA_ENDPOINT=https://ninedata.addx.live NINEDATA_AK=xxx NINEDATA_SK=yyy \
  go test -tags smoke ./internal/clients/ninedata/... -run TestSmoke -v
```

## 测试覆盖目标

第一版至少覆盖这 4 条路径：

1. **首次 observe → 不存在 → Create → 再 observe → 存在且 UpToDate**
2. **手写 CR → first observe 按 name 找到 → adopt + pin external-name**
3. **API 返回错误码 → 包成 `*APIError` → controller 不 panic**
4. **Delete CR → 调外部 Delete → 返回 nil**
