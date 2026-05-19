#!/usr/bin/env python3
"""
GitHub Copilot Metrics Report Generator

Pulls Copilot usage data via the GitHub REST API and generates
management-ready CSV reports at user and organization levels.

Usage:
    python copilot_metrics_report.py --enterprise my-ent   # auto-discovers all orgs
    python copilot_metrics_report.py --orgs my-org-1,my-org-2
    python copilot_metrics_report.py --token ghp_xxx --orgs my-org --days 14
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import httpx
except ImportError:
    sys.exit("ERROR: httpx is required. Install with: pip install httpx")

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None  # optional — works without .env file

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GITHUB_API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
DEFAULT_DAYS = 28
MAX_PER_PAGE = 100


# ---------------------------------------------------------------------------
# API Client
# ---------------------------------------------------------------------------


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }


def _handle_rate_limit(response: httpx.Response) -> None:
    """Sleep until rate limit resets if we hit 403/429."""
    remaining = response.headers.get("x-ratelimit-remaining")
    if remaining is not None and int(remaining) == 0:
        reset_ts = int(response.headers.get("x-ratelimit-reset", 0))
        wait = max(reset_ts - int(time.time()), 1)
        print(f"  ⏳ Rate-limited. Waiting {wait}s …")
        time.sleep(wait)


def validate_token(token: str) -> dict:
    """Validate the PAT and print diagnostic info (username, scopes)."""
    with httpx.Client(timeout=30) as client:
        resp = client.get(
            f"{GITHUB_API_BASE}/user",
            headers=_headers(token),
        )
        if resp.status_code != 200:
            sys.exit(f"ERROR: Token validation failed (HTTP {resp.status_code}). "
                     "Check your GITHUB_TOKEN is valid.")

        scopes = resp.headers.get("x-oauth-scopes", "")
        user = resp.json()
        login = user.get("login", "unknown")

        print(f"🔑 Authenticated as: {login}")
        print(f"   PAT scopes: {scopes if scopes else '(none — fine-grained token?)'}")

        required = {"admin:enterprise", "manage_billing:copilot", "read:org"}
        if scopes:
            granted = {s.strip() for s in scopes.split(",")}
            missing = required - granted
            # Parent scopes cover child scopes
            if "admin:org" in granted:
                missing.discard("read:org")
            if "copilot" in granted:
                missing.discard("manage_billing:copilot")
            if missing:
                print(f"   ⚠ Missing recommended scopes: {', '.join(missing)}")
                print(f"     Update your PAT at: https://github.com/settings/tokens")

        return user


def _fetch_enterprise_slug(token: str) -> str | None:
    """Try to discover the enterprise slug from the authenticated user's enterprises."""
    with httpx.Client(timeout=30) as client:
        # Try the user's enterprise memberships
        resp = client.get(
            f"{GITHUB_API_BASE}/user/enterprise",
            headers=_headers(token),
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict) and data.get("slug"):
                return data["slug"]
            if isinstance(data, list) and data:
                return data[0].get("slug")
    return None


def fetch_enterprise_orgs(token: str, enterprise: str) -> list[str]:
    """Fetch all organization slugs under a GitHub enterprise (paginated).

    Tries the enterprise orgs endpoint first. If that fails, falls back to
    listing the authenticated user's own organizations.
    """
    orgs: list[str] = []
    page = 1
    with httpx.Client(timeout=30) as client:
        while True:
            url = f"{GITHUB_API_BASE}/enterprises/{enterprise}/organizations"
            resp = client.get(
                url,
                headers=_headers(token),
                params={"page": page, "per_page": MAX_PER_PAGE},
            )

            if resp.status_code == 401:
                sys.exit("ERROR: Authentication failed. Check your GITHUB_TOKEN.")
            if resp.status_code in (403, 404):
                _try_print_response(resp)
                print(f"  ⚠ Enterprise orgs endpoint returned {resp.status_code}.")
                print(f"    Falling back to listing your own organizations …")
                return _fetch_user_orgs(token, client)
            if resp.status_code != 200:
                _try_print_response(resp)
                print(f"  ⚠ Unexpected {resp.status_code} from enterprise orgs API.")
                print(f"    Falling back to listing your own organizations …")
                return _fetch_user_orgs(token, client)

            data = resp.json()
            if not isinstance(data, list) or not data:
                break
            orgs.extend(o.get("login", "") for o in data if o.get("login"))
            if len(data) < MAX_PER_PAGE:
                break
            page += 1

    return orgs


