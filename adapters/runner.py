import asyncio
import json
import os
import shlex
import shutil
from dataclasses import dataclass

MAX_STDOUT_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class CliFailure(Exception):
    code: str
    safe_message: str
    retryable: bool = False

    def __str__(self):
        return self.safe_message


class JsonCliRunner:
    def __init__(self, command, *, timeout_seconds=60, environment=None):
        self.argv = shlex.split(command or "")
        self.timeout_seconds = timeout_seconds
        self.environment = environment or {}

    @property
    def available(self):
        return bool(self.argv and shutil.which(self.argv[0]))

    async def run(self, *arguments):
        if not self.argv:
            raise CliFailure("COMMAND_NOT_CONFIGURED", "Adapter 命令尚未配置")
        if not self.available:
            raise CliFailure("COMMAND_NOT_FOUND", "Adapter 命令在运行环境中不可用")
        env = os.environ.copy()
        env.update({key: value for key, value in self.environment.items() if value})
        try:
            process = await asyncio.create_subprocess_exec(
                *self.argv,
                *arguments,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, _stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError as exc:
            if "process" in locals() and process.returncode is None:
                process.kill()
                await process.wait()
            raise CliFailure("TIMEOUT", "Adapter 调用超时", retryable=True) from exc
        if len(stdout) > MAX_STDOUT_BYTES:
            raise CliFailure("OUTPUT_TOO_LARGE", "Adapter 返回内容超过安全上限")
        if process.returncode != 0:
            raise CliFailure(
                "COMMAND_FAILED",
                f"Adapter 命令执行失败（退出码 {process.returncode}）",
                retryable=process.returncode in {75, 111},
            )
        try:
            payload = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CliFailure("INVALID_JSON", "Adapter 未返回有效 JSON") from exc
        if isinstance(payload, list):
            return {"items": payload}
        if not isinstance(payload, dict):
            raise CliFailure("INVALID_SHAPE", "Adapter JSON 顶层必须是对象或数组")
        return payload
