# 发布到 GitHub：从源码到可点击下载的 DEB

## 发布内容

源码仓库包含界面、模型服务管理、首次准备向导、打包脚本、文档和测试。Release 附件包含：

- `cosmos-camera_0.2.0-1_arm64.deb`：指定版本安装包。
- `cosmos-camera_latest_arm64.deb`：内容相同的固定文件名，供 README 最新版直链使用。
- 对应的 `.deb.sha256` 和版本化 `.release.json`。

固定文件名必须由每次 Release 都上传。GitHub 支持 `https://github.com/OWNER/REPO/releases/latest/download/文件名`，详见 [官方说明](https://docs.github.com/en/repositories/releasing-projects-on-github/linking-to-releases)。

模型、容器、旧离线包、缓存、日志和拍摄图片不进入公开构建。模型需要用户自行获得原作者访问授权。

## 1. 创建仓库并设置下载地址

在 GitHub 创建一个空仓库。将本项目源码放入该仓库的本地目录，不要把整个旧开发目录连同模型一起上传。

```bash
python3 packaging/configure_repository.py https://github.com/OWNER/REPO \
  --maintainer '你的名字 <你的邮箱>'
```

把 `OWNER/REPO` 和维护者信息换成真实值。该命令同时更新 `config/release.json` 和 README 的下载区，不会创建仓库、提交或推送代码。没有首次 Release 时，下载直链尚不可用。

原项目没有整体开源许可证。本次没有代替作者授予许可；正式对外授权分发前，按 `THIRD_PARTY_NOTICES.md` 补充合适的 `LICENSE` 和署名。

## 2. 本地验证

Ubuntu 构建机需要 Python 3.10+、dpkg、desktop-file-utils；界面测试另需系统 PyQt5、OpenCV 和 Pillow。

```bash
sudo apt install python3 python3-pyqt5 python3-opencv python3-pil libqt5svg5 dpkg desktop-file-utils
QT_QPA_PLATFORM=offscreen /usr/bin/python3 -B -m unittest discover -s tests -v
python3 packaging/release_artifacts.py
```

打包和软件层测试不需要 sudo，不安装本项目，不修改 Docker，不下载模型。`dist/` 下会生成 DEB、SHA256 和发布清单。`logs/package-check-*` 保存解包检查报告。

完整源码分发包可用 `python3 packaging/build_source.py` 生成；它按明确的目录清单打包，不包含模型、缓存、日志、个人配置和旧二进制发布物。

## 3. 提交源码并发布

以下命令在你自己的 Git 仓库中执行，将地址换成真实地址：

```bash
git init
git add .
git commit -m 'Prepare Cosmos Camera 0.2.0 release'
git branch -M main
git remote add origin https://github.com/OWNER/REPO.git
git push -u origin main
```

如果已有 Git 仓库和远端，使用已有配置，无需重复 `git init` 或 `git remote add`。检查 GitHub Actions 的 Build and verify 通过，然后发布：

```bash
git tag -a v0.2.0 -m 'Cosmos Camera 0.2.0'
git push origin v0.2.0
```

`release.yml` 会检查 tag 与 `VERSION` 一致，并检查配置的仓库与实际仓库一致。测试和 DEB 检查通过后，创建草稿 Release、上传附件，再公开发布；没有附件的草稿不会成为下载入口。流程只使用 GitHub 自动提供的 `GITHUB_TOKEN`，不需要把个人 Token 写入仓库。

若上传中途失败，检查草稿 Release；删除这次未完成的草稿后，在 Actions 页面重新运行失败的流程。不要修改已公开 tag 对应的内容；修复应用后增加版本号发布。

也可以手动在 GitHub 创建 Release，并上传本地构建的上述文件。不要把 GitHub 自动生成的 Source code ZIP 当作 DEB 安装包。

## 4. 在另一台干净 Jetson 上验收

CI 验证的是软件逻辑和包结构，不具备 Jetson GPU。正式交付至少完成以下实机步骤，并记录设备、JetPack、版本和日志：

1. 在匹配的 Ubuntu 22.04 / L4T R36.4.x 系统，用 `sudo apt install ./cosmos-camera_latest_arm64.deb` 或 `sudo gdebi` 安装。
2. 确认应用菜单图标出现，普通用户可打开向导；缺少平台或依赖时有明确报错。
3. 验证首次系统配置及重新登录后的 Docker / NVIDIA runtime 权限。
4. 使用已获模型授权的账号下载。验证 401/403 提示、断网重试、暂停和继续，检查 Token 没有写入设置或日志。
5. 插入 USB 相机，点击开始分析，等待真实模型回答；确认画面与回答相符，暂停和退出能够清理本窗口启动的进程。
6. 重开应用，确认复用模型和镜像；断网时可进行本地推理。
7. 从旧版升级，确认个人设置、模型和日志保留；卸载后程序移除、用户数据保留。

本轮不默认对开发机执行系统安装、拉取大镜像或重新加载模型。不要将打包成功、模拟服务测试或界面截图描述为完成了干净设备实机验收。

## 下一次更新

修改 `VERSION`，更新 README 中的版本示例和 `docs/release-notes.md`，运行测试并提交，再推送新 tag。若换模型，需要同时更新固定 revision、文件校验清单，并重新验证模型与镜像组合；不要直接改成 `latest` 或 `main`。
