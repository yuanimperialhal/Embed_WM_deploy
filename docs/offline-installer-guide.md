# 把相机助手、模型和推理镜像装进一个可执行文件

> 历史开发参考：以下记录针对 0.1.1 私有离线部署。0.2.0 的公开交付方式是 DEB + 首次准备向导，见 [GitHub 发布指南](github-release.md)。当前量化模型页面标明 no redistribution；不要把含权重的旧离线包上传到公开仓库或 Releases。离线工具仅在你已有适用授权的范围内使用。

本项目现在提供 **Jetson Linux 单文件离线安装器**。目标文件名为：

```text
CosmosCamera-0.1.1-jetson-arm64.run
```

它是可执行的安装文件。安装后用户在应用菜单点击“Cosmos 相机助手”，使用 Qt 窗口里的按钮。Windows 的 `.exe` 需要另一套构建与适配；本文件面向当前这类 Jetson。

## 一个文件里有什么

```text
可执行的 .run 文件
├── 安装入口：校验、解包、执行安装步骤
├── 相机助手 .deb
├── 完整 Cosmos 模型目录（约 2.8 GB）
├── 已验证的 vLLM Docker 镜像归档（约 9.1 GB）
├── Python / Qt / OpenCV 等系统依赖 .deb
├── 离线软件源索引
└── 文件清单及 SHA256 校验值
```

最终文件经过压缩，实际大小以构建结果为准。界面程序原来只有约 112 KB，但把模型与镜像一起交付后，安装文件会达到 GB 级。

这是“单文件交付”。安装后会把不同内容放在合适的位置，正常启动软件不会重新解压这整个文件。

## 适用的目标设备

本版明确针对 **arm64 Jetson、Ubuntu 22.04、Jetson Linux / L4T R36.4.3**，需要已经具有：

- 与设备匹配的 JetPack 和 NVIDIA 驱动。
- 可用的本机 Docker 和已注册的 NVIDIA Container Runtime。
- 已登录的图形桌面。
- 将来运行界面的普通用户具有 Docker 和 USB 相机访问权限。

这些是设备基础环境。安装器不会刷写 JetPack、替换 GPU 驱动、修改 Docker 用户组，也不会以 root 身份运行相机界面。系统版本不匹配或权限不足时，会在安装前停止并说明原因。

Qt/Python/OpenCV 的应用依赖包已收进离线包中。安装时 APT 使用临时的本地文件软件源，不使用目标设备的互联网软件源。打包时需要联网下载这些依赖；安装时不需要下载模型或推理镜像。

## 怎样使用这个文件

复制 `.run` 到目标 Jetson。若用 U 盘，文件系统需要支持大于 4 GB 的单个文件；FAT32 不适合。

在文件所在目录执行：

```bash
chmod +x CosmosCamera-0.1.1-jetson-arm64.run
./CosmosCamera-0.1.1-jetson-arm64.run --info
```

`chmod +x` 赋予可执行权限，复制文件时这个权限可能丢失。

先只校验，不安装：

```bash
./CosmosCamera-0.1.1-jetson-arm64.run --check
```

关闭相机助手和本项目模型服务后，执行安装：

```bash
sudo ./CosmosCamera-0.1.1-jetson-arm64.run --install
```

这一步会写系统目录，需要管理员授权。它会依次：

1. 校验整个数据段，损坏时停止。
2. 解包到临时目录，再逐个校验文件。
3. 检查 Jetson 系统、Docker、NVIDIA runtime、普通用户权限和空间。
4. 使用包内的离线软件源模拟安装，拒绝会移除其他软件的计划。
5. 导入推理镜像；已存在正确镜像时跳过。
6. 将模型放到 `/var/lib/cosmos-camera/models/` 下按内容编号的目录。
7. 安装或升级相机助手及所缺依赖。
8. 写入系统默认模型位置和镜像身份，删除本次解包临时文件。

随后从应用菜单打开 **Cosmos 相机助手**。首次使用会自动找到随包安装的模型。若此前已有个人配置，安装器不会覆盖；可在设置中选择安装器打印的模型目录。

默认临时解包目录在 `/var/tmp`。空间不足时，可使用另一个具有足够空间、支持执行文件的 Linux 文件系统：

```bash
sudo ./CosmosCamera-0.1.1-jetson-arm64.run --install --work-dir /mnt/storage/install-work
```

除了 `.run` 本身，还需要临时解包、模型和 Docker 层存储空间。干净设备建议预留至少约 35 GB 的额外可用空间；以安装器的实际检查结果为准。模型和 Docker 若分处不同分区，应分别检查空间。

