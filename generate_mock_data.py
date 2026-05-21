#!/usr/bin/env python3
"""Generate realistic mock GitHub Copilot usage data for testing."""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from copilot_productivity_report import (
    aggregate_user_ndjson,
    build_team_summary,
    build_user_rows,
    write_excel,
)

SEED = 42
MOCK_ORG = "demo-org"
DATE_START = "2026-04-24"
DATE_END = "2026-05-21"
OUTPUT_DIR = Path(__file__).parent / "mock_data"
NDJSON_PATH = OUTPUT_DIR / "mock_copilot_users.ndjson"
SEATS_PATH = OUTPUT_DIR / "mock_seats.json"
REPORT_PATH = OUTPUT_DIR / "copilot_productivity_mock.xlsx"
REPORT_DATES = [
    (datetime.fromisoformat(DATE_START) + timedelta(days=offset)).date().isoformat()
    for offset in range((datetime.fromisoformat(DATE_END) - datetime.fromisoformat(DATE_START)).days + 1)
]


@dataclass(frozen=True)
class ProfileSpec:
    name: str
    count: int
    username_prefix: str
    active_days: tuple[int, int]
    interactions_per_day: tuple[int, int]
    code_gen_per_day: tuple[int, int]
    acceptance_pct: tuple[float, float]
    loc_suggested_per_day: tuple[int, int]
    loc_added_per_day: tuple[int, int]
    loc_deleted_per_day: tuple[int, int]
    chat_per_day: tuple[int, int]
    agent_per_day: tuple[int, int]
    features: tuple[str, ...]


PROFILE_SPECS = [
    ProfileSpec(
        name="Power User",
        count=10,
        username_prefix="power_user",
        active_days=(18, 25),
        interactions_per_day=(8, 15),
        code_gen_per_day=(10, 25),
        acceptance_pct=(0.30, 0.45),
        loc_suggested_per_day=(40, 120),
        loc_added_per_day=(25, 80),
        loc_deleted_per_day=(5, 20),
        chat_per_day=(3, 8),
        agent_per_day=(3, 10),
        features=("chat", "agent", "cli"),
    ),
    ProfileSpec(
        name="Healthy",
        count=25,
        username_prefix="healthy_user",
        active_days=(10, 18),
        interactions_per_day=(4, 10),
        code_gen_per_day=(5, 15),
        acceptance_pct=(0.25, 0.35),
        loc_suggested_per_day=(20, 60),
        loc_added_per_day=(15, 40),
        loc_deleted_per_day=(2, 10),
        chat_per_day=(2, 5),
        agent_per_day=(1, 3),
        features=("chat", "agent"),
    ),
    ProfileSpec(
        name="Agent-Heavy",
        count=10,
        username_prefix="agent_user",
        active_days=(12, 20),
        interactions_per_day=(5, 12),
        code_gen_per_day=(3, 8),
        acceptance_pct=(0.20, 0.30),
        loc_suggested_per_day=(10, 25),
        loc_added_per_day=(50, 200),
        loc_deleted_per_day=(10, 40),
        chat_per_day=(0, 0),
        agent_per_day=(5, 15),
        features=("agent",),
    ),
    ProfileSpec(
        name="Chat-Focused",
        count=10,
        username_prefix="chat_user",
        active_days=(7, 15),
        interactions_per_day=(5, 12),
        code_gen_per_day=(0, 3),
        acceptance_pct=(0.00, 0.20),
        loc_suggested_per_day=(0, 5),
        loc_added_per_day=(0, 3),
        loc_deleted_per_day=(0, 1),
        chat_per_day=(4, 10),
        agent_per_day=(0, 0),
        features=("chat",),
    ),
    ProfileSpec(
        name="Moderate",
        count=20,
        username_prefix="mod_user",
        active_days=(5, 10),
        interactions_per_day=(3, 6),
        code_gen_per_day=(3, 8),
        acceptance_pct=(0.15, 0.25),
        loc_suggested_per_day=(10, 30),
        loc_added_per_day=(8, 20),
        loc_deleted_per_day=(1, 5),
        chat_per_day=(1, 3),
        agent_per_day=(0, 0),
        features=("chat",),
    ),
    ProfileSpec(
        name="Low Usage",
        count=15,
        username_prefix="low_user",
        active_days=(1, 3),
        interactions_per_day=(3, 8),
        code_gen_per_day=(1, 5),
        acceptance_pct=(0.10, 0.25),
        loc_suggested_per_day=(5, 15),
        loc_added_per_day=(3, 10),
        loc_deleted_per_day=(0, 3),
        chat_per_day=(1, 3),
        agent_per_day=(0, 0),
        features=("chat",),
    ),
    ProfileSpec(
        name="Needs Enablement",
        count=10,
        username_prefix="inactive_user",
        active_days=(0, 0),
        interactions_per_day=(0, 0),
        code_gen_per_day=(0, 0),
        acceptance_pct=(0.0, 0.0),
        loc_suggested_per_day=(0, 0),
        loc_added_per_day=(0, 0),
        loc_deleted_per_day=(0, 0),
        chat_per_day=(0, 0),
        agent_per_day=(0, 0),
        features=(),
    ),
]

