from pathlib import Path
import time
import re
from pywinauto import Desktop
from pywinauto.keyboard import send_keys

def ui_open_video_preview(msg, timeout: int = 10):
    """
    打开视频预览窗口（双击视频消息）

    前提：
      - msg 是 wxauto4 的 VideoMessage
      - 微信主窗口在前台

    行为：
      - 双击视频消息
      - 等待视频预览窗口出现

    成功：
      - 返回 True

    失败：
      - 抛 RuntimeError
    """

    # ---------- Step 1：双击视频消息 ----------
    try:
        # wxauto4 不同版本方法名不同，尽量兼容
        if hasattr(msg, "double_click"):
            msg.double_click()
        elif hasattr(msg, "open"):
            msg.open()
        elif hasattr(msg, "click"):
            # 兜底：有些实现 click 会打开
            msg.click()
        else:
            raise RuntimeError("VideoMessage 不支持 click / double_click / open")

    except Exception as e:
        raise RuntimeError(f"双击视频消息失败: {e}")

    # ---------- Step 2：等待视频预览窗口 ----------
    start = time.time()

    pattern = re.compile(r"查看原视频|原视频")

    while time.time() - start < timeout:
        try:
            win = Desktop(backend="uia").window(title="图片查看")
            for c in win.descendants():
                text = c.window_text() or getattr(c.element_info, "name", "")
                if pattern.search(text or ""):
                    return True
        except Exception:
            pass
        time.sleep(0.5)

    raise RuntimeError("未检测到视频预览窗口（图片查看）")

# def _find_original_video_button():
#     """
#     返回：
#       - "need_click", button     -> 找到『查看原视频』
#       - "already_original", None -> 已是『原视频』
#       - None, None               -> 未找到
#     """
#     for win in Desktop(backend="uia").windows():
#         try:
#             # 情况 1：查看原视频(xxM)
#             btn = win.child_window(
#                 title_re=re.compile(r"查看原视频"),
#                 control_type="Button"
#             )
#             if btn.exists():
#                 return "need_click", btn

#             # 情况 2：原视频（已下载）
#             btn2 = win.child_window(
#                 title="原视频",
#                 control_type="Button"
#             )
#             if btn2.exists():
#                 return "already_original", None

#         except Exception:
#             pass

#     return None, None

# def ui_force_download_original_video(save_path: Path, timeout: int = 60) -> Path:
#     """
#     前提：
#       - 微信视频预览窗口已打开并在前台
#     行为：
#       - 若显示「查看原视频(xxM)」→ 点击并等待变成「原视频」
#       - 若已显示「原视频」→ 直接保存
#       - Ctrl+S 另存为到 save_path
#       - 等待文件稳定落盘
#     失败：
#       - 抛 RuntimeError
#     """
#     save_path = Path(save_path)
#     save_path.parent.mkdir(parents=True, exist_ok=True)

#     # ---------- Step 1：确认当前状态 ----------
#     start = time.time()
#     state = None
#     btn = None

#     while time.time() - start < 10:
#         state, btn = _find_original_video_button()
#         if state:
#             break
#         time.sleep(0.3)

#     if state is None:
#         raise RuntimeError("未找到『查看原视频』或『原视频』按钮（UI 状态异常）")

#     # ---------- Step 2：如需要，点击『查看原视频』 ----------
#     if state == "need_click":
#         btn.click_input()

#         # 等待状态切换为『原视频』
#         start = time.time()
#         while time.time() - start < 20:
#             state, _ = _find_original_video_button()
#             if state == "already":
#                 break
#             time.sleep(0.5)

#         if state != "already":
#             raise RuntimeError("点击『查看原视频』后，未成功进入『原视频』状态")

#     # ---------- Step 3：另存为（Ctrl + S） ----------
#     send_keys("^s")
#     time.sleep(1)

#     send_keys(str(save_path))
#     time.sleep(0.2)
#     send_keys("{ENTER}")

#     # ---------- Step 4：等待文件稳定落盘 ----------
#     last_size = -1
#     stable = 0
#     start = time.time()

