# Android 操作与证据规则

## 工具发现

优先 `command -v adb`，否则查看 `${ANDROID_HOME}/platform-tools/adb`、`${ANDROID_SDK_ROOT}/platform-tools/adb` 或 macOS `~/Library/Android/sdk/platform-tools/adb`。aapt 可在 SDK build-tools 下找到。不因 PATH 无 adb 就说设备能力不可用。

常用命令（所有变量由实际发现值填写，使用安全参数传递）：

```text
adb devices -l
aapt dump badging APK
adb -s SERIAL shell dumpsys package PACKAGE
adb -s SERIAL install -r APK
adb -s SERIAL shell am start -n PACKAGE/ACTIVITY
adb -s SERIAL shell uiautomator dump /sdcard/UNIQUE.xml
adb -s SERIAL shell cat /sdcard/UNIQUE.xml
adb -s SERIAL exec-out screencap -p
```

安装和启动属于动作步骤，应执行后验证状态。不要硬编码 Pixel 序列号、VicoNature 包名、旧账户或密码。

## UI 交互

- Flutter 页面可能主要用 content-desc；原生页常有 resource-id。同名 EditText 根据父级或当前边界区分账号、密码。
- uiautomator 偶尔返回上一页面或 loading，重新观察再操作。使用条件等待和超时；不要猜点击已生效。
- 键盘会移动控件；输入后收起键盘，再读取按钮边界。取消记住密码要检查 checked 状态；不要误切换。
- `adb shell input text` 有 shell 元字符限制。输入秘密需受控传参/标准输入助手，不把它拼接到 shell 命令；仅在确定目标密码框时输入。不打印子进程原始错误中可能含的凭据。
- 截图工具展示时可能缩放 1080×2400 为 922×2048；按原始分辨率换算，优先用 XML 坐标。
- 不执行无边界循环。最多两次同类重试后保存故障与恢复位置，继续其他独立检查。

## 辅助脚本

Python 3 标准库，无需安装 Appium。路径在 Skill 目录下：

```text
python3 scripts/android_evidence.py --help
python3 scripts/android_evidence.py probe --out RUN/preflight.json
python3 scripts/android_evidence.py --serial SERIAL capture --out RUN/03-entry
```

`probe` 自动选择唯一已授权设备；多设备拒绝选择；无设备输出状态为 blocked。`capture` 保存 `.png` 与 `.ui.json`，使用唯一临时 XML 并清理；发现 password=true 控件时默认拒绝截图，只保存脱敏 UI。失败明确返回非零，不能当成测试不通过或通过。不要在密码明文展示、Token 或不相关个人内容的页面采集。

该脚本仅提供证据采集，不保证截图与 XML 原子一致；动态状态应由 Agent 再确认。它不判断业务 AC，不声称已经实现全自动测试引擎。
