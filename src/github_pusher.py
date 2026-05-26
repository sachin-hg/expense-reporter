"""Push generated reports to GitHub via git CLI."""

import calendar
import subprocess
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


class GitHubPusher:
    def push(self, report_path: Path, year: int, month: int):
        month_name = calendar.month_name[month]
        json_path = report_path.with_suffix(".json")

        files_to_add = [str(report_path)]
        if json_path.exists():
            files_to_add.append(str(json_path))

        self._run(["git", "add"] + files_to_add)
        self._run(
            [
                "git",
                "commit",
                "-m",
                f"report: add {month_name} {year} expense report",
            ]
        )
        self._run(["git", "push"])
        logger.info(f"Pushed {report_path.name} to GitHub")

    def _run(self, cmd: list) -> str:
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(
                f"Command failed: {' '.join(cmd)}\nstderr: {result.stderr.strip()}"
            )
        return result.stdout.strip()
