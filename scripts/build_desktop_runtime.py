"""Build a relocatable interpreter and ONW wheel for desktop resources."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PYTHON_VERSION = "3.12.13"
# Targets other than the build host: the interpreter is downloaded for that platform, its wheels
# are resolved for it (nothing is executed), and the runtime is verified statically.
CROSS_TARGETS = {
    "windows-x64": {
        "python": f"cpython-{PYTHON_VERSION}-windows-x86_64-none",
        "uv_platform": "x86_64-pc-windows-msvc",
        "site_packages": Path("python/Lib/site-packages"),
        "platform": "windows",
        "arch": "x86_64",
        "wheel_tags": ("win_amd64", "any"),
        "native_suffixes": (".pyd", ".dll"),
        "foreign_suffixes": (".so", ".dylib"),
    }
}


def python_executable(runtime: Path) -> Path:
    for relative in ("python/bin/python3", "python/python.exe", "python/install/python.exe"):
        executable = runtime / relative
        if executable.is_file():
            return executable
    raise ValueError("runtime has no supported Python interpreter")


def validate_runtime(runtime: Path) -> None:
    root = runtime.resolve()
    python_executable(root)
    for path in root.rglob("*"):
        if path.is_junction():
            raise ValueError(f"runtime junction is not portable: {path.relative_to(root)}")
        if not path.is_symlink():
            continue
        if not path.resolve().is_relative_to(root):
            raise ValueError(f"runtime symlink points outside the bundle: {path.relative_to(root)}")
        if path.readlink().is_absolute():
            raise ValueError(f"runtime symlink must be relative: {path.relative_to(root)}")
        if not path.exists():
            raise ValueError(f"runtime symlink is broken: {path.relative_to(root)}")


def runtime_manifest(runtime: Path, version: str, target: str | None = None) -> dict:
    validate_runtime(runtime)
    cross = CROSS_TARGETS.get(target or "")
    return {
        "version": version,
        "protocol": 1,
        "python": python_executable(runtime).relative_to(runtime).as_posix(),
        "cwd": ".",
        "platform": cross["platform"] if cross else platform.system().lower(),
        "arch": cross["arch"] if cross else platform.machine(),
        "python_version": PYTHON_VERSION,
    }


def verify_cross_runtime(runtime: Path, target: str) -> None:
    """A runtime built for another OS cannot be executed here, so prove it is that OS's:
    every installed wheel is tagged for the target (or pure Python) and no other platform's
    native extension slipped in."""
    cross = CROSS_TARGETS[target]
    site = runtime / cross["site_packages"]
    wheels = sorted(site.glob("*.dist-info/WHEEL"))
    if not wheels:
        raise ValueError("no installed distributions found")
    for wheel in wheels:
        tags = [
            line.split(":", 1)[1].strip()
            for line in wheel.read_text(encoding="utf-8").splitlines()
            if line.startswith("Tag:")
        ]
        platforms = {tag.rsplit("-", 1)[-1] for tag in tags}
        if not any(any(ok in plat for ok in cross["wheel_tags"]) for plat in platforms):
            raise ValueError(f"{wheel.parent.name} is not a {target} wheel: {tags}")
    foreign = [p for p in site.rglob("*") if p.suffix in cross["foreign_suffixes"]]
    if foreign:
        raise ValueError(f"foreign native modules in the runtime: {foreign[:3]}")
    if not (site / "backend" / "main.py").is_file():
        raise ValueError("the ONW backend is not installed in the runtime")


def run(args: list[str], *, cwd: Path = REPO, env: dict | None = None) -> None:
    # CreateProcess does not apply PATHEXT: resolve `npm` to `npm.cmd` (and friends) up front.
    command = [shutil.which(args[0]) or args[0], *args[1:]]
    subprocess.run(command, cwd=cwd, env=env, check=True, timeout=900)


def build_assets() -> None:
    for directory, command in (("frontend", "build"), ("docs-site", "build:embed")):
        project = REPO / directory
        if not (project / "node_modules").exists():
            run(["npm", "ci", "--no-audit", "--no-fund"], cwd=project)
        run(["npm", "run", command], cwd=project)
    run(["uv", "run", "--no-project", "python", "scripts/bundle_assets.py"])


def install_runtime(staging: Path, wheel: Path, target: str | None = None) -> None:
    cross = CROSS_TARGETS.get(target or "")
    installations = staging / "interpreters"
    run(
        [
            "uv",
            "python",
            "install",
            cross["python"] if cross else PYTHON_VERSION,
            "--install-dir",
            str(installations),
            "--no-bin",
            "--no-registry",
        ]
    )
    # uv also leaves a minor-version link (a symlink, or a junction on Windows) beside the install.
    candidates = [
        path
        for path in installations.glob(f"cpython-{PYTHON_VERSION}-*")
        if not path.is_symlink() and not path.is_junction()
    ]
    if len(candidates) != 1:
        raise ValueError("expected exactly one managed CPython distribution")
    runtime = staging / "runtime"
    runtime.mkdir()
    shutil.move(str(candidates[0]), runtime / "python")
    # uv marks a managed install it did not create natively with an absolute, dangling link.
    if cross:
        (runtime / "python" / "python").unlink(missing_ok=True)
    locked = REPO / "desktop" / "runtime-requirements.lock"
    if cross:
        destination = [
            "--target",
            str(runtime / cross["site_packages"]),
            "--python-platform",
            cross["uv_platform"],
            "--python-version",
            PYTHON_VERSION.rsplit(".", 1)[0],
            "--only-binary",
            ":all:",
        ]
    else:
        destination = ["--python", str(python_executable(runtime)), "--break-system-packages"]
    run(["uv", "pip", "install", *destination, "--require-hashes", "-r", str(locked)])
    run(["uv", "pip", "install", *destination, "--no-deps", str(wheel)])
    prune_runtime(runtime)
    for name in ("LICENSE", "NOTICE"):
        shutil.copy2(REPO / name, runtime / name)
    shutil.copy2(locked, runtime / "runtime-requirements.lock")


# What the desktop app never runs, removed from the shipped runtime (~60 MB). The packaged-import
# smoke below runs AFTER this, so a removal the backend does need fails the build, not a user.
_PRUNED_PACKAGES = ("pip", "psycopg", "psycopg_binary", "psycopg_c")  # desktop books are SQLite
_PRUNED_STDLIB = ("ensurepip", "idlelib", "tkinter", "turtledemo", "lib2to3", "test")
_PRUNED_DIR_NAMES = frozenset({"tests"})


def prune_runtime(runtime: Path) -> None:
    for stdlib in runtime.glob("python/lib/python3*"):
        for name in _PRUNED_STDLIB:
            shutil.rmtree(stdlib / name, ignore_errors=True)
    for lib in runtime.glob("python/lib"):
        # On Windows python/Lib IS the stdlib (and the filesystem ignores case), so "thread*" would
        # delete threading.py. Only the POSIX layout keeps Tcl/Tk's own files directly in python/lib.
        if (lib / "os.py").exists():
            continue
        for pattern in ("libtcl*", "libtk*", "tcl*", "tk*", "itcl*", "thread*"):
            for path in lib.glob(pattern):
                shutil.rmtree(path, ignore_errors=True) if path.is_dir() else path.unlink()
    for site in runtime.rglob("site-packages"):
        for name in _PRUNED_PACKAGES:
            for path in site.glob(f"{name}"):
                shutil.rmtree(path, ignore_errors=True)
            for path in site.glob(f"{name}-*.dist-info"):
                shutil.rmtree(path, ignore_errors=True)
        for path in sorted(site.rglob("*"), key=lambda p: len(p.parts)):
            if path.is_dir() and path.name in _PRUNED_DIR_NAMES and path.exists():
                shutil.rmtree(path, ignore_errors=True)


def smoke_runtime(runtime: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="onw-desktop-build-qa-") as qa:
        # os.environ upper-cases names on Windows, so compare upper-cased.
        env = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TEMP", "TMP", "LANG"}
        }
        env.update({"ONW_ROOT": qa, "ONW_QA_MODE": "1", "PYTHONDONTWRITEBYTECODE": "1"})
        subprocess.run(
            [
                str(python_executable(runtime)),
                "-I",
                "-c",
                "import backend.main; from backend.core.version import APP_VERSION; "
                "from backend.core.shipped_assets import FRONTEND_DIST_DIR, DOCS_SITE_DIST_DIR; "
                "assert (FRONTEND_DIST_DIR/'index.html').is_file(); "
                "assert (DOCS_SITE_DIST_DIR/'index.html').is_file(); "
                "print('PACKAGED_IMPORT_OK', APP_VERSION)",
            ],
            cwd=qa,
            env=env,
            check=True,
            timeout=60,
        )


def build(destination: Path, *, skip_assets: bool = False, target: str | None = None) -> Path:
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("output already exists; choose a new output directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not skip_assets:
        build_assets()
    version = tomllib.loads((REPO / "pyproject.toml").read_text())["project"]["version"]
    with tempfile.TemporaryDirectory(prefix=".runtime-build-", dir=destination.parent) as temp:
        staging = Path(temp)
        wheels = staging / "wheels"
        run(["uv", "build", "--wheel", "--out-dir", str(wheels)])
        wheel = next(wheels.glob("*.whl"))
        install_runtime(staging, wheel, target)
        runtime = staging / "runtime"
        manifest = runtime_manifest(runtime, version, target)
        manifest["wheel_sha256"] = hashlib.sha256(wheel.read_bytes()).hexdigest()
        manifest["requirements_sha256"] = hashlib.sha256(
            (runtime / "runtime-requirements.lock").read_bytes()
        ).hexdigest()
        (runtime / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if target:
            verify_cross_runtime(runtime, target)
        else:
            smoke_runtime(runtime)
        shutil.move(str(runtime), destination)
    return destination


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=REPO / "desktop" / "runtime")
    parser.add_argument("--skip-assets", action="store_true", help="use already bundled assets")
    parser.add_argument(
        "--target",
        choices=sorted(CROSS_TARGETS),
        help="build a runtime for another platform (verified statically, not executed)",
    )
    args = parser.parse_args()
    try:
        output = build(args.output, skip_assets=args.skip_assets, target=args.target)
    except (ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"Desktop runtime build failed: {exc}\n")
    print(f"Desktop runtime: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
