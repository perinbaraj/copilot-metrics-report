#!/usr/bin/env python3
"""
GitHub Copilot Productivity Report Generator

Generates a formatted Excel workbook with per-user productivity metrics and
team-level summary KPIs using GitHub Copilot seat data and the NDJSON
users-28-day metrics report.

Usage:
    python copilot_productivity_report.py --enterprise my-ent
    python copilot_productivity_report.py --orgs org1,org2 --token ghp_xxx
    python copilot_productivity_report.py --enterprise my-ent --output-dir ./reports
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import httpx
except ImportError:
    sys.exit("ERROR: httpx is required. Install with: pip install httpx")

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("ERROR: openpyxl is required. Install with: pip install openpyxl")


GITHUB_API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
REPORT_DAYS = 28
MAX_PER_PAGE = 100
MINUTES_SAVED_PER_ACCEPTANCE = 5  # conservative estimate per accepted suggestion

USER_PRODUCTIVITY_COLUMNS = [
    "organization",
    "user_login",
    "active_days",
    "adoption_rate_pct",
    "total_interactions",
    "code_generations",
    "code_acceptances",
    "acceptance_rate_pct",
    "loc_suggested",
    "loc_added",
    "loc_deleted",
    "net_loc_change",
    "copilot_contribution_pct",
    "chat_interactions",
    "agent_interactions",
    "features_used",
    "engagement_depth",
    "estimated_time_saved_hrs",
    "health_profile",
    "health_notes",
]

HEADER_FILL = PatternFill(fill_type="solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF")
SECTION_FONT = Font(bold=True, color="1F4E79")
LABEL_FONT = Font(bold=True)
HEALTH_STYLES = {
    "Power User": {
        "fill": PatternFill(fill_type="solid", fgColor="C6EFCE"),
        "font": Font(color="006100"),
    },
    "Healthy": {
        "fill": PatternFill(fill_type="solid", fgColor="C6EFCE"),
        "font": Font(color="006100"),
    },
    "Agent-Heavy": {
        "fill": PatternFill(fill_type="solid", fgColor="FFEB9C"),
        "font": Font(color="9C6500"),
    },
    "Chat-Focused": {
        "fill": PatternFill(fill_type="solid", fgColor="FFEB9C"),
        "font": Font(color="9C6500"),
    },
    "Moderate": {
        "fill": PatternFill(fill_type="solid", fgColor="BDD7EE"),
        "font": Font(color="003399"),
    },
    "Low Usage": {
        "fill": PatternFill(fill_type="solid", fgColor="FFC7CE"),
        "font": Font(color="9C0006"),
    },
    "Needs Enablement": {
        "fill": PatternFill(fill_type="solid", fgColor="FFC7CE"),
        "font": Font(color="9C0006"),
    },
}


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }


def _try_print_response(resp: httpx.Response) -> None:
    try:
        body = resp.json()
        msg = body.get("message", "")
        if msg:
            print(f"    API: {msg}")
    except Exception:
        pass


def _handle_rate_limit(response: httpx.Response) -> bool:
    remaining = response.headers.get("x-ratelimit-remaining")
    if remaining is not None and remaining.isdigit() and int(remaining) == 0:
        reset_ts = int(response.headers.get("x-ratelimit-reset", 0) or 0)
        wait = max(reset_ts - int(time.time()), 1)
        print(f"    ⏳ Rate-limited. Waiting {wait}s …")
        time.sleep(wait)
        return True
    return False


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_pct(numerator: float, denominator: float, cap: float | None = None) -> float:
    if not denominator:
        return 0.0
    pct = round(numerator / denominator * 100, 1)
    if cap is not None:
        pct = min(pct, cap)
    return pct


def validate_token(token: str) -> None:
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{GITHUB_API_BASE}/user", headers=_headers(token))
        if resp.status_code != 200:
            sys.exit(f"ERROR: Token invalid (HTTP {resp.status_code}).")
        user = resp.json()
        scopes = resp.headers.get("x-oauth-scopes", "(unknown)")
        print(f"🔑 Authenticated as: {user.get('login', '?')}  |  Scopes: {scopes}")


def discover_orgs(token: str, enterprise: str) -> list[str]:
    """Discover orgs under an enterprise, falling back to user orgs."""
    orgs: list[str] = []
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{GITHUB_API_BASE}/enterprises/{enterprise}/organizations",
            headers=_headers(token),
            params={"per_page": MAX_PER_PAGE},
        )
        if resp.status_code in (403, 429) and _handle_rate_limit(resp):
            resp = client.get(
                f"{GITHUB_API_BASE}/enterprises/{enterprise}/organizations",
                headers=_headers(token),
                params={"per_page": MAX_PER_PAGE},
            )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                orgs = [o["login"] for o in data if o.get("login")]
                if orgs:
                    return orgs

        print("  ⚠ Enterprise endpoint unavailable. Using your org memberships.")
        page = 1
        while True:
            resp = client.get(
                f"{GITHUB_API_BASE}/user/orgs",
                headers=_headers(token),
                params={"page": page, "per_page": MAX_PER_PAGE},
            )
            if resp.status_code in (403, 429) and _handle_rate_limit(resp):
                continue
            if resp.status_code != 200:
                _try_print_response(resp)
                break
            data = resp.json()
            if not data:
                break
            orgs.extend(o["login"] for o in data if o.get("login"))
            if len(data) < MAX_PER_PAGE:
                break
            page += 1
    return orgs


def fetch_seats(token: str, org: str) -> list[dict]:
    """Fetch Copilot seat assignments for an org."""
    seats: list[dict] = []
    page = 1
    with httpx.Client(timeout=30) as client:
        while True:
            resp = client.get(
                f"{GITHUB_API_BASE}/orgs/{org}/copilot/billing/seats",
                headers=_headers(token),
                params={"page": page, "per_page": MAX_PER_PAGE},
            )
            if resp.status_code in (403, 429) and _handle_rate_limit(resp):
                continue
            if resp.status_code != 200:
                if resp.status_code in (403, 404):
                    _try_print_response(resp)
                break
            data = resp.json()
            page_seats = data.get("seats", [])
            if not page_seats:
                break
            seats.extend(page_seats)
            if len(seats) >= data.get("total_seats", len(seats)):
                break
            page += 1
    return seats


def fetch_ndjson(token: str, org: str) -> tuple[list[dict], str, str]:
    """Fetch per-user NDJSON usage metrics and report period metadata."""
    records: list[dict] = []
    report_start = ""
    report_end = ""
    with httpx.Client(timeout=60) as client:
        while True:
            resp = client.get(
                f"{GITHUB_API_BASE}/orgs/{org}/copilot/metrics/reports/users-28-day/latest",
                headers=_headers(token),
            )
            if resp.status_code in (403, 429) and _handle_rate_limit(resp):
                continue
            if resp.status_code != 200:
                return [], report_start, report_end
            break

        payload = resp.json()
        report_start = payload.get("report_start_day", "") or ""
        report_end = payload.get("report_end_day", "") or ""
        links = payload.get("download_links", [])

        if links:
            print(f"   📥 NDJSON report: {report_start} → {report_end} ({len(links)} file(s))")

        for link in links:
            dl = client.get(link, timeout=120)
            if dl.status_code in (401, 403):
                dl = client.get(link, headers=_headers(token), timeout=120)
            if dl.status_code != 200:
                continue
            for line in dl.text.strip().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return records, report_start, report_end


def aggregate_user_ndjson(records: list[dict]) -> dict[str, dict[str, Any]]:
    """Aggregate daily NDJSON records per user_login."""
    users: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "days_seen": set(),
            "total_interactions": 0,
            "code_generations": 0,
            "code_acceptances": 0,
            "loc_suggested": 0,
            "loc_added": 0,
            "loc_deleted": 0,
            "chat_interactions": 0,
            "agent_interactions": 0,
            "used_chat": False,
            "used_agent": False,
            "used_cli": False,
            "used_code_review": False,
        }
    )

    for rec in records:
        login = rec.get("user_login", "")
        if not login:
            continue

        user = users[login]
        day = rec.get("day")
        if day:
            user["days_seen"].add(day)

        user["total_interactions"] += _safe_int(rec.get("user_initiated_interaction_count"))
        user["code_generations"] += _safe_int(rec.get("code_generation_activity_count"))
        user["code_acceptances"] += _safe_int(rec.get("code_acceptance_activity_count"))
        user["loc_suggested"] += _safe_int(rec.get("loc_suggested_to_add_sum"))
        user["loc_added"] += _safe_int(rec.get("loc_added_sum"))
        user["loc_deleted"] += _safe_int(rec.get("loc_deleted_sum"))
        user["chat_interactions"] += (
            _safe_int(rec.get("chat_panel_ask_mode"))
            + _safe_int(rec.get("chat_panel_edit_mode"))
            + _safe_int(rec.get("chat_panel_plan_mode"))
            + _safe_int(rec.get("chat_panel_custom_mode"))
        )
        user["agent_interactions"] += _safe_int(rec.get("chat_panel_agent_mode"))

        if rec.get("used_chat"):
            user["used_chat"] = True
        if rec.get("used_agent"):
            user["used_agent"] = True
        if rec.get("used_cli"):
            user["used_cli"] = True
        if rec.get("used_copilot_code_review_active") or rec.get("used_copilot_code_review_passive"):
            user["used_code_review"] = True

    aggregated: dict[str, dict[str, Any]] = {}
    for login, metrics in users.items():
        aggregated[login] = {
            **metrics,
            "active_days": len(metrics["days_seen"]),
        }
        aggregated[login].pop("days_seen", None)
    return aggregated


def build_features_list(metrics: dict[str, Any]) -> str:
    features: list[str] = []
    if metrics.get("used_chat"):
        features.append("chat")
    if metrics.get("used_agent"):
        features.append("agent")
    if metrics.get("used_cli"):
        features.append("cli")
    if metrics.get("used_code_review"):
        features.append("code_review")
    return ", ".join(features) if features else "none"


def classify_health(metrics: dict[str, Any]) -> tuple[str, str]:
    active_days = metrics["active_days"]
    total_interactions = metrics["total_interactions"]
    acceptance_rate = metrics["acceptance_rate_pct"]
    engagement_depth = metrics["engagement_depth"]
    chat_interactions = metrics["chat_interactions"]
    agent_interactions = metrics["agent_interactions"]
    loc_added = metrics["loc_added"]
    loc_suggested = metrics["loc_suggested"]
    code_generations = metrics["code_generations"]

    if active_days == 0 or (active_days > 0 and total_interactions < 5):
        return "Needs Enablement", "No meaningful usage detected — training recommended"
    if 1 <= active_days <= 3 and total_interactions < 20:
        return "Low Usage", "Minimal usage — may need guidance on workflow integration"
    if acceptance_rate >= 30 and active_days >= 14 and engagement_depth >= 50:
        return "Power User", "Heavy, effective usage across features"
    if agent_interactions > chat_interactions and loc_added > loc_suggested * 2 and loc_added > 0:
        return "Agent-Heavy", "Primary usage through agent/edit mode — advanced adoption pattern"
    if chat_interactions > 0 and code_generations <= 5:
        return "Chat-Focused", "Uses Copilot for Q&A and understanding rather than code generation"
    if acceptance_rate >= 25 and active_days >= 7:
        return "Healthy", "Good adoption with consistent usage"
    return "Moderate", "Active but room to increase engagement"


def build_user_rows(org: str, seats: list[dict], ndjson_records: list[dict]) -> list[dict[str, Any]]:
    seat_map: dict[str, dict] = {}
    for seat in seats:
        assignee = seat.get("assignee", {}) or {}
        login = assignee.get("login", "")
        if login:
            seat_map[login] = seat

    ndjson_agg = aggregate_user_ndjson(ndjson_records)
    all_logins = sorted(set(seat_map) | set(ndjson_agg))
    rows: list[dict[str, Any]] = []

    for login in all_logins:
        aggregated = ndjson_agg.get(login, {})
        active_days = aggregated.get("active_days", 0)
        total_interactions = aggregated.get("total_interactions", 0)
        code_generations = aggregated.get("code_generations", 0)
        code_acceptances = aggregated.get("code_acceptances", 0)
        loc_suggested = aggregated.get("loc_suggested", 0)
        loc_added = aggregated.get("loc_added", 0)
        loc_deleted = aggregated.get("loc_deleted", 0)
        chat_interactions = aggregated.get("chat_interactions", 0)
        agent_interactions = aggregated.get("agent_interactions", 0)
        engagement_depth = chat_interactions + agent_interactions

        row: dict[str, Any] = {
            "organization": org,
            "user_login": login,
            "active_days": active_days,
            "adoption_rate_pct": round(active_days / REPORT_DAYS * 100, 1),
            "total_interactions": total_interactions,
            "code_generations": code_generations,
            "code_acceptances": code_acceptances,
            "acceptance_rate_pct": _safe_pct(code_acceptances, code_generations),
            "loc_suggested": loc_suggested,
            "loc_added": loc_added,
            "loc_deleted": loc_deleted,
            "net_loc_change": loc_added - loc_deleted,
            "copilot_contribution_pct": _safe_pct(loc_suggested, loc_added, cap=100.0),
            "chat_interactions": chat_interactions,
            "agent_interactions": agent_interactions,
            "features_used": build_features_list(aggregated),
            "engagement_depth": engagement_depth,
            "estimated_time_saved_hrs": round(code_acceptances * MINUTES_SAVED_PER_ACCEPTANCE / 60, 1),
            "used_chat": bool(aggregated.get("used_chat", False)),
            "used_agent": bool(aggregated.get("used_agent", False)),
            "used_cli": bool(aggregated.get("used_cli", False)),
            "used_code_review": bool(aggregated.get("used_code_review", False)),
            "plan_type": (seat_map.get(login, {}).get("plan_type") or "") if login in seat_map else "",
        }
        row["health_profile"], row["health_notes"] = classify_health(row)
        rows.append(row)

    rows.sort(key=lambda item: (item["organization"], item["user_login"].lower()))
    return rows


def build_team_summary(user_rows: list[dict[str, Any]], report_start: str, report_end: str) -> dict[str, Any]:
    total_users = len(user_rows)
    active_users = [row for row in user_rows if row["active_days"] > 0]
    active_user_count = len(active_users)

    total_code_generations = sum(row["code_generations"] for row in user_rows)
    total_code_acceptances = sum(row["code_acceptances"] for row in user_rows)
    total_loc_suggested = sum(row["loc_suggested"] for row in user_rows)
    total_loc_added = sum(row["loc_added"] for row in user_rows)
    total_loc_deleted = sum(row["loc_deleted"] for row in user_rows)
    total_interactions = sum(row["total_interactions"] for row in user_rows)
    total_estimated_time_saved = sum(row["estimated_time_saved_hrs"] for row in user_rows)
    avg_time_saved_per_active_user = round(
        total_estimated_time_saved / active_user_count, 1
    ) if active_user_count else 0.0

    health_order = [
        "Power User",
        "Healthy",
        "Agent-Heavy",
        "Chat-Focused",
        "Moderate",
        "Low Usage",
        "Needs Enablement",
    ]
    distribution = Counter(row["health_profile"] for row in user_rows)

    top_users = sorted(
        user_rows,
        key=lambda row: (row["engagement_depth"], row["active_days"], row["total_interactions"], row["user_login"].lower()),
        reverse=True,
    )[:5]
    enablement_users = [row["user_login"] for row in user_rows if row["health_profile"] == "Needs Enablement"]

    def feature_pct(flag: str) -> float:
        if not active_user_count:
            return 0.0
        count = sum(1 for row in active_users if row.get(flag))
        return round(count / active_user_count * 100, 1)

    return {
        "total_users": total_users,
        "active_users": active_user_count,
        "team_adoption_rate_pct": _safe_pct(active_user_count, total_users),
        "report_period": f"{report_start} → {report_end}" if report_start and report_end else "N/A",
        "total_code_generations": total_code_generations,
        "total_code_acceptances": total_code_acceptances,
        "team_acceptance_rate_pct": _safe_pct(total_code_acceptances, total_code_generations),
        "total_loc_suggested": total_loc_suggested,
        "total_loc_added": total_loc_added,
        "total_loc_deleted": total_loc_deleted,
        "team_copilot_contribution_pct": _safe_pct(total_loc_suggested, total_loc_added, cap=100.0),
        "total_estimated_time_saved_hrs": round(total_estimated_time_saved, 1),
        "avg_time_saved_per_active_user_hrs": avg_time_saved_per_active_user,
        "total_interactions": total_interactions,
        "avg_engagement_depth_per_user": round(
            sum(row["engagement_depth"] for row in user_rows) / total_users, 1
        ) if total_users else 0.0,
        "avg_active_days_per_user": round(
            sum(row["active_days"] for row in user_rows) / total_users, 1
        ) if total_users else 0.0,
        "feature_adoption": {
            "Chat": feature_pct("used_chat"),
            "Agent": feature_pct("used_agent"),
            "CLI": feature_pct("used_cli"),
            "Code Review": feature_pct("used_code_review"),
        },
        "health_distribution": {label: distribution.get(label, 0) for label in health_order},
        "top_users": top_users,
        "enablement_users": enablement_users,
    }


def _style_header_row(ws) -> None:
    for cell in ws[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
    ws.auto_filter.ref = ws.dimensions
    ws.freeze_panes = "A2"


def _autosize_columns(ws) -> None:
    for column_cells in ws.columns:
        max_length = 0
        column_letter = get_column_letter(column_cells[0].column)
        for cell in column_cells:
            value = "" if cell.value is None else str(cell.value)
            max_length = max(max_length, len(value))
        ws.column_dimensions[column_letter].width = min(max(max_length + 2, 12), 30)


def write_excel(output_path: Path, user_rows: list[dict[str, Any]], summary: dict[str, Any]) -> Path:
    workbook = Workbook()
    user_ws = workbook.active
    user_ws.title = "User Productivity"

    user_ws.append(USER_PRODUCTIVITY_COLUMNS)
    for row in user_rows:
        user_ws.append([row.get(column, "") for column in USER_PRODUCTIVITY_COLUMNS])

    _style_header_row(user_ws)

    percentage_columns = {
        "adoption_rate_pct",
        "acceptance_rate_pct",
        "copilot_contribution_pct",
    }
    decimal_columns = {
        "estimated_time_saved_hrs",
    }
    number_columns = {
        "active_days",
        "total_interactions",
        "code_generations",
        "code_acceptances",
        "loc_suggested",
        "loc_added",
        "loc_deleted",
        "net_loc_change",
        "chat_interactions",
        "agent_interactions",
        "engagement_depth",
    }
    header_index = {name: idx + 1 for idx, name in enumerate(USER_PRODUCTIVITY_COLUMNS)}
    health_col = header_index["health_profile"]

    for row_idx in range(2, user_ws.max_row + 1):
        for column_name in percentage_columns:
            user_ws.cell(row=row_idx, column=header_index[column_name]).number_format = "0.0"
        for column_name in decimal_columns:
            user_ws.cell(row=row_idx, column=header_index[column_name]).number_format = "0.0"
        for column_name in number_columns:
            user_ws.cell(row=row_idx, column=header_index[column_name]).number_format = "#,##0"

        health_cell = user_ws.cell(row=row_idx, column=health_col)
        style = HEALTH_STYLES.get(str(health_cell.value), HEALTH_STYLES["Moderate"])
        health_cell.fill = style["fill"]
        health_cell.font = style["font"]

    _autosize_columns(user_ws)

    summary_ws = workbook.create_sheet("Team Summary")
    summary_rows: list[tuple[str, Any, str]] = [
        ("Overview", "", "section"),
        ("Total Users", summary["total_users"], "label"),
        ("Active Users", summary["active_users"], "label"),
        ("Team Adoption Rate", summary["team_adoption_rate_pct"], "pct"),
        ("Report Period", summary["report_period"], "label"),
        ("", "", "blank"),
        ("Code Acceleration", "", "section"),
        ("Total Code Generations", summary["total_code_generations"], "number"),
        ("Total Code Acceptances", summary["total_code_acceptances"], "number"),
        ("Team Acceptance Rate", summary["team_acceptance_rate_pct"], "pct"),
        ("Total LOC Suggested", summary["total_loc_suggested"], "number"),
        ("Total LOC Added", summary["total_loc_added"], "number"),
        ("Total LOC Deleted", summary["total_loc_deleted"], "number"),
        ("Team Copilot Contribution %", summary["team_copilot_contribution_pct"], "pct"),
        ("", "", "blank"),
        ("Productivity Impact", "", "section"),
        ("Total Estimated Time Saved (hrs)", summary["total_estimated_time_saved_hrs"], "pct"),
        ("Avg Time Saved per Active User (hrs)", summary["avg_time_saved_per_active_user_hrs"], "pct"),
        ("Estimation Basis", f"{MINUTES_SAVED_PER_ACCEPTANCE} min per accepted suggestion (conservative)", "label"),
        ("", "", "blank"),
        ("Engagement", "", "section"),
        ("Total Interactions", summary["total_interactions"], "number"),
        ("Avg Engagement Depth per User", summary["avg_engagement_depth_per_user"], "pct"),
        ("Avg Active Days per User", summary["avg_active_days_per_user"], "pct"),
        ("", "", "blank"),
        ("Feature Adoption (% of active users)", "", "section"),
        ("Chat", summary["feature_adoption"]["Chat"], "pct"),
        ("Agent", summary["feature_adoption"]["Agent"], "pct"),
        ("CLI", summary["feature_adoption"]["CLI"], "pct"),
        ("Code Review", summary["feature_adoption"]["Code Review"], "pct"),
        ("", "", "blank"),
        ("Health Distribution", "", "section"),
    ]

    for label, count in summary["health_distribution"].items():
        summary_rows.append((label, count, "number"))

    summary_rows.extend([
        ("", "", "blank"),
        ("Top 5 Users by Engagement Depth", "", "section"),
    ])
    if summary["top_users"]:
        for row in summary["top_users"]:
            summary_rows.append((row["user_login"], f"depth: {row['engagement_depth']}", "label"))
    else:
        summary_rows.append(("None", "", "label"))

    summary_rows.extend([
        ("", "", "blank"),
        ("Users Needing Enablement", "", "section"),
        (
            "Users",
            ", ".join(summary["enablement_users"]) if summary["enablement_users"] else "None",
            "label",
        ),
    ])

    current_row = 1
    for label, value, row_type in summary_rows:
        summary_ws.cell(row=current_row, column=1, value=label)
        summary_ws.cell(row=current_row, column=2, value=value)
        if row_type == "section":
            summary_ws.cell(row=current_row, column=1).font = SECTION_FONT
        elif row_type in {"label", "number", "pct"}:
            summary_ws.cell(row=current_row, column=1).font = LABEL_FONT
        if row_type == "number":
            summary_ws.cell(row=current_row, column=2).number_format = "#,##0"
        elif row_type == "pct":
            summary_ws.cell(row=current_row, column=2).number_format = "0.0"
        current_row += 1

    _autosize_columns(summary_ws)

    try:
        workbook.save(output_path)
        return output_path
    except PermissionError:
        timestamp = datetime.now().strftime("%H%M%S")
        retry_path = output_path.with_name(f"{output_path.stem}_{timestamp}{output_path.suffix}")
        workbook.save(retry_path)
        return retry_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a formatted Excel productivity report from GitHub Copilot metrics.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python copilot_productivity_report.py --enterprise my-ent
  python copilot_productivity_report.py --orgs org1,org2 --token ghp_xxx
  python copilot_productivity_report.py --enterprise my-ent --output-dir ./reports
        """,
    )
    parser.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    parser.add_argument("--enterprise", default=os.environ.get("ENTERPRISE_SLUG", ""))
    parser.add_argument("--orgs", default=os.environ.get("ORGS", ""))
    parser.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR", "."))
    return parser.parse_args()


