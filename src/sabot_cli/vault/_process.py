"""Whether a process exists, when it started and when the machine did, in the standard library.

A write lock names the process that holds it. A process id can be reused after a reboot or a
crash, so the lock also records when its writer started (the same id with another start time is
a different process) and which boot of the machine it ran in (another boot is another set of
processes, whatever their ids). Every function here answers "cannot tell" (None, or True for
existence) rather than raising, because a lock written by another tool, or by another version of
this one, can hold any value.
"""

import ctypes
import os
import sys
import time

__all__ = ["boot_identity", "plausible_pid", "process_is_alive", "process_start", "same_boot"]

# A process id is a signed 32-bit number on POSIX systems and an unsigned one on Windows; a larger
# number was not written by a process of this host.
_PID_LIMIT = 2**32 if sys.platform == "win32" else 2**31
# Boot times read from the clock can differ by this many seconds between two readings of one
# boot, from a clock adjustment, so two of them within it are one boot.
_BOOT_TOLERANCE_SECONDS = 10


def plausible_pid(pid: int) -> bool:
    """Whether `pid` is a number a process of this host can have."""
    return 0 < pid < _PID_LIMIT


def process_is_alive(pid: int) -> bool:
    """Whether process `pid` exists on this host; True when that cannot be told."""
    if not plausible_pid(pid):
        return True
    if sys.platform == "win32":
        return _windows_process_is_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        # The process exists but belongs to someone else, or the platform declines to say.
        return True
    return True


def process_start(pid: int) -> str | None:
    """A string that identifies when process `pid` started, or None when it cannot be read.

    Two readings are equal exactly when they are of the same run of the same process on the same
    boot. The string is opaque: compare it, do not parse it.
    """
    if not plausible_pid(pid):
        return None
    try:
        if sys.platform.startswith("linux"):
            return _linux_start(pid)
        if sys.platform == "darwin":
            return _macos_start(pid)
        if sys.platform == "win32":
            return _windows_start(pid)
    except (OSError, ValueError, OverflowError, AttributeError, ImportError, ctypes.ArgumentError):
        return None
    return None


def boot_identity() -> str | None:
    """A string that identifies this boot of the machine, or None when it cannot be read.

    Compare two with `same_boot`: on macOS and Windows it is a boot time, which a clock
    adjustment can move a little.
    """
    try:
        if sys.platform.startswith("linux"):
            return _linux_boot()
        if sys.platform == "darwin":
            return _macos_boot()
        if sys.platform == "win32":
            return _windows_boot()
    except (OSError, ValueError, OverflowError, AttributeError, ImportError, ctypes.ArgumentError):
        return None
    return None


def same_boot(recorded: str, current: str) -> bool:
    """Whether two boot identities name one boot."""
    kind, _, recorded_value = recorded.partition(":")
    current_kind, _, current_value = current.partition(":")
    if kind != current_kind:
        return False
    if kind in ("darwin", "win32"):
        try:
            return abs(int(recorded_value) - int(current_value)) <= _BOOT_TOLERANCE_SECONDS
        except ValueError:
            pass
    return recorded_value == current_value


def _linux_boot() -> str | None:
    with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as handle:
        boot = handle.read().strip()
    return f"linux:{boot}" if boot else None


class _TimeVal(ctypes.Structure):
    _fields_ = [("sec", ctypes.c_int64), ("usec", ctypes.c_int32)]


def _macos_boot() -> str | None:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.sysctlbyname.argtypes = [
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    libc.sysctlbyname.restype = ctypes.c_int
    value = _TimeVal()
    size = ctypes.c_size_t(ctypes.sizeof(value))
    if libc.sysctlbyname(b"kern.boottime", ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
        return None
    if size.value != ctypes.sizeof(value) or value.sec <= 0:
        return None
    return f"darwin:{value.sec}"


def _windows_boot() -> str | None:
    if sys.platform != "win32":
        return None
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetTickCount64.argtypes = []
    kernel32.GetTickCount64.restype = ctypes.c_uint64
    milliseconds = int(kernel32.GetTickCount64())
    return f"win32:{int(time.time()) - milliseconds // 1000}"


def _linux_start(pid: int) -> str | None:
    with open(f"/proc/{pid}/stat", encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    # The command name is in parentheses and may itself hold spaces or parentheses, so the
    # fields are counted from the last closing one. `starttime` is field 22 of the file and the
    # 20th after the command name.
    fields = text.rsplit(")", 1)[1].split()
    ticks = fields[19]
    try:
        with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as handle:
            boot = handle.read().strip()
    except OSError:
        boot = ""
    return f"linux:{boot}:{int(ticks)}"


class _BsdInfo(ctypes.Structure):
    """The `proc_bsdinfo` record of macOS `libproc`, whose last two fields hold the start time."""

    _fields_ = [
        ("flags", ctypes.c_uint32),
        ("status", ctypes.c_uint32),
        ("xstatus", ctypes.c_uint32),
        ("pid", ctypes.c_uint32),
        ("ppid", ctypes.c_uint32),
        ("uid", ctypes.c_uint32),
        ("gid", ctypes.c_uint32),
        ("ruid", ctypes.c_uint32),
        ("rgid", ctypes.c_uint32),
        ("svuid", ctypes.c_uint32),
        ("svgid", ctypes.c_uint32),
        ("rfu_1", ctypes.c_uint32),
        ("comm", ctypes.c_char * 16),
        ("name", ctypes.c_char * 32),
        ("nfiles", ctypes.c_uint32),
        ("pgid", ctypes.c_uint32),
        ("pjobc", ctypes.c_uint32),
        ("e_tdev", ctypes.c_uint32),
        ("e_tpgid", ctypes.c_uint32),
        ("nice", ctypes.c_int32),
        ("start_sec", ctypes.c_uint64),
        ("start_usec", ctypes.c_uint64),
    ]


def _macos_start(pid: int) -> str | None:
    libproc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    libproc.proc_pidinfo.argtypes = [
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint64,
        ctypes.c_void_p,
        ctypes.c_int,
    ]
    libproc.proc_pidinfo.restype = ctypes.c_int
    info = _BsdInfo()
    bsd_info_flavor = 3
    written = libproc.proc_pidinfo(pid, bsd_info_flavor, 0, ctypes.byref(info), ctypes.sizeof(info))
    if written != ctypes.sizeof(info):
        return None
    return f"darwin:{info.start_sec}:{info.start_usec}"


_STILL_ACTIVE = 259
_ERROR_INVALID_PARAMETER = 87
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_ulong), ("high", ctypes.c_ulong)]


def _windows_kernel32() -> ctypes.CDLL:
    if sys.platform != "win32":
        raise OSError("not Windows")
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    kernel32.GetExitCodeProcess.restype = ctypes.c_int
    kernel32.GetProcessTimes.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
        ctypes.POINTER(_FileTime),
    ]
    kernel32.GetProcessTimes.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    return kernel32


def _windows_process_is_alive(pid: int) -> bool:
    if sys.platform != "win32":
        return True
    # `os.kill(pid, 0)` is not a probe on Windows: it terminates the process.
    kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        return ctypes.get_last_error() != _ERROR_INVALID_PARAMETER
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return True
        return code.value == _STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _windows_start(pid: int) -> str | None:
    if sys.platform != "win32":
        return None
    kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, 0, pid)
    if not handle:
        return None
    try:
        created, exited, kernel, user = (_FileTime() for _ in range(4))
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        return f"win32:{(created.high << 32) | created.low}"
    finally:
        kernel32.CloseHandle(handle)
