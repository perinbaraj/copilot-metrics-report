# Copilot Metrics Report Generator

Pull GitHub Copilot usage data via the REST API and generate management-ready
CSV reports at user and organization levels. Available in **Python** and **PowerShell**.

## Scripts

| Script | Language | Description |
|--------|----------|-------------|
| `copilot_customer_report.py` | Python | **Customer-ready** single CSV — 19 columns, zero empty, org summaries |
| `copilot_customer_report.ps1` | PowerShell | Same output as above — no external dependencies |
| `copilot_metrics_report.py` | Python | Detailed multi-CSV report (3 files) — full NDJSON metrics |
| `copilot_productivity_report.py` | Python | **Productivity analysis** — formatted Excel with per-user KPIs, health classifications, and team summary |
| `generate_mock_data.py` | Python | **Mock data** — generates 100 synthetic users with varied profiles for testing |

## Quick Start

### Python

```bash
cd copilot-metrics-report
pip install -r requirements.txt
cp .env.example .env   # edit with your token

# Customer report (recommended)
python copilot_customer_report.py --enterprise my-enterprise
python copilot_customer_report.py --orgs org1,org2 --token ghp_xxx

# Productivity report (Excel with health classifications)
python copilot_productivity_report.py --enterprise my-enterprise
python copilot_productivity_report.py --orgs org1,org2 --token ghp_xxx

# Generate mock data for testing (no API token needed)
python generate_mock_data.py
```

### PowerShell

```powershell
# No dependencies needed
.\copilot_customer_report.ps1 -Enterprise my-enterprise -Token ghp_xxx
.\copilot_customer_report.ps1 -Orgs "org1,org2"
```

## Prerequisites

1. **Python 3.10+** (for `.py` scripts) or **PowerShell 5.1+** (for `.ps1`)
2. **GitHub Personal Access Token (classic)** with scopes:
   - `manage_billing:copilot` — read seat/billing data
   - `read:org` — read org membership
   - `admin:enterprise` — list orgs under an enterprise (for `--enterprise`)
3. Copilot must be enabled for the target organizations.
4. For SAML-protected orgs, authorize your PAT for SSO at https://github.com/settings/tokens

## Customer Report Output

**`copilot_report_YYYYMMDD.csv`** — single CSV, one row per user, grouped by org.

### Columns (19 — all guaranteed populated)

| Column | Description |
|--------|-------------|
| `organization` | Org the user belongs to |
| `user_login` | GitHub username |
| `status` | `active` / `inactive` |
| `plan_type` | `business` / `enterprise` |
| `seat_assigned_date` | When Copilot seat was assigned |
| `last_activity_date` | Last Copilot usage date |
| `days_inactive` | Days since last activity (`never` if no activity) |
| `editor` | Last editor used (e.g. `vscode`, `JetBrains-IU`) |
| `copilot_model` | Copilot plugin version (e.g. `copilot-chat/0.28.5`) |
| `total_days_active` | Days with Copilot usage (out of 28) |
| `utilization_pct` | Active days / 28 × 100 |
| `total_interactions` | Total prompts sent to Copilot |
| `total_code_generations` | Code generation events |
| `total_code_acceptances` | Accepted code suggestions |
| `acceptance_rate_pct` | Acceptances / generations × 100 |
| `total_loc_suggested` | Lines of code suggested |
| `total_loc_added` | Lines actually added to code |
| `total_loc_deleted` | Lines deleted from code |
| `features_used` | Features used: `chat`, `agent`, `cli`, `code_review` |

### Org Summary Rows

Between each org's users, a summary row is inserted with:
- Total/active/inactive seat counts
- Average utilization %
- Org-wide acceptance rate
- Top 3 editors
- List of inactive users

### Sample Output

```
organization,user_login,status,plan_type,seat_assigned_date,last_activity_date,days_inactive,editor,copilot_model,total_days_active,utilization_pct,total_interactions,total_code_generations,total_code_acceptances,acceptance_rate_pct,total_loc_suggested,total_loc_added,total_loc_deleted,features_used
my-org,alice,active,business,2025-01-15,2026-05-20,1,vscode,copilot-chat/0.28.5,18,64.3,245,120,45,37.5,3200,1800,200,"chat, agent"
my-org,bob,inactive,business,2025-02-01,N/A,never,N/A,N/A,0,0.0,0,0,0,0,0,0,0,none
── my-org SUMMARY ──,50 seats,42 active / 8 inactive,,,,,"Top: vscode, JetBrains-IU",,,"avg 48.2%",12500,6200,2100,33.9,,8500,1200,"Inactive: bob, charlie"
```

## Productivity Report Output

### Overview

`copilot_productivity_{date}.xlsx` — formatted Excel workbook with per-user productivity analysis and team-level summary.

### Sheet 1: User Productivity

Per-user breakdown with auto-classified health profiles and color-coded indicators.

| Column | Description |
|--------|-------------|
| `organization` | Org the user belongs to |
| `user_login` | GitHub username |
| `active_days` | Days with Copilot usage (out of 28) |
| `adoption_rate_pct` | Active days / 28 × 100 |
| `total_interactions` | Total prompts sent to Copilot |
| `code_generations` | Code generation events |
| `code_acceptances` | Accepted code suggestions |
| `acceptance_rate_pct` | Acceptances / generations × 100 |
| `loc_suggested` | Lines of code suggested by Copilot |
| `loc_added` | Lines actually added to code |
| `loc_deleted` | Lines deleted from code |
| `net_loc_change` | loc_added − loc_deleted |
| `copilot_contribution_pct` | loc_suggested / loc_added × 100 |
| `chat_interactions` | Chat panel interactions (ask, edit, plan modes) |
| `agent_interactions` | Agent mode interactions |
| `features_used` | Features used: chat, agent, cli, code_review |
| `engagement_depth` | chat + agent interactions |
| `health_profile` | Auto-classified health label |
| `health_notes` | Explanation of classification |

