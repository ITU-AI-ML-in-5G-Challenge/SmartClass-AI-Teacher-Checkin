"""
打卡程序管理工具。

提供一键部署、添加打卡账号、程序升级、版本管理等功能。

用法：
    python manage.py setup                 一键部署环境
    python manage.py add-bot <微信号>       添加打卡工作微信号
    python manage.py upgrade               升级程序到最新版本
    python manage.py version --major       大版本号 +1
    python manage.py version --minor       小版本号 +1
    python manage.py help                  打印帮助信息
"""
import os
import sys
import subprocess
import argparse
import tempfile


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_ACCOUNTS_FILE = os.path.join(PROJECT_DIR, "bot_accounts.txt")


# ── 公共工具 ──────────────────────────────────────────────

def _run(cmd, cwd=None, shell=True):
    """执行命令并打印输出。"""
    print(f"  → {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    try:
        subprocess.check_call(cmd, cwd=cwd or PROJECT_DIR, shell=shell)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  ✗ 失败 (exit={e.returncode})")
        return False


def _ensure_pip():
    """确保 pip 可用。"""
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "--version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        print("  ✗ pip 不可用，请确认 Python 安装完整")
        return False


def _wxauto_installed():
    """
    判断机器上是否已安装微信自动化库（wxauto 3.9 或 wxauto4 4.0）。

    :return: 已装任一 wxauto 库返回 True，否则 False
    :rtype: bool
    """
    import importlib.util
    return (
        importlib.util.find_spec("wxauto") is not None
        or importlib.util.find_spec("wxauto4") is not None
    )


def _install_requirements(req, upgrade=False):
    """
    安装 requirements.txt 依赖。

    若机器已装 wxauto（3.9）或 wxauto4（4.0），则跳过其中的 wxauto 依赖行，
    避免在旧环境（微信 3.9 + wxauto 3.9）上误装/重复装 wxauto4。
    其余依赖正常安装。

    :param req: requirements.txt 路径
    :param upgrade: 是否加 --upgrade
    """
    with open(req, "r", encoding="utf-8") as f:
        lines = f.readlines()

    if _wxauto_installed():
        print("  ✓ 已检测到 wxauto/wxauto4，跳过微信自动化库安装")
        lines = [ln for ln in lines if not ln.strip().startswith("wxauto")]

    if not any(ln.strip() and not ln.strip().startswith("#") for ln in lines):
        print("  ✓ 依赖均已满足，无需安装")
        return

    # 用临时文件承载过滤后的依赖，避免改动被 git 跟踪的 requirements.txt
    fd, tmp_path = tempfile.mkstemp(suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.writelines(lines)
        cmd = [sys.executable, "-m", "pip", "install", "-r", tmp_path]
        if upgrade:
            cmd.append("--upgrade")
        _run(cmd)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ── 命令实现 ──────────────────────────────────────────────

def cmd_setup():
    """
    一键部署：安装依赖 + 配置环境 + 配置 GitHub + 提示安装微信。

    步骤：
        1. pip install -r requirements.txt
        2. 检查并引导创建 .env 配置文件
        3. 配置 GitHub SSH Key + 引导添加 collaborator
        4. 提示安装微信（不自动安装、不检测版本）
    """
    print("\n📦 打卡程序一键部署\n")

    print("=" * 62)
    print("  部署前请确认以下条件已满足：")
    print("    1. 已安装 Git")
    print("    2. 已安装 Python 3.9 - 3.12（任一版本）")
    print("    3. 已通过 git clone 下载本代码仓库")
    print("=" * 62)

    # ① 安装 Python 依赖
    print("[1/5] 安装 Python 依赖...")
    if not _ensure_pip():
        return
    req = os.path.join(PROJECT_DIR, "requirements.txt")
    if os.path.exists(req):
        _install_requirements(req)
    else:
        print("  ⚠ requirements.txt 不存在，跳过")

    # ② 配置 .env
    print("\n[2/5] 配置打卡参数...")
    env_path = os.path.join(PROJECT_DIR, ".env")
    if os.path.exists(env_path):
        print(f"  ✓ .env 已存在：{env_path}")
    else:
        print("  创建 .env 配置文件...")
        group_names = input("  请输入打卡群名称（逗号分隔，直接回车默认 语文群,数学群,英语群）：").strip()
        if not group_names:
            group_names = "语文群,数学群,英语群"
        checkin_dir = input("  请输入打卡数据目录（直接回车默认 WeChatTextExport）：").strip()
        if not checkin_dir:
            checkin_dir = "WeChatTextExport"
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(f'CHECKIN_GROUP_NAMES={group_names}\n')
            f.write(f'CHECKIN_DIR={checkin_dir}\n')
        print(f"  ✓ 已创建 .env：{env_path}")
        os.makedirs(os.path.join(PROJECT_DIR, checkin_dir), exist_ok=True)

    # ③ 配置 GitHub SSH
    print("\n[3/5] 配置 GitHub SSH Key（上传打卡数据必需）...")
    ssh_dir = os.path.join(os.path.expanduser("~"), ".ssh")
    id_rsa = os.path.join(ssh_dir, "id_rsa.pub")
    id_ed25519 = os.path.join(ssh_dir, "id_ed25519.pub")

    existing_key = None
    if os.path.exists(id_ed25519):
        existing_key = id_ed25519
    elif os.path.exists(id_rsa):
        existing_key = id_rsa

    if existing_key:
        print(f"  ✓ SSH Key 已存在：{existing_key}")
        with open(existing_key) as f:
            pubkey = f.read().strip()
        print(f"  公钥：{pubkey[:60]}...")
    else:
        print("  未找到 SSH Key，正在生成...")
        email = input("  请输入你的 GitHub 注册邮箱：").strip()
        if email:
            _run(["ssh-keygen", "-t", "ed25519", "-C", email, "-f",
                  os.path.join(ssh_dir, "id_ed25519"), "-N", ""], shell=False)
            with open(id_ed25519) as f:
                pubkey = f.read().strip()
        else:
            print("  ⚠ 未输入邮箱，跳过 SSH Key 生成")
            pubkey = None

    if pubkey:
        print()
        print("  ╔══════════════════════════════════════════════════╗")
        print("  ║  请完成以下 GitHub 设置（否则无法上传数据）：  ║")
        print("  ║                                                  ║")
        print("  ║  1. 注册 GitHub 账号：https://github.com/signup  ║")
        print("  ║  2. 添加 SSH Key：                                ║")
        print("  ║     https://github.com/settings/keys              ║")
        print("  ║     → New SSH Key → 粘贴以下公钥：                ║")
        print(f"  ║     {pubkey[:50]}...║")
        print("  ║  3. 联系管理员将你的 GitHub 账号加入 collaborator ║")
        print("  ║     仓库地址：                                    ║")
        print("  ║     https://github.com/ITU-AI-ML-in-5G-Challenge/checkin    ║")
        print("  ╚══════════════════════════════════════════════════╝")
        print()

    # ④ 克隆公开打卡结果仓库
    print("[4/5] 克隆公开打卡结果仓库...")
    results_dir = os.path.join(os.path.dirname(PROJECT_DIR), "results")
    results_repo = "git@github.com:ITU-AI-ML-in-5G-Challenge/results.git"
    if os.path.isdir(results_dir):
        print(f"  ✓ results 仓库已存在：{results_dir}")
    else:
        ok = _run(["git", "clone", results_repo, results_dir], shell=False)
        if not ok:
            print(f"  ⚠ 克隆失败，请确认 SSH Key 已配置好后手动运行：")
            print(f"    git clone {results_repo} {results_dir}")

    # ⑤ 提示安装微信客户端（不自动安装、不检测版本，仅提示）
    print("[5/5] 微信客户端...")
    installers = sorted(
        f for f in os.listdir(PROJECT_DIR)
        if f.startswith("WeChatWin_") and f.endswith(".exe")
    )
    if installers:
        print("  请手动安装代码库里的微信安装包并登录：")
        for name in installers:
            print(f"    - {name}")
    else:
        print("  ⚠ 代码库目录未找到微信安装包（WeChatWin_*.exe）。")
        print("    请将微信安装包放入代码库目录后，手动安装并登录。")

    print()
    print("✅ 部署完成！")
    print("   ⚠ 确认 SSH Key 和 collaborator 设置完毕后再运行：")
    print("     python export_v2.py")
    print()


def cmd_add_bot(name):
    """
    添加打卡工作微信号。

    :param name: 微信号/昵称
    """
    if not name:
        print("❌ 请指定要添加的微信号，例如：python manage.py add-bot TeacherLi")
        return

    print(f"\n👤 添加打卡工作账号：{name}\n")

    # 读取已有账号
    existing = []
    if os.path.exists(BOT_ACCOUNTS_FILE):
        with open(BOT_ACCOUNTS_FILE, "r", encoding="utf-8") as f:
            existing = [line.strip() for line in f if line.strip()]

    if name in existing:
        print(f"  ⚠ {name} 已在列表中，无需重复添加")
        return

    # 追加
    with open(BOT_ACCOUNTS_FILE, "a", encoding="utf-8") as f:
        f.write(f"{name}\n")

    print(f"  ✓ 已添加：{name}")
    print(f"  当前账号列表：{', '.join(existing + [name])}")

    # bot 列表变更属于不兼容变更，自动加大版本
    print("\n  🔼 bot 账号变更，自动升级大版本...")
    cmd_version(major=True)

    print(f"  💡 请 git add + commit + push 以同步给其他人\n")


def cmd_version(major=False, minor=False):
    """
    管理版本号。

    python manage.py version --major   → 大版本 +1，小版本归零
    python manage.py version --minor   → 小版本 +1
    """
    version_file = os.path.join(PROJECT_DIR, "version.txt")

    if not os.path.exists(version_file):
        print("  ⚠ version.txt 不存在，创建 v1.0 ...")
        with open(version_file, "w", encoding="utf-8") as f:
            f.write("1.0")
        print("  ✓ version.txt v1.0")
        return

    with open(version_file, "r", encoding="utf-8") as f:
        line = f.readline().strip()
    parts = line.split(".")
    major_ver = int(parts[0]) if len(parts) >= 1 else 1
    minor_ver = int(parts[1]) if len(parts) >= 2 else 0

    if major:
        major_ver += 1
        minor_ver = 0
    elif minor:
        minor_ver += 1
    else:
        print(f"  当前版本：v{major_ver}.{minor_ver}")
        print("  用法：--major（大版本+1）或 --minor（小版本+1）")
        return

    new_version = f"{major_ver}.{minor_ver}"
    with open(version_file, "w", encoding="utf-8") as f:
        f.write(new_version)
    print(f"  ✓ 版本已更新：v{line} → v{new_version}")
    print(f"  💡 请 git add + commit + push 以同步给其他人\n")


def cmd_upgrade():
    """
    升级：拉取最新代码并更新依赖。
    """
    print("\n🔄 升级打卡程序\n")

    print("[1/2] 拉取最新代码...")
    if not _run(["git", "pull", "origin", "master"], shell=False):
        print("  ⚠ git pull 失败，请检查网络或手动处理冲突")
        return

    print("\n[2/2] 更新 Python 依赖...")
    req = os.path.join(PROJECT_DIR, "requirements.txt")
    if os.path.exists(req):
        _install_requirements(req, upgrade=True)

    print("\n✅ 升级完成！\n")


# ── 入口 ──────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="打卡程序管理工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
    python manage.py setup
    python manage.py add-bot TeacherLi
    python manage.py upgrade
    python manage.py version --major
    python manage.py help
""",
    )
    sub = parser.add_subparsers(dest="command", help="可用命令")

    sub.add_parser("setup", help="一键部署环境（安装依赖 + 配置 .env + 检查/安装微信）")
    p_add = sub.add_parser("add-bot", help="添加打卡工作微信号")
    p_add.add_argument("name", help="微信号/昵称")
    sub.add_parser("upgrade", help="升级程序到最新版本")
    p_ver = sub.add_parser("version", help="管理版本号")
    p_ver.add_argument("--major", action="store_true", help="大版本号 +1")
    p_ver.add_argument("--minor", action="store_true", help="小版本号 +1")
    sub.add_parser("help", help="打印帮助信息")

    args = parser.parse_args()

    if args.command in ("help", None):
        parser.print_help()
    elif args.command == "setup":
        cmd_setup()
    elif args.command == "add-bot":
        cmd_add_bot(args.name)
    elif args.command == "upgrade":
        cmd_upgrade()
    elif args.command == "version":
        cmd_version(major=args.major, minor=args.minor)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
