"""Private JSON worker for bounded OpenSeesPy execution."""

from __future__ import annotations

import json
import os
import sys
import tempfile

from .fem import run_linear_static_job


def main() -> int:
    diagnostics = ""
    try:
        request = sys.stdin.read(500_001)
        if len(request.encode("utf-8")) > 500_000:
            raise ValueError("FEM request exceeds size limit")
        job = json.loads(request)
        with tempfile.TemporaryFile() as diagnostic_file:
            original_out, original_err = os.dup(1), os.dup(2)
            try:
                os.dup2(diagnostic_file.fileno(), 1)
                os.dup2(diagnostic_file.fileno(), 2)
                try:
                    result = run_linear_static_job(job)
                    failure = None
                except Exception as exc:
                    result = None
                    failure = type(exc).__name__
            finally:
                os.dup2(original_out, 1)
                os.dup2(original_err, 2)
                os.close(original_out)
                os.close(original_err)
            diagnostic_file.seek(0)
            log = diagnostic_file.read(16_001).decode("utf-8", errors="replace")
        if failure:
            print(
                json.dumps({"ok": False, "error": failure, "diagnostics": log[:16_000]})
            )
            return 1
        response = {"ok": True, "result": result, "diagnostics": log[:16_000]}
        print(json.dumps(response, separators=(",", ":"), allow_nan=False))
        return 0
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": type(exc).__name__,
                    "diagnostics": diagnostics[:16_000],
                }
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
