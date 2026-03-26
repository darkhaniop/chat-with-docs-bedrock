"""Offline suite runner: ruff, mypy, pytest, infra tests, vitest.

Kept offline and fast per docs/08-testing.md#strategy. Integration and e2e suites need a
deployed dev stack and are run separately (see docs/10-roadmap.md).
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class Step:
    def __init__(self, name: str, cmd: Sequence[str], cwd: Path = REPO_ROOT) -> None:
        self.name = name
        self.cmd = cmd
        self.cwd = cwd


def _npm_project_exists(cwd: Path) -> bool:
    return (cwd / "package.json").exists()


def build_steps() -> list[Step]:
    steps = [
        Step("ruff check", ["uv", "run", "ruff", "check", "."]),
        Step("ruff format --check", ["uv", "run", "ruff", "format", "--check", "."]),
        Step("mypy services/common", ["uv", "run", "mypy", "services/common"]),
        Step("pytest (unit)", ["uv", "run", "pytest"]),
    ]
    if shutil.which("npm") is not None:
        infra_dir = REPO_ROOT / "infra"
        web_dir = REPO_ROOT / "web"
        if _npm_project_exists(infra_dir):
            steps.append(Step("infra tests (jest)", ["npm", "test"], cwd=infra_dir))
        if _npm_project_exists(web_dir):
            steps.append(Step("web tests (vitest)", ["npm", "test"], cwd=web_dir))
    return steps


def main() -> None:
    results: list[tuple[Step, bool]] = []
    for step in build_steps():
        print(f"\n=== {step.name} ===")
        result = subprocess.run(step.cmd, cwd=step.cwd)
        results.append((step, result.returncode == 0))

    print("\n=== cwd-check summary ===")
    ok = True
    for step, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"{status:5} {step.name}")
        ok = ok and passed

    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
