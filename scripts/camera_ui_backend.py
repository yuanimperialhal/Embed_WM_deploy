"""Lightweight model lifecycle and cancellable inference for the desktop UI."""
import json
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from camera_infer import ROOT, infer
from app_runtime import cache_dir, model_files_error


def inference_worker(connection):
    """Separate, cancellable client process; never owns the camera or GUI."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        connection.send(('ready', None))
        while True:
            args, item = connection.recv()
            try:
                result = infer(opener, args, *item,
                               on_text=lambda text: connection.send(('text', text)), echo=False)
            except Exception as exc:
                result = {'status': 'error', 'error': str(exc), 'answer': ''}
            connection.send(('result', result))
    except (EOFError, BrokenPipeError, OSError):
        pass
    finally:
        connection.close()


class ModelService:
    """Only stop the serve.py process created by this window; reuse other servers."""

    def __init__(self, events, log_path, model_dir=None):
        self.events, self.log_path = events, log_path
        self.lock = threading.Lock()
        self.cancelled = threading.Event()
        self.process = None
        self.busy = False
        self.ready = False
        self.stream = None
        self.model_dir = model_dir

    def health(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open('http://127.0.0.1:8000/v1/models', timeout=2) as response:
            return any(m.get('id') == 'cosmos' for m in json.load(response).get('data', []))

    def start(self, launch=True):
        with self.lock:
            if self.busy:
                return
            self.busy = True
            self.cancelled.clear()
        threading.Thread(target=self._start, args=(launch,), daemon=True).start()

    def _start(self, launch):
        try:
            try:
                healthy = self.health()
            except (OSError, ValueError):
                healthy = False
            if self.cancelled.is_set():
                return
            if healthy:
                self.ready = True
                self.events.put(('model', 'ready'))
                return
            self.ready = False
            if not launch:
                self.events.put(('model', 'offline'))
                return
            if self.model_dir is not None:
                error = model_files_error(self.model_dir)
                if error:
                    raise RuntimeError(error)
            self.events.put(('model', 'loading'))
            with self.lock:
                if self.cancelled.is_set():
                    return
                if self.process is not None and self.process.poll() is None:
                    raise RuntimeError('服务进程仍在运行，请先关闭模型再重试。')
                if self.stream is not None:
                    self.stream.close()
                self.stream = self.log_path.open('a', encoding='utf-8')
                command = [sys.executable, str(ROOT/'scripts/serve.py')]
                if self.model_dir is not None:
                    command += ['--model-dir', str(self.model_dir), '--cache-dir', str(cache_dir())]
                self.process = subprocess.Popen(
                    command, cwd=str(ROOT),
                    stdout=self.stream, stderr=subprocess.STDOUT, start_new_session=True)
            deadline = time.monotonic() + 900
            while not self.cancelled.wait(2):
                if self.process.poll() is not None:
                    raise RuntimeError('模型启动失败，请打开服务日志查看原因。')
                if time.monotonic() >= deadline:
                    raise RuntimeError('模型加载超时，请关闭模型后查看服务日志。')
                try:
                    if self.health():
                        if not self.cancelled.is_set():
                            self.ready = True
                            self.events.put(('model', 'ready'))
                        return
                except (OSError, ValueError):
                    pass
        except Exception as exc:
            if not self.cancelled.is_set():
                self.events.put(('model_error', str(exc)))
        finally:
            with self.lock:
                self.busy = False

    def stop(self):
        self.cancelled.set()
        self.ready = False
        with self.lock:
            process = self.process
        if process is not None and process.poll() is None:
            # serve.py handles SIGTERM and cleans up its uniquely named container.
            process.terminate()
            process.wait(timeout=45)
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        self.process = None