EXPECTED_HEALTH_COUNTS = {spec.name: spec.count for spec in PROFILE_SPECS}
EDITOR_VARIANTS = [
    ("vscode/1.101.0", "copilot-chat/0.28.5"),
    ("JetBrains-IU/2025.1.1", "copilot-jetbrains/1.5.3"),
    ("JetBrains-IC/2025.1.1", "copilot-jetbrains/1.5.3"),
    ("unknown", "GitHubCopilotChat"),
]


def rand_between(rng: random.Random, bounds: tuple[int, int]) -> int:
    return rng.randint(bounds[0], bounds[1])


def rand_rate(rng: random.Random, bounds: tuple[float, float]) -> float:
    return rng.uniform(bounds[0], bounds[1])


def split_chat_modes(rng: random.Random, total_chat: int) -> tuple[int, int, int]:
    if total_chat <= 0:
        return 0, 0, 0

    ask = max(1, round(total_chat * rng.uniform(0.40, 0.60)))
    remaining = max(total_chat - ask, 0)
    edit = round(remaining * rng.uniform(0.35, 0.65)) if remaining else 0
    plan = max(total_chat - ask - edit, 0)
    return ask, edit, plan


def choose_active_days(rng: random.Random, active_days: int) -> list[str]:
    if active_days <= 0:
        return []
    return sorted(rng.sample(REPORT_DATES, active_days))


def iso_timestamp(day: str, hour: int, minute: int) -> str:
    return f"{day}T{hour:02d}:{minute:02d}:00Z"


def build_seat(user: dict, profile: ProfileSpec, rng: random.Random) -> dict:
    assignee = {"login": user["login"], "id": user["user_id"]}
    created_day = (datetime(2026, 1, 15) + timedelta(days=rng.randint(0, 35))).date().isoformat()
    created_at = iso_timestamp(created_day, 10 + rng.randint(0, 6), rng.choice([0, 15, 30, 45]))
    ide_version, plugin_version = user["editor"]

    if profile.name == "Needs Enablement":
        last_activity_at = None
        last_activity_editor = ""
    elif profile.name == "Low Usage":
        older_day = (datetime.fromisoformat(DATE_START) - timedelta(days=rng.randint(30, 55))).date().isoformat()
        last_activity_at = iso_timestamp(older_day, 9 + rng.randint(0, 8), rng.choice([0, 15, 30, 45]))
        last_activity_editor = f"{ide_version}/{plugin_version}"
    else:
        recent_day = rng.choice(user["active_days"])
        last_activity_at = iso_timestamp(recent_day, 9 + rng.randint(0, 9), rng.choice([0, 15, 30, 45]))
        last_activity_editor = f"{ide_version}/{plugin_version}"

    return {
        "assignee": assignee,
        "created_at": created_at,
        "last_activity_at": last_activity_at,
        "last_activity_editor": last_activity_editor,
        "plan_type": "business",
    }


def healthy_acceptance_bounds(spec: ProfileSpec, active_days: int) -> tuple[float, float]:
    if spec.name != "Healthy":
        return spec.acceptance_pct
    if active_days >= 14:
        return (0.25, 0.29)
    return spec.acceptance_pct


