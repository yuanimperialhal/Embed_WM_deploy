"""Verified, local-only installer, embedded into the .run payload.

This does not flash JetPack, change Docker groups, or alter existing user settings.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import shutil
import subprocess
import tempfile

from offline_common import verify_files

ROOT = Path(__file__).resolve().parent
STATE = Path('/var/lib/cosmos-camera')
DOCKER = ['docker', '--host', 'unix:///var/run/docker.sock']
GIB = 1024**3


def run(command, **kwargs):
    return subprocess.run(command, check=True, text=True, **kwargs)


def capture(command):
    return run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout.strip()


def find_image(manifest):
    for identity in manifest['image_ids']:
        result = subprocess.run(DOCKER + ['image', 'inspect', '--format', '{{.Id}}', identity],
                                capture_output=True, text=True)
        if result.returncode == 0 and result.stdout.strip() in manifest['image_ids']:
            return result.stdout.strip()
    return None


def apt_options(work):
    work.mkdir(exist_ok=True)
    (work/'lists/partial').mkdir(parents=True, exist_ok=True)
    (work/'archives/partial').mkdir(parents=True, exist_ok=True)
    sources = work/'offline.list'
    sources.write_text('deb [trusted=yes] ' + (ROOT/'host-debs').as_uri() + ' ./\n')
    return ['-o', 'Dir::Etc::sourcelist='+str(sources), '-o', 'Dir::Etc::sourceparts=-',
            '-o', 'Dir::State::lists='+str(work/'lists'),
            '-o', 'Dir::Cache::archives='+str(work/'archives'),
            '-o', 'Dir::Cache::pkgcache=', '-o', 'Dir::Cache::srcpkgcache=',
            '-o', 'Acquire::Languages=none',
            '-o', 'Acquire::CompressionTypes::Order::=uncompressed',
            '-o', 'APT::Sandbox::User=root']


def preflight(manifest, user):
    release = Path('/etc/nv_tegra_release')
    os_release = Path('/etc/os-release').read_text()
    if os.uname().machine != 'aarch64' or not release.is_file() or 'VERSION_ID="22.04"' not in os_release:
        raise RuntimeError('需要 Jetson arm64、Ubuntu 22.04。未执行安装。')
    if not re.search(r'^# R36 .*REVISION: 4\.3[, ]', release.read_text()):
        raise RuntimeError('此离线包仅针对已验证的 L4T R36.4.3，请为其他系统版本重新验证和构建。')
    for name in ('docker', 'apt-get', 'dpkg-deb', 'runuser'):
        if not shutil.which(name):
            raise RuntimeError('缺少系统组件：'+name+'。先准备匹配的 JetPack / Docker 环境。')
    if not user or pwd.getpwnam(user).pw_uid == 0:
        raise RuntimeError('请用普通用户执行 sudo 安装，或通过 --user 指定将来运行界面的普通用户。')
    runtime = json.loads(capture(DOCKER + ['info', '--format', '{{json .Runtimes}}']))
    if 'nvidia' not in runtime:
        raise RuntimeError('Docker 未配置 NVIDIA runtime，请先按当前 JetPack 版本配置。')
    run(['runuser', '-u', user, '--', *DOCKER, 'info', '--format', '{{.ServerVersion}}'], stdout=subprocess.DEVNULL)
    # Refuse concurrent use instead of replacing app code under a live model/UI.
    processes = capture(['ps', '-eo', 'args='])
    if any(re.search(r'python\S* .*scripts/(camera_ui|serve)\.py(?:\s|$)', line)
           for line in processes.splitlines()):
        raise RuntimeError('请先关闭相机助手及本项目的模型服务，再安装或升级。')
    image_present = find_image(manifest) is not None
    data_root = Path(capture(DOCKER + ['info', '--format', '{{.DockerRootDir}}']))
    docker_need = 0 if image_present else manifest['image_unpacked_bytes'] * 2 + GIB
    if shutil.disk_usage(data_root).free < docker_need:
        raise RuntimeError(f'Docker 所在磁盘空间不足，建议至少再留 {docker_need/GIB:.1f} GiB。')
    if shutil.disk_usage('/var/lib').free < manifest['model_bytes'] + docker_need + 2*GIB:
        raise RuntimeError('模型与 Docker 安装所需剩余空间不足，请腾出空间后重试。')
    return image_present


def install(manifest, user):
    print('检查平台、Docker、普通用户权限及磁盘空间…', flush=True)
    image_present = preflight(manifest, user)
    STATE.mkdir(parents=True, exist_ok=True)
    with (STATE/'install.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='apt-', dir=ROOT) as temporary:
            options = apt_options(Path(temporary))
            env = dict(os.environ, LC_ALL='C', DEBIAN_FRONTEND='noninteractive')
            print('检查离线依赖，只使用安装包内的软件源…', flush=True)
            run(['apt-get', *options, 'update'], env=env)
            package = ROOT/manifest['application']
            plan = run(['apt-get', *options, '--simulate', '--no-remove', '--no-install-recommends',
                        'install', str(package)], env=env, stdout=subprocess.PIPE).stdout
            if any(line.startswith('Remv ') for line in plan.splitlines()):
                raise RuntimeError('安装计划会移除其他软件，已停止。')
            print(plan, flush=True)
            if not image_present:
                print('导入推理镜像，这一步可能需要几分钟…', flush=True)
                run(DOCKER + ['image', 'load', '--input', str(ROOT/'image/vllm.tar')])
            loaded_id = find_image(manifest)
            if loaded_id is None:
                raise RuntimeError('导入的镜像身份不匹配，未更新应用配置。')
            target = STATE/'models'/manifest['model_id']
            target.parent.mkdir(parents=True, exist_ok=True)
            model_records = [dict(record, path=record['path'][len('model/'):])
                             for record in manifest['files'] if record['path'].startswith('model/')]
            if target.exists():
                verify_files(target, {'files': model_records})
                print('模型已经存在且校验通过，保留现有文件。', flush=True)
            else:
                pending = target.parent/(target.name+'.installing-'+str(os.getpid()))
                try:
                    if (ROOT/'model').stat().st_dev == target.parent.stat().st_dev:
                        (ROOT/'model').rename(pending)
                    else:
                        shutil.copytree(ROOT/'model', pending)
                    pending.chmod(0o755)
                    for path in pending.rglob('*'):
                        path.chmod(0o755 if path.is_dir() else 0o644)
                    pending.rename(target)
                except Exception:
                    if pending.exists():
                        shutil.rmtree(pending)
                    raise
            print('安装相机助手和缺少的运行库…', flush=True)
            run(['apt-get', *options, '--yes', '--no-remove', '--no-install-recommends',
                 'install', str(package)], env=env)
            # Commit deployment defaults only after the application installs successfully.
            deployment = dict(schema_version=1, model_dir=str(target), image_id=loaded_id,
                              version=manifest['version'], model_id=manifest['model_id'])
            pending_config = STATE/'deployment.json.new'
            with pending_config.open('w') as stream:
                json.dump(deployment, stream, indent=2)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            pending_config.chmod(0o644)
            pending_config.replace(STATE/'deployment.json')
            print('\n安装完成。请以普通用户从应用菜单打开“Cosmos 相机助手”。\n'
                  '首次使用会自动找到随包模型；已有的个人设置保持不变。\n'
                  '模型位置：'+str(target)+'\n'
                  '卸载程序：sudo apt remove cosmos-camera\n'
                  '模型和镜像会保留，可供重新安装使用。', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true')
    parser.add_argument('--verify-only', action='store_true')
    parser.add_argument('--user')
    args = parser.parse_args()
    manifest = json.loads((ROOT/'manifest.json').read_text())
    if manifest.get('schema_version') != 1:
        raise RuntimeError('不支持的离线包格式。')
    print('逐项校验应用、模型、依赖和镜像…', flush=True)
    verify_files(ROOT, manifest)
    if args.verify_only:
        print('全部文件校验通过；没有安装软件或导入镜像。')
        return
    if not args.install or os.geteuid() != 0:
        parser.error('请以 sudo 运行外层 .run 文件并指定 --install。')
    install(manifest, args.user or os.environ.get('SUDO_USER'))


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit('安装未完成：'+str(exc))