def _try_print_response(resp: httpx.Response) -> None:
    """Print the API response body for debugging."""
    try:
        body = resp.json()
        msg = body.get("message", "")
        doc = body.get("documentation_url", "")
        if msg:
            print(f"    API says: {msg}")
        if doc:
            print(f"    Docs: {doc}")
    except Exception:
        pass


def _fetch_user_orgs(token: str, client: httpx.Client | None = None) -> list[str]:
    """Fetch all organizations the authenticated user belongs to."""
    orgs: list[str] = []
    page = 1
    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=30)
    try:
        while True:
            resp = client.get(
                f"{GITHUB_API_BASE}/user/orgs",
                headers=_headers(token),
                params={"page": page, "per_page": MAX_PER_PAGE},
            )
            if resp.status_code != 200:
                _try_print_response(resp)
                print(f"  ⚠ Could not list user orgs (HTTP {resp.status_code}).")
                print(f"    Ensure your PAT has the read:org scope.")
                break
            data = resp.json()
            if not isinstance(data, list) or not data:
                break
            orgs.extend(o.get("login", "") for o in data if o.get("login"))
            if len(data) < MAX_PER_PAGE:
                break
            page += 1
    finally:
        if own_client:
            client.close()
    return orgs


def fetch_org_seats(token: str, org: str) -> list[dict]:
    """Fetch all Copilot seat assignments for an org (paginated).

    Tries /copilot/billing/seats first, then falls back to /copilot/billing
    for summary info if the seats endpoint is forbidden.
    """
    seats: list[dict] = []
    page = 1
    with httpx.Client(timeout=30) as client:
        while True:
            url = f"{GITHUB_API_BASE}/orgs/{org}/copilot/billing/seats"
            resp = client.get(
                url,
                headers=_headers(token),
                params={"page": page, "per_page": MAX_PER_PAGE},
            )

            if resp.status_code == 401:
                sys.exit("ERROR: Authentication failed. Check your GITHUB_TOKEN.")
            if resp.status_code == 403:
                _handle_rate_limit(resp)
                if int(resp.headers.get("x-ratelimit-remaining", "1")) == 0:
                    continue
                _try_print_response(resp)
                print(f"  ⚠ 403 on /copilot/billing/seats for {org}.")
                print(f"    Trying /copilot/billing for summary instead …")
                return _fetch_billing_summary_as_seats(client, token, org)
            if resp.status_code == 404:
                _try_print_response(resp)
                print(f"  ⚠ Copilot not enabled for '{org}' — skipping.")
                break
            if resp.status_code != 200:
                _try_print_response(resp)
                print(f"  ⚠ Unexpected {resp.status_code} from seats API for {org}.")
                break

            data = resp.json()
            page_seats = data.get("seats", [])
            if not page_seats:
                break
            seats.extend(page_seats)

            total = data.get("total_seats", len(seats))
            if len(seats) >= total:
                break
            page += 1

    return seats