def build_daily_record(
    profile: ProfileSpec,
    login: str,
    user_id: int,
    day: str,
    ide_version: str,
    plugin_version: str,
    rng: random.Random,
    remaining_chat_code_budget: list[int] | None = None,
) -> dict:
    interactions = rand_between(rng, profile.interactions_per_day)
    chat_total = rand_between(rng, profile.chat_per_day)
    agent_total = rand_between(rng, profile.agent_per_day)

    if profile.name == "Agent-Heavy":
        chat_total = 0
        agent_total = rand_between(rng, (8, 15))
    elif profile.name == "Chat-Focused":
        if remaining_chat_code_budget is None:
            raise ValueError("Chat-Focused generation requires a code budget")
        daily_max = min(2, remaining_chat_code_budget[0])
        code_generations = rng.randint(0, daily_max) if daily_max > 0 else 0
        remaining_chat_code_budget[0] -= code_generations
        loc_suggested = rng.randint(0, 2) if code_generations == 0 else rng.randint(1, 5)
        loc_added = min(rng.randint(0, 3), loc_suggested)
        loc_deleted = rng.randint(*profile.loc_deleted_per_day)
        ask_mode, edit_mode, plan_mode = split_chat_modes(rng, chat_total)
        acceptance_rate = rand_rate(rng, profile.acceptance_pct)
        return {
            "user_login": login,
            "user_id": user_id,
            "day": day,
            "user_initiated_interaction_count": max(interactions, chat_total),
            "code_generation_activity_count": code_generations,
            "code_acceptance_activity_count": round(code_generations * acceptance_rate),
            "loc_suggested_to_add_sum": loc_suggested,
            "loc_added_sum": loc_added,
            "loc_deleted_sum": loc_deleted,
            "chat_panel_ask_mode": ask_mode,
            "chat_panel_edit_mode": edit_mode,
            "chat_panel_plan_mode": plan_mode,
            "chat_panel_agent_mode": 0,
            "chat_panel_custom_mode": 0,
            "chat_panel_unknown_mode": 0,
            "used_chat": True,
            "used_agent": False,
            "used_cli": False,
            "used_copilot_code_review_active": False,
            "used_copilot_code_review_passive": False,
            "last_known_ide_version": ide_version,
            "last_known_plugin_version": plugin_version,
        }

    code_generations = rand_between(rng, profile.code_gen_per_day)
    acceptance_rate = rand_rate(rng, profile.acceptance_pct)
    loc_suggested = rand_between(rng, profile.loc_suggested_per_day)
    loc_added = rand_between(rng, profile.loc_added_per_day)
    loc_deleted = rand_between(rng, profile.loc_deleted_per_day)

    if profile.name == "Agent-Heavy":
        loc_added = max(loc_added, loc_suggested * 3)

    ask_mode, edit_mode, plan_mode = split_chat_modes(rng, chat_total)
    used_chat = "chat" in profile.features and chat_total > 0
    used_agent = "agent" in profile.features and agent_total > 0
    used_cli = "cli" in profile.features and rng.random() < 0.45

    return {
        "user_login": login,
        "user_id": user_id,
        "day": day,
        "user_initiated_interaction_count": max(interactions, chat_total + agent_total),
        "code_generation_activity_count": code_generations,
        "code_acceptance_activity_count": round(code_generations * acceptance_rate),
        "loc_suggested_to_add_sum": loc_suggested,
        "loc_added_sum": loc_added,
        "loc_deleted_sum": loc_deleted,
        "chat_panel_ask_mode": ask_mode,
        "chat_panel_edit_mode": edit_mode,
        "chat_panel_plan_mode": plan_mode,
        "chat_panel_agent_mode": agent_total,
        "chat_panel_custom_mode": 0,
        "chat_panel_unknown_mode": 0,
        "used_chat": used_chat,
        "used_agent": used_agent,
        "used_cli": used_cli,
        "used_copilot_code_review_active": False,
        "used_copilot_code_review_passive": False,
        "last_known_ide_version": ide_version,
        "last_known_plugin_version": plugin_version,
    }


