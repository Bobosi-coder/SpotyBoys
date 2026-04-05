from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .versioning import build_s3_uri, join_s3_key

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_aws_cli() -> str:
    env_path = os.environ.get("AWS_CLI_PATH")
    candidates = [
        env_path,
        shutil.which("aws"),
        PROJECT_ROOT / ".venv" / "bin" / "aws",
        PROJECT_ROOT / ".venv" / "Scripts" / "aws.exe",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        candidate_path = Path(candidate)
        if candidate_path.exists():
            return str(candidate_path)
        if isinstance(candidate, str) and shutil.which(candidate):
            return candidate
    raise FileNotFoundError(
        "Could not resolve AWS CLI. Set AWS_CLI_PATH or install aws in PATH/.venv."
    )


class S3ObjectStore:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None = None,
        no_verify_ssl: bool | None = None,
        aws_bin: str | None = None,
    ) -> None:
        self.bucket = bucket
        self.endpoint_url = endpoint_url or os.environ.get(
            "S3_ENDPOINT", "https://chi.tacc.chameleoncloud.org:7480"
        )
        if no_verify_ssl is None:
            no_verify_ssl = os.environ.get("S3_NO_VERIFY_SSL", "true").lower() == "true"
        self.no_verify_ssl = no_verify_ssl
        self.aws_bin = aws_bin or _resolve_aws_cli()

    def _base_command(self) -> list[str]:
        command = [self.aws_bin]
        if self.endpoint_url:
            command.extend(["--endpoint-url", self.endpoint_url])
        if self.no_verify_ssl:
            command.append("--no-verify-ssl")
        return command

    def _run(self, args: list[str]) -> None:
        subprocess.run(args, check=True)

    def upload_file(self, local_path: str | Path, key: str) -> str:
        key = join_s3_key(key)
        destination = build_s3_uri(self.bucket, key)
        command = self._base_command() + ["s3", "cp", str(local_path), destination]
        self._run(command)
        return destination

    def upload_directory(self, local_dir: str | Path, prefix: str) -> str:
        prefix = join_s3_key(prefix)
        destination = build_s3_uri(self.bucket, prefix)
        command = self._base_command() + ["s3", "sync", str(local_dir), destination]
        self._run(command)
        return destination