def _fetch_billing_summary_as_seats(client: httpx.Client, token: str, org: str) -> list[dict]:
    """Fallback: fetch /copilot/billing for org-level seat summary."""
    resp = client.get(
        f"{GITHUB_API_BASE}/orgs/{org}/copilot/billing",
        headers=_headers(token),
    )
    if resp.status_code == 200:
        data = resp.json()
        # Return a synthetic seat list from the billing summary
        seat_breakdown = data.get("seat_breakdown", {})
        total = seat_breakdown.get("total", 0) or data.get("total_seats", 0)
        active = seat_breakdown.get("active_this_cycle", 0)
        inactive = seat_breakdown.get("inactive_this_cycle", 0)
        print(f"  📋 Billing summary: {total} total, {active} active, {inactive} inactive")
        # Store the summary as metadata — no per-user detail available
        if total > 0:
            return [{"_billing_summary": True,
                     "total_seats": total,
                     "seat_breakdown": seat_breakdown,
                     "plan_type": data.get("plan_type", "")}]
    else:
        _try_print_response(resp)
        print(f"  ⚠ /copilot/billing also failed (HTTP {resp.status_code}) for {org}.")
    return []


def fetch_org_metrics(token: str, org: str, days: int = DEFAULT_DAYS) -> list[dict]:
    """Fetch daily Copilot aggregate metrics for an org.

    Tries /copilot/metrics first, then falls back to /copilot/usage.
    """
    until_dt = datetime.now(timezone.utc)
    since_dt = until_dt - timedelta(days=days)
    since_str = since_dt.strftime("%Y-%m-%dT00:00:00Z")
    until_str = until_dt.strftime("%Y-%m-%dT23:59:59Z")

    all_days: list[dict] = []
    page = 1

    with httpx.Client(timeout=30) as client:
        while True:
            url = f"{GITHUB_API_BASE}/orgs/{org}/copilot/metrics"
            resp = client.get(
                url,
                headers=_headers(token),
                params={
                    "since": since_str,
                    "until": until_str,
                    "page": page,
                    "per_page": MAX_PER_PAGE,
                },
            )

            if resp.status_code == 401:
                sys.exit("ERROR: Authentication failed. Check your GITHUB_TOKEN.")
            if resp.status_code in (403, 404):
                _try_print_response(resp)
                print(f"  ⚠ /copilot/metrics returned {resp.status_code} for {org}.")
                print(f"    Trying /copilot/usage instead …")
                return _fetch_org_usage(client, token, org, since_str, until_str)
            if resp.status_code != 200:
                _try_print_response(resp)
                print(f"  ⚠ Unexpected {resp.status_code} from metrics API for {org}.")
                break

            day_records = resp.json()
            if not isinstance(day_records, list) or not day_records:
                break
            all_days.extend(day_records)
            if len(day_records) < MAX_PER_PAGE:
                break
            page += 1

    return all_days


def _fetch_org_usage(client: httpx.Client, token: str, org: str,
                     since: str, until: str) -> list[dict]:
    """Fallback: fetch /copilot/usage (older endpoint, different format)."""
    all_days: list[dict] = []
    page = 1
    while True:
        resp = client.get(
            f"{GITHUB_API_BASE}/orgs/{org}/copilot/usage",
            headers=_headers(token),
            params={"since": since, "until": until,
                    "page": page, "per_page": MAX_PER_PAGE},
        )
        if resp.status_code == 200:
            data = resp.json()
            if not isinstance(data, list) or not data:
                break
            all_days.extend(data)
            if len(data) < MAX_PER_PAGE:
                break
            page += 1
        else:
            _try_print_response(resp)
            print(f"  ⚠ /copilot/usage also failed (HTTP {resp.status_code}) for {org}.")
            break
    return all_days


# ---------------------------------------------------------------------------
# New NDJSON Usage Metrics API (per-user granular data)
# ---------------------------------------------------------------------------

NEW_API_VERSION = "2022-11-28"  # works for both old and new endpoints


