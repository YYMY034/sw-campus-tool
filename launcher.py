#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
launcher.py — 运动世界校园 · 一键启动器
==========================================
菜单式启动入口（Start.bat 双击即可使用）：

  [1] 启动可视化控制台   → 启动 gui.py（登录/仪表盘/一键跑步），自动开浏览器
  [2] 登录链自检         → verify_login_chain2.py（信封/GT4/完整登录链 5 项）
  [3] 模块自检           → swclient / swsubmit / swobs 三组离线自检
  [4] 控制台运行状态     → 检测端口与登录态
  [0] 退出

说明：
- 自动使用当前解释器（sys.executable）运行所有子模块，保证同一套依赖。
- GUI 端口默认 8765；若已有一个实例在跑，启动器直接复用并打开浏览器，
  避免端口冲突。
- 本机健康检查绕过系统代理（环境 HTTP_PROXY 会把 127.0.0.1 也代理掉，
  与代码无关）。
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
GUI_PORT = 8765
GUI_URL = f"http://127.0.0.1:{GUI_PORT}"

BANNER = r"""
  ┌──────────────────────────────────────────────┐
  │     运动世界校园 · 一键启动器                   │
  │     登录 / 跑步生成 / 提交 / OBS 上传 全链路     │
  └──────────────────────────────────────────────┘
"""

MENU = """
   ┌──────────────────────────────────────────┐
   │  [1] 启动可视化控制台   (登录+一键跑步)      │
   │  [2] 登录链自检         (GT4滑块+登录链路)   │
   │  [3] 模块自检           (加密/提交/OBS)      │
   │  [4] 控制台运行状态                        │
   │  [0] 退出                                 │
   └──────────────────────────────────────────┘
"""


# ══════════════════════════════════════════════════════════════
# 工具
# ══════════════════════════════════════════════════════════════
def banner():
    print(BANNER)
    print("  工作目录:", HERE)


def port_in_use(port: int) -> bool:
    """检测端口是否已被监听"""
    s = socket.socket()
    s.settimeout(0.8)
    try:
        s.connect(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def http_get_no_proxy(url: str, timeout: float = 8.0):
    """绕过系统代理请求本机（环境 http_proxy 会拦截 127.0.0.1）"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=timeout) as r:
        return r.status, r.read()


def run_cmd(args, label: str):
    """运行子进程并透传输出，返回 returncode"""
    print(f"\n── {label} ──")
    print("  命令:", " ".join(os.path.basename(a) if os.sep in a else a for a in args))
    try:
        r = subprocess.run(args, cwd=HERE)
        return r.returncode
    except FileNotFoundError as e:
        print(f"  ✗ 找不到可执行文件: {e}")
        return -1
    except KeyboardInterrupt:
        return 130


def dep_check() -> bool:
    """关键依赖检查（只提示不阻断）"""
    missing = []
    for mod in ("Crypto", "PIL", "numpy"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        print("  ⚠ 缺少依赖:", ", ".join(missing), "（pip install pycryptodome Pillow numpy）")
        return False
    return True


# ══════════════════════════════════════════════════════════════
# 菜单 1：可视化控制台
# ══════════════════════════════════════════════════════════════
def start_gui() -> None:
    print("\n── 启动可视化控制台 ──")
    gui_py = os.path.join(HERE, "gui.py")
    if not os.path.exists(gui_py):
        print(f"  ✗ 找不到 {gui_py}")
        input("  按回车返回…")
        return

    if port_in_use(GUI_PORT):
        print(f"  ℹ 端口 {GUI_PORT} 已被占用 → 检测是否为本控制台…")
        try:
            st, body = http_get_no_proxy(GUI_URL + "/api/state")
            if st == 200 and b"logged" in body:
                print("  ✓ 控制台实例已在运行（复用）")
                print(f"  → 打开 {GUI_URL}")
                open_browser()
                input("  按回车返回…")
                return
        except Exception:
            pass
        print(f"  ✗ 端口 {GUI_PORT} 被其它程序占用，请关闭后重试")
        input("  按回车返回…")
        return

    print("  • 启动 gui.py …")
    try:
        import webbrowser
        webbrowser.open(GUI_URL)  # 先开浏览器，稍等 server 就绪
    except Exception:
        pass

    # 前台运行 --- 用户 Ctrl+C 退出后回到菜单
    proc = subprocess.Popen([sys.executable, "-u", gui_py, "--port", str(GUI_PORT)],
                            cwd=HERE)
    print(f"  • PID={proc.pid}  / 访问: {GUI_URL}")
    print("  • 按 Ctrl+C 停止控制台返回菜单")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        print("\n  • 控制台已停止")
    input("  按回车返回…")


def open_browser():
    try:
        import webbrowser
        webbrowser.open(GUI_URL)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════
# 菜单 2/3：自检
# ══════════════════════════════════════════════════════════════
def run_chain_selftest() -> None:
    print("\n── 登录链自检（verify_login_chain2.py）──")
    print("  ⚠ 会发起真实网络请求：checkGeeUse + GT4 滑块 + login(无效账号)")
    if not dep_check():
        input("  按回车返回…")
        return
    rc = run_cmd([sys.executable, os.path.join(HERE, "verify_login_chain2.py")],
                 "登录链回归自检")
    print(f"\n  {'✓ 自检通过' if rc == 0 else '✗ 自检未通过'} (返回码 {rc})")
    input("  按回车返回…")


def run_module_selftest() -> None:
    print("\n── 模块自检（离线）──")
    ok = True
    for mod in ("swclient", "swsubmit", "swobs"):
        f = os.path.join(HERE, mod + ".py")
        if not os.path.exists(f):
            print(f"  ✗ 找不到 {f}")
            ok = False
            continue
        rc = run_cmd([sys.executable, f, "--selftest"], f"{mod} 自检")
        if rc != 0:
            ok = False
    print(f"\n  {'✓ 全部模块自检通过' if ok else '✗ 部分模块自检失败'}")
    input("  按回车返回…")


def show_status() -> None:
    print("\n── 控制台运行状态 ──")
    try:
        st, body = http_get_no_proxy(GUI_URL + "/api/state")
        d = json.loads(body)
        print(f"  • 端口 {GUI_PORT}: 运行中 (HTTP {st})")
        print(f"  • 会话: {'已登录' if d.get('logged') else '未登录'}")
        if d.get("logged"):
            print(f"     uid={d.get('uid')}  name={d.get('name')}"
                  f"  device={d.get('device')}")
        print(f"  • 设备档案: {len(d.get('devices') or [])} 台")
    except Exception as e:
        print(f"  • 端口 {GUI_PORT}: 未运行（{e}）")
        print("  可用 [1] 启动可视化控制台")
    input("  按回车返回…")


# ══════════════════════════════════════════════════════════════
# 主循环
# ══════════════════════════════════════════════════════════════
def main():
    banner()
    dep_check()
    while True:
        print(MENU)
        try:
            choice = input("  请选择 [0-4]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n  Bye.")
            break
        if choice == "1":
            start_gui()
        elif choice == "2":
            run_chain_selftest()
        elif choice == "3":
            run_module_selftest()
        elif choice == "4":
            show_status()
        elif choice == "0":
            print("  Bye.")
            break
        else:
            print("  无效选择，请输入 0-4")


if __name__ == "__main__":
    main()