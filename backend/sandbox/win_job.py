"""Windows resource caps for the bare-subprocess sandbox: a Job Object per run.

POSIX applies ``setrlimit`` in the forked child. Windows has no fork, so the same T4
(resource exhaustion) envelope is a Job Object the child is born into:

  * ``PROCESS_MEMORY``  — per-process committed-memory cap     (RLIMIT_AS / RLIMIT_DATA)
  * ``PROCESS_TIME``    — per-process user-mode CPU-time cap   (RLIMIT_CPU)
  * ``ACTIVE_PROCESS=1``— the child cannot spawn another one   (RLIMIT_NPROC=0)
  * ``KILL_ON_JOB_CLOSE`` + ``terminate()`` — the whole tree dies with the run (killpg)

The child is created suspended, put in the job, then resumed, so no instruction runs before
the caps apply. Two POSIX caps have no Windows equivalent and are enforced differently:
``RLIMIT_FSIZE`` by a watchdog that polls the scratch dir (``ScratchGuard``, so a runaway
writer can overshoot the cap by up to one poll interval of writes), and ``RLIMIT_NOFILE``
not at all (Windows has no per-process handle cap).

Stdlib only — ``backend.sandbox`` is a secret-free leaf.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import threading
from ctypes import wintypes
from pathlib import Path
from typing import Callable

CREATE_SUSPENDED = 0x00000004

_JOB_OBJECT_LIMIT_PROCESS_TIME = 0x00000002
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001
_PROCESS_SUSPEND_RESUME = 0x0800
_CPU_TICKS_PER_SECOND = 10_000_000  # the job's CPU limit is in 100 ns units
_SCRATCH_POLL_SECONDS = 0.25


class _IoCounters(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_ulonglong)
        for name in (
            "ReadOperationCount",
            "WriteOperationCount",
            "OtherOperationCount",
            "ReadTransferCount",
            "WriteTransferCount",
            "OtherTransferCount",
        )
    ]


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimits),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll = ctypes.WinDLL("ntdll")
_kernel32.CreateJobObjectW.restype = wintypes.HANDLE
_kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
_kernel32.SetInformationJobObject.argtypes = [
    wintypes.HANDLE,
    ctypes.c_int,
    ctypes.c_void_p,
    wintypes.DWORD,
]
_kernel32.SetInformationJobObject.restype = wintypes.BOOL
_kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
_kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
_kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
_kernel32.TerminateJobObject.restype = wintypes.BOOL
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL
_ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
_ntdll.NtResumeProcess.restype = ctypes.c_long


def _check(ok: object) -> None:
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


class JobObject:
    """One run's job. ``attach`` puts a suspended child in it and resumes the child."""

    def __init__(self, *, cpu_seconds: int, memory_bytes: int) -> None:
        handle = _kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        self._handle = handle
        limits = _ExtendedLimits()
        basic = limits.BasicLimitInformation
        basic.LimitFlags = (
            _JOB_OBJECT_LIMIT_PROCESS_TIME
            | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
            | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        basic.PerProcessUserTimeLimit = cpu_seconds * _CPU_TICKS_PER_SECOND
        basic.ActiveProcessLimit = 1
        limits.ProcessMemoryLimit = memory_bytes
        try:
            _check(
                _kernel32.SetInformationJobObject(
                    handle,
                    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
                    ctypes.byref(limits),
                    ctypes.sizeof(limits),
                )
            )
        except OSError:
            self.close()
            raise

    def attach(self, proc: subprocess.Popen) -> None:
        """Move ``proc`` (started with ``CREATE_SUSPENDED``) into the job, then resume it.

        Any failure kills the child: an uncapped sandbox child must never run."""
        process = _kernel32.OpenProcess(
            _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_SUSPEND_RESUME, False, proc.pid
        )
        if not process:
            self.terminate()
            proc.kill()
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            try:
                _check(_kernel32.AssignProcessToJobObject(self._handle, process))
                if _ntdll.NtResumeProcess(process) < 0:
                    raise OSError("could not resume the sandbox child")
            except OSError:
                self.terminate()
                proc.kill()
                raise
        finally:
            _kernel32.CloseHandle(process)

    def terminate(self) -> None:
        """Kill every process in the job. Safe to call repeatedly or after the run ended."""
        if self._handle:
            _kernel32.TerminateJobObject(self._handle, 1)

    def close(self) -> None:
        if self._handle:
            _kernel32.CloseHandle(self._handle)
            self._handle = None


class ScratchGuard:
    """``RLIMIT_FSIZE`` stand-in: kill the run when any file under ``scratch`` outgrows
    ``max_file_bytes``. Polls, so it is a bound on damage, not an exact write cap."""

    def __init__(
        self, scratch: Path, max_file_bytes: int, on_violation: Callable[[], None]
    ) -> None:
        self._scratch = scratch
        self._max = max_file_bytes
        self._on_violation = on_violation
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _oversized(self) -> bool:
        stack = [self._scratch]
        while stack:
            try:
                with os.scandir(stack.pop()) as entries:
                    for entry in entries:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir():
                            stack.append(Path(entry.path))
                        elif os.stat(entry.path).st_size > self._max:  # DirEntry sizes lag on NTFS
                            return True
            except OSError:
                continue
        return False

    def _watch(self) -> None:
        while not self._stop.wait(_SCRATCH_POLL_SECONDS):
            if self._oversized():
                self._on_violation()
                return
