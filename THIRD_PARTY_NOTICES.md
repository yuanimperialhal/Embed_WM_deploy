# 第三方组件与分发范围

Copyright: 原项目作者及各自贡献者。发布者应在正式授权分发前补充具体署名与项目许可证。

本项目的 DEB 包含应用脚本、图标、文档和模型文件校验清单，不包含模型权重、vLLM 镜像、Qt 或 NVIDIA 驱动的二进制内容。系统依赖通过 APT 安装；模型与容器在首次准备时从原始发布者处获取。

## 模型

- 模型：[`embedl/Cosmos-Reason2-2B-W4A16`](https://huggingface.co/embedl/Cosmos-Reason2-2B-W4A16)。
- 基础模型：[`nvidia/Cosmos-Reason2-2B`](https://huggingface.co/nvidia/Cosmos-Reason2-2B)。
- 应用当前使用的固定 revision 和文件 SHA256 见 `config/model-source.json`。
- 模型卡注明 NVIDIA Open Model License、额外信息的 Apache 2.0，以及优化组件的 Embedl Models Community Licence v1.0，并标注 no redistribution。模型访问须由使用者向作者申请，当前条款以原作者页面为准。
- 请勿将现有的模型目录、含权重的离线包、个人 Hugging Face Token 或登录凭证上传到公开仓库或 Releases。

## 推理与桌面依赖

- [NVIDIA Jetson vLLM 镜像](https://github.com/NVIDIA-AI-IOT/vllm)：镜像按 `scripts/run_single_image.py` 中的摘要固定，由 Docker 拉取；各组件遵循各自许可。
- [PyQt5](https://www.riverbankcomputing.com/software/pyqt/)、Qt、OpenCV、Pillow、Noto 字体等通过系统包管理器安装。其许可证随各系统软件包提供。
- Docker 和 NVIDIA Container Toolkit 是系统级依赖。应用卸载不会主动移除它们或回滚共享的 Docker 配置。

## 项目源代码的发布许可

原项目未提供整体源代码许可证；本次整理也没有替作者选择开源许可证。公开上传源码不自动授予开源再利用许可。维护者正式对外授权分发前，应补充项目 `LICENSE`、真实维护者信息，并确认其与 PyQt5 等依赖许可的兼容性。本文只是依赖与发布范围记录，不替代任何组件的许可文件。
