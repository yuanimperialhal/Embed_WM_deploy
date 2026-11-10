# USB 相机实时推理操作指南

> 开发参考文档：示例中的 `/path/to/cosmos-camera` 需换成实际源码路径；已安装软件的操作以 [安装版说明](installed-app-guide.md) 为准。

**现在可以直接使用桌面界面，不必逐条输入终端命令。** 双击桌面“Cosmos 相机助手”，点击“开始连续分析”，即可同时看实时画面和中文结果。模型会自动启动，问题也可以在窗口里修改。

先看这份简短说明：[界面操作指南](camera-ui-guide.md)。以下保留原有命令行操作方式。

这份指南教你：**让 Jetson 从 USB 相机获取画面，交给 Cosmos 模型分析，并在终端显示中文回答。**

所有命令都在 **Jetson 的终端**执行，不需要进入 Docker。复制代码框里的命令，粘贴后按回车即可。

## 在 Jetson 屏幕上看实时画面（新增）

只预览，不加载模型、不保存照片或视频：

```bash
cd /path/to/cosmos-camera
python3 scripts/camera_infer.py --source /dev/video0 --preview-only
```

窗口中按 **Q / Esc**，或终端按 **Ctrl+C** 停止。通过 SSH 运行时，如果没有设置 DISPLAY，脚本会尝试使用当前 seeed 用户已登录的 Jetson 桌面 `:0`；窗口显示在 Jetson 的屏幕上。未登录图形桌面时先登录，再执行命令。不要同时运行两个占用同一相机的预览/推理程序。

边显示视频边用 Cosmos 分析，先停止上面的纯预览，然后开两个终端：

```bash
# 终端 A：保持运行；等待模型加载完成
cd /path/to/cosmos-camera
python3 scripts/serve.py
```

```bash
# 终端 B：检查返回内容中有 cosmos，再启动画面和推理
curl --noproxy '*' --max-time 5 http://127.0.0.1:8000/v1/models
python3 /path/to/cosmos-camera/scripts/camera_infer.py --source /dev/video0 --preview
```

视频窗口持续刷新，底部显示最近一次完成的中文分析，终端仍流式显示完整回答。画面与推理共用一个相机采集源，推理请求串行执行；上一次完成后再取最新帧，不积压旧图。界面标注分析所用帧序号及结果完成后的时间，**结果对应先前取样帧，不是当前视频帧的同步标注**。当前模型给出场景文字描述，不画检测框。预览帧率不等于模型推理频率，也不代表多帧/连续视频理解。

关闭预览只停止采集及当前客户端请求，模型仍由终端 A 管理；完全不用时，在 A 按 Ctrl+C 释放模型内存。该功能不会设置开机自启动。内存紧张时先关闭 Jetson 上不用的 Codex、浏览器等应用。

可选：`--preview-seconds 5` 运行预览 5 秒后退出；`--prompt '用一句话描述画面中的主要物体。'` 修改问题。中文结果显示使用设备已有的 Pillow 和 Noto CJK 字体。

## 1. 先让模型看一眼

相机设备是 `/dev/video0`。先用第 5 节的方法启动并检查模型服务；若服务已经就绪，运行：

```bash
python3 /path/to/cosmos-camera/scripts/camera_infer.py --source /dev/video0 --count 1
```

这条命令会读取相机画面、让模型回答一次，然后自动退出。

终端中会显示模型的回答和请求耗时。看到 `状态：ok`，表示这次调用正常完成，但还需要对照实际画面判断回答是否正确。

如果出现 `Connection refused` 或“连接被拒绝”，先按第 5 节启动模型，再回来执行这条命令。

## 2. 让模型持续看相机

运行：

```bash
python3 /path/to/cosmos-camera/scripts/camera_infer.py --source /dev/video0
```

它会不断重复以下过程：

1. 取一张最新的相机画面。
2. 交给模型分析，在终端显示回答。
3. 回答结束后，再取最新画面继续分析。

**停止时，在这个终端按 `Ctrl+C`。**

上述命令默认仅显示文字。加上 `--preview` 可以同时打开实时视频窗口；`--preview-only` 只看视频且不调用模型。回答需要时间，更新速度取决于模型处理速度。

## 3. 换成你想问的问题

先按 `Ctrl+C` 停止正在运行的相机分析，再运行下面的完整命令。修改引号里的中文，就能换问题。

例如，让模型判断有没有人：

```bash
python3 /path/to/cosmos-camera/scripts/camera_infer.py --source /dev/video0 --prompt '画面中有没有人？如果有，描述人在画面中的位置。看不清就说不确定，简短回答。'
```

例如，让模型描述物体：

```bash
python3 /path/to/cosmos-camera/scripts/camera_infer.py --source /dev/video0 --prompt '画面中有哪些主要物体？描述它们的相对位置。看不清就说不确定，用一两句话回答。'
```

每次只运行一条相机命令。如果只想问一次，在命令末尾加上 `--count 1`。

## 4. 总是回答“画面模糊”怎么办

