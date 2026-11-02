#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""让 pyusb 用 libusb_package 自带的 libusb-1.0.dll (Windows)。

为什么需要这个文件:
  candleLight / CANable 这类 gs_usb 适配器走 WinUSB + libusb, 而 pyusb 默认靠
  ctypes.util.find_library('usb-1.0') 去系统里找 libusb-1.0.dll —— 本机没有,
  于是 python-can 的 gs_usb 后端报 NoBackendError: No backend available。
  (实测: 装了 gs_usb 与 libusb-package 之后仍然如此, 因为 gs_usb 内部调用
   usb.core.find() 时不传 backend, pyusb 就用自己那套查找逻辑并缓存了失败结果。)

做法 (两级, 都是幂等的):
  1) 用 libusb_package.get_libusb1_backend() 拿到可用后端;
  2) 让 pyusb 的 usb.backend.libusb1.get_backend() 直接返回它, 并保证
     usb.core.find() 走我们的后端 (替换 find, 缺 backend 时自动补上)。

用法: 在 import can / 打开总线【之前】import 本模块即可:
    import usb_gs      # noqa: F401
    import can
    bus = can.Bus(interface='gs_usb', channel=0, bitrate=500000)

没装 libusb_package 时是空操作 (ZQWL 那类串口适配器完全不受影响)。
"""
_PATCHED = False

import time                  # patch_gs_usb_send_retry 里的小间隔重试用

try:
    import usb.core
    import usb.backend.libusb1 as _lb
    import libusb_package
except Exception:            # 缺任何一个就什么都不做
    usb = None
    _lb = None
    libusb_package = None


def _our_backend():
    try:
        be = libusb_package.get_libusb1_backend()
        if be is None:
            return None
        # 自检: 真的能枚举才算可用
        list(usb.core.find(find_all=True, backend=be))
        return be
    except Exception:
        return None


def reset_gs_usb(vid=0x1D50, pid=0x606F, wait=2.0):
    """复位 gs_usb 设备 (candleLight/CANable)。

    为什么需要: 实测会遇到这种情况 —— claim_interface(0) 成功、设备也在、驱动也是
    WinUSB, 但 python-can 打开时 gs_usb 的启动序列仍报
        USBError [Errno 13] Access denied (insufficient permissions)
    这种卡死状态下【复位一次 USB 设备】就能立刻打开 (验证过: reset 后第一次就 OK)。
    拔插适配器同理。所以调用方在打开失败时应该复位并重试一次。
    """
    if usb is None:
        return False
    ok = False
    try:
        import time as _t
        for d in usb.core.find(find_all=True, idVendor=vid, idProduct=pid):
            try:
                d.reset()
                ok = True
            except Exception:
                pass
        if ok:
            _t.sleep(wait)          # 等它重新枚举
    except Exception:
        return False
    return ok


def dispose_gs_usb(vid=0x1D50, pid=0x606F):
    """释放 pyusb 对 gs_usb 设备占用的接口资源。

    为什么需要: gs_usb 库的 GsUsb.start() 失败时【不会】释放 pyusb 的 Device,
    接口就一直是 claim 状态 -> 同一进程里之后所有打开尝试都是 Access denied
    (实测: 长时间运行的网页失败几次后彻底打不开, 只能重启进程/拔插)。
    打开失败后调用本函数, 再重试就能成功。
    """
    if usb is None:
        return False
    ok = False
    try:
        for d in usb.core.find(find_all=True, idVendor=vid, idProduct=pid):
            try:
                usb.util.dispose_resources(d)
                ok = True
            except Exception:
                pass
    except Exception:
        return False
    return ok


def patch_gs_usb_cleanup(vid=0x1D50, pid=0x606F):
    """给 python-can 的 gs_usb 后端加一层"打开失败就释放接口"的包装。

    为什么必须做 (实测根因):
      GsUsbBus.__init__ 里 GsUsb.start() 失败时【不会】释放 pyusb 的 Device,
      USB 接口一直处于 claim 状态 -> 同一进程之后每次打开都是
          USBError [Errno 13] Access denied
      而且是"连 reset 都做不了"(reset 也要先 claim) -> 网页重试几次后彻底锁死,
      只能重启页面或拔插适配器。实测: 失败后 dispose_resources 一下, 再 reset+开
      就成功; 所以这里把"失败即释放"固化进库调用路径。
    """
    try:
        from can.interfaces import gs_usb as cg
        if getattr(cg, "_dsh_cleanup_patched", False):
            return True
        orig_init = cg.GsUsbBus.__init__

        def _init(self, channel, *a, **kw):
            try:
                orig_init(self, channel, *a, **kw)
            except Exception:
                try:
                    for d in usb.core.find(find_all=True, idVendor=vid, idProduct=pid):
                        try:
                            usb.util.dispose_resources(d)
                        except Exception:
                            pass
                except Exception:
                    pass
                raise

        cg.GsUsbBus.__init__ = _init
        cg._dsh_cleanup_patched = True
        return True
    except Exception:
        return False


def patch_gs_usb_send_retry(tries=8, gap=0.002):
    """给 python-can 的 gs_usb 后端 send() 加"失败小睡再试"。

    为什么: candleLight 的 TX FIFO 满 / USB 忙时, 底层 usb write 会直接失败,
    python-can 抛 CanOperationError("The message could not be sent")。实测做 CAN-OTA
    连续灌数据时撞到, 整个传输被中止 (设备把半截镜像标记无效, 需要重来)。
    加重试基本都能发出去 (每次多等 2ms, 最坏 ~72ms)。
    """
    try:
        from can.interfaces import gs_usb as cg
        if getattr(cg, "_dsh_send_retry_patched", False):
            return True
        orig_send = cg.GsUsbBus.send

        def _send(self, msg, *a, **kw):
            last = None
            for i in range(tries):
                try:
                    return orig_send(self, msg, *a, **kw)
                except Exception as exc:
                    last = exc
                    # ★★ 关键: candleLight 的 OUT 端点会 halt (USBError EPIPE) ——
                    #   实测连续发 ~50 帧后就"再也发不出去"(第二次 0/300), 不清 halt
                    #   的话之后每一次 send 都立刻失败, CAN-OTA 传输就是这样被中止的。
                    #   清掉 0x02 (OUT) 端点 halt 后重试即可继续。
                    try:
                        dev = getattr(getattr(self, "gs_usb", None), "gs_usb", None)
                        if dev is not None:
                            dev.clear_halt(0x02)
                    except Exception:
                        pass
                    time.sleep(gap * (i + 1))
            raise last

        cg.GsUsbBus.send = _send
        cg._dsh_send_retry_patched = True
        return True
    except Exception:
        return False


def patch_gs_usb_reader():
    """给 python-can 的 gs_usb 后端常驻一个"排空 IN(0x81)"的读线程。

    为什么必须做 (实测根因, 本机 candleLight/gs_usb):
      IN 端点(0x81)如果没人持续读走, 适配器固件会把 OUT 端点的受理拖到 ~1s 才完成,
      于是 send() 恒定 1007.7 ms/帧 (1.0 帧/s), 连发约 50 帧后直接失败
      (python-can: CanOperationError "The message could not be sent")。
      网页仪表盘因为有读线程一直 recv 所以正常; CAN-OTA 灌数据时中间大段只发不收,
      就是被这个卡住/中止的 (设备把半截镜像标记无效)。

      实测对照 (同一适配器, 同一根线):
        只发不读      : 5/5 帧, 1007.7 ms/帧, 1.0 帧/s
        后台线程持续读: 300/300 帧, 0.06 s, 5443 帧/s, 0.2 ms/帧

    做法: 读线程【独占】USB 读, 收到的帧塞进 spill 队列; _recv_internal 只从队列取,
    因此不会有两个线程同时读 USB, 上层 recv() / Notifier / 网页的读循环用法完全不变。
    """
    try:
        import queue as _queue
        import threading

        from can.interfaces import gs_usb as cg

        if getattr(cg, "_dsh_reader_patched", False):
            return True
        orig_init = cg.GsUsbBus.__init__
        orig_recv = cg.GsUsbBus._recv_internal
        orig_shutdown = cg.GsUsbBus.shutdown

        def _drain(self):
            while not self._dsh_stop.is_set():
                try:
                    msg, _filtered = orig_recv(self, 0.01)
                except Exception:
                    time.sleep(0.01)
                    continue
                if msg is not None:
                    try:
                        self._dsh_spill.put_nowait(msg)
                    except _queue.Full:
                        pass          # 上层不来取就丢弃最旧的, 绝不阻塞读线程

        def _init(self, *a, **kw):
            orig_init(self, *a, **kw)
            self._dsh_spill = _queue.Queue(maxsize=20000)
            self._dsh_stop = threading.Event()
            self._dsh_thread = threading.Thread(
                target=_drain, args=(self,), name="gs_usb-drain", daemon=True)
            self._dsh_thread.start()

        def _recv_internal(self, timeout):
            if timeout is None:
                to = None            # 一直等
            elif timeout <= 0:
                to = 0.0             # 非阻塞
            else:
                to = float(timeout)
            try:
                if to is None:
                    return self._dsh_spill.get(), False
                return self._dsh_spill.get(timeout=to), False
            except _queue.Empty:
                return None, False

        def _shutdown(self):
            try:
                self._dsh_stop.set()
                self._dsh_thread.join(timeout=0.3)
            except Exception:
                pass
            return orig_shutdown(self)

        cg.GsUsbBus.__init__ = _init
        cg.GsUsbBus._recv_internal = _recv_internal
        cg.GsUsbBus.shutdown = _shutdown
        cg._dsh_reader_patched = True
        return True
    except Exception:
        return False


def ensure_backend():
    """把 libusb_package 的后端交给 pyusb; 返回 True 表示已接管。"""
    global _PATCHED
    if _PATCHED:
        return True
    if usb is None or _lb is None or libusb_package is None:
        return False

    be = _our_backend()
    if be is None:
        return False

    # ① 让所有 get_backend() 调用都拿到我们的后端
    try:
        _lb.get_backend = lambda find_library=None: be
    except Exception:
        return False

    # ② 兜底: usb.core.find 若没给 backend, 自动补上 (gs_usb 就是这么调的)
    try:
        _orig_find = usb.core.find

        def _find(*args, **kwargs):
            if kwargs.get("backend") is None:
                kwargs["backend"] = be
            return _orig_find(*args, **kwargs)

        usb.core.find = _find
    except Exception:
        return False

    _PATCHED = True
    return True


ensure_backend()
patch_gs_usb_cleanup()
patch_gs_usb_reader()
patch_gs_usb_send_retry()
