# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 The Linux Foundation
"""Summarise an aislop JSON report for the GitHub Actions step summary.

Read the aislop JSON report at ``$AISLOP_JSON`` and write a
human-readable markdown summary to ``$GITHUB_STEP_SUMMARY``. Also emit
a compact listing to stdout and, when annotations are enabled,
``::warning``/``::error``/``::notice`` workflow commands for the top
findings so they surface as inline PR annotations.

Configuration comes from the environment:

* ``AISLOP_JSON``     path to the aislop JSON report (required)
* ``AISLOP_TOP_N``    max findings to detail and annotate (default 10)
* ``AISLOP_SCOPE``    scope label shown in the summary header
* ``AISLOP_ANNOTATE`` emit workflow-command annotations when ``true``
"""

import json
import os
import sys
from collections import Counter
from pathlib import Path
from posixpath import basename
from typing import Any
from urllib.parse import quote

LEVEL_LABEL = {
    "error": "\u26d4 Error",
    "warning": "\u26a0\ufe0f Warning",
    "info": "\u2139\ufe0f Info",
}
LEVEL_ORDER = ["error", "warning", "info"]
WARN_CMD = {"error": "error", "warning": "warning", "info": "notice"}
MAX_MSG_LEN = 200

Finding = dict[str, Any]


def _escape_wf_data(value: object) -> str:
    """Escape the message body of a GitHub workflow command.

    Per GitHub's workflow-command rules, ``%``, ``CR`` and ``LF`` must
    be percent-encoded in the data (post-``::``) portion.
    """
    return (
        str(value)
        .replace("%", "%25")
        .replace("\r", "%0D")
        .replace("\n", "%0A")
    )


def _escape_wf_property(value: object) -> str:
    """Escape a property value of a GitHub workflow command.

    Properties live in the comma-separated ``key=value`` list before
    ``::``. In addition to the data-escapes, ``,`` and ``:`` must be
    encoded so they cannot terminate the property list or the command
    prefix.
    """
    return _escape_wf_data(value).replace(":", "%3A").replace(",", "%2C")


def _render_link(
    file: str, line: int | None, repo: str, sha: str, server: str
) -> str:
    """Render a path:line link with the basename as visible text."""
    if not file:
        return ""
    short = basename(file) or file
    label = short + (f":{line}" if line else "")
    if not repo or not sha:
        return f"`{label}`"
    anchor = f"#L{line}" if line else ""
    url = f"{server}/{repo}/blob/{sha}/{quote(file)}{anchor}"
    return f"[`{label}`]({url})"