#     while time.time() - start < timeout:
#         if save_path.exists():
#             size = save_path.stat().st_size
#             if size > 0 and size == last_size:
#                 stable += 1
#                 if stable >= 3:
#                     return save_path
#             else:
#                 stable = 0
#                 last_size = size
#         time.sleep(0.5)

#     raise RuntimeError("原视频保存超时或文件未稳定")

def ui_click_view_original_area_and_save(
    save_path: Path,
    *,
    window_title: str = "图片查看",
    click_retries: int = 3,
    after_click_wait: float = 1.0,
    timeout: int = 60,
    # 右上角按钮区域的“相对坐标”（可微调）
    # x=0.90 表示窗口宽度的 90% 处；y=0.08 表示窗口高度的 8% 处
    rel_x: float = 0.90,
    rel_y: float = 0.08,
) -> Path:
    """
    适用于微信视频预览窗口 UIA 难以稳定识别控件的情况：
      1) 找到「图片查看」窗口并置前
      2) 在窗口右上角“查看原视频/原视频”区域按相对坐标点击（自绘UI用坐标最稳）
      3) Ctrl+S 另存为
      4) 等文件稳定落盘

    失败：抛 RuntimeError
    """
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    # 1) 找到预览窗口
    try:
        win = Desktop(backend="uia").window(title=window_title)
        if not win.exists(timeout=2):
            raise RuntimeError(f"未找到预览窗口：{window_title}")
        win.set_focus()
        time.sleep(0.2)
    except Exception as e:
        raise RuntimeError(f"定位/聚焦预览窗口失败: {e}")

    # 2) 点击右上角“查看原视频/原视频”区域（坐标点击，绕过 UIA 控件识别）
    rect = win.rectangle()
    width = rect.width()
    height = rect.height()

    # 计算点击坐标（屏幕绝对坐标）
    click_x = rect.left + int(width * rel_x)
    click_y = rect.top + int(height * rel_y)

    last_click_err = None
    for i in range(1, click_retries + 1):
        try:
            win.click_input(coords=(click_x - rect.left, click_y - rect.top))
            time.sleep(after_click_wait)
            last_click_err = None
            break
        except Exception as e:
            last_click_err = e
            time.sleep(0.3)

    if last_click_err:
        raise RuntimeError(f"点击『查看原视频』区域失败（坐标点击多次仍失败）: {last_click_err}")

    # 3) 保存（Ctrl+S）
    send_keys("^s")
    time.sleep(0.8)

    save_path = Path(save_path).resolve()
    send_keys(str(save_path))
    time.sleep(0.2)
    send_keys("{ENTER}")

    # 4) 等文件稳定落盘
    last_size = -1
    stable = 0
    start = time.time()

    while time.time() - start < timeout:
        if save_path.exists():
            size = save_path.stat().st_size
            if size > 0 and size == last_size:
                stable += 1
                if stable >= 3:
                    return save_path
            else:
                stable = 0
                last_size = size
        time.sleep(0.5)

    raise RuntimeError("保存超时：文件未生成或未稳定")


