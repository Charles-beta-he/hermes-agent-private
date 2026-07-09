#!/usr/bin/env python3
"""Smoke-test built Hermes release artifacts in an isolated virtualenv."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run(cmd: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(cwd or REPO_ROOT), text=True, capture_output=True)


def _failure(proc: subprocess.CompletedProcess[str], max_chars: int = 1600) -> str:
    out = "\n".join(part for part in (proc.stdout.strip(), proc.stderr.strip()) if part)
    return out[-max_chars:] if out else f"exit {proc.returncode}"


def _venv_python(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def smoke_wheel(wheel: Path, *, keep_env: bool = False) -> bool:
    wheel = wheel.resolve()
    if not wheel.exists():
        print(f"✗ wheel not found: {wheel}", file=sys.stderr)
        return False
    if wheel.suffix != ".whl":
        print(f"✗ artifact is not a wheel: {wheel}", file=sys.stderr)
        return False

    tmp = tempfile.TemporaryDirectory(prefix="hermes-release-smoke-")
    env_dir = Path(tmp.name)
    try:
        uv_bin = shutil.which("uv")
        if uv_bin:
            create = _run([uv_bin, "venv", str(env_dir)])
            if create.returncode != 0:
                print("✗ uv venv failed", file=sys.stderr)
                print(_failure(create), file=sys.stderr)
                return False
        else:
            try:
                builder = venv.EnvBuilder(with_pip=True)
                builder.create(env_dir)
            except Exception as exc:
                print(f"✗ venv creation failed: {exc}", file=sys.stderr)
                return False
        py = _venv_python(env_dir)

        if uv_bin:
            install = _run([uv_bin, "pip", "install", "--python", str(py), str(wheel)])
        else:
            install = _run([str(py), "-m", "pip", "install", "--no-input", str(wheel)])
        if install.returncode != 0:
            print("✗ pip install failed", file=sys.stderr)
            print(_failure(install), file=sys.stderr)
            return False

        checks = (
            [str(py), "-m", "hermes_cli.main", "--version"],
            [str(py), "-m", "hermes_cli.main", "context", "--help"],
        )
        for cmd in checks:
            proc = _run(cmd)
            if proc.returncode != 0:
                print(f"✗ smoke failed: {' '.join(cmd)}", file=sys.stderr)
                print(_failure(proc), file=sys.stderr)
                return False
            first_line = (proc.stdout.strip() or proc.stderr.strip()).splitlines()[0]
            print(f"✓ {' '.join(cmd[1:])}: {first_line[:160]}")
        print(f"✓ release artifact smoke passed: {wheel.name}")
        return True
    finally:
        if keep_env:
            print(f"kept smoke env: {env_dir}")
        else:
            tmp.cleanup()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a built Hermes wheel")
    parser.add_argument("wheel", type=Path, help="Path to the built .whl artifact")
    parser.add_argument("--keep-env", action="store_true", help="Keep the temporary venv for debugging")
    args = parser.parse_args(argv)
    return 0 if smoke_wheel(args.wheel, keep_env=args.keep_env) else 1


if __name__ == "__main__":
    raise SystemExit(main())
