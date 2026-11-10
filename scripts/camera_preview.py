"""Display the shared camera feed while a separate process makes serial requests."""
from datetime import datetime
import json
import multiprocessing
import os
from pathlib import Path
import signal
import time
import urllib.request

from camera_infer import ROOT, encode_frame, infer


def request_loop(args, connection):
    # Only the parent handles keyboard interruption and owns the camera/GUI.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        while True:
            item = connection.recv()
            if item is None:
                return
            try:
                connection.send(infer(opener, args, *item))
            except Exception as exc:
                connection.send({'status': 'error', 'error': str(exc)})
    except (EOFError, BrokenPipeError):
        pass
    finally:
        connection.close()


def prepare_display():
    if not os.environ.get('DISPLAY'):
        auth = Path(f'/run/user/{os.getuid()}/gdm/Xauthority')
        if auth.is_file() and Path('/tmp/.X11-unix/X0').exists():
            os.environ['DISPLAY'] = ':0'
            os.environ.setdefault('XAUTHORITY', str(auth))
        else:
            raise RuntimeError('未找到已登录的桌面；请在 Jetson 屏幕的终端运行预览，或设置 DISPLAY/XAUTHORITY')


def make_caption(cv2, width, answer):
    # Render only when an answer changes, rather than loading fonts every frame.
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    font_path = '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
    if not Path(font_path).is_file():
        raise RuntimeError('中文预览需要 NotoSansCJK-Regular.ttc；文字推理仍可省略 --preview 使用')
    font = ImageFont.truetype(font_path, 20)
    panel = Image.new('RGB', (width, 140), (24, 28, 34))
    draw = ImageDraw.Draw(panel)
    text = '最近一次分析（对应先前取样帧）：' + answer
    lines, line = [], ''
    for char in text:
        if char == '\n' or draw.textlength(line + char, font=font) > width - 24:
            lines.append(line)
            line = '' if char == '\n' else char
        else:
            line += char
    if line:
        lines.append(line)
    if len(lines) > 4:
        lines = lines[:4]
        lines[-1] = lines[-1][:-2] + '…'
    for index, line in enumerate(lines):
        draw.text((12, 8 + index * 29), line, font=font, fill=(235, 240, 245))
    return cv2.cvtColor(np.asarray(panel), cv2.COLOR_RGB2BGR)


def run_preview(cv2, camera, args):
    prepare_display()
    title = 'Cosmos Camera - Q / Esc to quit'
    worker = connection = log = None
    pending = False
    last_sent = completed = shown = last_shown = 0
    next_start = 0.0
    result_time = None
    result_seq = None
    caption = None
    exit_code = 0
    started = time.perf_counter()
    try:
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        cv2.resizeWindow(title, args.width, args.height + (180 if args.preview else 40))
        if args.preview:
            output = args.output or ROOT/'logs'/datetime.now().strftime('camera-preview-%Y%m%d-%H%M%S-%f.jsonl')
            output.parent.mkdir(parents=True, exist_ok=True)
            log = output.open('a', encoding='utf-8')
            context = multiprocessing.get_context('spawn')
            connection, child = context.Pipe()
            worker = context.Process(target=request_loop, args=(args, child), daemon=True)
            worker.start()
            child.close()
            print(f'结果日志：{output.resolve()}；完整中文回答同时显示在终端。', flush=True)
        print(f'视频窗口显示在 DISPLAY={os.environ["DISPLAY"]}；Q/Esc、关闭窗口或 Ctrl+C 停止。', flush=True)
        while not args.preview_seconds or time.perf_counter() - started < args.preview_seconds:
            now = time.perf_counter()
            with camera.condition:
                if camera.error:
                    raise RuntimeError(camera.error)
                seq, received, timestamp, raw = camera.latest
                frame = raw.copy()
            if now - received > args.max_frame_age:
                raise TimeoutError('相机已停止更新，退出预览；请检查 USB 连接')
            if seq != last_shown:
                shown += 1
                last_shown = seq
            if worker is not None and pending and connection.poll():
                result = connection.recv()
                pending = False
                completed += 1
                log.write(json.dumps(result, ensure_ascii=False) + '\n')
                log.flush()
                if result['status'] == 'error':
                    raise RuntimeError(result['error'] + '；若连接被拒绝，请先运行 scripts/serve.py')
                result_time = time.perf_counter()
                result_seq = result['frame_sequence']
                caption = make_caption(cv2, frame.shape[1], result['answer'])
                print(f'\n状态：{result["status"]}；请求耗时：{result["total_seconds"]:.2f}s；'
                      f'分析帧：#{result_seq}', flush=True)
                if result['status'] == 'truncated':
                    exit_code = 2
                    print('回答被截断；请缩短问题或输出长度。', flush=True)
                if args.count and completed >= args.count:
                    break
            if worker is not None and not worker.is_alive():
                raise RuntimeError('推理请求进程异常退出')
            if worker is not None and not pending and seq > last_sent and now >= next_start:
                jpeg, size = encode_frame(cv2, frame, args.max_side, args.jpeg_quality)
                connection.send((jpeg, seq, received, timestamp, size))
                last_sent, pending = seq, True
                next_start = time.perf_counter() + args.interval
                print(f'\n[{completed + 1}] 分析最新帧 #{seq}：', flush=True)
            status = f'LIVE #{seq} | Q / Esc: quit'
            if args.preview:
                status += ' | analyzing...' if pending else ' | waiting'
            frame = cv2.copyMakeBorder(frame, 0, 64, 0, 0, cv2.BORDER_CONSTANT, value=(24, 28, 34))
            cv2.putText(frame, status, (10, frame.shape[0] - 40), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (220, 240, 220), 1, cv2.LINE_AA)
            if result_time is not None:
                cv2.putText(frame, f'Last result: frame #{result_seq}, {now - result_time:.1f}s ago',
                            (10, frame.shape[0] - 16), cv2.FONT_HERSHEY_SIMPLEX,
                            0.5, (220, 220, 220), 1, cv2.LINE_AA)
            if args.preview:
                if caption is None:
                    caption = make_caption(cv2, frame.shape[1], '正在等待模型回答…')
                frame = cv2.vconcat([frame, caption])
            cv2.imshow(title, frame)
            key = cv2.waitKey(30) & 0xff
            if key in (ord('q'), ord('Q'), 27) or cv2.getWindowProperty(title, cv2.WND_PROP_VISIBLE) < 1:
                break
        return exit_code
    finally:
        if worker is not None:
            if worker.is_alive() and not pending:
                try:
                    connection.send(None)
                except (BrokenPipeError, OSError):
                    pass
                worker.join(timeout=0.5)
            if worker.is_alive():
                worker.terminate()
                worker.join(timeout=2)
            if worker.is_alive():
                worker.kill()
                worker.join(timeout=1)
        if connection is not None:
            connection.close()
        if log is not None:
            log.close()
        cv2.destroyAllWindows()
        print(f'预览结束：显示 {shown} 个不同画面，完成 {completed} 次推理。'
              '模型服务由 serve.py 所在终端管理。', flush=True)
