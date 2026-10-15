from pathlib import Path

import pytest

from scripts.build_desktop_runtime import (
    prune_runtime,
    python_executable,
    runtime_manifest,
    validate_runtime,
)


def test_manifest_uses_relative_executable_and_release_version(tmp_path):
    interpreter = tmp_path / "python" / "bin" / "python3"
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    manifest = runtime_manifest(tmp_path, "1.2.3")
    assert manifest["python"] == "python/bin/python3"
    assert manifest["version"] == "1.2.3"
    assert manifest["protocol"] == 1
    assert manifest["cwd"] == "."


def test_windows_interpreter_is_recognized(tmp_path):
    interpreter = tmp_path / "python" / "python.exe"
    interpreter.parent.mkdir()
    interpreter.touch()
    assert python_executable(tmp_path) == interpreter


def test_runtime_rejects_symlink_outside_bundle(tmp_path):
    runtime = tmp_path / "runtime"
    interpreter = runtime / "python" / "bin" / "python3"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(Path(__file__).resolve())
    with pytest.raises(ValueError, match="outside"):
        validate_runtime(runtime)


def test_runtime_accepts_internal_interpreter_symlink(tmp_path):
    binary = tmp_path / "python" / "bin" / "python3.12"
    binary.parent.mkdir(parents=True)
    binary.touch()
    binary.with_name("python3").symlink_to("python3.12")
    validate_runtime(tmp_path)


def test_runtime_rejects_absolute_internal_symlink_before_relocation(tmp_path):
    binary = tmp_path / "staging" / "python" / "bin" / "python3.12"
    binary.parent.mkdir(parents=True)
    binary.touch()
    binary.with_name("python3").symlink_to(binary)
    with pytest.raises(ValueError, match="relative"):
        validate_runtime(tmp_path / "staging")


def test_relative_runtime_remains_valid_after_relocation(tmp_path):
    runtime = tmp_path / "staging"
    binary = runtime / "python" / "bin" / "python3.12"
    binary.parent.mkdir(parents=True)
    binary.touch()
    binary.with_name("python3").symlink_to("python3.12")
    runtime.rename(tmp_path / "installed")
    validate_runtime(tmp_path / "installed")


def test_runtime_rejects_broken_internal_resource_link(tmp_path):
    binary = tmp_path / "python" / "bin" / "python3"
    binary.parent.mkdir(parents=True)
    binary.touch()
    binary.with_name("pydoc3").symlink_to("pydoc3.12")
    with pytest.raises(ValueError, match="broken"):
        validate_runtime(tmp_path)


def test_missing_interpreter_is_refused(tmp_path):
    with pytest.raises(ValueError, match="interpreter"):
        python_executable(tmp_path)


def test_prune_keeps_the_windows_stdlib_whose_folder_is_python_lib(tmp_path):
    """Windows ships the stdlib directly in python/Lib; Tcl/Tk pruning must not touch it."""
    stdlib = tmp_path / "python" / "lib"  # Windows' "Lib" (case-insensitive there)
    stdlib.mkdir(parents=True)
    for name in ("os.py", "threading.py", "tkinter_helper.py"):
        (stdlib / name).write_text("", encoding="utf-8")
    prune_runtime(tmp_path)
    assert (stdlib / "threading.py").exists()
    assert (stdlib / "os.py").exists()


def test_prune_still_removes_tcl_tk_from_the_posix_lib_folder(tmp_path):
    lib = tmp_path / "python" / "lib"
    (lib / "python3.12").mkdir(parents=True)
    (lib / "python3.12" / "threading.py").write_text("", encoding="utf-8")
    (lib / "tcl8.6").mkdir()
    (lib / "thread2.8.9").mkdir()
    (lib / "libtk8.6.dylib").write_text("", encoding="utf-8")
    prune_runtime(tmp_path)
    assert not (lib / "tcl8.6").exists() and not (lib / "thread2.8.9").exists()
    assert not (lib / "libtk8.6.dylib").exists()
    assert (lib / "python3.12" / "threading.py").exists()