def adjust_records_for_profile(profile: ProfileSpec, records: list[dict], rng: random.Random) -> None:
    if profile.name == "Low Usage" and records:
        total_interactions = sum(rec["user_initiated_interaction_count"] for rec in records)
        if total_interactions < 5:
            records[0]["user_initiated_interaction_count"] += 5 - total_interactions
        elif total_interactions >= 20:
            overflow = total_interactions - 19
            for rec in records:
                reducible = max(rec["user_initiated_interaction_count"] - 1, 0)
                reduction = min(reducible, overflow)
                rec["user_initiated_interaction_count"] -= reduction
                overflow -= reduction
                if overflow == 0:
                    break
            if sum(rec["user_initiated_interaction_count"] for rec in records) < 5:
                records[0]["user_initiated_interaction_count"] = 5

    if profile.name == "Agent-Heavy" and records:
        chat_total = sum(
            rec["chat_panel_ask_mode"] + rec["chat_panel_edit_mode"] + rec["chat_panel_plan_mode"]
            for rec in records
        )
        agent_total = sum(rec["chat_panel_agent_mode"] for rec in records)
        if agent_total <= chat_total:
            records[0]["chat_panel_agent_mode"] += (chat_total - agent_total) + 1
        suggested = sum(rec["loc_suggested_to_add_sum"] for rec in records)
        added = sum(rec["loc_added_sum"] for rec in records)
        if added <= suggested * 2:
            records[0]["loc_added_sum"] += (suggested * 2 - added) + rng.randint(5, 25)

    if profile.name == "Chat-Focused" and records:
        total_generations = sum(rec["code_generation_activity_count"] for rec in records)
        while total_generations > 5:
            for rec in reversed(records):
                if rec["code_generation_activity_count"] > 0:
                    rec["code_generation_activity_count"] -= 1
                    rec["code_acceptance_activity_count"] = min(
                        rec["code_acceptance_activity_count"],
                        rec["code_generation_activity_count"],
                    )
                    total_generations -= 1
                    if total_generations <= 5:
                        break

    if profile.name == "Healthy" and records:
        total_generations = sum(rec["code_generation_activity_count"] for rec in records)
        total_acceptances = sum(rec["code_acceptance_activity_count"] for rec in records)
        active_days = len(records)
        acceptance_pct = round((total_acceptances / total_generations) * 100, 1) if total_generations else 0.0
        engagement_depth = sum(
            rec["chat_panel_ask_mode"]
            + rec["chat_panel_edit_mode"]
            + rec["chat_panel_plan_mode"]
            + rec["chat_panel_agent_mode"]
            for rec in records
        )
        if active_days >= 14 and engagement_depth >= 50 and acceptance_pct >= 30.0:
            for rec in records:
                if rec["code_acceptance_activity_count"] > 0:
                    rec["code_acceptance_activity_count"] -= 1
                    total_acceptances -= 1
                    acceptance_pct = round((total_acceptances / total_generations) * 100, 1) if total_generations else 0.0
                    if acceptance_pct < 30.0:
                        break

    if profile.name == "Moderate" and records:
        total_generations = sum(rec["code_generation_activity_count"] for rec in records)
        total_acceptances = sum(rec["code_acceptance_activity_count"] for rec in records)
        acceptance_pct = round((total_acceptances / total_generations) * 100, 1) if total_generations else 0.0
        if acceptance_pct >= 25.0:
            for rec in records:
                if rec["code_acceptance_activity_count"] > 0:
                    rec["code_acceptance_activity_count"] -= 1
                    total_acceptances -= 1
                    acceptance_pct = round((total_acceptances / total_generations) * 100, 1) if total_generations else 0.0
                    if acceptance_pct < 25.0:
                        break


