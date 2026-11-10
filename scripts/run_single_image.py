"""Launch local vLLM, sample Jetson RAM, benchmark one image twice, then stop."""
import argparse
import json
import re
import shlex
import shutil
import signal
import socket
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = 'ghcr.io/nvidia-ai-iot/vllm@sha256:817f0f940d2d9c9067d861d2118d7bf58c40873598f0c35e19c8516269ebc4bd'


def server_command(name, port=8000, max_model_len=2048, max_pixels=262144,
                   kv_cache_bytes=268435456, model_dir=None, cache_dir=None, image=None):
    """Share the tested, single-image configuration with the persistent server."""
    return [
        'docker', 'run', '--rm', '--pull=never', '--name', name,
        '--runtime=nvidia', '--network=host', '--shm-size=1g',
        '-v', f'{model_dir or ROOT/"models/cosmos-download"}:/model:ro',
        '-v', f'{cache_dir or ROOT/"cache"}:/cache', '-e', 'HF_HOME=/cache/huggingface',
        '-e', 'XDG_CACHE_HOME=/cache', '-e', 'HF_HUB_OFFLINE=1',
        '-e', 'TRANSFORMERS_OFFLINE=1', '-e', 'VLLM_NO_USAGE_STATS=1',
        image or IMAGE, 'vllm', 'serve', '/model', '--served-model-name', 'cosmos',
        '--host', '127.0.0.1', '--port', str(port), '--dtype', 'half',
        '--max-model-len', str(max_model_len), '--max-num-seqs', '1',
        # With an explicit KV budget, this relaxes the initial free-memory check;
        # it does not cap total memory usage.
        '--gpu-memory-utilization', '0.1',
        '--max-num-batched-tokens', '512', '--kv-cache-memory-bytes', str(kv_cache_bytes),
        '--enforce-eager', '--no-enable-prefix-caching',
        '--mm-processor-cache-gb', '0', '--limit-mm-per-prompt', '{"image":1,"video":0}',
        '--mm-processor-kwargs', json.dumps({'min_pixels': 65536, 'max_pixels': max_pixels}),
    ]


def parse_memory(line):
    ram = re.search(r'\bRAM (\d+)/(\d+)MB', line)
    swap = re.search(r'\bSWAP (\d+)/(\d+)MB', line)
    if not ram:
        return None
    return dict(ram_mb=int(ram[1]), total_ram_mb=int(ram[2]),
                swap_mb=int(swap[1]) if swap else None)


def memory_summary(samples):
    baseline = [s['ram_mb'] for s in samples if s['phase'] == 'baseline']
    base = statistics.median(baseline) if baseline else None
    result = {'unit': 'MB as reported by tegrastats', 'interval_ms': 100,
              'baseline_ram_mb': base, 'phases': {}}
    for phase in ('loading', 'ready', 'first_request', 'second_request'):
        rows = [s for s in samples if s['phase'] == phase]
        if rows:
            peak = max(s['ram_mb'] for s in rows)
            swaps = [s['swap_mb'] for s in rows if s['swap_mb'] is not None]
            result['phases'][phase] = dict(
                samples=len(rows), peak_ram_mb=peak,
                increase_over_baseline_mb=None if base is None else peak-base,
                peak_swap_mb=max(swaps) if swaps else None)
    return result


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')