def fetch_user_metrics_ndjson(token: str, org: str) -> list[dict]:
    """Fetch per-user usage metrics via the new NDJSON report endpoint.

    Tries: GET /orgs/{org}/copilot/metrics/reports/users-28-day/latest
    Returns a list of per-user-per-day dicts parsed from NDJSON.
    """
    records: list[dict] = []
    with httpx.Client(timeout=60) as client:
        # Step 1: Get download links
        url = f"{GITHUB_API_BASE}/orgs/{org}/copilot/metrics/reports/users-28-day/latest"
        resp = client.get(url, headers=_headers(token))

        if resp.status_code != 200:
            _try_print_response(resp)
            print(f"  ⚠ NDJSON users endpoint returned {resp.status_code} for {org}.")
            return []

        data = resp.json()
        download_links = data.get("download_links", [])
        if not download_links:
            print(f"  ⚠ No download links in NDJSON response for {org}.")
            return []

        report_start = data.get("report_start_day", "")
        report_end = data.get("report_end_day", "")
        print(f"  📥 NDJSON report: {report_start} → {report_end} ({len(download_links)} file(s))")

        # Step 2: Download and parse each NDJSON file
        # Download links are pre-signed URLs — try without auth first,
        # fall back to auth headers if that fails.
        for link in download_links:
            dl_resp = client.get(link, timeout=120)
            if dl_resp.status_code in (401, 403):
                # Retry with auth headers
                dl_resp = client.get(link, headers=_headers(token), timeout=120)
            if dl_resp.status_code != 200:
                print(f"  ⚠ Failed to download NDJSON file (HTTP {dl_resp.status_code}).")
                continue
            for line in dl_resp.text.strip().splitlines():
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

    return records


# ---------------------------------------------------------------------------
# Detailed User Activity CSV (from NDJSON)
# ---------------------------------------------------------------------------

USER_ACTIVITY_COLUMNS = [
    "day",
    "organization",
    "user_login",
    "user_id",
    # Feature usage booleans
    "used_chat",
    "used_agent",
    "used_cli",
    "used_code_review_active",
    "used_code_review_passive",
    # Interaction counts
    "interaction_count",
    "chat_ask_mode",
    "chat_edit_mode",
    "chat_plan_mode",
    "chat_agent_mode",
    "chat_custom_mode",
    # Code completions
    "code_gen_count",
    "code_accept_count",
    "loc_suggested_add",
    "loc_added",
    "loc_deleted",
    # CLI metrics
    "cli_sessions",
    "cli_requests",
    "cli_prompts",
    "cli_output_tokens",
    "cli_prompt_tokens",
    "cli_avg_tokens_per_req",
    # IDE info
    "ide_version",
    "plugin_version",
]


def build_user_activity_rows(org: str, ndjson_records: list[dict]) -> list[dict]:
    """Flatten NDJSON per-user-per-day records into CSV rows."""
    rows = []
    for rec in ndjson_records:
        cli = rec.get("totals_by_cli", {}) or {}
        cli_tokens = cli.get("token_usage", {}) or {}

        rows.append({
            "day": rec.get("day", ""),
            "organization": org,
            "user_login": rec.get("user_login", ""),
            "user_id": rec.get("user_id", ""),
            # Feature booleans
            "used_chat": rec.get("used_chat", ""),
            "used_agent": rec.get("used_agent", ""),
            "used_cli": rec.get("used_cli", ""),
            "used_code_review_active": rec.get("used_copilot_code_review_active", ""),
            "used_code_review_passive": rec.get("used_copilot_code_review_passive", ""),
            # Interactions
            "interaction_count": rec.get("user_initiated_interaction_count", ""),
            "chat_ask_mode": rec.get("chat_panel_ask_mode", ""),
            "chat_edit_mode": rec.get("chat_panel_edit_mode", ""),
            "chat_plan_mode": rec.get("chat_panel_plan_mode", ""),
            "chat_agent_mode": rec.get("chat_panel_agent_mode", ""),
            "chat_custom_mode": rec.get("chat_panel_custom_mode", ""),
            # Code completions
            "code_gen_count": rec.get("code_generation_activity_count", ""),
            "code_accept_count": rec.get("code_acceptance_activity_count", ""),
            "loc_suggested_add": rec.get("loc_suggested_to_add_sum", ""),
            "loc_added": rec.get("loc_added_sum", ""),
            "loc_deleted": rec.get("loc_deleted_sum", ""),
            # CLI
            "cli_sessions": cli.get("session_count", ""),
            "cli_requests": cli.get("request_count", ""),
            "cli_prompts": cli.get("prompt_count", ""),
            "cli_output_tokens": cli_tokens.get("output_tokens_sum", ""),
            "cli_prompt_tokens": cli_tokens.get("prompt_tokens_sum", ""),
            "cli_avg_tokens_per_req": cli_tokens.get("avg_tokens_per_request", ""),
            # IDE
            "ide_version": rec.get("last_known_ide_version", ""),
            "plugin_version": rec.get("last_known_plugin_version", ""),
        })
    return rows