def ui_click_view_original_area_and_save_debug(
    save_path: Path,
    *,
    window_title: str = "图片查看",
    click_retries: int = 3,
    after_click_wait: float = 1.0,
    timeout: int = 60,
    rel_x: float = 0.90,
    rel_y: float = 0.08,
    debug_print: bool = True,
) -> Path:
    """
    坐标点击右上角“查看原视频/原视频”区域，然后 Ctrl+S 另存为。
    debug_print=True 时，会打印窗口矩形与点击坐标，便于调参 rel_x/rel_y。

    失败：抛 RuntimeError
    """
    save_path = Path(save_path)
    save_path.parent.mkdir(parents=True, exist_ok=True)

    # 1) 定位预览窗口
    try:
        win = Desktop(backend="uia").window(title=window_title)
        if not win.exists(timeout=2):
            raise RuntimeError(f"未找到预览窗口：{window_title}")
        win.set_focus()
        time.sleep(0.2)
    except Exception as e:
        raise RuntimeError(f"定位/聚焦预览窗口失败: {e}")

    # 2) 计算点击坐标（相对窗口）
    rect = win.rectangle()
    w = rect.width()
    h = rect.height()

    # 屏幕绝对坐标
    abs_x = rect.left + int(w * rel_x)
    abs_y = rect.top + int(h * rel_y)

    # click_input 需要的是“窗口内相对坐标”
    rel_click_x = abs_x - rect.left
    rel_click_y = abs_y - rect.top

    if debug_print:
        print(f"[DEBUG] window_title={window_title}")
        print(f"[DEBUG] rect=({rect.left},{rect.top})-({rect.right},{rect.bottom})  w={w} h={h}")
        print(f"[DEBUG] rel_x={rel_x} rel_y={rel_y}")
        print(f"[DEBUG] click_abs=({abs_x},{abs_y})  click_rel=({rel_click_x},{rel_click_y})")

    # 3) 坐标点击（多次重试）
    last_click_err = None
    for i in range(1, click_retries + 1):
        try:
            if debug_print:
                print(f"[DEBUG] click attempt {i}/{click_retries} ...")
            win.click_input(coords=(rel_click_x, rel_click_y))
            time.sleep(after_click_wait)
            last_click_err = None
            break
        except Exception as e:
            last_click_err = e
            if debug_print:
                print(f"[DEBUG] click attempt {i} failed: {e}")
            time.sleep(0.3)

    if last_click_err:
        raise RuntimeError(f"点击『查看原视频』区域失败（坐标点击多次仍失败）: {last_click_err}")

    # 4) Ctrl+S 保存
    if debug_print:
        print("[DEBUG] send Ctrl+S")
    send_keys("^s")
    time.sleep(0.8)

    save_path = Path(save_path).resolve()
    if debug_print:
        print(f"[DEBUG] type save path: {save_path}")
    send_keys(str(save_path))
    time.sleep(0.2)
    send_keys("{ENTER}")

    # 5) 等文件稳定落盘
    last_size = -1
    stable = 0
    start = time.time()

    while time.time() - start < timeout:
        if save_path.exists():
            size = save_path.stat().st_size
            if debug_print:
                print(f"[DEBUG] saving... size={size}")
            if size > 0 and size == last_size:
                stable += 1
                if stable >= 3:
                    if debug_print:
                        print("[DEBUG] file settled.")
                    return save_path
            else:
                stable = 0
                last_size = size
        time.sleep(0.5)

    raise RuntimeError("保存超时：文件未生成或未稳定")


