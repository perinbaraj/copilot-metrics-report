#!/usr/bin/env python3
"""
GitHub Copilot Customer Report Generator

Produces a single, clean CSV report with organization-level user metrics.
Each row is one user, grouped by org, with org summary rows between groups.
Zero empty columns — uses only verified-populated API fields.

Usage:
    python copilot_customer_report.py --enterprise my-ent
    python copilot_customer_report.py --orgs org1,org2 --token ghp_xxx
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("ERROR: httpx is required. Install with: pip install httpx")

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GITHUB_API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
REPORT_DAYS = 28

REPORT_COLUMNS = [
    "organization",
    "user_login",
    "status",
    "plan_type",
    "seat_assigned_date",
    "last_activity_date",
    "days_inactive",
    "editor",
    "copilot_model",
    "total_days_active",
    "utilization_pct",
    "total_interactions",
    "total_code_generations",
    "total_code_acceptances",
    "acceptance_rate_pct",
    "total_loc_suggested",
    "total_loc_added",
    "total_loc_deleted",
    "features_used",
]


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------


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


def validate_token(token: str) -> None:
    with httpx.Client(timeout=30) as client:
        resp = client.get(f"{GITHUB_API_BASE}/user", headers=_headers(token))
        if resp.status_code != 200:
            sys.exit(f"ERROR: Token invalid (HTTP {resp.status_code}).")
        user = resp.json()
        scopes = resp.headers.get("x-oauth-scopes", "(unknown)")
        print(f"🔑 Authenticated as: {user.get('login', '?')}  |  Scopes: {scopes}")


def discover_orgs(token: str, enterprise: str) -> list[str]:
    """Discover orgs — try enterprise endpoint, fall back to user orgs."""
    orgs: list[str] = []
    with httpx.Client(timeout=30) as client:
        # Try enterprise
        resp = client.get(
            f"{GITHUB_API_BASE}/enterprises/{enterprise}/organizations",
            headers=_headers(token),
            params={"per_page": 100},
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                orgs = [o["login"] for o in data if o.get("login")]
                if orgs:
                    return orgs

        # Fallback: user's own orgs
        print(f"  ⚠ Enterprise endpoint unavailable. Using your org memberships.")
        page = 1
        while True:
            resp = client.get(
                f"{GITHUB_API_BASE}/user/orgs",
                headers=_headers(token),
                params={"page": page, "per_page": 100},
            )
            if resp.status_code != 200:
                _try_print_response(resp)
                break
            data = resp.json()
            if not data:
                break
            orgs.extend(o["login"] for o in data if o.get("login"))
            if len(data) < 100:
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
                params={"page": page, "per_page": 100},
            )
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


def fetch_ndjson(token: str, org: str) -> list[dict]:
    """Fetch per-user NDJSON usage metrics."""
    records: list[dict] = []
    with httpx.Client(timeout=60) as client:
        resp = client.get(
            f"{GITHUB_API_BASE}/orgs/{org}/copilot/metrics/reports/users-28-day/latest",
            headers=_headers(token),
        )
        if resp.status_code != 200:
            return []

        links = resp.json().get("download_links", [])
        for link in links:
            dl = client.get(link, timeout=120)
            if dl.status_code in (401, 403):
                dl = client.get(link, headers=_headers(token), timeout=120)
            if dl.status_code != 200:
                continue
            for line in dl.text.strip().splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    return records


# ---------------------------------------------------------------------------
# Data Processing
# ---------------------------------------------------------------------------


def parse_editor(raw: str) -> tuple[str, str]:
    """Parse editor name and copilot model version from last_activity_editor.

    Examples:
      'vscode/1.101.0/copilot-chat/0.28.5' → ('vscode', 'copilot-chat/0.28.5')
      'JetBrains-IU/261.23567.138/copilot-intellij/1.8.2-243' → ('JetBrains-IU', 'copilot-intellij/1.8.2-243')
      'unknown/GitHubCopilotChat/0.37.9' → ('unknown', 'GitHubCopilotChat/0.37.9')
    """
    if not raw:
        return "N/A", "N/A"

    parts = raw.split("/")
    editor_name = parts[0] if parts else raw

    # Find copilot plugin version — look for 'copilot' in the parts
    copilot_parts = []
    for i, p in enumerate(parts):
        if "copilot" in p.lower() or "GitHubCopilot" in p:
            copilot_parts = parts[i:]
            break

    copilot_model = "/".join(copilot_parts) if copilot_parts else "N/A"
    return editor_name, copilot_model


def aggregate_user_ndjson(records: list[dict]) -> dict[str, dict]:
    """Aggregate NDJSON daily records per user_login."""
    users: dict[str, dict] = defaultdict(lambda: {
        "days_active": 0,
        "interactions": 0,
        "code_gen": 0,
        "code_accept": 0,
        "loc_suggested": 0,
        "loc_added": 0,
        "loc_deleted": 0,
        "used_chat": False,
        "used_agent": False,
        "used_cli": False,
        "used_code_review": False,
    })

    for rec in records:
        login = rec.get("user_login", "")
        if not login:
            continue
        u = users[login]
        u["days_active"] += 1
        u["interactions"] += int(rec.get("user_initiated_interaction_count", 0) or 0)
        u["code_gen"] += int(rec.get("code_generation_activity_count", 0) or 0)
        u["code_accept"] += int(rec.get("code_acceptance_activity_count", 0) or 0)
        u["loc_suggested"] += int(rec.get("loc_suggested_to_add_sum", 0) or 0)
        u["loc_added"] += int(rec.get("loc_added_sum", 0) or 0)
        u["loc_deleted"] += int(rec.get("loc_deleted_sum", 0) or 0)
        if rec.get("used_chat"):
            u["used_chat"] = True
        if rec.get("used_agent"):
            u["used_agent"] = True
        if rec.get("used_cli"):
            u["used_cli"] = True
        if rec.get("used_copilot_code_review_active") or rec.get("used_copilot_code_review_passive"):
            u["used_code_review"] = True

    return dict(users)


def build_features_list(u: dict) -> str:
    """Build a comma-separated list of features used."""
    features = []
    if u.get("used_chat"):
        features.append("chat")
    if u.get("used_agent"):
        features.append("agent")
    if u.get("used_cli"):
        features.append("cli")
    if u.get("used_code_review"):
        features.append("code_review")
    return ", ".join(features) if features else "none"


def build_report_rows(org: str, seats: list[dict], ndjson: list[dict]) -> list[dict]:
    """Build user rows + org summary row for one organization."""
    now = datetime.now(timezone.utc)
    ndjson_agg = aggregate_user_ndjson(ndjson)

    user_rows: list[dict] = []
    inactive_logins: list[str] = []
    editor_counts: Counter = Counter()
    total_interactions = 0
    total_code_gen = 0
    total_code_accept = 0
    total_loc_added = 0
    total_loc_deleted = 0
    utilizations: list[float] = []

    for seat in seats:
        assignee = seat.get("assignee", {}) or {}
        login = assignee.get("login", "")
        if not login:
            continue

        last_activity = seat.get("last_activity_at")
        days_inactive = ""
        last_date = "N/A"
        if last_activity:
            try:
                la_dt = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
                days_inactive = (now - la_dt).days
                last_date = la_dt.strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        # Seat assigned date
        seat_created = seat.get("created_at", "")
        seat_date = "N/A"
        if seat_created:
            try:
                seat_date = datetime.fromisoformat(seat_created.replace("Z", "+00:00")).strftime("%Y-%m-%d")
            except (ValueError, TypeError):
                pass

        status = "active" if last_activity else "inactive"
        if status == "inactive":
            inactive_logins.append(login)

        raw_editor = seat.get("last_activity_editor", "")
        editor_name, copilot_model = parse_editor(raw_editor)
        if editor_name and editor_name != "N/A":
            editor_counts[editor_name] += 1

        # Merge with NDJSON aggregated data
        u = ndjson_agg.get(login, {})
        days_active = u.get("days_active", 0)
        utilization = round(days_active / REPORT_DAYS * 100, 1)
        utilizations.append(utilization)

        interactions = u.get("interactions", 0)
        code_gen = u.get("code_gen", 0)
        code_accept = u.get("code_accept", 0)
        accept_rate = round(code_accept / code_gen * 100, 1) if code_gen else 0

        total_interactions += interactions
        total_code_gen += code_gen
        total_code_accept += code_accept
        total_loc_added += u.get("loc_added", 0)
        total_loc_deleted += u.get("loc_deleted", 0)

        features = build_features_list(u)

        user_rows.append({
            "organization": org,
            "user_login": login,
            "status": status,
            "plan_type": seat.get("plan_type", "N/A"),
            "seat_assigned_date": seat_date,
            "last_activity_date": last_date,
            "days_inactive": days_inactive if days_inactive != "" else "never",
            "editor": editor_name,
            "copilot_model": copilot_model,
            "total_days_active": days_active,
            "utilization_pct": utilization,
            "total_interactions": interactions,
            "total_code_generations": code_gen,
            "total_code_acceptances": code_accept,
            "acceptance_rate_pct": accept_rate,
            "total_loc_suggested": u.get("loc_suggested", 0),
            "total_loc_added": u.get("loc_added", 0),
            "total_loc_deleted": u.get("loc_deleted", 0),
            "features_used": features,
        })

    # Sort: active users first, then inactive
    user_rows.sort(key=lambda r: (0 if r["status"] == "active" else 1, r["user_login"]))

    # Org summary row
    total_seats = len(seats)
    active_count = total_seats - len(inactive_logins)
    avg_util = round(sum(utilizations) / len(utilizations), 1) if utilizations else 0
    org_accept_rate = round(total_code_accept / total_code_gen * 100, 1) if total_code_gen else 0
    top_editors = ", ".join(e for e, _ in editor_counts.most_common(3))
    inactive_list = ", ".join(inactive_logins[:20])
    if len(inactive_logins) > 20:
        inactive_list += f" (+{len(inactive_logins) - 20} more)"

    summary_row = {
        "organization": f"── {org} SUMMARY ──",
        "user_login": f"{total_seats} seats",
        "status": f"{active_count} active / {len(inactive_logins)} inactive",
        "plan_type": "",
        "seat_assigned_date": "",
        "last_activity_date": "",
        "days_inactive": "",
        "editor": f"Top: {top_editors}" if top_editors else "N/A",
        "copilot_model": "",
        "total_days_active": "",
        "utilization_pct": f"avg {avg_util}%",
        "total_interactions": total_interactions,
        "total_code_generations": total_code_gen,
        "total_code_acceptances": total_code_accept,
        "acceptance_rate_pct": org_accept_rate,
        "total_loc_suggested": "",
        "total_loc_added": total_loc_added,
        "total_loc_deleted": total_loc_deleted,
        "features_used": f"Inactive: {inactive_list}" if inactive_list else "None inactive",
    }

    user_rows.append(summary_row)
    return user_rows


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate a customer-ready GitHub Copilot usage report (single CSV).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python copilot_customer_report.py --enterprise my-ent
  python copilot_customer_report.py --orgs org1,org2 --token ghp_xxx
  python copilot_customer_report.py --enterprise my-ent --output-dir ./reports
        """,
    )
    p.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    p.add_argument("--enterprise", default=os.environ.get("ENTERPRISE_SLUG", ""))
    p.add_argument("--orgs", default=os.environ.get("ORGS", ""))
    p.add_argument("--output-dir", default=os.environ.get("OUTPUT_DIR", "."))
    p.add_argument("--raw-json", action="store_true", help="Also save raw API data.")
    return p.parse_args()


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
        orgs = [o.strip() for o in orgs_str.split(",") if o.strip()]
    elif enterprise:
        print(f"🏢 Discovering orgs under: {enterprise}")
        orgs = discover_orgs(token, enterprise)
        if not orgs:
            sys.exit("ERROR: No orgs found.")
        print(f"   Found {len(orgs)} org(s)\n")
    else:
        sys.exit("ERROR: Use --enterprise or --orgs.")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    csv_path = out_dir / f"copilot_report_{today}.csv"

    all_rows: list[dict] = []
    raw_data: dict = {}

    print(f"📊 Copilot Report — {REPORT_DAYS}-day window\n")

    for org in orgs:
        print(f"🔍 {org}")

        seats = fetch_seats(token, org)
        print(f"   Seats: {len(seats)}")

        ndjson = fetch_ndjson(token, org)
        print(f"   NDJSON records: {len(ndjson)}")

        if not seats and not ndjson:
            print(f"   ⚠ No data — skipping.")
            continue

        rows = build_report_rows(org, seats, ndjson)
        all_rows.extend(rows)

        if args.raw_json:
            raw_data[org] = {"seats": seats, "ndjson": ndjson}

    # Write CSV
    print(f"\n📝 Writing report …")
    try:
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)
    except PermissionError:
        ts = datetime.now().strftime("%H%M%S")
        csv_path = csv_path.with_stem(f"{csv_path.stem}_{ts}")
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=REPORT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(all_rows)

    user_rows = [r for r in all_rows if not r["organization"].startswith("──")]
    summary_rows = len(all_rows) - len(user_rows)
    print(f"  ✅ {len(user_rows)} users + {summary_rows} org summaries → {csv_path}")

    if args.raw_json and raw_data:
        json_path = out_dir / f"copilot_raw_{today}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, indent=2, default=str)
        print(f"  ✅ Raw JSON → {json_path}")

    print(f"\n✅ Done! Report saved to {csv_path.resolve()}\n")


if __name__ == "__main__":
    main()
