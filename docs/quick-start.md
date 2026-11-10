# 安装与快速上手

本项目面向 NVIDIA Jetson Orin / Ubuntu 22.04 / JetPack 6 / L4T R36.4.x，参考验证版本为 R36.4.3。需要已登录图形桌面、USB 相机，以及与设备匹配的 JetPack 和 NVIDIA 驱动。建议预留至少 25 GiB 空间；Docker 所在存储盘也需要足够空间。

## 获取安装包

在 [GitHub Releases](https://github.com/yuanimperialhal/Embed_WM_deploy/releases) 查看已发布的版本。当前若尚无 Release，可按 [发布指南](github-release.md#2-本地验证) 从源码生成 DEB。不要把 GitHub 的 Source code ZIP 当作安装包。

有正式 Release 后，下载 `cosmos-camera_latest_arm64.deb` 和对应的 `.deb.sha256`。在两份文件所在目录执行：

```bash
sha256sum -c cosmos-camera_latest_arm64.deb.sha256
sudo apt update
sudo apt install ./cosmos-camera_latest_arm64.deb
```

无需解压，也可以双击 DEB 使用系统图形安装器。习惯 GDebi 时：

```bash
sudo apt install gdebi
sudo gdebi ./cosmos-camera_latest_arm64.deb
```

APT 与 GDebi 两种方式任选一种。系统包管理器负责 Python、Qt、OpenCV、字体、Docker 和 NVIDIA Container Toolkit 等依赖。软件源须与当前 JetPack 匹配；缺少 NVIDIA Container Toolkit 软件源时，参考 [NVIDIA 官方安装说明](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。

## 首次准备

打开应用菜单中的 **Cosmos 相机助手**，或运行：

```bash
cosmos-camera
```

1. 选择自动下载已验证模型，或选择已有的完整模型目录。
2. 自动下载前，去 [模型作者页面](https://huggingface.co/embedl/Cosmos-Reason2-2B-W4A16) 申请访问。获批后填写具有该模型读取权限的 [Hugging Face Token](https://huggingface.co/settings/tokens)。Token 不写入配置和日志。
3. 点击「开始准备」。向导检查环境、下载固定版本模型并验证 SHA256，再拉取固定摘要的 vLLM 镜像。模型约 2.8 GB，首次准备需要访问 Hugging Face、ghcr.io 和系统软件源。
4. 如提示 Docker 权限或 runtime 问题，使用「配置系统环境…」。操作会先说明影响并请求管理员授权；若新增了用户组权限，注销桌面并重新登录后继续。
5. 准备完成后进入主界面，连接 USB 相机，点击「分析一次」或「开始连续分析」。

系统配置会启用 Docker，必要时注册 NVIDIA runtime 并重启 Docker，将当前用户加入 docker 和 video 组。Docker 组具有管理员级能力；重启 Docker 可能影响其他容器。已有运行环境正常时无需执行此步骤。

下载中断后可重试，模型支持断点续传。准备完成后，本地推理无需重复下载模型或镜像。模型首次加载可能需要几分钟。

## 常见问题

| 情况 | 处理方法 |
| --- | --- |
| 下载返回 401 / 403 | 确认模型访问已获批，Token 有该模型的读取权限 |
| 无法连接 Docker | 完成系统配置；增加用户组权限后注销并重新登录 |
| 未注册 NVIDIA runtime | 使用准备向导中的系统配置入口 |
| 相机无画面 | 检查 USB 连接与设备选择，关闭其他占用相机的程序 |
| 模型加载失败或内存不足 | 关闭其他大型应用，再查看模型服务日志 |
| 文件校验失败 | 按提示重试；不要用损坏文件继续加载模型 |
| 下载链接不可用 | 确认仓库已发布带 DEB 附件的正式 Release |

诊断与重新准备命令：

```bash
cosmos-camera --check
cosmos-camera --setup
```

请使用普通用户启动界面。环境检查通过表示基础文件和权限满足要求，实际推理仍需在目标设备验证。

## 升级与卸载

升级前退出应用，再安装新版 DEB。已有设置、模型和日志保留。

```bash
sudo apt remove cosmos-camera
```

卸载保留个人配置、日志、缓存、模型和 Docker 镜像，也不主动回滚共享的 Docker 配置。数据目录与完整操作说明见 [安装版使用说明](installed-app-guide.md)。

公开发布的是小型应用 DEB，模型和镜像分别从原作者处获取。旧私有离线包不进入公开发布流程；详见 [第三方说明](../THIRD_PARTY_NOTICES.md)。
