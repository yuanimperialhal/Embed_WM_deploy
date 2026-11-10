# 从相机 Demo 到第一款可安装的软件

> 开发参考文档：示例中的 `/path/to/cosmos-camera` 需换成实际源码路径；已安装软件的操作以 [安装版说明](installed-app-guide.md) 为准。

这份教程直接使用你当前的 Cosmos 相机项目。你已经跑通了相机、模型和 Qt 界面，我们从现有代码继续，把“能在开发目录里运行”推进到“能够交给别人安装使用”。

本次实际生成的是 **Cosmos 相机助手 0.1.0 的 Jetson Linux 安装包**。它是首个可安装预览版；第二台干净设备上的安装、升级、卸载和长时间运行还需要验收，不能把“生成了安装包”直接等同于“已经达到正式发布质量”。

## 1. 先给第一版画一个终点

第一版只解决一件事：**USB 相机接在 Jetson 上，用户打开软件，点击按钮，就能看到画面和中文分析。**

它的完整使用过程应该是：

1. 拿到安装包，知道自己的设备是否支持。
2. 安装后在应用菜单找到图标。
3. 第一次打开，在界面中选择模型目录。
4. 看到相机画面，点击“分析一次”，得到回答。
5. 遇到断线或加载失败，知道发生了什么、到哪里看日志。
6. 关掉后能再次打开；保存过的设置仍在。
7. 升级不会覆盖个人配置和模型；卸载能移除程序。

这七件事就是第一版的验收标准。先完成它们，再加多路相机、检测框、历史搜索、Windows 客户端等功能。

## 2. 认识你已经写出的软件结构

| 部分 | 当前文件 | 你可以怎样理解 |
| --- | --- | --- |
| 软件入口 | `scripts/camera_ui.py` | 用户点图标后先执行这里，检查参数，再打开窗口 |
| Qt 界面 | `scripts/camera_ui_qt.py` | 窗口、按钮、输入框、设置与状态显示 |
| 后台任务 | `scripts/camera_ui_backend.py` | 启停模型，发送请求，把回答交给界面 |
| 相机与图像请求 | `scripts/camera_infer.py` | 从 USB 相机持续读图，取最新一帧，发给模型 |
| 推理服务 | `scripts/serve.py` | 启动当前已验证的 NVIDIA 推理容器 |
| 配置与目录 | `scripts/app_runtime.py` | 决定配置、日志、模型缓存放在哪里 |
| 环境检查 | `scripts/app_diagnostics.py` | 检查模型、设备权限、Docker 和 NVIDIA runtime |
| 版本 | `VERSION` | 当前为 `0.1.0` |
| 打包入口 | `packaging/build_deb.py` | 把应该交付的文件整理成安装包 |

例如“分析一次”按钮的路径：

```text
点击按钮
  → start_analysis(once=True)
  → 等待模型就绪、取得最新相机帧
  → _submit() 把请求交给独立进程
  → inference_worker() 调用模型
  → 回答片段经进程管道返回
  → Qt 定时读取片段，更新右侧文字
```

你要学会的重点是：**按钮负责表达用户意图，后台负责耗时工作，界面负责展示状态和结果。** 如果让按钮回调直接等待几十秒网络请求，窗口就容易卡住。

这里 Qt 每隔约 80 毫秒检查一次相机和消息。相机线程一直取最新帧，模型请求串行进行，不会把每一帧都排队。

## 3. 第一步：让代码离开你的个人目录也能运行

原先程序从项目目录寻找模型，把日志也写在项目里。安装后程序会在系统目录，普通用户不应该在那里写文件。

现在分成下面几类：

| 内容 | 安装版默认位置 | 谁管理 |
| --- | --- | --- |
| Python 程序、图标、说明 | `/usr/lib/cosmos-camera/` | 安装包 |
| 启动命令 | `/usr/bin/cosmos-camera` | 安装包 |
| 应用菜单入口 | `/usr/share/applications/cosmos-camera.desktop` | 安装包 |
| 用户设置 | `~/.config/cosmos-camera/settings.json` | 当前用户 |
| 日志和分析结果 | `~/.local/state/cosmos-camera/logs/` | 当前用户 |
| 模型缓存 | `~/.cache/cosmos-camera/` | 当前用户/推理服务 |
| 模型权重 | 设置窗口选择的目录 | 设备部署者 |

