# Copilot Metrics Report Generator

A standalone Python CLI tool that pulls GitHub Copilot usage data via the
REST API and generates management-ready CSV reports.

## What It Produces

| File | Description |
|------|-------------|
| `copilot_users_YYYYMMDD.csv` | One row per user per org — seat status, last activity, editor |
| `copilot_user_activity_YYYYMMDD.csv` | **NEW** — One row per user per day — chat modes, agent, CLI tokens, code completions, code review |
| `copilot_org_summary_YYYYMMDD.csv` | One row per org — adoption rate, suggestions, acceptance rate, agent/CLI/code review stats |

## Prerequisites

1. **Python 3.10+**
2. **GitHub Personal Access Token (classic)** with these scopes:
   - `manage_billing:copilot` — read seat/billing data
   - `read:org` — read org membership
   - `read:enterprise` — list orgs under an enterprise (only needed with `--enterprise`)
3. Copilot must be enabled for the target organizations.

## Setup

```bash
cd copilot-metrics-report
pip install -r requirements.txt

# Copy and edit the env file
cp .env.example .env
# Edit .env with your token and org list
```

## Usage

```bash
# Auto-discover all orgs under an enterprise (easiest)
python copilot_metrics_report.py --enterprise my-enterprise

# Specific orgs only
python copilot_metrics_report.py --orgs my-org

# Multiple orgs
python copilot_metrics_report.py --orgs org1,org2,org3

# Custom date range (last 14 days)
python copilot_metrics_report.py --enterprise my-ent --days 14

# Specify token inline + output directory
python copilot_metrics_report.py --token ghp_xxx --enterprise my-ent --output-dir ./reports

# Also save raw API JSON responses
python copilot_metrics_report.py --enterprise my-ent --raw-json
```

## CLI Options

| Flag | Env Var | Default | Description |
|------|---------|---------|-------------|
| `--token` | `GITHUB_TOKEN` | — | GitHub PAT |
| `--enterprise` | `ENTERPRISE_SLUG` | — | Enterprise slug (auto-discovers all orgs) |
| `--orgs` | `ORGS` | — | Comma-separated org slugs (overrides `--enterprise`) |
| `--days` | `DAYS` | `28` | Metrics window in days |
| `--output-dir` | `OUTPUT_DIR` | `.` | Directory for CSV output |
| `--raw-json` | — | off | Also dump raw API JSON |

## Sample Output

### `copilot_users_20260519.csv`

```
organization,login,name,email,seat_created_at,copilot_assigned_at,last_activity_at,last_activity_editor,status,days_since_last_activity,plan_type
my-org,octocat,The Octocat,octocat@github.com,2025-01-15T10:00:00Z,,2026-05-18T14:30:00Z,vscode,active,1,business
my-org,hubot,Hubot,,2025-02-01T08:00:00Z,,,,,inactive,,business
```

### `copilot_user_activity_20260519.csv` (NEW — per-user per-day detail)

```
day,organization,user_login,user_id,used_chat,used_agent,used_cli,used_code_review_active,used_code_review_passive,interaction_count,chat_ask_mode,chat_edit_mode,chat_plan_mode,chat_agent_mode,chat_custom_mode,code_gen_count,code_accept_count,loc_suggested_add,loc_added,loc_deleted,cli_sessions,cli_requests,cli_prompts,cli_output_tokens,cli_prompt_tokens,cli_avg_tokens_per_req,ide_version,plugin_version
2026-05-18,my-org,octocat,12345,True,True,True,True,False,45,10,8,5,20,2,30,22,500,350,50,3,15,12,8000,3000,733,vscode/1.90,1.200.0
```

### `copilot_org_summary_20260519.csv`

```
organization,total_seats,active_seats,inactive_seats,adoption_rate_pct,date_range_start,date_range_end,avg_daily_active_users,total_suggestions_shown,total_suggestions_accepted,acceptance_rate_pct,total_lines_suggested,total_lines_accepted,total_chat_turns,total_agent_users,total_cli_users,total_code_review_users,total_cli_tokens,agent_adoption_pct,top_languages,top_editors
my-org,50,42,8,84.0,2026-04-21,2026-05-19,35.2,125000,37500,30.0,250000,75000,8500,15,8,12,450000,35.7,"python, javascript, typescript, go, java","vscode, jetbrains, neovim"
```

## Troubleshooting

| Error | Fix |
|-------|-----|
| `401 Authentication failed` | Check your PAT is valid and not expired |
| `403 Forbidden` | Ensure PAT has `manage_billing:copilot` and `read:org` scopes |
| `404 Not found` | Verify the org slug is correct and Copilot is enabled |
| Rate-limited | The script auto-waits; for large orgs, run during off-peak hours |
