import atexit
import re
import shutil
import subprocess
import threading
from collections import deque


class QuickTunnel:
    """Own one cloudflared child in a single-process local app."""

    def __init__(self, executable, origin, logger):
        self.executable = executable
        self.origin = origin
        self.logger = logger
        self.lock = threading.RLock()
        self.process = None
        self.reader = None
        self.url = ""
        self.connected = False
        self.error = ""
        self.logs = deque(maxlen=12)
        atexit.register(self.stop)

    def snapshot(self):
        with self.lock:
            running = self.process is not None and self.process.poll() is None
            return {
                "running": running,
                "ready": running and self.connected and bool(self.url),
                "url": self.url if running and self.connected else "",
                "error": self.error,
                "logs": list(self.logs),
            }

    def start(self):
        with self.lock:
            if self.process is not None and self.process.poll() is None:
                return
            executable = shutil.which(self.executable)
            if not executable:
                raise RuntimeError("cloudflared is not installed. Install it or set CLOUDFLARED_PATH, then restart WorkTrack.")
            self.url = ""
            self.connected = False
            self.error = ""
            self.logs.clear()
            try:
                process = subprocess.Popen(
                    [executable, "tunnel", "--no-autoupdate", "--url", self.origin],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                    errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except OSError as error:
                self.logger.exception("Could not start Cloudflare tunnel")
                raise RuntimeError(f"Could not start cloudflared: {error}") from error
            self.process = process
            self.reader = threading.Thread(target=self._read, args=(process,), daemon=True)
            self.reader.start()

    def _read(self, process):
        try:
            for line in process.stdout:
                line = line.strip()
                with self.lock:
                    if self.process is not process:
                        return
                    self.logs.append(line)
                    match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com\b", line)
                    if match:
                        self.url = match.group(0)
                    if "Registered tunnel connection" in line:
                        self.connected = True
            code = process.wait()
            with self.lock:
                if self.process is process:
                    self.connected = False
                    self.error = f"Cloudflare tunnel exited (code {code}). Review the diagnostics and create a new link."
                    self.logger.error(self.error)
        finally:
            process.stdout.close()

    def stop(self):
        with self.lock:
            process = self.process
            reader = self.reader
            self.process = None
            self.reader = None
            self.url = ""
            self.connected = False
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if reader is not None:
            reader.join(timeout=2)
