"""Read-only deployment diagnostics; never installs drivers or downloads weights."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess

from app_runtime import VERSION, ROOT, cache_dir, logs_dir, settings_path, model_files_error, deployed_image
from run_single_image import IMAGE


def check_environment(model_dir):
    checks = []
    def add(name, ok, detail):
        checks.append(dict(name=name, ok=ok, detail=detail))
    release = Path('/etc/nv_tegra_release')
    add('Jetson 系统', platform.machine() in ('aarch64', 'arm64') and release.is_file(),
        release.read_text().splitlines()[0] if release.is_file() else '需要 Jetson Linux；本版在 L4T R36.4.3 / Ubuntu 22.04 上验证。')
    error = model_files_error(model_dir)
    add('模型文件', error is None, error or str(model_dir))
    cameras = sorted(Path('/dev').glob('video*'))
    add('相机设备权限', any(os.access(p, os.R_OK | os.W_OK) for p in cameras),
        '、'.join(str(p) for p in cameras) or '未发现 USB 相机。请接入相机后重试。')
    docker = shutil.which('docker')
    add('Docker 命令', bool(docker), docker or '请按当前 JetPack 版本的文档配置 Docker 和 NVIDIA Container Runtime。')
    if docker:
        commands = [
            ('Docker / NVIDIA Runtime', [docker, 'info', '--format', '{{json .Runtimes}}']),
            ('固定版本推理镜像', [docker, 'image', 'inspect', '--format', '{{.Id}}', deployed_image(IMAGE)]),
        ]
        for name, command in commands:
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=8)
                output = (result.stdout if result.returncode == 0 else result.stderr).strip()
                ok = result.returncode == 0
                if ok and name == 'Docker / NVIDIA Runtime':
                    ok = 'nvidia' in json.loads(output)
                    output = ('Docker 已连接；NVIDIA runtime 已注册。' if ok else
                              'Docker 可用，但未注册 nvidia runtime。')
                elif ok:
                    output = '已找到本项目固定版本的推理镜像。'
                add(name, ok, output[-1500:] or '检查失败，请确认 Docker 服务及当前用户权限。')
            except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
                add(name, False, str(exc))
    return dict(version=VERSION, app_root=str(ROOT), settings_file=str(settings_path()),
                logs_dir=str(logs_dir()), cache_dir=str(cache_dir()),
                checks=checks, ready=all(check['ok'] for check in checks),
                note='文件和权限检查不等于实际推理成功；首次交付仍需完成一次真实相机和模型验收。')


def format_report(report):
    rows = [f'Cosmos 相机助手 {report["version"]}', '']
    for item in report['checks']:
        rows += [('通过' if item['ok'] else '需要处理') + ' · ' + item['name'], item['detail'], '']
    rows += ['配置：' + report['settings_file'], '日志：' + report['logs_dir'],
             '缓存：' + report['cache_dir'], '', report['note']]
    return '\n'.join(rows)
