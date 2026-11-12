"""
多人协作打卡的 Git 同步模块。

工作流：
    - Excel 打卡结果存在公开 results 仓库（任何人可浏览）
    - mapping.xlsx 存在私人 checkin 仓库（只有 collaborator 可访问）
    - 打卡前从 results 拉取前一天的数据作为基础
    - 打卡后将新结果推送到 results，mapping 变化推送到私人仓库

用法：由 export_v2.py 的 main() 调用，无需单独运行。
"""
import os
import shutil
import subprocess
from io import BytesIO
import openpyxl
from logger import get_logger

# 公开仓库的默认相对路径
_DEFAULT_PUBLIC_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'results'
)

# 公开 results 仓库的 SSH 地址（与 manage.py 部署时保持一致）
_PUBLIC_REPO_URL = "git@github.com:ITU-AI-ML-in-5G-Challenge/results.git"

# ANSI 颜色 — git 同步日志用粉色
PINK = "\033[95m"
RESET = "\033[0m"


def _pink(text):
    return f"{PINK}{text}{RESET}"


def _run_git(args, cwd, logger, timeout=30):
    log = get_logger(logger)
    try:
        result = subprocess.run(
            ['git'] + args,
            cwd=cwd,
            capture_output=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode == 0:
            return True, output
        else:
            log.warning(f"git {' '.join(args)} 失败 (exit={result.returncode}): {output}")
            return False, output
    except FileNotFoundError:
        log.error("git 命令不可用，请确认 Git 已安装并在 PATH 中")
        return False, ""
    except subprocess.TimeoutExpired:
        log.error(f"git {' '.join(args)} 超时（{timeout}秒），网络可能不可用")
        return False, ""
    except Exception as e:
        log.error(f"git {' '.join(args)} 异常: {e}")
        return False, ""


def get_public_dir():
    """返回公开 results 仓库的本地路径。"""
    return _DEFAULT_PUBLIC_DIR


def ensure_public_dir(logger=None):
    """
    确保公开 results 仓库已克隆到本地；不存在则自动 git clone 初始化。

    使用 SSH 地址（git@github.com:...），不依赖账号密码。

    :return: 仓库目录是否已就绪（已存在或 clone 成功）
    """
    public_dir = get_public_dir()
    # 含 .git 目录即视为已是 git 仓库
    if os.path.isdir(os.path.join(public_dir, '.git')):
        return True
    log = get_logger(logger)
    log.info(_pink(f"📥 公开 results 仓库不存在，正在 git clone 初始化到 {public_dir} ..."))
    parent = os.path.dirname(public_dir)
    try:
        os.makedirs(parent, exist_ok=True)
    except Exception:
        pass
    ok, output = _run_git(
        ['clone', _PUBLIC_REPO_URL, public_dir], parent, logger, timeout=120
    )
    if ok:
        log.info(_pink("✅ 公开 results 仓库初始化成功"))
    else:
        log.error(
            f"❌ 公开 results 仓库 clone 失败，请确认 SSH Key 已配置好：\n{output}"
        )
    return ok


def git_pull_public(logger=None):
    """
    拉取公开 results 仓库最新数据（仓库不存在时自动 clone 初始化）。

    :return: 是否成功
    """
    if not ensure_public_dir(logger):
        return False
    public_dir = get_public_dir()
    log = get_logger(logger)
    log.info(_pink("🔄 同步公开打卡数据..."))
    ok, output = _run_git(['pull', '--rebase'], public_dir, logger)
    if ok and output:
        log.info(_pink(f"git pull results: {output}"))
    elif ok:
        log.info(_pink("git pull results: 已是最新"))
    return ok


def check_date_exists_in_public(target_date, logger=None, groupname=None):
    """
    检查公开仓库中是否已存在指定日期（可选指定群）的打卡结果。

    :param target_date: 目标日期 (datetime)
    :param groupname: 群名称；若提供则只检查该群，否则检查任意群
    :return: 已存在则返回 True
    """
    public_dir = get_public_dir()
    if not os.path.isdir(public_dir):
        return False
    log = get_logger(logger)
    date_str = target_date.strftime('%Y%m%d')
    date_str_old = target_date.strftime('%m%d')
    for fname in os.listdir(public_dir):
        if not fname.endswith('.xlsx') or fname.startswith('~$'):
            continue
        if date_str not in fname and date_str_old not in fname:
            continue
        if groupname and groupname not in fname:
            continue
        label = f" {groupname}" if groupname else ""
        log.info(_pink(
            f"🔍 公开仓库中已存在 {target_date.strftime('%Y-%m-%d')}{label} 的打卡数据: {fname}"
        ))
        return True
    return False


def copy_yesterday_from_public(yesterday, checkin_dir, logger=None):
    """
    从公开仓库复制前一天的 Excel 到本地打卡目录。

    同时兼容新旧文件名格式。

    :param yesterday: 前一天日期 (datetime)
    :param checkin_dir: 打卡数据目录
    :return: 复制了多少个文件
    """
    public_dir = get_public_dir()
    if not os.path.isdir(public_dir):
        return 0
    log = get_logger(logger)
    copied = 0
    new_fmt = yesterday.strftime('%Y%m%d')
    old_fmt = yesterday.strftime('%m%d')
    for fname in os.listdir(public_dir):
        if not fname.endswith('.xlsx') or fname.startswith('~$'):
            continue
        if fname == 'mapping.xlsx':
            continue
        if new_fmt in fname or old_fmt in fname:
            src = os.path.join(public_dir, fname)
            dst = os.path.join(checkin_dir, fname)
            try:
                shutil.copy2(src, dst)
                copied += 1
            except Exception as e:
                log.warning(f"复制 {fname} 失败：{e}")
    if copied:
        log.info(_pink(f"📋 从公开仓库复制了 {copied} 个前一天 Excel 文件"))
    return copied


def git_push_public(checkin_dir, target_date, message, logger=None, groupname=None):
    """
    将当日打卡结果 Excel 复制到公开仓库并推送。

    只复制与 target_date 匹配的 .xlsx 文件，
    跳过 mapping.xlsx、~$ 临时文件和历史文件。
    若指定 groupname，则只复制该群的文件。

    :param checkin_dir: 打卡数据目录
    :param target_date: 目标日期 (datetime)
    :param message: commit 信息
    :param groupname: 群名称；若提供则只推送该群
    """
    public_dir = get_public_dir()
    log = get_logger(logger)

    if not os.path.isdir(public_dir):
        log.warning(f"公开仓库未设置（{public_dir} 不存在），跳过同步")
        return True

    log.info(_pink("📤 同步打卡结果到公开仓库..."))

    new_fmt = target_date.strftime('%Y%m%d')
    old_fmt = target_date.strftime('%m%d')

    copied = 0
    for fname in os.listdir(checkin_dir):
        if not fname.endswith('.xlsx'):
            continue
        if fname == 'mapping.xlsx' or fname.startswith('~$'):
            continue
        # 只复制当天日期的文件
        if new_fmt not in fname and old_fmt not in fname:
            continue
        # 指定群时，只复制该群的文件
        if groupname and groupname not in fname:
            continue
        src = os.path.join(checkin_dir, fname)
        dst = os.path.join(public_dir, fname)
        try:
            shutil.copy2(src, dst)
            copied += 1
        except Exception as e:
            log.warning(f"复制 {fname} 失败：{e}")

    ok, _ = _run_git(['add', '*.xlsx'], public_dir, logger)
    if not ok:
        return False

    ok, _ = _run_git(['commit', '-m', message], public_dir, logger)
    if not ok:
        log.info(_pink("公开仓库：没有新文件需要同步"))
        return True

    ok, output = _run_git(['push'], public_dir, logger)
    if ok:
        log.info(_pink(f"✅ 打卡结果已公开同步（{copied} 个文件）"))
    else:
        log.error(f"❌ 公开仓库推送失败: {output}")
    return ok


def _get_remote_mapping_bytes(project_root, repo_path):
    """从 origin/master 读取 mapping.xlsx 字节内容，失败返回 None。"""
    try:
        result = subprocess.run(
            ['git', 'show', f'origin/master:{repo_path}'],
            cwd=project_root,
            capture_output=True,
            timeout=15,
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    return None


def _mapping_repo_path(checkin_dir):
    """返回 mapping.xlsx 在 git 仓库内的相对路径（正斜杠，供 git show 使用）。"""
    project_root = os.path.dirname(os.path.abspath(__file__))
    mapping_abs = os.path.abspath(os.path.join(checkin_dir, 'mapping.xlsx'))
    return os.path.relpath(mapping_abs, project_root).replace('\\', '/')


def _xlsx_bytes_to_mapping(data):
    """把 xlsx 字节解析为 {微信号: 学号} 字典。"""
    wb = openpyxl.load_workbook(BytesIO(data), data_only=True)
    sheet = wb['Sheet1'] if 'Sheet1' in wb.sheetnames else wb.active
    mapping = {}
    for row in sheet.iter_rows(min_row=2, values_only=True):
        wx_id, student_id = row[0], row[1]
        if wx_id and student_id:
            mapping[str(wx_id).strip()] = str(student_id).strip()
    wb.close()
    return mapping


def _read_local_mapping(checkin_dir):
    """读本地 mapping.xlsx 为字典，文件不存在或读取失败返回 {}。"""
    path = os.path.join(checkin_dir, 'mapping.xlsx')
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'rb') as f:
            return _xlsx_bytes_to_mapping(f.read())
    except Exception:
        return {}


def git_sync_mapping_before_checkin(checkin_dir, logger=None):
    """
    打卡前：fetch 远程私人仓库，把远程最新 mapping.xlsx 与本地合并成总 mapping。

    合并规则：以远程为底、本地覆盖，两边各自新加的学号都保留。
    只 fetch + 单文件读取，不 pull、不 merge，代码文件一个字都不动。

    :param checkin_dir: 打卡数据目录（通常是 WeChatTextExport/）
    :return: 是否成功（失败不阻塞打卡，仅告警，继续用本地映射）
    """
    log = get_logger(logger)
    project_root = os.path.dirname(os.path.abspath(__file__))
    repo_path = _mapping_repo_path(checkin_dir)

    # 1) fetch（只拉头，不动工作区任何文件）
    ok, _ = _run_git(['fetch', 'origin', 'master'], project_root, logger)
    if not ok:
        log.warning(_pink("⚠️ fetch 远程失败，跳过 mapping 同步，使用本地映射"))
        return False

    # 2) 读远程最新 mapping（不存在或读取失败 → 空字典）
    remote_bytes = _get_remote_mapping_bytes(project_root, repo_path)
    remote_dict = _xlsx_bytes_to_mapping(remote_bytes) if remote_bytes is not None else {}
    if not remote_dict:
        log.info(_pink("远程尚无 mapping.xlsx（首次提交），仅使用本地映射"))

    # 3) 读本地 mapping
    local_dict = _read_local_mapping(checkin_dir)

    # 4) 合并：远程为底、本地覆盖（双方新学号都不丢）
    merged = dict(remote_dict)
    merged.update(local_dict)

    if not merged:
        log.info(_pink("远程与本地均无 mapping，跳过写回"))
        return True

    # 5) 写回本地（复用 Wxname2StudentID 的合并/排序写回逻辑）
    from wxname2studentid import Wxname2StudentID
    Wxname2StudentID(directory=checkin_dir).save_mapping(merged)

    log.info(_pink(
        f"✅ mapping 已与远程合并：远程 {len(remote_dict)} 条 + 本地 {len(local_dict)} 条"
        f" → 总 {len(merged)} 条"
    ))
    return True


def _push_mapping_commit(project_root, repo_path, message, logger):
    """
    基于 origin/master 构造一个「只改 mapping.xlsx」的新 commit 并推送。

    用独立临时 index 完成，不碰本地分支、工作区、主 index ——
    因此远程的代码改动不会被拉进本地（4.x 稳定代码不受影响），
    也不会因本地分支落后而 non-fast-forward。

    :return: 是否推送成功
    """
    import tempfile

    def _git_raw(args, env=None, timeout=30):
        try:
            r = subprocess.run(
                ['git'] + args, cwd=project_root, capture_output=True,
                encoding='utf-8', errors='replace', timeout=timeout, env=env,
            )
            return r.returncode == 0, (r.stdout + r.stderr).strip()
        except Exception as e:
            return False, str(e)

    # 1) 拿 origin/master 的 commit hash
    ok, remote_commit = _git_raw(['rev-parse', 'origin/master'])
    if not ok or not remote_commit:
        return False
    remote_commit = remote_commit.strip()

    # 2) 独立 index：read-tree 远程 → 用本地 mapping 覆盖 → write-tree
    fd, index_path = tempfile.mkstemp(prefix='mapping_idx_', suffix='.idx')
    os.close(fd)
    os.remove(index_path)  # 让 git 自建 index，避免空文件被误判
    try:
        index_env = dict(os.environ)
        index_env['GIT_INDEX_FILE'] = index_path

        ok, _ = _git_raw(['read-tree', remote_commit], env=index_env)
        if not ok:
            return False
        # 把本地工作区的 mapping.xlsx 登记进 index（覆盖远程版本）
        ok, _ = _git_raw(['add', '--', repo_path], env=index_env)
        if not ok:
            return False
        ok, tree = _git_raw(['write-tree'], env=index_env)
        if not ok or not tree:
            return False
        tree = tree.strip()

        # 3) 基于 origin/master 建 commit（parent = 远程最新，保证可 fast-forward push）
        ok, new_commit = _git_raw(
            ['commit-tree', tree, '-p', remote_commit, '-m', message]
        )
        if not ok or not new_commit:
            return False
        new_commit = new_commit.strip()

        # 4) 推送新 commit 到远程 master
        ok, _ = _git_raw(['push', 'origin', f'{new_commit}:refs/heads/master'])
        return ok
    finally:
        try:
            os.remove(index_path)
        except Exception:
            pass


def git_push_mapping(checkin_dir, message, logger=None):
    """
    打卡后：若本地 mapping.xlsx 相对远程有新增，合并后推送到私人仓库。

    流程：
        1. fetch origin master（拿远程最新）
        2. 读远程 mapping + 本地 mapping，合并（远程为底、本地覆盖）
        3. 合并后 == 远程（本地无新增）→ 不推送
        4. 写回本地 = 合并结果
        5. 基于 origin/master 构造只改 mapping 的新 commit 并 push
           （不碰本地分支/代码，避免把远程代码改动拉进本地）
        6. push 失败（并发）→ 重试一次

    :param checkin_dir: 打卡数据目录（通常是 WeChatTextExport/）
    :param message: commit 信息
    """
    log = get_logger(logger)
    mapping = os.path.join(checkin_dir, 'mapping.xlsx')
    if not os.path.exists(mapping):
        return True

    project_root = os.path.dirname(os.path.abspath(__file__))
    repo_path = _mapping_repo_path(checkin_dir)

    def _merge_remote_and_local():
        """返回 (merged_dict, has_change)，has_change = 本地相对远程有新增。"""
        remote_bytes = _get_remote_mapping_bytes(project_root, repo_path)
        remote_dict = _xlsx_bytes_to_mapping(remote_bytes) if remote_bytes is not None else {}
        local_dict = _read_local_mapping(checkin_dir)
        merged = dict(remote_dict)
        merged.update(local_dict)
        return merged, (merged != remote_dict)

    for attempt in range(1, 3):  # 最多 2 次（并发时重试 1 次）
        # 1) fetch 远程最新
        if not _run_git(['fetch', 'origin', 'master'], project_root, logger)[0]:
            log.warning(_pink("⚠️ fetch 远程失败，跳过 mapping 推送"))
            return False

        # 2) 合并远程 + 本地
        merged, has_change = _merge_remote_and_local()

        if not has_change:
            log.info(_pink("mapping.xlsx 无变化，跳过推送"))
            return True

        # 3) 写回本地 = 合并结果（保证不丢任何一方）
        from wxname2studentid import Wxname2StudentID
        Wxname2StudentID(directory=checkin_dir).save_mapping(merged)
        log.info(_pink(
            f"📤 mapping.xlsx 有更新（合并后共 {len(merged)} 条），推送到私人仓库..."
        ))

        # 4) 基于 origin/master 构造单文件 commit 并 push
        if _push_mapping_commit(project_root, repo_path, message, logger):
            log.info(_pink("✅ mapping.xlsx 已上传"))
            return True

        if attempt == 1:
            log.warning(_pink("⚠️ 推送失败（可能并发），fetch 后重试..."))

    log.error("❌ mapping.xlsx 推送失败（已重试）")
    return False
