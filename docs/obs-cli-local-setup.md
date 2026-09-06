# 本机 OBS CLI 安装记录

验证日期：2026-09-06。

已通过 pipx 安装 `cli-anything-obs-studio 1.0.0`，使用 Python 3.12.10，依赖与本项目隔离。

- 来源：https://github.com/HKUDS/CLI-Anything/tree/810c18b0d1ab9b234bc996c9fd999318523a3ef0/obs-studio/agent-harness
- 源码：`C:\Users\Mayn\.local\share\cli-anything\obs-studio\agent-harness`
- 命令：`C:\Users\Mayn\.local\bin\cli-anything-obs-studio.exe`，当前 PATH 可直接调用。
- 已有 OBS：`D:\obs-studio\bin\64bit\obs64.exe`，版本 31.0.1。

## 使用

```powershell
cli-anything-obs-studio --help
cli-anything-obs-studio --json output presets
cli-anything-obs-studio --json project new --name capture -o E:\devlop\ai-notes\outputs\recordings\capture-config.json
cli-anything-obs-studio --json --project E:\devlop\ai-notes\outputs\recordings\capture-config.json project info
```

重新安装本次检出的版本：

```powershell
pipx install C:\Users\Mayn\.local\share\cli-anything\obs-studio\agent-harness
```

已安装时可通过 `pipx list` 查看；上述安装命令不会自动更新源码。

## 能力边界

本次检出的实现编辑其自身 JSON 配置，支持场景、来源、音频、滤镜及输出设置。源码没有 OBS WebSocket 客户端，也没有开始录制、停止录制或查询实际录制状态的命令。

`output recording` 只修改配置文件，不会启动 OBS 录制。生成配置直接导入 OBS 的兼容性尚未验证；不要用它覆盖现有 OBS 配置。

若要完成视频录屏，仍需连接 OBS 的控制接口，并验证采集画面、声音及实际视频文件。本次安装没有开始录屏。

## 验证结果

直接调用已安装的可执行文件，7 项 subprocess 检查通过：创建项目、添加场景、添加显示器来源、保存录制配置、dry-run 不改文件、重新读取项目，以及缺失文件返回非零退出码。对保存的 JSON 另行断言场景数、来源类型和录制格式。

帮助及 JSON 编码预设查询正常。临时验证文件已清理。未运行上游完整测试套件，未验证实际 OBS 录制。
