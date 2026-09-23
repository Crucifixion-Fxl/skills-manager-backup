# 素材管理

路径均相对于 Skill 根目录。共享手机框放 `assets/shared/phone-frames/`；产品硬件、截图和品牌素材分别放 `assets/<product>/hardware/`、`screenshots/`、`brand/`。

每个产品的 `profile.json` 包含核心卖点、外观差异、品牌配置和素材清单。空列表或 null 表示尚未提供。主强调卖点、支持的功能与外观特征由用户材料确定，不从品牌名推断。

向 `assets` 列表添加记录时使用：

```json
{
  "id": "由实际素材命名的稳定标识",
  "path": "assets/产品目录/分类/文件名",
  "kind": "hardware_reference",
  "locale": null,
  "model": null,
  "source": "用户提供的原始来源",
  "usage": "外观参考或可直接合成素材",
  "status": "provided",
  "notes": "用户指定的用途与限制"
}
```

`kind` 可为 hardware_reference、hardware_cutout、screenshot、logo、font、phone_frame。外观参考照片与可直接合成的透明底硬件图必须区分；前者不能声称已经完成抠图或精确保形。

共享手机框在 `assets/shared/manifest.json` 记录同样结构，补充屏幕蒙版区域、适配截图比例、是否自带状态栏。避免双重状态栏或双重手机边框。

收录时查看素材、核实文件可读并保留原件；不覆盖同名的不同文件。涉及截图语言、硬件型号或产品归属不明确且影响使用时，集中询问缺失信息。尚未收到的素材不创建假文件。