def _load_report(json_path: Path) -> dict[str, Any] | None:
    """Load the aislop JSON report; return None when unreadable."""
    try:
        with json_path.open(encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def _normalise(data: dict[str, Any]) -> list[Finding]:
    """Extract and sort the diagnostics from an aislop report.

    aislop reports ``line: 0`` for whole-file findings; normalise that
    to ``None`` so summaries and annotations omit the meaningless
    location.
    """
    findings: list[Finding] = []
    diags = data.get("diagnostics")
    if not isinstance(diags, list):
        diags = []
    for diag in diags:
        if not isinstance(diag, dict):
            continue
        raw_line = diag.get("line")
        line: int | None = None
        if raw_line is not None:
            try:
                line = int(raw_line) or None
            except (TypeError, ValueError):
                line = None
        findings.append(
            {
                "engine": diag.get("engine", "?"),
                "rule": diag.get("rule", "?"),
                "level": diag.get("severity", "warning"),
                "msg": str(diag.get("message", "")).strip(),
                "file": diag.get("filePath", ""),
                "line": line,
            }
        )

    def sort_key(f: Finding) -> tuple[int, str, str, int]:
        try:
            idx = LEVEL_ORDER.index(f["level"])
        except ValueError:
            idx = 99
        return (idx, f["rule"], f["file"], f["line"] or 0)

    findings.sort(key=sort_key)
    return findings


def _int_env(name: str, default: int) -> int:
    """Read an integer from the environment; fall back on bad input."""
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _read_context() -> dict[str, Any]:
    """Collect configuration and repository context from the env."""
    sha = os.environ.get("GITHUB_SHA", "")
    return {
        "json_path": Path(os.environ["AISLOP_JSON"]),
        "top_n": _int_env("AISLOP_TOP_N", 10),
        "scope": os.environ.get("AISLOP_SCOPE", ""),
        "annotate": os.environ.get("AISLOP_ANNOTATE", "") == "true",
        "summary_path": os.environ.get("GITHUB_STEP_SUMMARY"),
        "server": os.environ.get(
            "GITHUB_SERVER_URL", "https://github.com"
        ),
        "repo": os.environ.get("GITHUB_REPOSITORY", ""),
        "sha": sha,
        "short_sha": sha[:7] if sha else "?",
    }


def _render_header(
    data: dict[str, Any], total: int, ctx: dict[str, Any]
) -> list[str]:
    """Render the score line, scan context, and finding totals."""
    counts = data.get("summary")
    if not isinstance(counts, dict):
        counts = {}
    score = data.get("score")
    label = data.get("label", "")
    out: list[str] = []
    if score is None:
        out.append("Score: not scoreable (no supported files in scope)")
    else:
        out.append(f"Score: **{score}/100** ({label})")
    out.append("")
    out.append(f"`{ctx['repo']}@{ctx['short_sha']}`")
    if ctx["scope"]:
        out.append(f"scope: {ctx['scope']}")
    out.append(f"cli: `aislop {data.get('cliVersion', '?')}`")
    out.append("")
    if total == 0:
        out.append("No findings \u2705")
    else:
        out.append(
            f"{total} finding(s): "
            f"{counts.get('errors', 0)} error(s), "
            f"{counts.get('warnings', 0)} warning(s); "
            f"{counts.get('fixable', 0)} auto-fixable "
            f"across {counts.get('files', 0)} file(s) \u26a0\ufe0f"
        )
    out.append("")
    return out


def _render_engines(data: dict[str, Any]) -> list[str]:
    """Render the per-engine issue and skip table."""
    engines = data.get("engines")
    if not isinstance(engines, dict) or not engines:
        return []
    out = ["## Engines", "", "| Engine | Issues | Skipped |"]
    out.append("| --- | ---: | --- |")
    for name, eng in engines.items():
        if not isinstance(eng, dict):
            eng = {}
        issues = eng.get("issues", 0)
        skipped = "yes" if eng.get("skipped") else "no"
        out.append(f"| `{name}` | {issues} | {skipped} |")
    out.append("")
    return out


def _render_breakdowns(
    level_counts: Counter[str], rule_counts: Counter[str]
) -> list[str]:
    """Render the counts-by-severity and counts-by-rule tables."""
    out = ["## Counts by severity", "", "| Severity | Count |"]
    out.append("| --- | ---: |")
    for lvl in LEVEL_ORDER:
        if lvl in level_counts:
            out.append(
                f"| {LEVEL_LABEL.get(lvl, lvl)} | {level_counts[lvl]} |"
            )
    out.extend(["", "## Counts by rule", "", "| Rule | Count |"])
    out.append("| --- | ---: |")
    for rid, n in rule_counts.most_common():
        out.append(f"| `{rid}` | {n} |")
    out.append("")
    return out


def _render_findings_table(
    findings: list[Finding], ctx: dict[str, Any]
) -> list[str]:
    """Render the detail table for the top findings."""
    total = len(findings)
    shown = findings[: ctx["top_n"]]
    extra = total - len(shown)
    heading = f"## Top {len(shown)} findings"
    if extra > 0:
        heading += f" (of {total}; {extra} more in the report)"
    out = [heading, ""]
    out.append("| Severity | Engine | Rule | Location | Message |")
    out.append("| --- | --- | --- | --- | --- |")
    for f in shown:
        lvl_label = LEVEL_LABEL.get(f["level"], f["level"])
        msg = f["msg"].replace("|", "\\|").replace("\n", " ")
        if len(msg) > MAX_MSG_LEN:
            msg = msg[: MAX_MSG_LEN - 3] + "..."
        loc = _render_link(
            f["file"], f["line"], ctx["repo"], ctx["sha"], ctx["server"]
        )
        out.append(
            f"| {lvl_label} | `{f['engine']}` | `{f['rule']}` "
            f"| {loc} | {msg} |"
        )
    return out


def _print_console(
    data: dict[str, Any],
    findings: list[Finding],
    level_counts: Counter[str],
    rule_counts: Counter[str],
    top_n: int,
) -> None:
    """Print a compact grouped listing to the job log."""
    print("::group::aislop summary")
    by_level = {LEVEL_LABEL.get(k, k): v for k, v in level_counts.items()}
    print(
        f"Score: {data.get('score')}  label: {data.get('label', '')}  "
        f"total: {len(findings)}  by-level: {by_level}  "
        f"rules: {len(rule_counts)}"
    )
    for f in findings[:top_n]:
        lvl_label = LEVEL_LABEL.get(f["level"], f["level"])
        loc_str = f["file"] + (f":{f['line']}" if f["line"] else "")
        print(f"  {lvl_label}  {f['rule']:<28}  {loc_str}")
    print("::endgroup::")


def _emit_annotations(findings: list[Finding], top_n: int) -> None:
    """Emit workflow-command annotations for the top findings.

    The commands keep the full rule id in the title since GitHub shows
    them out of context (for example in PR file annotations). Property
    values follow GitHub's workflow-command escaping rules so ``,``
    and ``:`` in titles or paths cannot break the format.
    """
    for f in findings[:top_n]:
        cmd = WARN_CMD.get(f["level"], "warning")
        parts = []
        if f["file"]:
            parts.append(f"file={_escape_wf_property(f['file'])}")
        if f["line"]:
            parts.append(f"line={f['line']}")
        ann_title = f"aislop: {f['rule']}"
        parts.append(f"title={_escape_wf_property(ann_title)}")
        msg = f["msg"].replace("\n", " ").strip() or f["rule"]
        print(f"::{cmd} {','.join(parts)}::{_escape_wf_data(msg)}")


def _write(lines: list[str], summary_path: str | None) -> None:
    """Append the rendered markdown lines to the step summary file."""
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def summarise() -> int:
    """Read the aislop report and write the summary; return exit code."""
    ctx = _read_context()
    title = "# \U0001f9f9 AI Slop Scan"
    if ctx["repo"]:
        title += f": {ctx['repo']}"
    out = [title, ""]

    data = _load_report(ctx["json_path"])
    if data is None:
        out.append("aislop produced no readable JSON report \u26a0\ufe0f")
        out.append("")
        out.append("Check the scan step log for tool errors.")
        _write(out, ctx["summary_path"])
        print("aislop summary: no readable JSON report")
        return 0

    findings = _normalise(data)
    total = len(findings)
    level_counts: Counter[str] = Counter(f["level"] for f in findings)
    rule_counts: Counter[str] = Counter(f["rule"] for f in findings)

    out.extend(_render_header(data, total, ctx))
    out.extend(_render_engines(data))
    if total > 0:
        out.extend(_render_breakdowns(level_counts, rule_counts))
        out.extend(_render_findings_table(findings, ctx))
    _write(out, ctx["summary_path"])

    _print_console(data, findings, level_counts, rule_counts, ctx["top_n"])
    if ctx["annotate"]:
        _emit_annotations(findings, ctx["top_n"])
    return 0


if __name__ == "__main__":
    sys.exit(summarise())
