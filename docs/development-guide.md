# Cosmos 单图与 USB 相机推理

> 历史开发记录：原项目的调试和实测结果供参考；其中的图片、模型、日志和 0.1.x 离线产物不随源码分发。0.2.0 的安装方式和当前能力以 [项目 README](../README.md) 为准。

## 单文件离线安装器（0.1.1）

打包应用、完整模型、推理镜像和应用依赖：`python3 packaging/build_offline.py`。输出为 `dist/CosmosCamera-0.1.1-jetson-arm64.run`，支持 `--info`、`--check`、`--extract 新目录` 和 `--install`。

目标环境为 Jetson arm64、Ubuntu 22.04、L4T R36.4.3，需要预先配置匹配的 JetPack、Docker 和 NVIDIA runtime。使用方式及实现原理见 [单文件离线安装教程](offline-installer-guide.md)。构建只下载和导出文件，不安装软件；系统安装需要用户显式以 `sudo` 执行 `.run --install`。

## 做成可安装的软件

从头跟着做：[从 Demo 到软件的实操教程](from-demo-to-software.md)。当前版本为 **0.1.1 Jetson 预览版**，也可单独构建 arm64 `.deb`：

```bash
python3 packaging/build_deb.py
```

输出到 `dist/`，包含安装包、SHA256 和发行清单。程序与用户配置、日志、缓存分开，提供模型目录设置、环境检查和版本信息。安装版首次使用见 [安装版使用说明](installed-app-guide.md)，限制见 [发行说明](release-notes.md)。

已验证安装包文件清单、应用入口、只读程序目录下运行，以及系统 Qt/OpenCV + 真实 USB 相机 + 本地 SSE 测试服务的交互；配置/后台回归共 14 项通过。记录：`logs/package-check-20261029-195538/package-report.json`。尚未执行系统安装、第二台干净设备验收或本轮真实模型加载；模型权重和 NVIDIA 推理环境仍需单独准备。

## 桌面界面（推荐）

双击 Jetson 桌面的“Cosmos 相机助手”，左侧显示实时视频，右侧显示流式中文分析。点击“开始连续分析”会自动启动模型；支持单次分析、暂停、修改问题、相机开关、历史结果和服务日志。

操作说明：[界面使用指南](camera-ui-guide.md)。备用启动命令：

```bash
python3 /path/to/cosmos-camera/scripts/camera_ui.py
```

界面使用设备已有的 PyQt5 5.15 和 OpenCV，采用浅灰背景、白色圆角面板和蓝色主按钮。相机只打开一次，预览共享最新帧；模型调用放在可取消的独立客户端进程，文字通过进程管道逐段传回，GUI 线程不等待网络请求。每次只发送一张最新图片，保持现有低内存服务参数。结果保存到本次独立的 `logs/camera-ui-日期时间/results.jsonl`，模型启动日志保存为同目录的 `server.log`。

关闭窗口会停止相机、推理客户端以及该窗口启动的模型；复用的已有模型服务不会被关闭。暂停分析会断开当前客户端请求，模型服务继续运行。右侧取样缩略图与画面年龄明确标记结果对应的画面，避免把延迟结果当作当前视频的同步标注。旧命令行及 `--preview` 功能继续保留。

2026-10-29 的旧版界面已完成真实模型调用验证：31 个输出 token，总耗时 8.37 秒，推理期间预览更新 92 次；记录见 `logs/camera-ui-20261029-125539-871040/smoke-report.json`。Qt 改版继续使用同一模型管理和请求代码。本轮使用真实 USB 相机及本地 SSE 测试服务，验证了默认/最小窗口尺寸、流式显示、分析期间预览刷新、相机重连、修改问题、取消、断流报错及退出清理；未重新加载模型权重。记录见 `logs/camera-ui-20261029-131128-151223/qt-smoke-report.json`。

后台回归检查：`python3 -m unittest discover -s tests -v`。桌面集成检查：先关闭占用相机的窗口，再运行 `python3 tests/qt_camera_smoke.py`；该检查使用带有“界面测试”标记的模拟回复，不启动模型。Qt 界面在 `scripts/camera_ui_qt.py`，轻量模型后端在 `scripts/camera_ui_backend.py`，`scripts/camera_ui.py` 保持为原桌面快捷方式的入口。

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

## USB 相机持续推理