# ---------------------------------------------------------------------------

USER_CSV_COLUMNS = [
    "organization",
    "login",
    "name",
    "email",
    "seat_created_at",
    "copilot_assigned_at",
    "last_activity_at",
    "last_activity_editor",
    "status",
    "days_since_last_activity",
    "plan_type",
]


def build_user_rows(org: str, seats: list[dict]) -> list[dict]:
    """Convert raw seat data into flat rows for the user CSV."""
    now = datetime.now(timezone.utc)
    rows = []
    for seat in seats:
        # Skip billing summary pseudo-entries (no per-user data)
        if seat.get("_billing_summary"):
            continue

        assignee = seat.get("assignee", {}) or {}
        login = assignee.get("login", "")

        last_activity = seat.get("last_activity_at")
        days_since = ""
        if last_activity:
            try:
                la_dt = datetime.fromisoformat(last_activity.replace("Z", "+00:00"))
                days_since = (now - la_dt).days
            except (ValueError, TypeError):
                pass

        rows.append({
            "organization": org,
            "login": login,
            "name": assignee.get("name", "") or "",
            "email": assignee.get("email", "") or "",
            "seat_created_at": seat.get("created_at", ""),
            "copilot_assigned_at": seat.get("assigning_team", {}).get("created_at", "") if seat.get("assigning_team") else "",
            "last_activity_at": last_activity or "",
            "last_activity_editor": seat.get("last_activity_editor", ""),
            "status": "active" if last_activity else "inactive",
            "days_since_last_activity": days_since,
            "plan_type": seat.get("plan_type", ""),
        })
    return rows


ORG_CSV_COLUMNS = [
    "organization",
    "total_seats",
    "active_seats",
    "inactive_seats",
    "adoption_rate_pct",
    "date_range_start",
    "date_range_end",
    "avg_daily_active_users",
    "total_suggestions_shown",
    "total_suggestions_accepted",
    "acceptance_rate_pct",
    "total_lines_suggested",
    "total_lines_accepted",
    "total_chat_turns",
    "total_agent_users",
    "total_cli_users",
    "total_code_review_users",
    "total_cli_tokens",
    "agent_adoption_pct",
    "top_languages",
    "top_editors",
]


def _safe_sum(items: list[dict], *keys: str) -> int:
    """Sum a nested key path across a list of daily records."""
    total = 0
    for item in items:
        val = item
        for k in keys:
            if isinstance(val, dict):
                val = val.get(k, 0)
            elif isinstance(val, list):
                val = sum(_deep_get(v, *keys[keys.index(k):]) for v in val)
                break
            else:
                val = 0
                break
        if isinstance(val, (int, float)):
            total += int(val)
    return total


def _deep_get(d: dict, *keys: str):
    """Safely navigate nested dicts."""
    for k in keys:
        if isinstance(d, dict):
            d = d.get(k, 0)
        else:
            return 0
    return d if isinstance(d, (int, float)) else 0