### Health Profiles

| Profile | Color | Criteria |
|---------|-------|----------|
| Power User | 🟢 Green | acceptance ≥ 30%, active ≥ 14 days, depth ≥ 50 |
| Healthy | 🟢 Green | acceptance ≥ 25%, active ≥ 7 days |
| Agent-Heavy | 🟡 Yellow | Agent-dominant usage, loc_added >> loc_suggested |
| Chat-Focused | 🟡 Yellow | Chat usage with minimal code generation |
| Moderate | 🔵 Blue | Active but room to grow |
| Low Usage | 🔴 Red | 1-3 active days, <20 interactions |
| Needs Enablement | 🔴 Red | No meaningful usage detected |

### Sheet 2: Team Summary

Consolidated KPIs including adoption rates, code acceleration metrics, engagement depth, feature adoption percentages, health distribution, top users by engagement, and users needing enablement.

## Mock Data

`generate_mock_data.py` creates realistic synthetic data for 100 GitHub Copilot users
across 7 usage profiles — no API token required. Useful for testing reports and demos.

```bash
python generate_mock_data.py
```

**Output** (`mock_data/` directory):
| File | Description |
|------|-------------|
| `mock_copilot_users.ndjson` | ~985 per-user-per-day records in exact API NDJSON format |
| `mock_seats.json` | 100 seat assignments matching the billing/seats API schema |
| `copilot_productivity_mock.xlsx` | Demo productivity report generated from the mock data |

**User Profile Distribution:**
| Profile | Count | Description |
|---------|-------|-------------|
| Power User | 10 | Heavy, effective usage across all features |
| Healthy | 25 | Good adoption with consistent daily usage |
| Agent-Heavy | 10 | Primary usage through agent/edit mode |
| Chat-Focused | 10 | Uses Copilot for Q&A rather than code generation |
| Moderate | 20 | Active but room to increase engagement |
| Low Usage | 15 | Minimal usage, 1-3 active days |
| Needs Enablement | 10 | No meaningful usage detected |

## Metrics Documentation

See **[COPILOT_METRICS_GUIDE.md](COPILOT_METRICS_GUIDE.md)** for a comprehensive reference covering:

- 🌳 GitHub Copilot REST API endpoint tree (enterprise → org → user)
- 📊 Complete NDJSON field reference with types and descriptions
- 📈 Developer productivity measurement framework (4 dimensions)
- 🎯 Composite KPIs with formulas and target ranges
- 🏥 Health classification system and thresholds
- ❓ Common patterns, gotchas, and FAQ

## CLI Options

### Customer Report (Python)

| Flag | Env Var | Default | Description |
|------|---------|---------|-------------|
| `--token` | `GITHUB_TOKEN` | — | GitHub PAT |
| `--enterprise` | `ENTERPRISE_SLUG` | — | Enterprise slug (auto-discovers all orgs) |
| `--orgs` | `ORGS` | — | Comma-separated org slugs (overrides `--enterprise`) |
| `--output-dir` | `OUTPUT_DIR` | `.` | Directory for CSV output |
| `--raw-json` | — | off | Also save raw API JSON responses |

### Customer Report (PowerShell)

| Parameter | Env Var | Default | Description |
|-----------|---------|---------|-------------|
| `-Token` | `GITHUB_TOKEN` | — | GitHub PAT |
| `-Enterprise` | `ENTERPRISE_SLUG` | — | Enterprise slug |
| `-Orgs` | `ORGS` | — | Comma-separated org slugs |
| `-OutputDir` | `OUTPUT_DIR` | `.` | Output directory |
| `-RawJson` | — | off | Also save raw API JSON |

### Productivity Report (Python — `copilot_productivity_report.py`)

| Flag | Env Var | Default | Description |
|------|---------|---------|-------------|
| `--token` | `GITHUB_TOKEN` | — | GitHub PAT |
| `--enterprise` | `ENTERPRISE_SLUG` | — | Enterprise slug (auto-discovers all orgs) |
| `--orgs` | `ORGS` | — | Comma-separated org slugs (overrides `--enterprise`) |
| `--output-dir` | `OUTPUT_DIR` | `.` | Directory for report output |

### Detailed Report (Python — `copilot_metrics_report.py`)

| Flag | Env Var | Default | Description |
|------|---------|---------|-------------|
| `--token` | `GITHUB_TOKEN` | — | GitHub PAT |
| `--enterprise` | `ENTERPRISE_SLUG` | — | Enterprise slug |
| `--orgs` | `ORGS` | — | Comma-separated org slugs |
| `--days` | `DAYS` | `28` | Metrics window in days |
| `--output-dir` | `OUTPUT_DIR` | `.` | Directory for CSV output |
| `--raw-json` | — | off | Also dump raw API JSON |

## Troubleshooting

| Error | Fix |
|-------|-----|
| `401 Authentication failed` | Check your PAT is valid and not expired |
| `403 Forbidden` | Ensure PAT has required scopes; for SAML orgs, authorize SSO |
| `404 Not found` | Verify the org/enterprise slug is correct and Copilot is enabled |
| `SAML enforcement` | Authorize PAT for SSO at https://github.com/settings/tokens |
| Rate-limited | The script auto-waits; for large orgs, run during off-peak hours |
| File locked (PermissionError) | Close the CSV in Excel before re-running |