数据流：**USB 相机 → 宿主机 OpenCV 持续取帧 → 最新一帧 JPEG → 本地 vLLM → 流式文字回答**。
模型只加载一次并保持运行。相机采集和模型推理解耦：请求串行执行，采集线程始终只保留最新一帧，不积压待处理图片。模型输入使用 vLLM 的 [image_url / base64 图片接口](https://docs.vllm.ai/en/latest/features/multimodal_inputs/)。

当前模型是 Cosmos-Reason2-2B 的 INT4 版本，负责图像/视频理解并输出文字。相机服务使用单图输入 `image=1, video=0`、1024 上下文；下面的脚本每次独立分析一张最新图片，不携带历史。分析连续动作或运动趋势需要多帧/短视频输入，并重新验证上下文预算、内存和延迟。

### 1. 在 Jetson 宿主机确认相机

```bash
cd /path/to/cosmos-camera
python3 scripts/camera_infer.py --list-devices
python3 scripts/camera_infer.py --source /dev/video0 --snapshot-only images/camera-check.jpg
```

打开 `images/camera-check.jpg` 确认画面正常。设备路径按列出的结果调整；一个 USB 相机可能产生多个节点，其中元数据节点不能用于取图。若没有 `/dev/video*`，先检查 USB 连接和驱动；若打开失败，再检查相机是否被其他应用占用、当前用户是否有设备访问权限。`--snapshot-only` 不需要模型服务，使用指定路径保存照片，会覆盖同名文件。

宿主机需要 OpenCV。可用 `python3 -c "import cv2; print(cv2.__version__)"` 检查；缺少时安装系统包 `sudo apt-get install python3-opencv`。无需在宿主机安装 PyTorch 或 vLLM，也无需将 USB 设备映射进模型容器。

### 2. 终端 A：启动常驻模型服务

```bash
cd /path/to/cosmos-camera
python3 scripts/serve.py
```

保持终端打开，等服务加载完成；另一终端执行 `curl http://127.0.0.1:8000/v1/models`，返回的模型列表应包含 `cosmos`。如果已有相同接口的模型服务在运行，直接复用，不重复启动。`serve.py` 沿用单图测试的固定容器镜像，相机模式默认将上下文降到 1024 token、KV 缓存降到 128 MiB、图片预算降到 131072 像素，以留出采集的内存空间；Ctrl+C 会停止本次创建的容器。

图片、问题和输出合计必须在 1024 token 内；单纯调大客户端 `--max-tokens` 可能超出上下文。需要恢复原单图服务预算时可运行 `python3 scripts/serve.py --max-model-len 2048 --kv-cache-mib 256 --max-pixels 262144`，但会增加内存压力。原 `run_single_image.py` 的测试默认配置保持不变。

原来的 `run_single_image.py` 会在两次测试后停止服务，因此不能将它当作常驻服务。

### 3. 终端 B：持续取最新画面并推理

```bash
cd /path/to/cosmos-camera
python3 scripts/camera_infer.py --source /dev/video0
```

默认请求相机输出 640×480、15fps（驱动可能协商其他值），提交给模型前将最长边缩到 384 像素；每次最多生成 128 token，使用 1.1 的重复惩罚。默认问题要求画面模糊时说明无法可靠判断，清晰时用一句话描述。终端流式显示中文回答，结果追加到每次独立的 `logs/camera-日期时间.jsonl`；默认不保存视频或相机照片。Ctrl+C 停止采集和请求，常驻模型由终端 A 管理。

自定义关注的问题，或先只验证一次调用：

```bash
python3 scripts/camera_infer.py --source /dev/video0 --count 1 \
  --prompt '画面中有没有人？如果有，描述其位置和可见姿态；看不清就说明不确定。'
```

| 参数 | 含义 |
| --- | --- |
| `--interval 2` | 两次请求**开始时间**至少间隔 2 秒；一次回答若耗时 6 秒，则结束后才发下一次，不保证每 2 秒得到结果 |
| `--max-tokens 256` | 增加输出预算；若状态为 `truncated`，回答未完整结束，需要缩短问题或在上下文允许范围内增加预算 |
| `--max-side 320` | 进一步降低提交图片分辨率，可测量速度与细节识别的取舍 |
| `--repetition-penalty 1.1` | 默认抑制重复输出；设为 1 关闭，模糊画面仍应先改善对焦 |
| `--fourcc MJPG` | 可选 USB 采集格式；仅在相机支持该格式时使用，默认由驱动协商 |
| `--count 5` | 完成 5 次请求后退出；默认 0 表示持续运行 |
| `--url http://127.0.0.1:8001/v1/chat/completions` | 对应 `serve.py --port 8001` |
| `--max-frame-age 2` | 只提交进程在最近 2 秒内收到的帧；无法取得新鲜帧时超时报错 |

日志记录首段文字延迟、请求总耗时、发送时画面年龄、回答结束时画面年龄、完整回答及截断状态。画面时间是进程收到帧的时间，不是传感器曝光时间；相机/驱动/网络自身缓存的延迟仍需实际测量。请求失败时脚本保存错误并停止，便于排查。

已有单图测试的一次热请求为 **6.37 秒 / 93 个输出 token**，首段文字约 **0.42 秒**；这只是该图片的一次测量，不保证相机输入有相同表现。先按数秒更新一次分析来验证，不把相机的 15/30fps 当作模型的分析频率。

脚本也接受 RTSP URL，或通过 `--gstreamer` 接收管线；这些输入依赖对应 OpenCV 后端。当前环境的 OpenCV 支持 V4L2/FFmpeg，但未启用 GStreamer。USB 相机无需 GStreamer。

本机 USB 相机已识别为 `USB2.0_CAM1`（USB ID `05a3:9230`），`/dev/video0` 已成功采集 640×480 图片，见 `images/camera-check.jpg`。同一相机还产生 `/dev/video1` 节点。初始画面较模糊，实际使用前应固定相机并调整对焦。

2026-10-25 已使用上述默认相机配置完成两次真实调用，日志为 `logs/camera-live-low-memory-20261025.jsonl`。两次均正常结束（`status=ok`），输出“画面模糊，无法可靠判断。”；首次请求 14.53 秒，第二次 0.83 秒。处理的帧序号从 8 跳到 223，证明推理期间的旧帧没有在客户端排队。这验证了真实采集、图片提交、流式输出和最新帧调度；短回答的 0.83 秒不能代表复杂场景分析速度，清晰画面的识别质量仍需继续实测。

此前使用较大服务预算及普通描述提示词的两次相机请求耗时 49.23/41.62 秒，回答重复且被截断，记录在 `logs/camera-live-check-20261025.jsonl`。当时设备交换空间用满。默认配置已据此降低预算并增加重复抑制；由于提示词、输出长度和服务参数同时变化，不能把两组时间当作严格的性能对照。

## 原有单图测试

用已有 `images/test.jpg`，测量启动加载期间整机 RAM 采样峰值、客户端首段文字延迟和一次回答的总耗时。适配当前 Jetson Orin 8GB 与已下载的 INT4 模型。

## 直接运行

在设备终端执行：

```bash
cd /path/to/cosmos-camera
python3 scripts/run_single_image.py
```

宿主机只需要 Python 标准库、Docker 和已有的 `tegrastats`；PyTorch、vLLM 和 Transformers 使用现有容器内的版本。无需在宿主机安装这些包。脚本离线读取模型，使用镜像摘要固定版本。

脚本会打印日志目录，然后自动完成：

1. 每 100ms 采集整机 RAM 和 SWAP，先记录 1 秒基线。
2. 启动只监听 `127.0.0.1:8000` 的模型服务。
3. 等待 `/health` 成功且 `/v1/models` 出现 `cosmos`，保存加载内存统计。
4. 发送第一次单图请求，实时显示回答、记录耗时。
5. 再发一次相同请求，记录模型初始化后的表现。
6. 停止本次测试容器，保存全部结果。Ctrl+C 也会尝试清理并保存已采集的数据。

若首次启动久等，可在另一个终端执行（将目录替换为脚本打印的目录）：

```bash
tail -f logs/single-image-日期时间/server.log
```

不要同时重复运行测试。首次编译或初始化可能比之后慢；默认最多等 900 秒，可使用 `--startup-timeout 1800` 延长。

## 保存的文件

每次测试创建独立的 `logs/single-image-日期时间/`，不会覆盖前次记录。

| 文件 | 内容 |
| --- | --- |
| `summary.json` | 状态、启动耗时、各阶段内存峰值、两次请求结果与完整启动命令 |
| `load-memory.json` | 服务就绪时冻结的加载内存统计 |
| `first.json` / `second.json` | 单次回答、首段文字延迟、总耗时、停止原因、token 用量 |
| `memory.jsonl` | 带阶段标签及时间的逐次采样，保留 tegrastats 原文 |
| `server.log` | vLLM 初始化和运行日志 |
| `server-command.txt` | 本次使用的完整 Docker 命令 |

成功标准：`summary.json` 的 `status` 为 `complete`，两次请求均有非空 `answer`、正常结束且未达到 token 上限。还应人工对照原图，确认回答与图像内容一致。

## 三个指标怎么读

- **加载峰值**：`summary.json → memory → phases → loading → peak_ram_mb`。这是从 Docker 启动到服务接口就绪期间的整机共享 RAM 采样峰值，包含模型权重、初始化、KV 缓存及其他进程。单位沿用 tegrastats 的 MB。100ms 采样可能漏掉更短的峰值。
- **启动前 RAM**：`memory.baseline_ram_mb`，为 1 秒基线采样的中位数。`increase_over_baseline_mb` 是峰值相对基线的整机增加量，其他程序和缓存变化也会影响它，不是模型独占内存。
- **首段文字延迟**：`requests.first.first_text_seconds`。从客户端发请求到收到第一段非空 `delta.content`；包含传输、图片处理和模型计算，不含读图及 base64 编码，也不含服务加载。它是客户端观察值，不是引擎内部首 token 时间。模型若把 `<think>` 直接写在 content 内，它也会算作首段文字；独立 reasoning 字段另行记录。
- **完整回答耗时**：`requests.first.total_seconds`。从发请求到流正常结束。只有未截断、未报错且正常收完时，才称为完整回答耗时。

第一次请求反映新启动服务的首次请求，第二次反映同一进程初始化后的表现。前缀缓存和多模态处理缓存均关闭；这仍是两次样本，不能据此得出稳定的平均值或 P95。系统磁盘缓存没有清空，因此加载时间不是断电后的冷启动时间。

## 为什么这样配置

| 配置 | 用途 |
| --- | --- |
| `--max-model-len 2048` | 图片 token、提示词和输出合计最多 2048，减少上下文预算 |
| `--max-num-seqs 1` | 一次处理一个请求，符合先跑通单图的目标 |
| `--dtype half` | 使用 FP16 激活，保留模型自带的 INT4 权重量化 |
| `--kv-cache-memory-bytes 268435456` | 固定 256MiB KV 缓存；按本地模型配置，2048 token 的 KV 约需 224MiB |
| `--gpu-memory-utilization 0.1` | 当前 v0.19 在固定 KV 大小时仍检查此值；降低启动空闲内存门槛。这时它不是总内存上限，真实内存仍必须足够 |
| `--enforce-eager` | 关闭 CUDA Graph，避免其额外占用；先验证功能，之后再调性能 |
| `--max-num-batched-tokens 512` | 限制每次调度的 token 预算 |
| `--mm-processor-kwargs` | 图片预处理限制到最多 262144 像素；保留纵横比并按模型要求调整尺寸，原文件不改动 |
| `--no-enable-prefix-caching` | 不让第二次请求直接复用第一次的提示词 KV |
| `--mm-processor-cache-gb 0` | 关闭图片处理缓存 |
| `--limit-mm-per-prompt '{"image":1,"video":0}'` | 仅允许单图，不为视频输入分配预算 |

固定 KV 预算不包含模型权重和视觉编码器等开销，也不会把内存不足的设备变成内存足够。

## 请求与计时核心

`scripts/bench_image.py` 识别图片真实格式，封装成 data URL，再向本地 `/v1/chat/completions` 发请求。现有 `test.jpg` 实际是 PNG，因此不能只依赖文件后缀决定 MIME。

请求使用 `stream=true`，让服务器边生成边返回文本，才能观察开始出字的时间。客户端用 `time.perf_counter()` 的单调时钟计时，忽略只含 role 的事件，收到第一段非空 content 才记录首字时间。最终必须收到正常结束标志；HTTP 错误、断流及空回答均不会作为成功记录。

## 调整与排错

端口占用：

```bash
python3 scripts/run_single_image.py --port 8001
```

若 `status=truncated` 或 `finish_reason=length`，表示达到输出上限，不能当成完整回答。默认最多生成 1024 token，可增加到 1536；图片和提示词也占用 2048 上下文，过大时会报超限：

```bash
python3 scripts/run_single_image.py --max-tokens 1536
```

若日志显示 CUDA out of memory，先关闭自己不需要的应用、推理服务或容器，释放共享 RAM，再重测。脚本不会自动停止已有服务。也可先缩小图片预算：

```bash
python3 scripts/run_single_image.py --max-pixels 131072
```

若显示 `Free memory ... less than desired GPU memory utilization`，说明启动时连当前设定的空闲内存门槛也无法满足；先释放内存。不要把调大利用率当成解决方法。

若报算子、量化或架构不支持，保留 `server.log` 最后的完整错误；CUDA 算子检查通过不保证所有模型路径都兼容，缩小图片不能解决不支持的算子。

若显示 Docker socket 权限错误，说明终端用户无法访问 Docker。此前的受限沙箱访问结果不代表设备终端一定也有此问题；以当前终端实际报错为准。

## 手动拆分时的请求命令

若你自己用 `server-command.txt` 的命令启动并保持服务运行，可在另一终端单独发图：

```bash
python3 scripts/bench_image.py --max-tokens 1024 --output logs/manual-first.json
```

自动流程结束时会停掉服务，所以不能在它结束后直接运行上面的请求命令。自动流程已经做完两次请求，结果在该次日志目录中。


2026-10-25 视频窗口验证：`--preview-only --preview-seconds 5` 成功显示 66 个不同画面；真实 Cosmos 服务加载约 120.15 秒，`--preview --count 1 --max-tokens 64` 调用状态为 ok，请求耗时 3.25 秒，推理期间显示 51 个不同画面。返回“画面模糊，无法可靠判断。”。这只验证了采集、显示、模型调用与不阻塞预览，不代表清晰场景识别质量或稳定推理速度。测试后停止模型，没有新增常驻服务。日志：`logs/preview-check-20261025-225811/`。