`~` 代表当前用户的家目录，不再假设所有人都叫 `seeed`。这些默认用户目录遵循 [XDG 目录规范](https://specifications.freedesktop.org/basedir/latest/)。源码模式仍把日志和缓存放在项目目录，便于继续开发。

打开 `scripts/app_runtime.py`，先读四个函数：

- `settings_path()`：配置存在哪里。
- `default_settings()`：第一次运行时使用什么默认值。
- `load_settings()`：读取并检查配置。
- `save_settings()`：先写临时文件，完整写入后再替换正式文件，降低意外中断导致配置损坏的可能。

**亲手练习：**在 Qt 窗口里改一个问题，暂停分析，打开“…” → 设置，选择有效模型目录，点击“保存设置”。退出并重开，检查问题是否恢复。只在输入框里修改而没有保存，不会覆盖上次保存的设置。

## 4. 第二步：让用户不需要看代码来排查错误

打开右上角“…”：

- **设置**：选择模型目录，保存当前设备和问题。
- **检查运行环境**：检查模型文件、设备权限、Docker、NVIDIA runtime 和固定镜像。
- **打开日志文件夹**：找到可供排查的记录。
- **关于相机助手**：查看版本和目录。

这次补了配置格式校验。配置损坏时会提示，并保留原文件；不会悄悄把坏配置覆盖掉。模型缺失时会告诉用户去设置里选目录，而不是让窗口无声退出。

**亲手练习：**暂时把相机拔掉，观察程序的反馈，再插回来重新打开。不要只验证一条“全部正常”的路径。

## 5. 第三步：亲手构建安装包

下面的命令在 Jetson 项目目录执行。打包不需要管理员权限，也不会安装系统软件、下载模型或启动推理容器。

```bash
cd /path/to/cosmos-camera
python3 packaging/build_deb.py
```

本次会生成：

```text
dist/
├── cosmos-camera_0.1.0-1_arm64.deb
├── cosmos-camera_0.1.0-1_arm64.deb.sha256
└── cosmos-camera_0.1.0-1_arm64.release.json
```

- `.deb` 是安装包。
- `.sha256` 用来检查文件传输后是否一致。
- `.release.json` 记录版本、架构、大小、校验值和外部依赖。

文件名中的 `0.1.0` 是软件版本，`-1` 是这次打包的修订号，`arm64` 对应 Jetson 的 CPU 架构。

打开 `packaging/build_deb.py`，按顺序看这五步：

1. 从 `VERSION` 读取版本号。
2. 按明确的文件清单复制程序、图标和说明，避免把模型、缓存、日志和个人文件打进去。
3. 创建启动脚本和 `.desktop` 应用入口。
4. 写 `DEBIAN/control`，告诉系统包名、版本、架构和依赖。
5. 调用 `dpkg-deb --build` 生成安装包，再计算 SHA256。

`.deb` 不要求把 Python 变成机器码。本版选择让系统包管理器安装 Python、PyQt5、OpenCV 等依赖，所以应用包本身很小。这是软件交付的一种正常方式。Debian 对包结构和依赖的解释见 [官方文档](https://www.debian.org/doc/manuals/debian-faq/pkg-basics.en.html)。

## 6. 第四步：先检查包，再安装

查看安装包信息和包含哪些文件：

```bash
cd /path/to/cosmos-camera
dpkg-deb --info dist/cosmos-camera_0.1.0-1_arm64.deb
dpkg-deb --contents dist/cosmos-camera_0.1.0-1_arm64.deb
```

检查传输后的校验值：

```bash
cd /path/to/cosmos-camera/dist
sha256sum -c cosmos-camera_0.1.0-1_arm64.deb.sha256
```

检查系统准备如何安装，不真的安装：

```bash
cd /path/to/cosmos-camera
apt-get -s install ./dist/cosmos-camera_0.1.0-1_arm64.deb
```

实际安装这一步会写系统目录，需要管理员权限。先关闭开发版窗口，避免两个程序争抢相机，再执行：

```bash
cd /path/to/cosmos-camera
sudo apt install ./dist/cosmos-camera_0.1.0-1_arm64.deb
```

安装后在应用菜单搜索 **Cosmos 相机助手**。可以固定到 Dock；若要放到桌面，复制 `/usr/share/applications/cosmos-camera.desktop` 到自己的桌面，并按桌面提示“允许启动”。

**不要继续点击之前指向开发目录的旧快捷方式来验证安装版。** 可以在“关于”中确认安装版日志位于 `~/.local/state/cosmos-camera/logs/`。

本次构建和检查没有替你执行系统安装。你可以把上面的安装过程作为自己的第一轮实际操作。

## 7. AI 软件还要交付模型运行环境

安装 Qt 软件不等于安装好了整个 AI 环境。当前部署包含三部分：

1. **相机助手安装包**：窗口、相机读取、请求发送、设置、日志。
2. **模型文件**：完整的 Cosmos 权重、tokenizer 和配置。
3. **推理环境**：匹配的 JetPack/L4T、Docker、NVIDIA Container Runtime、固定版本 vLLM 镜像。

本项目固定使用的镜像是：

```text
ghcr.io/nvidia-ai-iot/vllm@sha256:817f0f940d2d9c9067d861d2118d7bf58c40873598f0c35e19c8516269ebc4bd
```

当前这台设备已有这些条件。安装版第一次打开后，选择你已有的模型目录：

```text
/path/to/cosmos-camera/models/cosmos-download
```

这是**当前设备上的一次配置选择**，没有写进安装包的启动路径。另一台机器选择它自己的实际目录。

安装器不会在后台偷偷下载巨大文件，也不会改系统的 Docker 权限。把同一套环境交付到第二台 Jetson 时，要先准备并验证这些依赖。后续版本可以进一步做成图形化的部署向导或离线环境包。

## 8. 第五步：做真实的发布验收

现在已经有自动检查入口：

```bash
cd /path/to/cosmos-camera
python3 -m unittest discover -s tests -v
python3 tests/package_smoke.py dist/cosmos-camera_0.1.0-1_arm64.deb
```

有桌面和空闲 USB 相机时，可以运行带界面的检查：

```bash
python3 tests/package_smoke.py dist/cosmos-camera_0.1.0-1_arm64.deb --gui
```

这会从安装包中解出程序，将程序目录设为只读，使用系统 Python/Qt/OpenCV，检查相机预览、流式显示、保存设置、取消和退出。回答来自明确标记的本地测试服务，**不会加载真实模型**。它能验证界面和交付结构，不能替代真实推理验收。

正式发布前，按以下表格在另一台符合条件的 Jetson 上验收：

| 检查 | 合格标准 |
| --- | --- |
| 干净安装 | 不依赖开发目录，应用菜单可以启动 |
| 首次设置 | 能选择模型目录，保存后重开仍然有效 |
| 真实模型 | 点击“分析一次”确实启动模型并完成回答 |
| 相机异常 | 拔插后有明确反馈，可以恢复 |
| 暂停与关闭 | 停止请求，释放相机，正确清理自己启动的模型 |
| 长时间使用 | 观察至少一段实际使用时长，记录崩溃、内存与响应情况 |
| 升级 | 新版本替换程序，配置和模型保留 |
| 卸载 | 程序入口消失，个人数据按说明保留 |

不要把“检查运行环境显示通过”作为真实模型测试的替代，也不要把“解包启动成功”写成“干净系统安装通过”。

## 9. 第六步：发布下一版，形成闭环

例如你修改了一个界面问题：

1. 修改代码并验证。
2. 将 `VERSION` 从 `0.1.0` 改为 `0.1.1`。
3. 在 `docs/release-notes.md` 记下改动和已知限制。
4. 重新运行测试和 `python3 packaging/build_deb.py`。
5. 退出旧应用，安装 `cosmos-camera_0.1.1-1_arm64.deb`。
6. 检查设置还在，旧问题已修复。

升级依然是 `sudo apt install ./新的安装包.deb`。卸载是：

```bash
sudo apt remove cosmos-camera
```

卸载不会自动删除用户家目录里的配置、日志、缓存或单独存放的模型。本版不设置开机启动，也不自动更新。

真正对外发布前，还要把打包脚本中占位的维护者信息换成你的发布信息，并明确项目许可证、用户帮助和问题反馈方式。这些是交付内容的一部分。

## 10. 如果下一步要做 Windows EXE

你现在用的是 Python + Qt，Windows 界面可以复用大量代码。要完成 Windows 版，需要先处理当前的 Linux 专用部分，例如文件锁、`/dev/video0` 相机设备、Jetson 容器启动方式和目录规范。

一个自然的架构是：模型继续在 Jetson 上运行，Windows 客户端通过经过配置的接口访问它。是否使用这条路线，要先确定相机接在哪台设备、通信方式和访问权限。

完成 Windows 适配后，在 Windows 上使用 PyInstaller 打包。一般先验证目录模式，便于定位缺失文件，再考虑单文件模式；[PyInstaller 官方说明](https://pyinstaller.org/en/stable/operating-mode.html)也建议这个顺序。当前项目不能直接加一个 `--onefile` 就得到可用的 Windows 版本。

## 今天最值得亲手做的三个动作

1. **运行构建命令**：看到自己的 `.deb` 重新生成。
2. **安装并从应用菜单打开**：确认自己运行的是安装版。
3. **保存一个问题、退出再打开**：体会程序、配置和安装包是怎样配合工作的。

完成后，你就已经亲手走过了一次“写代码 → 构建 → 安装 → 使用”的软件交付流程。接着按验收表补齐真实部署验证，再迭代下一版。
