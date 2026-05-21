# Copilot Metrics Report Generator

Pull GitHub Copilot usage data via the REST API and generate management-ready
CSV reports at user and organization levels. Available in **Python** and **PowerShell**.

## Scripts

| Script | Language | Description |
|--------|----------|-------------|
| `copilot_customer_report.py` | Python | **Customer-ready** single CSV — 19 columns, zero empty, org summaries |
| `copilot_customer_report.ps1` | PowerShell | Same output as above — no external dependencies |
| `copilot_metrics_report.py` | Python | Detailed multi-CSV report (3 files) — full NDJSON metrics |

## Quick Start

### Python

```bash
cd copilot-metrics-report
pip install -r requirements.txt
cp .env.example .env   # edit with your token

# Customer report (recommended)
python copilot_customer_report.py --enterprise my-enterprise
python copilot_customer_report.py --orgs org1,org2 --token ghp_xxx
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