def generate_profile_users(profile: ProfileSpec, user_id_start: int, rng: random.Random) -> tuple[list[dict], list[dict], int]:
    records: list[dict] = []
    seats: list[dict] = []
    next_user_id = user_id_start

    for index in range(1, profile.count + 1):
        login = f"{profile.username_prefix}_{index:03d}"
        user_id = next_user_id
        next_user_id += 1
        ide_version, plugin_version = rng.choice(EDITOR_VARIANTS)
        active_days = rand_between(rng, profile.active_days)
        active_dates = choose_active_days(rng, active_days)
        user = {
            "login": login,
            "user_id": user_id,
            "editor": (ide_version, plugin_version),
            "active_days": active_dates,
        }

        chat_budget = [rng.randint(0, 5)] if profile.name == "Chat-Focused" else None
        acceptance_bounds = healthy_acceptance_bounds(profile, active_days)

        profile_for_days = ProfileSpec(
            name=profile.name,
            count=profile.count,
            username_prefix=profile.username_prefix,
            active_days=profile.active_days,
            interactions_per_day=profile.interactions_per_day,
            code_gen_per_day=profile.code_gen_per_day,
            acceptance_pct=acceptance_bounds,
            loc_suggested_per_day=profile.loc_suggested_per_day,
            loc_added_per_day=profile.loc_added_per_day,
            loc_deleted_per_day=profile.loc_deleted_per_day,
            chat_per_day=profile.chat_per_day,
            agent_per_day=profile.agent_per_day,
            features=profile.features,
        )

        user_records = [
            build_daily_record(
                profile_for_days,
                login,
                user_id,
                day,
                ide_version,
                plugin_version,
                rng,
                remaining_chat_code_budget=chat_budget,
            )
            for day in active_dates
        ]
        adjust_records_for_profile(profile, user_records, rng)
        records.extend(user_records)
        seats.append(build_seat(user, profile, rng))

    return records, seats, next_user_id


def write_ndjson(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, sort_keys=False))
            handle.write("\n")


def validate_health_distribution(user_rows: list[dict]) -> None:
    actual = Counter(row["health_profile"] for row in user_rows)
    if dict(actual) != EXPECTED_HEALTH_COUNTS:
        details = ", ".join(
            f"{label}={actual.get(label, 0)} (expected {expected})"
            for label, expected in EXPECTED_HEALTH_COUNTS.items()
        )
        raise RuntimeError(f"Mock data did not match expected health distribution: {details}")


def main() -> int:
    rng = random.Random(SEED)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("🎲 Generating mock data for 100 users …")
    display_labels = {
        "Power User": "Power Users",
        "Healthy": "Healthy",
        "Agent-Heavy": "Agent-Heavy",
        "Chat-Focused": "Chat-Focused",
        "Moderate": "Moderate",
        "Low Usage": "Low Usage",
        "Needs Enablement": "Needs Enablement",
    }
    for profile in PROFILE_SPECS:
        print(f"   {display_labels[profile.name]}: {profile.count}")

    all_records: list[dict] = []
    all_seats: list[dict] = []
    next_user_id = 100001
    for profile in PROFILE_SPECS:
        profile_records, profile_seats, next_user_id = generate_profile_users(profile, next_user_id, rng)
        all_records.extend(profile_records)
        all_seats.extend(profile_seats)

    all_records.sort(key=lambda item: (item["user_login"], item["day"]))
    all_seats.sort(key=lambda item: item["assignee"]["login"])

    print(f"📝 Writing NDJSON → {NDJSON_PATH.relative_to(Path(__file__).parent)} ({len(all_records):,} records)")
    write_ndjson(NDJSON_PATH, all_records)

    print(f"📝 Writing seats → {SEATS_PATH.relative_to(Path(__file__).parent)} ({len(all_seats)} seats)")
    SEATS_PATH.write_text(json.dumps(all_seats, indent=2), encoding="utf-8")

    print("📊 Generating productivity report …")
    ndjson_records = [json.loads(line) for line in NDJSON_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    seats = json.loads(SEATS_PATH.read_text(encoding="utf-8"))
    _ = aggregate_user_ndjson(ndjson_records)
    user_rows = build_user_rows(MOCK_ORG, seats, ndjson_records)
    validate_health_distribution(user_rows)
    team_summary = build_team_summary(user_rows, DATE_START, DATE_END)
    write_excel(REPORT_PATH, user_rows, team_summary)

    print("✅ Done! Files saved to mock_data/")
    print(f"   - {NDJSON_PATH.name}")
    print(f"   - {SEATS_PATH.name}")
    print(f"   - {REPORT_PATH.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