先按 `Ctrl+C` 停止相机分析，再拍一张照片检查：

```bash
python3 /path/to/cosmos-camera/scripts/camera_infer.py --source /dev/video0 --snapshot-only /path/to/cosmos-camera/images/camera-check.jpg
```

然后打开这张照片：

在文件管理器打开你自己生成的 `images/camera-check.jpg`（源码包不包含旧相机照片）。

也可以在文件管理器中找到 `/path/to/cosmos-camera/images/camera-check.jpg` 并打开。

如果照片确实模糊，固定相机、改善光线，并调整相机与物体的距离；支持调焦的相机可以调整焦点。重新执行拍照命令会更新这张照片。画面清晰后，再运行第 2 节的持续分析命令。

拍照不需要启动模型。此前测试拍到的画面较模糊，因此模型返回了“画面模糊，无法可靠判断”。

## 5. 重启设备后，怎么重新运行

需要打开两个终端窗口，下面分别叫“终端 A”和“终端 B”。

### 终端 A：启动模型

```bash
python3 /path/to/cosmos-camera/scripts/serve.py
```

**保持这个终端打开。** 加载模型需要等待，期间会出现很多日志，不要重复运行启动命令。

### 终端 B：检查模型是否准备好

```bash
curl --max-time 5 http://127.0.0.1:8000/v1/models
```

如果返回的内容中有 `"id":"cosmos"`，就说明模型已经准备好。空格多少不影响判断。

如果连接失败，先查看终端 A：模型仍在加载时，稍等后再检查；如果已经报错退出，就需要根据错误排查。

### 还是终端 B：启动相机分析

```bash
python3 /path/to/cosmos-camera/scripts/camera_infer.py --source /dev/video0
```

这时终端 A 负责运行模型，终端 B 负责采集相机画面并显示回答。

## 6. 怎么停止

| 想做什么 | 操作 |
| --- | --- |
| 暂时停止相机分析 | 在运行相机命令的终端按 `Ctrl+C` |
| 换一个问题 | 停止相机分析，再运行带新问题的命令 |
| 完全停止并释放模型占用的内存 | 先停止相机分析，再到启动模型的终端 A 按 `Ctrl+C` |

如果模型是之前由助手启动的，你找不到终端 A，可以先查看它的容器名称：

```bash
docker ps --filter name=cosmos-camera- --format '{{.Names}}'
```

复制输出中以 `cosmos-camera-` 开头的完整名称。在终端输入 `docker stop`，加一个空格，再粘贴这个名称并回车，即可停止对应的模型服务。如果没有输出，表示没有匹配的运行中容器。

## 7. 常见问题

| 看到的情况 | 怎么处理 |
| --- | --- |
| 连接被拒绝 / `Connection refused` | 按第 5 节启动模型，确认就绪后再运行相机命令 |
| 无法打开相机 | 检查 USB 连接，关闭其他使用相机的软件，再按下面的命令检查设备 |
| 8000 端口已占用 | 先用第 5 节的 `curl` 命令检查；如果返回 `cosmos`，直接使用已有服务 |
| 一直说画面模糊 | 按第 4 节拍照，先确认实际画面是否清楚 |
| `状态：truncated` | 回答没有完整生成；先把问题改短，要求只回答一句话 |
| 回答很慢 | 首次请求可能较慢；先使用简短问题，并关闭自己不需要的应用以释放内存 |

检查相机设备的命令：

```bash
python3 /path/to/cosmos-camera/scripts/camera_infer.py --list-devices
```

这台设备此前验证可用的相机路径是 `/dev/video0`。重新插拔后编号可能变化；若变化，需要把相机命令中的路径一起替换。一个相机也可能列出多个节点，并非每个节点都能取图。

## 8. 目前已经验证到哪一步

2026 年 10 月 25 日，已经完成真实 USB 相机到 Cosmos 的调用测试，两次都正常结束。

- 两次回答都是“画面模糊，无法可靠判断”。
- 首次请求约 14.53 秒，第二次约 0.83 秒。
- 0.83 秒对应的是很短的回答，不能代表复杂问题的处理速度。

目前每次只分析一张最新图片，不保留前面画面的记忆。它可以用于查看场景、描述物体和空间关系；要判断连续动作、运动方向或一段时间内的变化，还需要增加多帧或短视频输入。

分析记录保存在 `/path/to/cosmos-camera/logs/` 下，文件名以 `camera-` 开头、以 `.jsonl` 结尾。每次启动时，终端都会打印本次记录的完整路径。


2026-10-25 视频窗口验证：`--preview-only --preview-seconds 5` 成功显示 66 个不同画面；真实 Cosmos 服务加载约 120.15 秒，`--preview --count 1 --max-tokens 64` 调用状态为 ok，请求耗时 3.25 秒，推理期间显示 51 个不同画面。返回“画面模糊，无法可靠判断。”。这只验证了采集、显示、模型调用与不阻塞预览，不代表清晰场景识别质量或稳定推理速度。测试后停止模型，没有新增常驻服务。日志：`logs/preview-check-20261025-225811/`。