def _extract_language_counts(daily_records: list[dict]) -> Counter:
    """Aggregate language usage from the copilot_ide_code_completions breakdown."""
    counts: Counter = Counter()
    for day in daily_records:
        completions = day.get("copilot_ide_code_completions", {})
        if not completions:
            continue
        editors = completions.get("editors", []) if isinstance(completions, dict) else []
        for editor in editors:
            models = editor.get("models", []) or []
            for model in models:
                languages = model.get("languages", []) or []
                for lang in languages:
                    name = lang.get("name", "unknown")
                    total = lang.get("total_code_suggestions", 0) or 0
                    counts[name] += total
    return counts


def _extract_editor_counts(daily_records: list[dict]) -> Counter:
    """Aggregate editor usage from the copilot_ide_code_completions breakdown."""
    counts: Counter = Counter()
    for day in daily_records:
        completions = day.get("copilot_ide_code_completions", {})
        if not completions:
            continue
        editors = completions.get("editors", []) if isinstance(completions, dict) else []
        for editor in editors:
            name = editor.get("name", "unknown")
            models = editor.get("models", []) or []
            total = sum((m.get("total_code_suggestions", 0) or 0) for m in models)
            counts[name] += total
    return counts


def build_org_summary(org: str, seats: list[dict], daily_records: list[dict],
                     days: int, ndjson_records: list[dict] | None = None) -> dict:
    """Build a single org-summary row."""
    # Check if we have a billing summary (fallback) instead of per-user seats
    billing_summary = next((s for s in seats if s.get("_billing_summary")), None)
    if billing_summary:
        bd = billing_summary.get("seat_breakdown", {})
        total = billing_summary.get("total_seats", 0)
        active = bd.get("active_this_cycle", 0)
        inactive = bd.get("inactive_this_cycle", 0)
    else:
        total = len(seats)
        active = sum(1 for s in seats if s.get("last_activity_at"))
        inactive = total - active

    # Date range
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")

    # Aggregate daily metrics
    active_user_vals = [d.get("total_active_users", 0) or 0 for d in daily_records]
    avg_active = round(sum(active_user_vals) / len(active_user_vals), 1) if active_user_vals else 0

    # Suggestions & lines from copilot_ide_code_completions
    total_suggestions = 0
    total_accepted = 0
    total_lines_suggested = 0
    total_lines_accepted = 0
    for day in daily_records:
        comp = day.get("copilot_ide_code_completions", {}) or {}
        total_suggestions += comp.get("total_code_suggestions", 0) or 0
        total_accepted += comp.get("total_code_acceptances", 0) or 0
        total_lines_suggested += comp.get("total_code_lines_suggested", 0) or 0
        total_lines_accepted += comp.get("total_code_lines_accepted", 0) or 0

    acceptance_rate = round(total_accepted / total_suggestions * 100, 1) if total_suggestions else 0

    # Chat turns
    total_chat = 0
    for day in daily_records:
        ide_chat = day.get("copilot_ide_chat", {}) or {}
        total_chat += ide_chat.get("total_chats", 0) or 0
        dotcom_chat = day.get("copilot_dotcom_chat", {}) or {}
        total_chat += dotcom_chat.get("total_chats", 0) or 0

    # Top languages & editors
    lang_counts = _extract_language_counts(daily_records)
    top_langs = ", ".join(l for l, _ in lang_counts.most_common(5)) if lang_counts else ""
    editor_counts = _extract_editor_counts(daily_records)
    top_editors = ", ".join(e for e, _ in editor_counts.most_common(3)) if editor_counts else ""

    # New metrics from NDJSON per-user data
    agent_users = 0
    cli_users = 0
    code_review_users = 0
    total_cli_tokens = 0
    if ndjson_records:
        agent_logins = set()
        cli_logins = set()
        cr_logins = set()
        for rec in ndjson_records:
            login = rec.get("user_login", "")
            if rec.get("used_agent"):
                agent_logins.add(login)
            if rec.get("used_cli"):
                cli_logins.add(login)
            if rec.get("used_copilot_code_review_active") or rec.get("used_copilot_code_review_passive"):
                cr_logins.add(login)
            cli_data = rec.get("totals_by_cli", {}) or {}
            cli_tok = cli_data.get("token_usage", {}) or {}
            total_cli_tokens += (cli_tok.get("output_tokens_sum", 0) or 0)
            total_cli_tokens += (cli_tok.get("prompt_tokens_sum", 0) or 0)
        agent_users = len(agent_logins)
        cli_users = len(cli_logins)
        code_review_users = len(cr_logins)

    active_for_adoption = active if active > 0 else (total if total > 0 else 1)
    agent_adoption = round(agent_users / active_for_adoption * 100, 1) if agent_users else 0

    return {
        "organization": org,
        "total_seats": total,
        "active_seats": active,
        "inactive_seats": inactive,
        "adoption_rate_pct": round(active / total * 100, 1) if total else 0,
        "date_range_start": start,
        "date_range_end": end,
        "avg_daily_active_users": avg_active,
        "total_suggestions_shown": total_suggestions,
        "total_suggestions_accepted": total_accepted,
        "acceptance_rate_pct": acceptance_rate,
        "total_lines_suggested": total_lines_suggested,
        "total_lines_accepted": total_lines_accepted,
        "total_chat_turns": total_chat,
        "total_agent_users": agent_users,
        "total_cli_users": cli_users,
        "total_code_review_users": code_review_users,
        "total_cli_tokens": total_cli_tokens,
        "agent_adoption_pct": agent_adoption,
        "top_languages": top_langs,
        "top_editors": top_editors,
    }


