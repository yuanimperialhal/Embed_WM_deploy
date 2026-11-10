"""Pinned, resumable first-run preparation. All downloads run as the user."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from app_runtime import (ROOT, user_base, load_settings, save_settings, validate_settings, model_files_error,
                         deployed_image)
from run_single_image import IMAGE
from setup_host import platform_error

MODEL_PAGE = 'https://huggingface.co/embedl/Cosmos-Reason2-2B-W4A16'
CHUNK = 1024 * 1024


class SetupError(Exception):
    pass


def manifest():
    data = json.loads((ROOT/'config/model-source.json').read_text())
    if (data.get('schema_version') != 1
            or data.get('repository') != 'embedl/Cosmos-Reason2-2B-W4A16'
            or not re.fullmatch('[0-9a-f]{40}', data.get('revision', ''))):
        raise SetupError('模型版本清单无效，请重新安装应用。')
    seen = set()
    for record in data['files']:
        name = record['name']
        if (not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', name)
                or name in seen or not re.fullmatch('[0-9a-f]{64}', record['sha256'])
                or not isinstance(record['bytes'], int) or record['bytes'] <= 0):
            raise SetupError('模型文件清单无效。')
        seen.add(name)
    if not {'model.safetensors', 'config.json', 'tokenizer.json', 'tokenizer_config.json',
            'preprocessor_config.json'}.issubset(seen):
        raise SetupError('模型文件清单缺少必需文件。')
    return data


def managed_model_dir():
    return user_base('XDG_DATA_HOME', '.local/share')/'cosmos-camera/models'/manifest()['revision']


def state_dir():
    return user_base('XDG_STATE_HOME', '.local/state')/'cosmos-camera'


def receipt_path():
    return state_dir()/'setup.json'


def needs_setup(settings):
    from app_runtime import read_deployment
    return bool(model_files_error(settings['model_dir']) or
                (not receipt_path().is_file() and not read_deployment()))


def emit(message):
    print(message, flush=True)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(CHUNK), b''):
            h.update(block)
    return h.hexdigest()


def matches(path, record):
    return (not path.is_symlink() and path.is_file() and
            path.stat().st_size == record['bytes'] and digest(path) == record['sha256'])


class HTTPSRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith('https://'):
            raise SetupError('下载源重定向到了非 HTTPS 地址，已停止。')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        origin = urllib.parse.urlsplit(req.full_url)
        destination = urllib.parse.urlsplit(newurl)
        # HF may redirect small files to its own resolve-cache API. It still
        # needs authorization there, but signed CDN URLs must not receive it.
        if (origin.scheme, origin.netloc) == (destination.scheme, destination.netloc):
            authorization = req.get_header('Authorization')
            if authorization and redirected is not None:
                redirected.add_unredirected_header('Authorization', authorization)
        return redirected


def download_file(url, path, record, token='', opener=None, progress=emit):
    """Keep .partial on interruption; never replace a pre-existing invalid file."""
    if path.is_symlink():
        raise SetupError('下载目标不能是符号链接：' + path.name)
    if path.exists():
        if matches(path, record):
            progress('已校验，跳过：' + path.name)
            return
        raise SetupError('已有文件与固定版本不符，请移动或重命名后重试：' + str(path))
    partial = path.with_name(path.name + '.partial')
    if partial.is_symlink():
        raise SetupError('临时下载目标不能是符号链接。')
    size = record['bytes']
    if partial.exists() and partial.stat().st_size >= size:
        if matches(partial, record):
            partial.replace(path)
            return
        partial.unlink()  # Only our incomplete download is discarded.
    opener = opener or urllib.request.build_opener(HTTPSRedirect())
    for attempt in range(3):
        offset = partial.stat().st_size if partial.exists() else 0
        req = urllib.request.Request(url, headers={'User-Agent': 'CosmosCamera/0.2', 'Accept-Encoding': 'identity'})
        if token:
            # Our redirect handler retains this only on same-origin redirects.
            req.add_unredirected_header('Authorization', 'Bearer ' + token)
        if offset:
            req.add_header('Range', f'bytes={offset}-')
        try:
            with opener.open(req, timeout=30) as response:
                if response.status == 206:
                    value = response.headers.get('Content-Range', '')
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', value)
                    if not match or int(match[1]) != offset or int(match[3]) != size:
                        raise SetupError('下载续传范围无效，未修改已有数据。')
                elif response.status == 200:
                    offset = 0  # Origin ignored Range: start over, do not append.
                else:
                    raise SetupError('下载服务器返回了意外状态。')
                last = 0
                with partial.open('ab' if offset else 'wb') as stream:
                    while True:
                        block = response.read(CHUNK)
                        if not block:
                            break
                        if offset + len(block) > size:
                            raise SetupError('下载内容超过清单中的文件大小。')
                        stream.write(block)
                        offset += len(block)
                        if time.monotonic() - last >= 1 or offset == size:
                            progress(f'{path.name}：{offset / size:.0%}（{offset/1024**2:.0f} / {size/1024**2:.0f} MiB）')
                            last = time.monotonic()
                    stream.flush()
                    os.fsync(stream.fileno())
                if offset != size:
                    raise ConnectionError('下载未完成')
            progress('校验 SHA256：' + path.name)
            if not matches(partial, record):
                partial.unlink()
                raise SetupError('文件校验失败，请重试：' + path.name)
            partial.replace(path)
            return
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise SetupError('模型访问被拒绝。请在原作者页面申请访问，获批后输入具有此模型读取权限的 Hugging Face Token。') from None
            if exc.code == 404:
                raise SetupError('固定版本文件不存在，请检查发行说明或联系项目维护者。') from None
            if exc.code not in (429, 500, 502, 503, 504):
                raise SetupError(f'下载失败（HTTP {exc.code}），可稍后重试。') from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
            pass
        if attempt < 2:
            progress('连接中断，正在重试；已下载部分会保留。')
            time.sleep(attempt + 1)
    raise SetupError('多次下载失败。请检查网络后重试；已下载部分会保留。')


def check_host():
    error = platform_error()
    if error:
        raise SetupError(error)
    if not shutil.which('docker'):
        raise SetupError('缺少 Docker，请用 APT 修复安装依赖。')
    # This app mounts local models into containers, so remote Docker is unsupported.
    endpoint = subprocess.run(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'],
                              capture_output=True, text=True, timeout=15)
    docker_host = os.environ.get('DOCKER_HOST') or endpoint.stdout.strip()
    if endpoint.returncode or not docker_host.startswith('unix://'):
        raise SetupError('请选择本机 Docker（docker context use default），并取消远程 DOCKER_HOST 设置。')
    result = subprocess.run(['docker', 'info', '--format', '{{json .Runtimes}}'],
                            capture_output=True, text=True, timeout=15)
    if result.returncode:
        raise SetupError('当前用户无法连接 Docker。请点击“配置系统环境”；完成后注销桌面并重新登录。')
    if 'nvidia' not in json.loads(result.stdout):
        raise SetupError('NVIDIA runtime 未注册，请点击“配置系统环境”。')


def image_ready():
    result = subprocess.run(['docker', 'image', 'inspect', '--format', '{{.Os}}/{{.Architecture}}', deployed_image(IMAGE)],
                            capture_output=True, text=True, timeout=15)
    return result.returncode == 0 and result.stdout.strip() == 'linux/arm64'


@contextmanager
def setup_lock():
    state_dir().mkdir(parents=True, exist_ok=True)
    with (state_dir()/'setup.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SetupError('另一个准备任务正在运行，请等待完成。') from None
        yield


def prepare(model_dir=None, token='', progress=emit):
    if os.geteuid() == 0:
        raise SetupError('请使用普通桌面用户准备模型，不要用 sudo 运行应用。')
    if token and not re.fullmatch(r'hf_[A-Za-z0-9]+', token):
        raise SetupError('Token 格式无效，请粘贴 Hugging Face 的读取 Token。')
    with setup_lock():
        settings = load_settings()  # Preserve corrupt settings rather than replacing them.
        progress('正在检查 Jetson 和 Docker 环境…')
        check_host()
        ready = image_ready()
        if model_dir:
            model_dir = Path(model_dir).expanduser().resolve()
            validate_settings(dict(settings, model_dir=str(model_dir)))
            error = model_files_error(model_dir)
            if error:
                raise SetupError(error)
            progress('使用已有模型：' + str(model_dir))
        else:
            data = manifest()
            model_dir = managed_model_dir()
            model_dir.mkdir(parents=True, exist_ok=True)
            # Include room for staging the largest file, but reuse verified files.
            remaining = sum(r['bytes'] for r in data['files'] if not (model_dir/r['name']).is_file())
            if shutil.disk_usage(model_dir).free < remaining + 512*1024**2:
                raise SetupError('模型目录空间不足，请至少为模型预留 4 GiB 可用空间。')
            for record in data['files']:
                url = f"https://huggingface.co/{data['repository']}/resolve/{data['revision']}/{record['name']}"
                download_file(url, model_dir/record['name'], record, token, progress=progress)
        if not ready:
            progress('正在下载固定版本 vLLM 镜像，Docker 存储盘建议预留至少 20 GiB；此过程可能较长…')
            # No token is passed to Docker, its environment, arguments or logs.
            environment = {key: value for key, value in os.environ.items() if key != 'HF_TOKEN'}
            process = subprocess.Popen(['docker', 'pull', IMAGE], env=environment)
            try:
                if process.wait() != 0:
                    raise SetupError('镜像下载失败，请检查网络、Docker 服务和存储空间后重试。')
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
            if not image_ready():
                raise SetupError('镜像未就绪或平台不匹配，请检查 Docker 存储空间。')
        save_settings(dict(settings, model_dir=str(model_dir)))
        receipt = receipt_path()
        temporary = receipt.with_suffix('.tmp')
        temporary.write_text(json.dumps(dict(schema_version=1, model_dir=str(model_dir), image=deployed_image(IMAGE)))+'\n')
        temporary.replace(receipt)
        progress('准备完成。点击“进入相机助手”，再点击“开始连续分析”即可自动加载模型。')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--json-input', action='store_true', help='从 stdin 读取向导参数；Token 不进入命令行')
    parser.add_argument('--model-dir', type=Path, help='使用已有的完整模型目录')
    args = parser.parse_args()
    try:
        options = json.loads(sys.stdin.readline()) if args.json_input else {}
        prepare(options.get('model_dir') or args.model_dir, options.get('token', '') or os.environ.get('HF_TOKEN', ''))
        return 0
    except (SetupError, OSError, ValueError, subprocess.SubprocessError) as exc:
        # Never include request URLs/headers, since CDN query strings may contain credentials.
        emit('准备失败：' + str(exc))
        return 1
    except KeyboardInterrupt:
        emit('已暂停准备，下次打开可以继续。')
        return 130


def interrupted(*_):
    raise KeyboardInterrupt


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, interrupted)
    sys.exit(main())
