"""Display projection and file output for completed document reviews."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def render_review_report(
    payload: dict[str, Any],
    approval_status: str,
    output_digest: str,
    approval_record: dict[str, Any] | None = None,
) -> tuple[str, str]:
    from pipelines.outputs.renderers import render_review_html, render_review_markdown

    args = {
        **payload,
        "approval_status": approval_status,
        "output_digest": output_digest,
        "approval_record": approval_record,
    }
    return render_review_markdown(**args), render_review_html(**args)


def write_review_report(paths: dict[str, str] | None, markdown: str, html: str) -> None:
    if not paths:
        return
    Path(paths["html"]).write_text(html, encoding="utf-8")
    Path(paths["markdown"]).write_text(markdown, encoding="utf-8")
