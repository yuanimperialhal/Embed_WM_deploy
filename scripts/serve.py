"""Keep the tested Cosmos single-image service running until Ctrl+C."""
import argparse
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from run_single_image import IMAGE, ROOT, server_command, stop_container, stop_process
from app_runtime import model_files_error, deployed_image


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--max-pixels', type=int, default=131072)
    parser.add_argument('--max-model-len', type=int, default=1024)
    parser.add_argument('--kv-cache-mib', type=int, default=128)
    parser.add_argument('--dry-run', action='store_true', help='仅打印启动命令')
    parser.add_argument('--model-dir', type=Path, default=ROOT/'models/cosmos-download')
    parser.add_argument('--cache-dir', type=Path, default=ROOT/'cache')
    parser.add_argument('--image', default=deployed_image(IMAGE))
    args = parser.parse_args(argv)
    args.model_dir = args.model_dir.expanduser().resolve()
    args.cache_dir = args.cache_dir.expanduser().resolve()
    if any(':' in str(path) or '\n' in str(path) for path in (args.model_dir, args.cache_dir)):
        parser.error('模型和缓存目录不能包含冒号或换行')
    if not 1 <= args.port <= 65535 or not 65536 <= args.max_pixels <= 262144:
        parser.error('port 应在 1..65535，max-pixels 应在 65536..262144')
    if not 1 <= args.max_model_len <= 2048 or not 1 <= args.kv_cache_mib <= 256:
        parser.error('max-model-len 应在 1..2048，kv-cache-mib 应在 1..256')
    # This local model needs 112 KiB of FP16 KV per token, excluding block rounding.
    if args.max_model_len * 112 * 1024 > args.kv_cache_mib * 1024**2:
        parser.error('KV 缓存预算不足以容纳指定上下文；1024 token 建议至少 128 MiB')
    name = 'cosmos-camera-' + str(time.time_ns())
    command = server_command(name, port=args.port, max_pixels=args.max_pixels,
                             max_model_len=args.max_model_len,
                             kv_cache_bytes=args.kv_cache_mib * 1024**2,
                             model_dir=args.model_dir, cache_dir=args.cache_dir, image=args.image)
    if args.dry_run:
        print(shlex.join(command))
        return 0
    if not shutil.which('docker'):
        parser.error('找不到 docker')
    error = model_files_error(args.model_dir)
    if error:
        parser.error(error)
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', args.port)) == 0:
            parser.error(f'端口 {args.port} 已占用；若模型已经启动，可直接运行相机脚本')
    subprocess.run(['docker', 'image', 'inspect', args.image], check=True,
                   stdout=subprocess.DEVNULL)
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    print(f'启动常驻模型服务，容器名：{name}\n'
          f'就绪检查：curl http://127.0.0.1:{args.port}/v1/models\n'
          '保持此终端打开；Ctrl+C 停止本次服务。', flush=True)
    process = None
    try:
        process = subprocess.Popen(command)
        return process.wait()
    except KeyboardInterrupt:
        return 130
    finally:
        if process is not None:
            # Clean up only the uniquely named container created by this run.
            stop_container(name)
            stop_process(process)
            subprocess.run(['docker', 'rm', '-f', name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(130))
    sys.exit(main())
