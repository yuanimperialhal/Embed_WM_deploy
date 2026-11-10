"""Explicit administrator action used by the first-run dialog, never postinst."""
import grp
import json
import os
from pathlib import Path
import platform
import pwd
import shutil
import subprocess
import sys


def platform_error():
    if platform.machine() not in ('aarch64', 'arm64'):
        return '本安装包需要 NVIDIA Jetson Orin（arm64）。'
    release = Path('/etc/os-release').read_text()
    if 'ID=ubuntu\n' not in release or 'VERSION_ID="22.04"' not in release:
        return '当前版本需要 Ubuntu 22.04 / JetPack 6 / L4T R36.4.x。'
    tegra = Path('/etc/nv_tegra_release')
    if not tegra.is_file():
        return '未找到 Jetson 驱动。请先安装匹配的 JetPack 6。'
    first = tegra.read_text().splitlines()[0]
    if '# R36 ' not in first or 'REVISION: 4.' not in first:
        return '当前版本需要 L4T R36.4.x；参考验证版本是 R36.4.3。'
    return None


def configure_host():
    if os.geteuid() != 0:
        raise ValueError('请运行 sudo cosmos-camera-setup-host，或使用准备向导中的系统配置按钮。')
    uid = int(os.environ.get('PKEXEC_UID') or os.environ.get('SUDO_UID') or '0')
    account = pwd.getpwuid(uid)
    if uid < 1000 or account.pw_shell.endswith(('/false', '/nologin')):
        raise ValueError('请从普通桌面用户通过 sudo 或图形授权运行此命令。')
    error = platform_error()
    if error:
        raise ValueError(error)
    # Do not trust a caller-controlled PATH in this privileged helper.
    os.environ['PATH'] = '/usr/sbin:/usr/bin:/sbin:/bin'
    for name in ('docker', 'nvidia-ctk', 'systemctl', 'usermod'):
        if not shutil.which(name):
            raise ValueError('缺少系统依赖：' + name + '。请通过 APT 修复 cosmos-camera 的依赖。')
    subprocess.run(['systemctl', 'enable', '--now', 'docker'], check=True)
    result = subprocess.run(['docker', 'info', '--format', '{{json .Runtimes}}'],
                            capture_output=True, text=True, check=True, timeout=20,
                            env={'PATH': os.environ['PATH']})
    if 'nvidia' not in json.loads(result.stdout):
        subprocess.run(['nvidia-ctk', 'runtime', 'configure', '--runtime=docker'], check=True)
        subprocess.run(['systemctl', 'restart', 'docker'], check=True)
        print('已注册 NVIDIA runtime 并重启 Docker。', flush=True)
    changed = False
    groups = os.getgrouplist(account.pw_name, account.pw_gid)
    for name in ('docker', 'video'):
        group = grp.getgrnam(name)
        if group.gr_gid not in groups:
            subprocess.run(['usermod', '-aG', name, account.pw_name], check=True)
            changed = True
    print('系统配置完成。' + ('请注销桌面并重新登录，然后再次打开应用。' if changed else '请返回向导继续。'), flush=True)


if __name__ == '__main__':
    try:
        configure_host()
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print('系统配置失败：' + str(exc), file=sys.stderr)
        sys.exit(1)