def stop_process(process):
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def stop_container(name):
    try:
        subprocess.run(['docker', 'stop', '-t', '10', name],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    except subprocess.TimeoutExpired:
        subprocess.run(['docker', 'kill', name], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=10)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', type=Path, default=ROOT/'images/test.jpg')
    parser.add_argument('--max-tokens', type=int, default=1024)
    parser.add_argument('--max-model-len', type=int, default=2048)
    parser.add_argument('--max-pixels', type=int, default=262144)
    parser.add_argument('--port', type=int, default=8000)
    parser.add_argument('--startup-timeout', type=int, default=900)
    args = parser.parse_args()
    for binary in ('docker', 'tegrastats'):
        if not shutil.which(binary):
            parser.error(f'找不到 {binary}')
    if not args.image.is_file() or not (ROOT/'models/cosmos-download/model.safetensors').is_file():
        parser.error('找不到图片或本地模型文件')
    if not (0 < args.max_tokens < args.max_model_len <= 2048):
        parser.error('请满足 0 < max-tokens < max-model-len <= 2048（固定 KV 缓存为 256MiB）')
    if not 65536 <= args.max_pixels <= 262144:
        parser.error('单图验证的 max-pixels 应在 65536 到 262144 之间')
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', args.port)) == 0:
            parser.error(f'端口 {args.port} 已占用，请更换 --port')
    subprocess.run(['docker', 'image', 'inspect', IMAGE], check=True,
                   stdout=subprocess.DEVNULL)
    run_dir = ROOT/'logs'/datetime.now().strftime('single-image-%Y%m%d-%H%M%S-%f')
    run_dir.mkdir(parents=True)
    (ROOT/'cache').mkdir(exist_ok=True)
    name = 'cosmos-single-image-' + str(time.time_ns())
    command = server_command(name, args.port, args.max_model_len, args.max_pixels)
    report = {'status': 'running', 'container_image': IMAGE, 'command': command,
              'image': str(args.image.resolve()), 'max_model_len': args.max_model_len,
              'max_pixels': args.max_pixels, 'kv_cache_bytes': 268435456}
    (run_dir/'server-command.txt').write_text(shlex.join(command) + '\n')
    print(f'日志目录：{run_dir}', flush=True)
    print('正在采集 1 秒启动前内存基线……', flush=True)
    phase = 'baseline'
    samples = []
    origin = time.perf_counter()
    monitor = subprocess.Popen(['tegrastats', '--interval', '100'],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1)

    def capture():
        with (run_dir/'memory.jsonl').open('w') as out:
            for line in monitor.stdout:
                row = parse_memory(line)
                if row:
                    row.update(elapsed_s=time.perf_counter()-origin, phase=phase,
                               raw=line.strip())
                    samples.append(row)
                    out.write(json.dumps(row) + '\n')
                    out.flush()

    thread = threading.Thread(target=capture, daemon=True)
    thread.start()
    server = None
    launched = False
    exit_code = 1
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    base_url = f'http://127.0.0.1:{args.port}'
    try:
        time.sleep(1)
        if not samples:
            raise RuntimeError('tegrastats 未产生内存样本，请检查权限')
        phase = 'loading'
        load_start = time.perf_counter()
        with (run_dir/'server.log').open('w') as log:
            server = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
            launched = True
            print('正在加载模型。另一终端可用 tail -f 查看 server.log。', flush=True)
            while True:
                if server.poll() is not None:
                    raise RuntimeError(f'vLLM 提前退出（{server.returncode}），请看 server.log')
                if time.perf_counter()-load_start > args.startup_timeout:
                    raise TimeoutError('等待服务就绪超时，请看 server.log')
                try:
                    with opener.open(base_url+'/health', timeout=1) as response:
                        healthy = response.status == 200
                    with opener.open(base_url+'/v1/models', timeout=1) as response:
                        models = json.load(response).get('data', [])
                    if healthy and any(m.get('id') == 'cosmos' for m in models):
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(1)
            phase = 'ready'
            report['startup_to_ready_seconds'] = time.perf_counter()-load_start
            if not any(s['phase'] == 'loading' for s in samples):
                raise RuntimeError('加载阶段没有内存样本，请检查 tegrastats')
            write_json(run_dir/'load-memory.json', memory_summary(list(samples)))
            print('服务已就绪；加载峰值已保存到 load-memory.json。', flush=True)
            statuses = []
            for index, label in enumerate(('first', 'second')):
                phase = 'first_request' if index == 0 else 'second_request'
                print(f'\n开始第 {index+1} 次单图请求：', flush=True)
                result_file = run_dir/f'{label}.json'
                result = subprocess.run([
                    sys.executable, str(ROOT/'scripts/bench_image.py'),
                    '--image', str(args.image.resolve()), '--url', base_url+'/v1/chat/completions',
                    '--max-tokens', str(args.max_tokens), '--output', str(result_file)])
                if result_file.is_file():
                    report.setdefault('requests', {})[label] = json.loads(result_file.read_text())
                statuses.append(result.returncode)
                if result.returncode not in (0, 2):
                    raise RuntimeError(f'第 {index+1} 次请求失败，请看 {result_file}')
            report['status'] = 'complete' if statuses == [0, 0] else 'truncated'
            exit_code = 0 if report['status'] == 'complete' else 2
    except KeyboardInterrupt:
        report.update(status='interrupted', error='用户中断')
        exit_code = 130
    except Exception as error:
        report.update(status='failed', error=str(error))
        print(f'测试失败：{error}', file=sys.stderr, flush=True)
    finally:
        phase = 'cleanup'
        actions = [('内存采样', lambda: stop_process(monitor))]
        if launched:
            actions.insert(0, ('容器', lambda: stop_container(name)))
        if server is not None:
            actions.append(('Docker 客户端', lambda: stop_process(server)))
        if launched:
            # Catch a container created after the first stop request during startup.
            actions.append(('容器最终清理', lambda: subprocess.run(
                ['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, timeout=10)))
        for label, action in actions:
            try:
                action()
            except Exception as error:
                report.setdefault('cleanup_errors', []).append(f'{label}: {error}')
        thread.join(timeout=5)
        report['memory'] = memory_summary(samples)
        write_json(run_dir/'summary.json', report)
        if report.get('cleanup_errors'):
            print('部分清理操作失败：' + '；'.join(report['cleanup_errors']), file=sys.stderr)
            print(f'请检查测试容器是否仍运行：docker ps -a --filter name={name}', file=sys.stderr)
            exit_code = 1
        loading = report['memory']['phases'].get('loading')
        if loading:
            print(f'\n启动前 RAM：{report["memory"]["baseline_ram_mb"]} MB；'
                  f'加载采样峰值：{loading["peak_ram_mb"]} MB；'
                  f'相对增加：{loading["increase_over_baseline_mb"]} MB。', flush=True)
        print(f'\n结果：{report["status"]}；汇总：{run_dir}/summary.json', flush=True)
    return exit_code


if __name__ == '__main__':
    def on_terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_terminate)
    sys.exit(main())
