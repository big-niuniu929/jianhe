# -*- coding: utf-8 -*-
"""开发用：重启本地职练服务（杀掉 8848 上的旧进程 → 启动新进程 → 等健康检查）

用法：python tools/restart.py
日志：app/data/server.log
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(ROOT, "app")
PORT = "8848"
LOG = os.path.join(APP, "data", "server.log")
PIDFILE = os.path.join(APP, "data", "server.pid")


def listening_pids():
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
    pids = set()
    for line in out.splitlines():
        if f":{PORT}" in line and "LISTENING" in line:
            parts = line.split()
            if parts and parts[-1].isdigit():
                pids.add(parts[-1])
    if not pids:
        # 某些 Windows 环境 netstat 不显示 LISTENING 行，用 PowerShell 兜底
        try:
            ps = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"(Get-NetTCPConnection -LocalPort {PORT} -State Listen -ErrorAction SilentlyContinue).OwningProcess"],
                capture_output=True, text=True, timeout=15)
            for tok in ps.stdout.split():
                if tok.isdigit():
                    pids.add(tok)
        except Exception:
            pass
    return pids


def cim_uvicorn_pids():
    """用 CIM 找出所有跑 uvicorn main:app 的 python 进程（最可靠）。"""
    try:
        ps = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match 'uvicorn.*main:app' } | "
             "Select-Object -ExpandProperty ProcessId"],
            capture_output=True, text=True, timeout=20)
        return {tok for tok in ps.stdout.split() if tok.isdigit()}
    except Exception:
        return set()


def kill(pids):
    for pid in pids:
        print("kill", pid)
        subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)


def health(timeout=2):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def routes():
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/openapi.json", timeout=3) as r:
            return list(json.load(r).get("paths", {}).keys())
    except Exception:
        return []


def kill_pidfile():
    try:
        with open(PIDFILE, encoding="utf-8") as fh:
            pid = fh.read().strip()
        if pid.isdigit():
            print("kill pidfile", pid)
            subprocess.run(["taskkill", "/PID", pid, "/F"], capture_output=True)
    except FileNotFoundError:
        pass


def main():
    kill_pidfile()
    kill(cim_uvicorn_pids())
    kill(listening_pids())
    for _ in range(20):
        if not listening_pids():
            break
        time.sleep(0.3)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    log = open(LOG, "w", encoding="utf-8")
    kwargs = {"cwd": APP, "stdout": log, "stderr": log}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", PORT], **kwargs)
    with open(PIDFILE, "w", encoding="utf-8") as fh:
        fh.write(str(proc.pid))
    for _ in range(60):
        time.sleep(0.5)
        if health():
            print("health OK")
            print("routes:", len(routes()))
            for r in routes():
                print("  ", r)
            return 0
    print("启动失败，日志：", LOG)
    print(open(LOG, encoding="utf-8", errors="ignore").read()[-3000:])
    return 1


if __name__ == "__main__":
    sys.exit(main())
