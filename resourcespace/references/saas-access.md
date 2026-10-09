# ResourceSpace 认证与能力契约

- 认证 owner：resourcespace；T2，官方文档化签名API。URL、user、API key全部由宿主注入。长期API key由该用户账号页获取，不使用部署scramble key作为客户端凭据。
- 官方能力调研（2026-10-09）：未在厂商知识库或官方源码发行中查到Agent Skill/官方OpenCLI；这是一项检索结果，不是证明不存在。官方API：https://www.resourcespace.com/knowledge-base/api/；客户端原生浏览器API与签名API不同，不能直接混用。
- 本实现策略：OpenCLI LOCAL，调用官方签名API；不逆向私有XHR，不复制完整API清单。契约参照ResourceSpace 11.0官方SVN release revision29860及部署黑盒；换版需按官方当前版本复核。
- API签名由客户端生成；query字段顺序和编码与签名输入一致。multipart同时带签名file_name，成功HTTP204无JSON；离线JPEG和MP4生成可能先后完成，上传成功不代表预览完成。
- discovery/profile探针：status→types/fields→已知样本resource/metadata/search。服务端按API用户权限执行；status不是独立whoami API，不能声称核验额外租户/角色。
- 已支持13条命令；search显式分页，读写的结构化结果有已知key脱敏。download仅同源，避免signed URL泄露/外部网关重定向；接口非2xx、业务false/error、非JSON、超时均失败，不把错误当空结果。
- 上传保留已有资源ID，更新只更改明确field，不做删除/权限/分享操作。集合添加后通过collection查询加成员search读回。
- 测试与证据见本目录acceptance.json；fixture、runtime分别标注。线上写验收限已授权本机合成样本；不能据此声称生产角色、所有格式或大文件吞吐通过。

本机配置可在私有shell wrapper中注入环境后调用opencli；不要把实例API key复制进Skill目录。调用直接CLI与OpenCLI adapter共享同一client实现：`node <Skill目录>/scripts/cli.mjs help`。