# ---------------------------------------------------------------------------
# CSV Writers
# ---------------------------------------------------------------------------


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Write rows to a CSV file. Adds a suffix if the file is locked."""
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
    except PermissionError:
        # File likely open in Excel — write with a timestamp suffix
        ts = datetime.now().strftime("%H%M%S")
        alt_path = path.with_stem(f"{path.stem}_{ts}")
        print(f"  ⚠ {path.name} is locked (open in another app?). Writing to {alt_path.name}")
        with open(alt_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        path = alt_path
    print(f"  ✅ Wrote {len(rows)} rows → {path}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Generate GitHub Copilot usage CSV reports for management.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python copilot_metrics_report.py --enterprise my-enterprise
  python copilot_metrics_report.py --orgs my-org
  python copilot_metrics_report.py --token ghp_xxx --orgs org1,org2 --days 14
  python copilot_metrics_report.py --enterprise my-ent --output-dir ./reports --raw-json
        """,
    )
    p.add_argument(
        "--token",
        default=os.environ.get("GITHUB_TOKEN", ""),
        help="GitHub PAT (or set GITHUB_TOKEN env var).",
    )
    p.add_argument(
        "--enterprise",
        default=os.environ.get("ENTERPRISE_SLUG", ""),
        help="Enterprise slug — auto-discovers all orgs under it.",
    )
    p.add_argument(
        "--orgs",
        default=os.environ.get("ORGS", ""),
        help="Comma-separated list of GitHub org slugs (overrides --enterprise).",
    )
    p.add_argument(
        "--days",
        type=int,
        default=int(os.environ.get("DAYS", str(DEFAULT_DAYS))),
        help=f"Metrics window in days (default: {DEFAULT_DAYS}).",
    )
    p.add_argument(
        "--output-dir",
        default=os.environ.get("OUTPUT_DIR", "."),
        help="Directory for output CSV files (default: current dir).",
    )
    p.add_argument(
        "--raw-json",
        action="store_true",
        help="Also save raw API JSON responses alongside CSVs.",
    )
    return p.parse_args()


