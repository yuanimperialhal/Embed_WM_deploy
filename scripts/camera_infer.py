"""Continuously sample the latest camera frame and stream Cosmos text locally."""
import argparse
import base64
from datetime import datetime, timezone
import glob
import http.client
import json
import math
from pathlib import Path
import signal
import sys
import threading
import time
import urllib.error
import urllib.request

from bench_image import read_stream

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROMPT = (
    '这是相机当前的一帧。如果画面模糊，请只回答“画面模糊，无法可靠判断”。'
    '如果画面清晰，用中文一句话描述主要物体及空间关系。'
    '只描述可见内容，不凭单帧推断运动方向或速度，不重复。'
)


class LatestFrame:
    """Drain capture continuously, retaining one frame rather than a FIFO queue."""

    def __init__(self, cv2, args):
        self.cv2, self.args = cv2, args
        self.condition = threading.Condition()
        self.stopped = threading.Event()
        self.latest = None
        self.error = None
        self.thread = threading.Thread(target=self._capture, daemon=True)

    def start(self):
        self.thread.start()

    def _capture(self):
        cap = None
        try:
            source = int(self.args.source) if self.args.source.isdecimal() else self.args.source
            local = isinstance(source, int) or str(source).startswith('/dev/')
            backend = self.cv2.CAP_GSTREAMER if self.args.gstreamer else (
                self.cv2.CAP_V4L2 if local else self.cv2.CAP_ANY)
            cap = self.cv2.VideoCapture(source, backend)
            if not cap.isOpened():
                raise RuntimeError('无法打开相机；检查设备路径、权限、占用情况及视频后端')
            if local and not self.args.gstreamer:
                if self.args.fourcc:
                    cap.set(self.cv2.CAP_PROP_FOURCC, self.cv2.VideoWriter_fourcc(*self.args.fourcc))
                cap.set(self.cv2.CAP_PROP_FRAME_WIDTH, self.args.width)
                cap.set(self.cv2.CAP_PROP_FRAME_HEIGHT, self.args.height)
                cap.set(self.cv2.CAP_PROP_FPS, self.args.fps)
            # Some drivers ignore this; continuously reading is the primary defense.
            cap.set(self.cv2.CAP_PROP_BUFFERSIZE, 1)
            seq = 0
            while not self.stopped.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    raise RuntimeError('相机取帧失败或视频流结束；检查 USB 连接及相机格式')
                seq += 1
                with self.condition:
                    # Times describe receipt by this process, not sensor exposure.
                    self.latest = (seq, time.perf_counter(),
                                   datetime.now(timezone.utc).isoformat(), frame)
                    self.condition.notify_all()
        except Exception as exc:
            with self.condition:
                self.error = str(exc)
                self.condition.notify_all()
        finally:
            if cap is not None:
                cap.release()

    def get(self, after=0):
        deadline = time.perf_counter() + self.args.frame_timeout
        with self.condition:
            while True:
                if self.error:
                    raise RuntimeError(self.error)
                if self.latest is not None:
                    seq, received, timestamp, frame = self.latest
                    if seq > after and time.perf_counter() - received <= self.args.max_frame_age:
                        return seq, received, timestamp, frame.copy()
                remaining = deadline - time.perf_counter()
                if remaining <= 0:
                    raise TimeoutError('等待新鲜相机画面超时；未重复发送旧画面')
                self.condition.wait(min(remaining, 0.2))

    def close(self):
        self.stopped.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=3)