def main() -> None:
    if load_dotenv:
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)

    args = parse_args()
    token = args.token or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        sys.exit("ERROR: No token. Use --token or set GITHUB_TOKEN.")

    print()
    validate_token(token)
    print()

    orgs_str = args.orgs or os.environ.get("ORGS", "")
    enterprise = args.enterprise or os.environ.get("ENTERPRISE_SLUG", "")

    if orgs_str:
        orgs = [org.strip() for org in orgs_str.split(",") if org.strip()]
    elif enterprise:
        print(f"🏢 Discovering orgs under: {enterprise}")
        orgs = discover_orgs(token, enterprise)
        if not orgs:
            sys.exit("ERROR: No orgs found.")
        print(f"   Found {len(orgs)} org(s)\n")
    else:
        sys.exit("ERROR: Use --enterprise or --orgs.")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    output_path = output_dir / f"copilot_productivity_{today}.xlsx"

    print(f"📊 Copilot Productivity Report — {REPORT_DAYS}-day window\n")

    all_user_rows: list[dict[str, Any]] = []
    report_starts: list[str] = []
    report_ends: list[str] = []

    for org in orgs:
        print(f"🔍 {org}")
        seats = fetch_seats(token, org)
        print(f"   Seats: {len(seats)}")

        ndjson_records, report_start, report_end = fetch_ndjson(token, org)
        print(f"   NDJSON records: {len(ndjson_records)}")

        if report_start:
            report_starts.append(report_start)
        if report_end:
            report_ends.append(report_end)

        if not seats and not ndjson_records:
            print("   ⚠ No data — skipping.")
            continue

        user_rows = build_user_rows(org, seats, ndjson_records)
        print(f"   📊 Users aggregated: {len(user_rows)}")
        all_user_rows.extend(user_rows)

    if not all_user_rows:
        sys.exit("ERROR: No reportable user data found.")

    all_user_rows.sort(key=lambda row: (row["organization"], row["user_login"].lower()))
    summary = build_team_summary(
        all_user_rows,
        min(report_starts) if report_starts else "",
        max(report_ends) if report_ends else "",
    )

    print("\n📊 Writing Excel report …")
    final_path = write_excel(output_path, all_user_rows, summary)
    print(f"✅ Done! Report saved to {final_path.resolve()}\n")


if __name__ == "__main__":
    main()