def main() -> None:
    # Load .env if python-dotenv is available
    if load_dotenv:
        env_path = Path(__file__).parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)

    args = parse_args()

    token = args.token or os.environ.get("GITHUB_TOKEN", "")
    if not token:
        sys.exit("ERROR: No GitHub token. Use --token or set GITHUB_TOKEN env var.")

    # Validate token and show diagnostics
    print()
    validate_token(token)
    print()

    orgs_str = args.orgs or os.environ.get("ORGS", "")
    enterprise = args.enterprise or os.environ.get("ENTERPRISE_SLUG", "")

    if orgs_str:
        orgs = [o.strip() for o in orgs_str.split(",") if o.strip()]
    elif enterprise:
        print(f"🏢 Discovering orgs under enterprise: {enterprise}")
        orgs = fetch_enterprise_orgs(token, enterprise)
        if not orgs:
            sys.exit(f"ERROR: No orgs found under enterprise '{enterprise}'.\n"
                     f"  Tip 1: The enterprise slug is the URL path at github.com/enterprises/<slug>\n"
                     f"  Tip 2: Ensure your PAT has admin:enterprise + read:org scopes\n"
                     f"  Tip 3: You can skip enterprise discovery by using --orgs org1,org2 directly")
        print(f"   Found {len(orgs)} org(s): {', '.join(orgs)}\n")
    else:
        sys.exit("ERROR: Specify --enterprise or --orgs (or set ENTERPRISE_SLUG / ORGS env var).")
    days = args.days
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    today_str = datetime.now().strftime("%Y%m%d")
    user_csv_path = out_dir / f"copilot_users_{today_str}.csv"
    activity_csv_path = out_dir / f"copilot_user_activity_{today_str}.csv"
    org_csv_path = out_dir / f"copilot_org_summary_{today_str}.csv"

    all_user_rows: list[dict] = []
    all_activity_rows: list[dict] = []
    all_org_rows: list[dict] = []
    raw_data: dict[str, dict] = {}

    print(f"\n📊 Copilot Metrics Report — {days}-day window")
    print(f"   Orgs: {', '.join(orgs)}\n")

    for org in orgs:
        print(f"🔍 Processing org: {org}")

        # Fetch seat data (per-user)
        print(f"  Fetching seat assignments …")
        seats = fetch_org_seats(token, org)
        print(f"  Found {len(seats)} seats.")

        # Fetch aggregate metrics (legacy)
        print(f"  Fetching {days}-day aggregate metrics …")
        daily = fetch_org_metrics(token, org, days)
        print(f"  Received {len(daily)} daily records.")

        # Fetch per-user NDJSON metrics (new API)
        print(f"  Fetching per-user NDJSON metrics …")
        ndjson = fetch_user_metrics_ndjson(token, org)
        print(f"  Received {len(ndjson)} per-user-day records.")

        # Build rows
        user_rows = build_user_rows(org, seats)
        all_user_rows.extend(user_rows)

        activity_rows = build_user_activity_rows(org, ndjson)
        all_activity_rows.extend(activity_rows)

        org_row = build_org_summary(org, seats, daily, days, ndjson)
        all_org_rows.append(org_row)

        if args.raw_json:
            raw_data[org] = {"seats": seats, "daily_metrics": daily, "ndjson_records": ndjson}

    # Write CSVs
    print(f"\n📝 Writing reports …")
    write_csv(user_csv_path, USER_CSV_COLUMNS, all_user_rows)
    if all_activity_rows:
        write_csv(activity_csv_path, USER_ACTIVITY_COLUMNS, all_activity_rows)
    else:
        print(f"  ℹ No NDJSON user activity data — skipping {activity_csv_path.name}")
    write_csv(org_csv_path, ORG_CSV_COLUMNS, all_org_rows)

    # Optionally save raw JSON
    if args.raw_json and raw_data:
        json_path = out_dir / f"copilot_raw_{today_str}.json"
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(raw_data, f, indent=2, default=str)
        print(f"  ✅ Raw JSON → {json_path}")

    print(f"\n✅ Done! Reports saved to {out_dir.resolve()}\n")


if __name__ == "__main__":
    main()