def encode_frame(cv2, frame, max_side, quality):
    height, width = frame.shape[:2]
    scale = min(1.0, max_side / max(height, width))
    if scale < 1:
        frame = cv2.resize(frame, (max(1, round(width * scale)), max(1, round(height * scale))),
                           interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError('JPEG 编码失败')
    return encoded.tobytes(), [frame.shape[1], frame.shape[0]]


def infer(opener, args, jpeg, seq, received, timestamp, size, on_text=None, echo=True):
    result = dict(status='error', frame_sequence=seq, frame_received_at=timestamp,
                  image_size=size, image_bytes=len(jpeg), model=args.model, prompt=args.prompt,
                  max_tokens=args.max_tokens, repetition_penalty=args.repetition_penalty,
                  first_text_seconds=None, first_reasoning_seconds=None,
                  total_seconds=None, finish_reason=None, usage=None,
                  received_done=False, answer='', reasoning='')
    payload = dict(model=args.model, temperature=0, max_tokens=args.max_tokens,
                   repetition_penalty=args.repetition_penalty,
                   stream=True, stream_options={'include_usage': True}, messages=[
                       {'role': 'user', 'content': [
                           {'type': 'image_url', 'image_url': {
                               'url': 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode('ascii')}},
                           {'type': 'text', 'text': args.prompt}]}])
    request = urllib.request.Request(args.url, data=json.dumps(payload).encode(),
                                     headers={'Content-Type': 'application/json'})
    start = time.perf_counter()
    result['frame_age_at_send_seconds'] = start - received
    if result['frame_age_at_send_seconds'] > args.max_frame_age:
        raise TimeoutError('编码后画面已过期；停止发送，请降低分辨率或检查系统负载')
    try:
        with opener.open(request, timeout=args.timeout) as response:
            read_stream(response, result, start, on_text=on_text, echo=echo)
        result['status'] = 'truncated' if result['finish_reason'] == 'length' else 'ok'
    except urllib.error.HTTPError as exc:
        result['error'] = f'HTTP {exc.code}: {exc.read(4096).decode(errors="replace")}'
    except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        result['total_seconds'] = time.perf_counter() - start
        result['frame_age_at_end_seconds'] = time.perf_counter() - received
    return result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', default='/dev/video0', help='USB 设备路径、编号或 RTSP URL')
    parser.add_argument('--list-devices', action='store_true')
    parser.add_argument('--snapshot-only', type=Path, help='只拍一张图到此路径，不调用模型')
    preview = parser.add_mutually_exclusive_group()
    preview.add_argument('--preview', action='store_true', help='显示实时视频并持续推理')
    preview.add_argument('--preview-only', action='store_true', help='只显示实时视频，不启动或调用模型')
    parser.add_argument('--preview-seconds', type=float, default=0,
                        help='预览运行秒数；0 为持续运行，Q/Esc 或 Ctrl+C 停止')
    parser.add_argument('--gstreamer', action='store_true', help='将 source 作为 GStreamer 管线')
    parser.add_argument('--width', type=int, default=640)
    parser.add_argument('--height', type=int, default=480)
    parser.add_argument('--fps', type=float, default=15)
    parser.add_argument('--fourcc', default='', help='可选 USB 格式，例如 MJPG 或 YUYV')
    parser.add_argument('--max-side', type=int, default=384, help='提交图像的最长边')
    parser.add_argument('--jpeg-quality', type=int, default=80)
    parser.add_argument('--url', default='http://127.0.0.1:8000/v1/chat/completions')
    parser.add_argument('--model', default='cosmos')
    parser.add_argument('--prompt', default=DEFAULT_PROMPT)
    parser.add_argument('--max-tokens', type=int, default=128)
    parser.add_argument('--repetition-penalty', type=float, default=1.1,
                        help='vLLM 重复惩罚；1 表示不启用')
    parser.add_argument('--interval', type=float, default=2, help='两次请求开始时间的最小间隔，秒')
    parser.add_argument('--timeout', type=float, default=120, help='HTTP socket 读写超时，秒')
    parser.add_argument('--frame-timeout', type=float, default=10)
    parser.add_argument('--max-frame-age', type=float, default=2, help='画面收到后允许等待发送的最长秒数')
    parser.add_argument('--count', type=int, default=0, help='推理次数；0 表示持续运行')
    parser.add_argument('--output', type=Path, help='追加 JSONL 日志；默认每次创建独立日志')
    args = parser.parse_args(argv)
    if not math.isfinite(args.preview_seconds) or args.preview_seconds < 0:
        parser.error('preview-seconds 不能为负数或非有限数')
    if args.snapshot_only and (args.preview or args.preview_only):
        parser.error('拍照与视频预览请分开运行')
    if any(not math.isfinite(v) or v <= 0 for v in (
            args.width, args.height, args.fps, args.max_side, args.timeout,
            args.frame_timeout, args.max_frame_age)):
        parser.error('尺寸、帧率和超时必须为正数')
    if not math.isfinite(args.interval) or args.interval < 0 or args.count < 0:
        parser.error('interval 和 count 不能为负数')
    if not 1 <= args.max_tokens <= 1536 or not 1 <= args.jpeg_quality <= 100:
        parser.error('max-tokens 应在 1..1536，jpeg-quality 应在 1..100')
    if not math.isfinite(args.repetition_penalty) or args.repetition_penalty <= 0:
        parser.error('repetition-penalty 必须为正数')
    if args.fourcc and len(args.fourcc) != 4:
        parser.error('fourcc 必须是四个字符，如 MJPG')
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.list_devices:
        for device in sorted(glob.glob('/dev/video*')):
            name = Path('/sys/class/video4linux') / Path(device).name / 'name'
            print(device, name.read_text().strip() if name.is_file() else '')
        if not glob.glob('/dev/video*'):
            print('未发现 /dev/video*；请在相机连接的 Jetson 宿主机检查 USB 和驱动。')
        return 0
    try:
        import cv2
    except ImportError:
        print('缺少 OpenCV；在宿主机安装 python3-opencv 后重试。', file=sys.stderr)
        return 1
    if args.gstreamer and 'GStreamer:                   YES' not in cv2.getBuildInformation():
        print('当前 OpenCV 未启用 GStreamer；USB 摄像头请省略 --gstreamer。', file=sys.stderr)
        return 1
    camera = LatestFrame(cv2, args)
    try:
        camera.start()
        # Start the warmup after the driver has actually delivered its first frame.
        initial_seq, _, _, _ = camera.get()
        time.sleep(0.5)
        if args.snapshot_only:
            _, _, _, frame = camera.get(after=initial_seq)
            args.snapshot_only.parent.mkdir(parents=True, exist_ok=True)
            if not cv2.imwrite(str(args.snapshot_only), frame):
                raise RuntimeError('无法保存相机图片')
            print(f'相机图片：{args.snapshot_only.resolve()}')
            return 0
        if args.preview or args.preview_only:
            from camera_preview import run_preview
            return run_preview(cv2, camera, args)
        output = args.output or ROOT/'logs'/datetime.now().strftime('camera-%Y%m%d-%H%M%S-%f.jsonl')
        output.parent.mkdir(parents=True, exist_ok=True)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        print(f'结果日志：{output.resolve()}\n每次使用最新单帧，Ctrl+C 停止。', flush=True)
        last_seq, count, next_start, exit_code = initial_seq, 0, 0.0, 0
        with output.open('a', encoding='utf-8') as log:
            while args.count == 0 or count < args.count:
                time.sleep(max(0, next_start - time.perf_counter()))
                seq, received, timestamp, frame = camera.get(after=last_seq)
                jpeg, size = encode_frame(cv2, frame, args.max_side, args.jpeg_quality)
                next_start = time.perf_counter() + args.interval
                print(f'\n[{count + 1}] 帧 #{seq}，{size[0]}×{size[1]}：', flush=True)
                result = infer(opener, args, jpeg, seq, received, timestamp, size)
                log.write(json.dumps(result, ensure_ascii=False) + '\n')
                log.flush()
                print(f'\n状态：{result["status"]}；请求耗时：{result["total_seconds"]:.2f}s；'
                      f'回答结束时画面已过去：{result["frame_age_at_end_seconds"]:.2f}s', flush=True)
                if result['status'] == 'error':
                    raise RuntimeError(result['error'])
                if result['status'] == 'truncated':
                    print('回答达到 token/上下文上限，未完整结束；可缩短问题或增加 --max-tokens。',
                          file=sys.stderr)
                    exit_code = 2
                last_seq, count = seq, count + 1
        return exit_code
    except KeyboardInterrupt:
        print('\n已停止相机推理。')
        return 130
    except (OSError, ValueError, RuntimeError, cv2.error) as exc:
        print(f'相机推理失败：{exc}', file=sys.stderr)
        return 1
    finally:
        camera.close()


if __name__ == '__main__':
    def on_terminate(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, on_terminate)
    sys.exit(main())