def ui_click_view_original_then_save(
    save_path: Path,
    *,
    window_title: str = "图片查看",
    rel_x: float = 0.95,
    rel_y: float = 0.06,
    click_retries: int = 3,
    wait_original_sec: int = 30,
    min_mb: float = 40.0,   # 小于这个就认为没拿到原视频（你也可以改成 30）
    timeout: int = 120,
    debug_print: bool = True,
) -> Path:
    """
    1) 找到「图片查看」窗口并置前
    2) 坐标点击右上角“查看原视频”区域（重试）
    3) 等待 UI 文本从「查看原视频」变为「原视频」（最多 wait_original_sec 秒）
    4) Ctrl+S 保存
    5) 校验文件大小 >= min_mb，否则视为失败
    """
    save_path = Path(save_path).resolve()
    save_path.parent.mkdir(parents=True, exist_ok=True)

    win = Desktop(backend="uia").window(title=window_title)
    if not win.exists(timeout=2):
        raise RuntimeError(f"未找到预览窗口：{window_title}")
    win.set_focus()
    time.sleep(0.2)

    rect = win.rectangle()
    w, h = rect.width(), rect.height()
    rel_click_x = int(w * rel_x)
    rel_click_y = int(h * rel_y)

    if debug_print:
        print(f"[DEBUG] rect=({rect.left},{rect.top})-({rect.right},{rect.bottom})  w={w} h={h}")
        print(f"[DEBUG] rel_x={rel_x} rel_y={rel_y} click_rel=({rel_click_x},{rel_click_y})")

    # 用 UIA 文本“验证状态”，而不是做前置检测
    patt_view = re.compile(r"查看原视频")
    patt_orig = re.compile(r"^原视频$")

    def _snapshot_texts():
        texts = []
        try:
            for c in win.descendants():
                t = c.window_text() or getattr(c.element_info, "name", "") or ""
                if t:
                    texts.append(t)
        except Exception:
            pass
        return texts

    def _has(pattern):
        for t in _snapshot_texts():
            if pattern.search(t):
                return True
        return False

    # 1) 点击右上角区域（尝试多次）
    for i in range(1, click_retries + 1):
        if debug_print:
            print(f"[DEBUG] click attempt {i}/{click_retries} ...")
        win.click_input(coords=(rel_click_x, rel_click_y))
        time.sleep(0.6)
        # 如果还能看到“查看原视频”，说明没触发，继续点
        # 如果已经出现“原视频”，说明触发成功
        if _has(patt_orig):
            if debug_print:
                print("[DEBUG] already in 原视频 state after click")
            break
        if not _has(patt_view):
            # 有些版本点击后“查看原视频”会消失，先等一等
            time.sleep(0.6)

    # 2) 等待变成“原视频”状态（关键）
    start = time.time()
    while time.time() - start < wait_original_sec:
        if _has(patt_orig):
            if debug_print:
                print("[DEBUG] 原视频 state detected")
            break
        time.sleep(0.5)

    # 如果一直没变成“原视频”，这次就很可能仍是预览资源
    if not _has(patt_orig):
        raise RuntimeError("未进入『原视频』状态（可能没点中“查看原视频”或下载未触发）")

    # 3) Ctrl+S 保存
    if debug_print:
        print("[DEBUG] send Ctrl+S")
    send_keys("^s")
    time.sleep(0.8)

    save_path = Path(save_path).resolve()
    if debug_print:
        print(f"[DEBUG] type save path: {save_path}")
    send_keys(str(save_path))
    time.sleep(0.2)
    send_keys("{ENTER}")

    # 4) 等文件稳定
    last = -1
    stable = 0
    start = time.time()
    while time.time() - start < timeout:
        if save_path.exists():
            sz = save_path.stat().st_size
            if debug_print:
                print(f"[DEBUG] saving... size={sz}")
            if sz > 0 and sz == last:
                stable += 1
                if stable >= 3:
                    mb = sz / 1024 / 1024
                    if mb < min_mb:
                        raise RuntimeError(f"保存成功但大小仅 {mb:.1f}MB，疑似未拿到原视频")
                    return save_path
            else:
                stable = 0
                last = sz
        time.sleep(0.5)

    raise RuntimeError("保存超时：文件未生成或未稳定")
import time
from pathlib import Path
from pywinauto.keyboard import send_keys


def ui_click_original_area_and_save_on_window(
    win,
    save_path: Path,
    *,
    rel_x: float = 0.95,
    rel_y: float = 0.06,
    after_click_wait: float = 1.0,
    timeout: int = 120,
    debug_print: bool = True,
) -> Path:
    save_path = Path(save_path).resolve()
    save_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        win.set_focus()
    except Exception:
        pass
    time.sleep(0.2)

    rect = win.rectangle()
    w, h = rect.width(), rect.height()
    x = int(w * rel_x)
    y = int(h * rel_y)

    if debug_print:
        print(f"[DEBUG] USING window title={win.window_text()!r}")
        print(f"[DEBUG] rect=({rect.left},{rect.top})-({rect.right},{rect.bottom}) w={w} h={h}")
        print(f"[DEBUG] click_rel=({x},{y})")

    win.click_input(coords=(x, y))
    time.sleep(after_click_wait)

    send_keys("^s")
    time.sleep(0.8)
    send_keys(str(save_path))
    time.sleep(0.2)
    send_keys("{ENTER}")

    # 等文件稳定落盘
    last = -1
    stable = 0
    start = time.time()

    while time.time() - start < timeout:
        if save_path.exists():
            sz = save_path.stat().st_size
            if debug_print:
                print(f"[DEBUG] saving... size={sz}")
            if sz > 0 and sz == last:
                stable += 1
                if stable >= 3:
                    return save_path
            else:
                stable = 0
                last = sz
        time.sleep(0.5)

    raise RuntimeError("保存超时：文件未生成或未稳定")
