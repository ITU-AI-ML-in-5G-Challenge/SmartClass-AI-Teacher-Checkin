"""
版本检查模块。

在入口脚本（export_v2.py、export.py、update.py 等）的 main() 开头调用
``check_version_or_exit()``，确保程序与仓库代码版本兼容：

    - 大版本不同 → 阻止运行，提示 ``python manage.py upgrade``
    - 小版本不同 → 警告但允许继续
    - 版本相同 → 正常通过

manage.py 不受版本限制。
"""
import os
import subprocess
import sys

PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
VERSION_FILE = os.path.join(PROJECT_DIR, "version.txt")

PINK = "\033[95m"
RESET = "\033[0m"


def _read_version(path):
    """读取版本文件，返回 (major, minor)。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            line = f.readline().strip()
        if "." in line:
            parts = line.split(".")
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 0, 0


def get_local_version():
    """读取本地版本号。"""
    return _read_version(VERSION_FILE)


def get_remote_version():
    """读取远程版本号（需要网络）。失败时返回 (0, 0) 即放行。"""
    try:
        # git fetch（只取头，不 merge）
        subprocess.run(
            ["git", "fetch", "origin", "master"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        )
        # 读取远程版本文件内容
        result = subprocess.run(
            ["git", "show", "origin/master:version.txt"],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        line = result.stdout.strip()
        if "." in line:
            parts = line.split(".")
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 0, 0  # 网络失败 → 放行


def check_version_or_exit(label=""):
    """
    检查版本兼容性，不兼容则退出进程。

    通过标准输出打印提示；manage.py 调用者（脚本名含 'manage'）放行。

    :param label: 调用方标识（如 export_v2），用于日志
    """
    # manage.py 调用的任何子进程不受版本限制
    if "manage" in sys.argv[0].lower():
        return

    local_major, local_minor = get_local_version()
    remote_major, remote_minor = get_remote_version()

    prefix = f"[{label}] " if label else ""

    if remote_major == 0 and remote_minor == 0:
        # 网络不可用 → 放行
        return

    if remote_major > local_major:
        print()
        print(f"  {PINK}╔══════════════════════════════════════════════════╗{RESET}")
        print(f"  {PINK}║  ⚠️  代码大版本已更新！                        ║{RESET}")
        print(f"  {PINK}║  本地: v{local_major}.{local_minor}    远程: v{remote_major}.{remote_minor}     ║{RESET}")
        print(f"  {PINK}║  请先运行: python manage.py upgrade            ║{RESET}")
        print(f"  {PINK}╚══════════════════════════════════════════════════╝{RESET}")
        print()
        sys.exit(1)

    if remote_minor > local_minor:
        print(f"  {PINK}{prefix}⚠️ 小版本更新可用 (本地 v{local_major}.{local_minor} → 远程 v{remote_major}.{remote_minor})，建议稍后 upgrade{RESET}")
