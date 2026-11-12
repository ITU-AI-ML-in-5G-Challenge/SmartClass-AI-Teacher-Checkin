"""
微信自动化库兼容层：按当前微信客户端版本自动选择后端库。

背景：
    checkin_v2 原项目固定 `from wxauto4 import WeChat`，只支持微信 4.x。
    微信 3.x 需用 `wxauto`（3.x 库），微信 4.x 需用 `wxauto4`（4.x 库）。

本模块职责：
    - detect_wechat_version()：通过主窗口类名识别当前微信是 3.x 还是 4.x
    - get_wechat_main_window()：按版本分流查找微信主窗口句柄
    - WeChat：代理类，实例化时按检测结果实例化对应后端库的 WeChat
    - WxParam：惰性代理，访问属性时返回对应后端库的 WxParam

版本识别依据（窗口类名）：
    - 微信 3.x 主窗口类名 = WeChatMainWndForPC
    - 微信 4.x 主窗口类名 = Chrome_WidgetWin_0

兜底开关：
    可在 .env 里设 ``WECHAT_VERSION=3`` 或 ``=4`` 强制指定，跳过自动检测。
"""
import os
import win32gui
import win32con

from dotenv import load_dotenv

# 确保能读到 .env 里的 WECHAT_VERSION
load_dotenv()

_V3_CLASS = 'WeChatMainWndForPC'
_V4_CLASS = 'Chrome_WidgetWin_0'


def _find_main_hwnd():
    """
    定位微信主窗口句柄：标题"微信" + GWL_STYLE 降序取最前。

    为什么不按类名枚举判版本：微信 3.9 内置 Chromium，会开出多个
    Chrome_WidgetWin_0 窗口（公众号/小程序/视频号），直接按类名枚举会被
    这些窗口干扰、把 3.x 误判成 4.x。按标题"微信" + 层级排序则能稳定命中
    真正的主窗口（checkin/export.py 已验证：3.x 拿到 WeChatMainWndForPC，
    4.x 拿到 Chrome_WidgetWin_0）。

    :return: 微信主窗口句柄或 None
    :rtype: int or None
    """
    results = []

    def _cb(hwnd, _):
        if win32gui.GetWindowText(hwnd) == '微信':
            layer = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
            results.append((hwnd, layer))

    win32gui.EnumWindows(_cb, None)
    if results:
        results.sort(key=lambda x: x[1], reverse=True)
        return results[0][0]
    return None


def detect_wechat_version():
    """
    识别当前登录微信的客户端版本。

    先定位主窗口（标题"微信"+层级排序，见 _find_main_hwnd），再按主窗口
    类名判断版本（标题无法区分版本：3.x / 4.x 主窗口标题都是"微信"）：
      - WeChatMainWndForPC  → 微信 3.x（类名唯一）
      - Chrome_WidgetWin_0  → 微信 4.x

    :return: '3'（微信 3.x）、'4'（微信 4.x），检测不到返回 None
    :rtype: str or None
    """
    # 1) 允许 .env 手动覆盖，兜底检测失效的场景
    override = os.getenv('WECHAT_VERSION')
    if override in ('3', '4'):
        return override

    # 2) 定位主窗口后按类名判版本（不被微信 3.9 内置的 Chrome 窗口干扰）
    hwnd = _find_main_hwnd()
    if not hwnd:
        return None
    cls = win32gui.GetClassName(hwnd)
    if cls == _V3_CLASS:
        return '3'
    if cls == _V4_CLASS:
        return '4'
    return None


def _backend():
    """
    返回 (后端模块, 版本号)。

    检测不到微信窗口时抛 RuntimeError，提示用户先登录微信或手动指定版本。
    """
    ver = detect_wechat_version()
    if ver == '4':
        import wxauto4 as mod
    elif ver == '3':
        import wxauto as mod
    else:
        raise RuntimeError(
            "未检测到微信主窗口（类名既不是 WeChatMainWndForPC 也不是 Chrome_WidgetWin_0）。"
            "请先登录微信；若类名异常，可在 .env 里设 WECHAT_VERSION=3 或 4 强制指定。"
        )
    return mod, ver


class WeChat:
    """代理类：实例化时按当前微信版本实例化对应后端库的 WeChat。"""

    def __new__(cls, *args, **kwargs):
        mod, _ = _backend()
        return mod.WeChat(*args, **kwargs)


class _WxParamProxy:
    """惰性代理：访问属性时返回对应后端库的 WxParam 属性。"""

    def __getattr__(self, name):
        mod, _ = _backend()
        return getattr(mod.WxParam, name)


WxParam = _WxParamProxy()


def get_wechat_main_window():
    """
    返回微信主窗口句柄，找不到返回 None。

    统一用标题"微信" + GWL_STYLE 层级排序定位（checkin/export.py 已验证），
    3.x / 4.x 均适用：主窗口层级最高，TrayNotifyWnd 等干扰窗口层级为负、排最后。

    :return: 微信主窗口句柄或 None
    :rtype: int or None
    """
    return _find_main_hwnd()