直接用 root 账号部署时，要指明将来运行图形软件的普通用户：

```bash
sudo ./CosmosCamera-0.1.1-jetson-arm64.run --install --user seeed
```

这里的 `seeed` 需替换为目标设备上实际存在的普通用户名。

## 仅解包，观察里面的结构

学习时可以先解包到一个尚不存在的新目录：

```bash
./CosmosCamera-0.1.1-jetson-arm64.run --extract /path/to/new-folder
```

会得到 `install.py`、`manifest.json`、`app/`、`model/`、`image/` 和 `host-debs/`。这条命令不会安装应用、不会导入 Docker 镜像，但会占用解包空间。

## 自己重新制作这个单文件安装器

在原开发设备执行：

```bash
cd /path/to/cosmos-camera
python3 packaging/build_offline.py
```

当前用户需要能访问本机 Docker。不要为了构建而把整个项目以 root 身份运行。

构建脚本会自动：

1. 检查模型文件、固定镜像身份和磁盘空间。
2. 导出镜像；有有效的导出缓存时复用它。
3. 收集 Python/Qt/OpenCV 的离线依赖包；已有完整索引时复用。
4. 调用 `build_deb.py` 构建应用包。
5. 计算每个文件的校验值，生成清单。
6. 把启动代码和压缩数据段拼接成 `.run`，设置可执行权限。
7. 输出整包 SHA256、发行信息和完整文件清单。

构建产物在 `dist/`：

```text
CosmosCamera-0.1.1-jetson-arm64.run
CosmosCamera-0.1.1-jetson-arm64.run.sha256
CosmosCamera-0.1.1-jetson-arm64.manifest.json
CosmosCamera-0.1.1-jetson-arm64.release.json
```

使用者只需要 `.run`。附带 `.sha256` 方便独立检查传输结果；清单和发行信息便于开发者追踪问题。

`dist/offline-assets/` 是镜像导出和依赖下载缓存，不需要发送给使用者。保留它可以加快下次构建。构建过程只下载/导出文件，不安装或升级本机的软件。

## 代码应该按什么顺序学习

| 文件 | 学习重点 |
| --- | --- |
| `packaging/build_offline.py` | 怎样收集材料、写清单、生成一个文件 |
| `packaging/offline_bootstrap.py.in` | 可执行文件怎样找到自己后面的压缩数据，并校验、解包 |
| `packaging/offline_install.py` | 安装顺序、环境检查、离线 APT、导入镜像、保存默认配置 |
| `packaging/offline_common.py` | 文件校验与镜像身份验证 |
| `packaging/collect_runtime_debs.py` | 只下载应用依赖，不改变开发设备 |
| `scripts/app_runtime.py` | 安装后程序怎样找到随包模型和镜像 |

可以把 `.run` 理解为两段拼在一起：前面是很短的启动代码，后面是压缩的全部材料。操作系统先执行前面的代码，由它完成校验和解包，再运行安装脚本。

Docker 镜像的官方离线搬运方法是 [`docker image save`](https://docs.docker.com/reference/cli/docker/image/save/) 和 [`docker image load`](https://docs.docker.com/reference/cli/docker/image/load/)。这里特别处理了新旧 Docker 镜像存储对身份标识的差异，避免导入后应用仍找不到镜像。

## 升级、卸载与数据保留

升级应用不一定需要再次分发整个大包。模型和推理镜像不变时，继续分发新版本的小 `.deb` 即可；它会读取之前保存的系统部署信息。

卸载应用：

```bash
sudo apt remove cosmos-camera
```

这会保留模型、Docker 镜像、系统部署记录和个人设置。它们体积较大，且可能被重装后的程序复用。安装器不会自动删除已有模型或其他镜像。

安装中断时，本次临时解包会清理；已经导入的镜像或放好的模型可能保留。修复失败原因后可以重试，已有模型会先校验再复用。APT 安装自身若中断，需要先检查系统包状态，再重试；本版不是全系统事务回滚工具。

## 交付前还必须验证什么

生成安装器和校验成功，不能替代另一台真实设备的验收。正式发布前应验证：

- 符合目标版本的第二台 Jetson 上，断网安装成功。
- 首次打开能自动找到模型，真实推理成功。
- 不兼容的 JetPack、空间不足或损坏文件能被拒绝。
- 升级保留个人设置；卸载行为与说明一致。
- 相机插拔、取消、退出和长期运行正常。

本次构建不等于已经替你执行了系统安装，也不会在后台重装当前设备。
