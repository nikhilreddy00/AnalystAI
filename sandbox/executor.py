"""
E2B sandbox wrapper. Executes LLM-generated Python code in an isolated environment.
Two untrusted inputs (LLM code + user data) never touch the FastAPI process.

E2B v1.x reads the API key from the E2B_API_KEY environment variable.
We set it explicitly from settings before creating the sandbox.

Data is transferred as CSV (not parquet) because E2B's default Python
environment does not include pyarrow or fastparquet.
"""
from __future__ import annotations

import io
import os
from dataclasses import dataclass, field

import pandas as pd
from e2b_code_interpreter import AsyncSandbox

from api.config import settings


@dataclass
class ExecutionResult:
    stdout: str
    stderr: str
    error: str | None
    figures_base64: list[str] = field(default_factory=list)
    output_json: str | None = None  # last stdout line if JSON


def _ensure_api_key() -> None:
    if not os.environ.get("E2B_API_KEY"):
        os.environ["E2B_API_KEY"] = settings.e2b_api_key


def _parquet_to_csv_bytes(df_parquet: bytes) -> bytes:
    """Convert parquet bytes → CSV bytes locally (pyarrow is available here)."""
    df = pd.read_parquet(io.BytesIO(df_parquet))
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


async def run_code(code: str, df_parquet: bytes | None = None) -> ExecutionResult:
    """
    Run Python code inside an isolated E2B sandbox.
    If df_parquet is provided, the DataFrame is available via pd.read_csv('/data/df.csv').
    """
    _ensure_api_key()

    # BUG-25: Wrap sandbox creation in try/except with clear error message
    try:
        # No internet: generated code can read the uploaded CSV but cannot send it anywhere.
        sandbox = await AsyncSandbox.create(allow_internet_access=False)
    except Exception as exc:
        return ExecutionResult(
            stdout="",
            stderr=str(exc),
            error=f"Code execution sandbox unavailable. Check E2B API key and connectivity. Detail: {exc}",
        )

    try:
        if df_parquet:
            csv_bytes = _parquet_to_csv_bytes(df_parquet)
            await sandbox.files.write("/data/df.csv", csv_bytes)

        # BUG-40: Add timeout to prevent infinite loops in LLM-generated code
        try:
            execution = await sandbox.run_code(code, timeout=60)
        except Exception as exc:   # e2b raises on timeout; callers get a typed SandboxError
            return ExecutionResult(stdout="", stderr=str(exc),
                                   error=f"Sandbox execution failed: {type(exc).__name__}: {exc}")

        figures: list[str] = []
        for result in execution.results:
            if hasattr(result, "png") and result.png:
                figures.append(result.png)  # already base64

        stdout = "\n".join(execution.logs.stdout) if execution.logs.stdout else ""
        stderr = "\n".join(execution.logs.stderr) if execution.logs.stderr else ""
        error = str(execution.error) if execution.error else None

        output_json: str | None = None
        if stdout:
            last_line = stdout.strip().split("\n")[-1]
            if last_line.startswith("{") or last_line.startswith("["):
                output_json = last_line

        return ExecutionResult(
            stdout=stdout,
            stderr=stderr,
            error=error,
            figures_base64=figures,
            output_json=output_json,
        )
    finally:
        await sandbox.kill()
