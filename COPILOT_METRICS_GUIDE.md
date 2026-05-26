# GitHub Copilot Metrics & Productivity Guide

A comprehensive reference for understanding GitHub Copilot usage metrics,
the REST API structure, and how to measure developer productivity.

This guide is written for engineering managers, DevOps leaders, platform teams, and CTO offices evaluating GitHub Copilot adoption, value realization, and productivity impact. It explains **where the data comes from**, **what each metric means**, and **how to turn raw API exports into customer-ready productivity insights**.

> [!IMPORTANT]
> This guide focuses on the **billing, seat, usage, and productivity-reporting** endpoints that matter most for Copilot ROI analysis. It is intentionally customer-shareable and avoids internal-only terminology.

---

## 1. GitHub Copilot REST API — Endpoint Tree

```text
GitHub Copilot REST API (metrics + billing + seat visibility)
│
├── 🏢 Enterprise Level
│   ├── Legacy aggregate metrics
│   │   └── GET /enterprises/{enterprise}/copilot/metrics
│   │       └── Returns daily aggregate metrics (legacy; sunset April 2026)
│   │
│   ├── New usage-metrics reports
│   │   ├── GET /enterprises/{enterprise}/copilot/metrics/reports/enterprise-1-day?day=YYYY-MM-DD
│   │   │   └── Returns signed download_links[] for enterprise totals for one day
│   │   ├── GET /enterprises/{enterprise}/copilot/metrics/reports/enterprise-28-day/latest
│   │   │   └── Returns signed download_links[] for latest 28-day enterprise totals
│   │   ├── GET /enterprises/{enterprise}/copilot/metrics/reports/users-1-day?day=YYYY-MM-DD
│   │   │   └── Returns signed download_links[] for per-user metrics for one day
│   │   ├── GET /enterprises/{enterprise}/copilot/metrics/reports/users-28-day/latest
│   │   │   └── Returns signed download_links[] for per-user metrics (★ best enterprise user endpoint)
│   │   └── GET /enterprises/{enterprise}/copilot/metrics/reports/user-teams-1-day?day=YYYY-MM-DD
│   │       └── Returns signed download_links[] for user ↔ team mappings
│   │
│   └── Notes
│       └── Team rollups are built by joining daily users-1-day + user-teams-1-day
│
├── 🏛️ Organization Level
│   ├── Billing & seats
│   │   ├── GET /orgs/{org}/copilot/billing
│   │   │   └── Returns org-level billing summary, policy settings, seat breakdown, plan type
│   │   └── GET /orgs/{org}/copilot/billing/seats
│   │       └── Returns per-user seat assignments, last activity, editor, plan type
│   │
│   ├── Legacy aggregate metrics
│   │   └── GET /orgs/{org}/copilot/metrics
│   │       └── Returns daily aggregate metrics (legacy; sunset April 2026)
│   │
│   ├── New usage-metrics reports
│   │   ├── GET /orgs/{org}/copilot/metrics/reports/organization-1-day?day=YYYY-MM-DD
│   │   │   └── Returns signed download_links[] for org totals for one day
│   │   ├── GET /orgs/{org}/copilot/metrics/reports/organization-28-day/latest
│   │   │   └── Returns signed download_links[] for latest 28-day org totals
│   │   ├── GET /orgs/{org}/copilot/metrics/reports/users-1-day?day=YYYY-MM-DD
│   │   │   └── Returns signed download_links[] for per-user metrics for one day
│   │   ├── GET /orgs/{org}/copilot/metrics/reports/users-28-day/latest
│   │   │   └── Returns signed download_links[] for per-user metrics (★ primary endpoint)
│   │   └── GET /orgs/{org}/copilot/metrics/reports/user-teams-1-day?day=YYYY-MM-DD
│   │       └── Returns signed download_links[] for user ↔ team mappings
│   │
│   └── Notes
│       └── There is no documented 28-day user-teams snapshot; build 28-day team reports from daily joins
│
└── 👤 User Level (via enterprise/org report scopes)
    ├── Per-user data is embedded in users-1-day and users-28-day/latest reports
    ├── Each NDJSON line = one user × one day × one reporting entity
    └── Team-level attribution requires joining to user-teams-1-day on user_id + day + entity_id
```

### Authentication and access notes

| Topic | Guidance |
|---|---|
| **Recommended header** | `X-GitHub-Api-Version: 2022-11-28` |
| **Standard headers** | `Authorization: Bearer <PAT>` and `Accept: application/vnd.github+json` |
| **Org-level access** | Typically requires `read:org`; billing endpoints often require `manage_billing:copilot` or org-owner rights |
| **Enterprise-level access** | Enterprise owner / billing manager / enterprise metrics permission; classic PATs commonly use `read:enterprise` and/or `manage_billing:copilot` |
| **SAML SSO** | If the org uses SAML SSO, the PAT must be explicitly authorized for that SSO session |
| **Rate limiting** | Standard authenticated REST rate limits apply; watch `X-RateLimit-Limit`, `X-RateLimit-Remaining`, and `X-RateLimit-Reset` headers |
| **Signed report links** | `download_links[]` are temporary signed URLs; download promptly |

> [!NOTE]
> In practice, **`/orgs/{org}/copilot/metrics/reports/users-28-day/latest`** is the most useful endpoint for customer-facing productivity analytics because it provides the richest **per-user, per-day** detail.

---

## 2. API Response Formats

### 2.1 Billing / Seats Response

The org seat endpoint returns a paginated seat inventory.

```json
{
  "total_seats": 150,
  "seats": [
    {
      "assignee": { "login": "octocat", "id": 123 },
      "created_at": "2025-01-15T10:00:00Z",
      "last_activity_at": "2026-05-20T14:30:00Z",
      "last_activity_editor": "vscode/1.101.0/copilot-chat/0.28.5",
      "plan_type": "business"
    }
  ]
}
```

### 2.2 NDJSON Report Flow

The new usage-metrics API is a **two-step download pattern**:

1. **Call the report endpoint**
2. **Receive `download_links[]`**
3. **Download each signed URL**
4. **Parse NDJSON line by line**

```json
{
  "download_links": [
    "https://signed-url-part-1.ndjson",
    "https://signed-url-part-2.ndjson"
  ],
  "report_start_day": "2026-04-23",
  "report_end_day": "2026-05-20"
}
```

Each downloaded file is newline-delimited JSON:

```json
{"user_login":"octocat","user_id":123,"day":"2026-05-20","used_chat":true,"used_agent":false}
{"user_login":"hubot","user_id":456,"day":"2026-05-20","used_chat":true,"used_agent":true}
```

> [!TIP]
> Treat each NDJSON line as an independent record. Do **not** try to load the whole file as one JSON array.

> [!WARNING]
> **User-level NDJSON returns each user's GLOBAL Copilot activity for every org they hold a seat in — not org-scoped activity.**
>
> If a developer is a member of 4 orgs, calling the endpoint on all 4 orgs will return 4 sets of records with **identical** numbers per `(user_login, day)` — once per org. The activity is the user's total across all surfaces (VS Code, JetBrains, GitHub.com, CLI, etc.), regardless of which org you queried.
>
> **Implication:** Naïvely summing counts across orgs multiplies every total by the number of orgs each user belongs to. This toolkit dedupes by `(user_login, day)` before aggregation so Team Summary and Unique Users sheets show correct totals. Per-org rows in **User Productivity** still appear (so you can see which orgs each user is enrolled in), but the metrics shown are the user's global numbers — that's why the same user has identical numbers across orgs.
>
> If you build your own aggregation pipeline, deduplicate on `(user_login, day)` first.

---

## 3. NDJSON Metrics Reference — Complete Field Guide

### 3.1 Identity & Scope Fields

| Field | Type | Description |
|---|---|---|
| `user_login` | string | GitHub username |
| `user_id` | integer | Unique GitHub user ID |
| `day` | string (`YYYY-MM-DD`) | Calendar day represented by the record |
| `organization_id` | string | Organization ID for org-scoped reports |
| `enterprise_id` | string | Enterprise ID for enterprise-scoped reports |
| `report_start_day` | string | Start of the 28-day window in rolled-up report metadata |
| `report_end_day` | string | End of the 28-day window in rolled-up report metadata |

### 3.2 Interaction Metrics

| Field | Type | Description | Important Notes |
|---|---|---|---|
| `user_initiated_interaction_count` | integer | Number of explicit prompts sent to Copilot | Does **not** include opening chat, switching modes, shortcuts, or configuration changes |
| `chat_panel_ask_mode` | integer | Interactions sent while Ask mode was selected | Subset of `user_initiated_interaction_count` |
| `chat_panel_edit_mode` | integer | Interactions sent while Edit mode was selected | Often associated with file changes |
| `chat_panel_plan_mode` | integer | Interactions sent while Plan mode was selected | Reflects planning / reasoning workflows |
| `chat_panel_agent_mode` | integer | Interactions sent while Agent mode was selected | Includes autonomous multi-step workflows |
| `chat_panel_custom_mode` | integer | Interactions sent to a custom agent | Useful for custom enterprise agents |
| `chat_panel_unknown_mode` | integer | Interactions where mode attribution was unknown | Usually small |

### 3.3 Code Generation Metrics

| Field | Type | Description | Important Notes |
|---|---|---|---|
| `code_generation_activity_count` | integer | Number of distinct Copilot output events | Includes comments and docstrings; one prompt can create multiple generated blocks |
| `code_acceptance_activity_count` | integer | Number of suggestions / blocks accepted | Counts apply-to-file, insert-at-cursor, insert-into-terminal, copy button; excludes raw OS clipboard actions |
| `loc_suggested_to_add_sum` | integer | Lines Copilot suggested to add | Includes completions, inline chat, chat panel; **excludes agent edits** |
| `loc_suggested_to_delete_sum` | integer | Lines Copilot suggested to delete | Future support is broader than today; often sparsely populated |
| `loc_added_sum` | integer | Lines actually added in the editor | Includes accepted suggestions **and** direct agent/edit-mode writes |
| `loc_deleted_sum` | integer | Lines actually deleted in the editor | Today this is heavily associated with agent/edit actions |

> [!WARNING]
> `loc_suggested_to_add_sum` and `loc_added_sum` are **not apples-to-apples**. The first is **suggestion scope**; the second is **actual editor change scope**. Agent-mode edits can make `loc_added_sum` much larger than `loc_suggested_to_add_sum`.

### 3.4 Feature Usage Flags

| Field | Type | Description |
|---|---|---|
| `used_chat` | boolean | User used IDE chat that day |
| `used_agent` | boolean | User used agent mode in the IDE that day |
| `used_cli` | boolean | User used Copilot CLI that day |
| `used_copilot_code_review_active` | boolean | User actively requested / applied Copilot code review that day |
| `used_copilot_code_review_passive` | boolean | Copilot code review was auto-assigned to the user's PR that day |

### 3.5 Agent Edit Metrics

| Field | Type | Description |
|---|---|---|
| `agent_edit` | object | Captures lines added/deleted when Copilot writes directly into files in edit, agent, or custom-agent mode |

**Why it matters:** `agent_edit` activity is **not** part of suggestion-style metrics. That is exactly why advanced users can look “anomalous” unless you separate **suggestion workflows** from **agentic workflows**.

### 3.6 CLI Metrics (`totals_by_cli`)

| Field | Type | Description |
|---|---|---|
| `totals_by_cli.session_count` | integer | CLI sessions initiated that day |
| `totals_by_cli.request_count` | integer | Total CLI requests, including automated agent follow-ups |
| `totals_by_cli.prompt_count` | integer | User prompts / commands executed |
| `totals_by_cli.token_usage.output_tokens_sum` | integer | Output tokens generated |
| `totals_by_cli.token_usage.prompt_tokens_sum` | integer | Prompt tokens sent |
| `totals_by_cli.token_usage.avg_tokens_per_request` | number | `(output_tokens_sum + prompt_tokens_sum) / request_count` |
| `totals_by_cli.last_known_cli_version` | object | Most recent CLI version seen for the user that day |

### 3.7 IDE, Plugin, and Breakdown Arrays

| Field | Type | Description |
|---|---|---|
| `totals_by_ide` | array | Breakdown by IDE |
| `totals_by_feature` | array | Breakdown by feature (completion, chat, etc.) |
| `totals_by_language_feature` | array | Breakdown by language + feature |
| `totals_by_model_feature` | array | Breakdown by model + feature |
| `totals_by_language_model` | array | Breakdown by language + model |
| `last_known_ide_version` | string / nested object in `totals_by_ide` | Most recent IDE version detected |
| `last_known_plugin_version` | string / nested object in `totals_by_ide` | Most recent Copilot Chat extension version detected |

### 3.8 Aggregate Activity Fields (org / enterprise scope)

| Field | Type | Description |
|---|---|---|
| `daily_active_users` | integer | Unique Copilot-active users on that day |
| `weekly_active_users` | integer | Unique active users in the trailing 7-day window |
| `monthly_active_users` | integer | Unique active users in the trailing 28-day window |
| `monthly_active_chat_users` | integer | Unique users who used chat in the trailing 28-day window |
| `monthly_active_agent_users` | integer | Unique users who used agent mode in the trailing 28-day window |
| `daily_active_cli_users` | integer | Unique users who used Copilot CLI on that day |

### 3.9 Code Review Metrics (org / enterprise scope)

| Field | Type | Description |
|---|---|---|
| `daily_active_copilot_code_review_users` | integer | Users who actively used Copilot code review on that day |
| `daily_passive_copilot_code_review_users` | integer | Users whose PRs were passively auto-reviewed on that day |
| `weekly_active_copilot_code_review_users` | integer | Active code-review users in the trailing 7-day window |
| `weekly_passive_copilot_code_review_users` | integer | Passive-only code-review users in the trailing 7-day window |
| `monthly_active_copilot_code_review_users` | integer | Active code-review users in the trailing 28-day window |
| `monthly_passive_copilot_code_review_users` | integer | Passive-only code-review users in the trailing 28-day window |

### 3.10 Pull Request Metrics (org / enterprise scope)

| Field | Type | Description |
|---|---|---|
| `pull_requests.total_created` | integer | PRs created on that day |
| `pull_requests.total_reviewed` | integer | PRs reviewed on that day |
| `pull_requests.total_merged` | integer | PRs merged on that day |
| `pull_requests.median_minutes_to_merge` | number | Median minutes from PR creation to merge |
| `pull_requests.total_suggestions` | integer | All PR review suggestions created that day |
| `pull_requests.total_applied_suggestions` | integer | All PR review suggestions applied that day |
| `pull_requests.total_created_by_copilot` | integer | PRs authored by Copilot |
| `pull_requests.total_reviewed_by_copilot` | integer | PRs reviewed by Copilot |
| `pull_requests.total_merged_created_by_copilot` | integer | Copilot-authored PRs merged that day |
| `pull_requests.total_merged_reviewed_by_copilot` | integer | PRs merged after Copilot review |
| `pull_requests.median_minutes_to_merge_copilot_authored` | number | Median merge time for Copilot-authored PRs |
| `pull_requests.median_minutes_to_merge_copilot_reviewed` | number | Median merge time for Copilot-reviewed PRs |
| `pull_requests.total_copilot_suggestions` | integer | PR suggestions generated by Copilot |
| `pull_requests.total_copilot_applied_suggestions` | integer | Copilot suggestions that were applied |
| `pull_requests.copilot_suggestions_by_comment_type` | array | Suggestion counts by comment type such as `bug_risk` or `security` |

### 3.11 Operational / Partition Fields

| Field | Type | Description |
|---|---|---|
| `etl_id` | string | Internal pipeline/batch identifier |
| `day_partition` | string | Partition key used in data exports |
| `entity_id_partition` | integer | Partition key for the enterprise/org entity |

---

## 4. Dashboard Metrics vs API Fields

> [!NOTE]
> The **dashboard** and the **API** use the same underlying concepts, but several dashboard metrics are **derived**, not directly stored as a single field. Also, dashboard visuals generally **exclude CLI usage**, while API exports include it explicitly.

| Dashboard Metric | API Field / Derivation | Direct or Derived? |
|---|---|---|
| Daily Active Users | Count of unique `user_login` per day | Derived |
| Weekly Active Users | Count of unique users in a trailing 7-day window | Derived |
| Total Active Users | Count of unique users in the 28-day window | Derived |
| Code Completion Acceptance Rate | `code_acceptance_activity_count / code_generation_activity_count` | Derived |
| Agent Adoption % | `% of active users with used_agent = true` | Derived |
| Requests per Chat Mode | `chat_panel_*_mode` fields | Direct |
| Lines of Code Changed with AI | `loc_added_sum + loc_deleted_sum` | Derived |
| Agent Contribution % | `agent_edit LOC / total LOC changed` | Derived |
| Feature Breadth | Count of distinct flags / modes used (`used_chat`, `used_agent`, `used_cli`, code review) | Derived |
| Average Chat Requests per Active User | `Σ user_initiated_interaction_count / active_users` | Derived |

---

## 5. Measuring Developer Productivity — The Framework

### 5.1 The Four Dimensions

```text
┌─────────────────────────────────────────────────────┐
│           Developer Productivity Framework          │
├─────────────┬─────────────┬──────────┬──────────────┤
│  Adoption   │    Code     │ Workflow │    Code      │
│  & Engage-  │  Acceler-   │  Impact  │  Velocity    │
│    ment     │   ation     │          │              │
├─────────────┼─────────────┼──────────┼──────────────┤
│ Are they    │ Is Copilot  │ How has  │ What's the   │
│ using it?   │ speeding up │ workflow │ net output?  │
│             │ coding?     │ changed? │              │
└─────────────┴─────────────┴──────────┴──────────────┘
```

A strong productivity view does **not** rely on one metric. It combines **adoption**, **quality of interaction**, **workflow behavior**, and **code-change patterns**.

### 5.2 Dimension 1: Adoption & Engagement

**Key metrics**
- `active_days` = count of daily records per user in the 28-day window
- `user_initiated_interaction_count`
- `used_agent`

| KPI | Formula | Target | What It Means |
|---|---|---|---|
| Adoption Rate | `active_days / 28 × 100` | `≥ 50%` | Copilot is part of the user's normal workflow |
| Engagement Depth | `Σ user_initiated_interaction_count` | Varies | Depth of real usage, not just seat possession (counts active prompts across **all** Copilot surfaces) |
| Feature Breadth | Count of distinct features used | `≥ 2` | Indicates mature adoption beyond one narrow use case |

### Interpretation guide

| Signal | Interpretation |
|---|---|
| Adoption Rate `< 25%` | Licensed but rarely using Copilot; enablement needed |
| Adoption Rate `25%–50%` | Moderate usage; often task-specific rather than habitual |
| Adoption Rate `> 50%` | Good recurring adoption |
| High interactions but low active days | Burst-style usage on concentrated coding days |

### 5.3 Dimension 2: Code Acceleration

**Key metrics**
- `code_generation_activity_count`
- `code_acceptance_activity_count`
- `loc_suggested_to_add_sum`
- `loc_added_sum`

| KPI | Formula | Healthy Range | What It Means |
|---|---|---|---|
| Acceptance Rate | `code_acceptances / code_generations × 100` | `25%–40%` | Are suggestions relevant enough to accept? |
| Copilot Code Contribution % | `min(loc_suggested / loc_added, 1.0) × 100` | `30%–70%` | Share of visible output that was suggestion-assisted |
| Time Saved Estimate | `code_acceptances × avg_lines_per_completion × time_per_line` | N/A | Rough directional estimate of time saved |

#### Estimating Time Saved with Copilot

There is no direct "time saved" field in the API — you estimate it using accepted completions as a proxy:

```text
Time Saved (hours) = code_acceptances × MINUTES_PER_ACCEPTANCE / 60
```

| Variable | Value | Source |
|----------|-------|--------|
| `code_acceptances` | From API: `code_acceptance_activity_count` | Direct metric |
| Minutes per acceptance | **5 minutes** (conservative) | Industry benchmark: writing 3-5 lines of code manually takes ~5-10 minutes including thinking, typing, and verifying |

> 📊 **Research backing**: GitHub's randomized controlled trials with 4,867 developers found Copilot users completed coding tasks **26–55% faster** than non-users. The 5 min/acceptance estimate is conservative and directional.
>
> Source: [The Impact of AI on Developer Productivity: Evidence from GitHub Copilot](https://www.microsoft.com/en-us/research/publication/the-impact-of-ai-on-developer-productivity-evidence-from-github-copilot/) (Microsoft/MIT/Princeton/Wharton, 2024)

**Worked Example:**

| User | code_acceptances | Estimated Time Saved | Per Day (28-day window) |
|------|-----------------|---------------------|------------------------|
| User A (Power User) | 401 | `401 × 5 / 60 = 33.4 hrs` | ~1.2 hrs/day |
| User B (Healthy) | 137 | `137 × 5 / 60 = 11.4 hrs` | ~0.4 hrs/day |
| User C (Low Usage) | 56 | `56 × 5 / 60 = 4.7 hrs` | ~0.2 hrs/day |
| **Team of 100** | **5,200** | **`5,200 × 5 / 60 = 433 hrs`** | **~15.5 hrs/day saved** |

> ⚠️ **Important caveats:**
> - This is a **directional estimate**, not a precise measurement
> - Actual time saved varies by language, task complexity, and developer experience
> - Not all acceptances represent equal time savings (a 1-line import vs. a 20-line function)
> - Some accepted code may still require modification after acceptance
> - Use this metric for **trend analysis and ROI discussions**, not performance evaluation

#### Understanding Copilot's Contribution to Output

```text
Copilot Contribution % = min(loc_suggested_to_add_sum / loc_added_sum, 1.0) × 100
```

This metric shows what percentage of the developer's final code output was assisted by Copilot through **inline suggestions and chat**. However, there is a critical nuance:

| Metric | What It Captures | What It Excludes |
|--------|-----------------|-----------------|
| `loc_suggested_to_add_sum` | Inline completions, chat panel suggestions | Agent/edit mode edits |
| `loc_added_sum` | **Everything** — completions + chat + agent edits | Nothing |

This means:

| Scenario | loc_suggested | loc_added | Contribution % | Interpretation |
|----------|--------------|-----------|-----------------|----------------|
| **Selective user** | 4,855 | 800 | 100% (capped) | Copilot suggested heavily; user was selective — accepted only the best suggestions |
| **Balanced user** | 959 | 1,500 | 63.9% | Good mix of Copilot-assisted and manual code |
| **Agent-heavy user** | 38 | 1,422 | 2.7% | Almost all code via agent mode — **not a low value!** |
| **Agent-heavy user** | 10 | 200 | 5.0% | Agent writes directly into files, bypassing suggestion pipeline |

> 💡 **Key insight**: A low Copilot Contribution % with high `loc_added_sum` is actually a sign of **advanced adoption** — the developer is using agent mode where Copilot writes code directly into files rather than offering inline suggestions. The "contribution" is happening through a different channel that `loc_suggested_to_add_sum` doesn't capture.

**For a complete picture of Copilot's contribution, consider:**
1. **Suggestion Contribution**: `loc_suggested / loc_added` — what % came through suggestions
2. **Agent Contribution**: `(loc_added - loc_suggested) / loc_added` — rough proxy for agent-written code (when `loc_added > loc_suggested`)
3. **Total AI Contribution**: High when either channel shows strong numbers

### ⚠️ Interpretation gotchas

1. **Acceptance rate below 20% is not automatically bad.** Some domains are less completion-friendly.
2. **Acceptance rate above 60% deserves a sanity check.** It may reflect extremely good fit—or insufficient review discipline.
3. **`code_acceptance_activity_count > code_generation_activity_count` is possible.** One generated response can be accepted in multiple ways.
4. **`loc_added_sum >> loc_suggested_to_add_sum` usually means agent/edit usage.** It is a workflow signal, not a data-quality issue.

### 5.4 Dimension 3: Workflow Impact

**Key metrics**
- `chat_panel_ask_mode`, `chat_panel_edit_mode`, `chat_panel_plan_mode`, `chat_panel_agent_mode`
- `used_chat`, `used_agent`, `used_cli`

| KPI | Formula | What It Means |
|---|---|---|
| Chat-to-Code Ratio | `chat_interactions / code_generations` | Higher means more understanding, debugging, and reasoning usage |
| Agent Adoption | `used_agent = true on ≥ 1 day` | Indicates exposure to agentic workflows |
| Mode Distribution | `% split across ask/edit/plan/agent` | Reveals *how* the team uses Copilot |

### Usage patterns

| Pattern | Characteristics | Interpretation |
|---|---|---|
| Code-First | High completions, low chat | Traditional inline-completion user |
| Chat-Heavy | High chat, low completions | Learning, debugging, or architecture support |
| Agent-Driven | High agent, high LOC change gap | Advanced autonomous workflow adoption |
| Full-Stack | Uses chat, completions, agents, CLI, review | Mature usage across surfaces |

### 5.5 Dimension 4: Code Velocity

**Key metrics**
- `loc_added_sum`
- `loc_deleted_sum`
- `net_loc_change = loc_added_sum - loc_deleted_sum`

> [!WARNING]
> **Lines of Code is not a productivity metric on its own.** Deleting 500 lines of dead code can be more valuable than adding 1,000 lines of boilerplate.

### How to use LOC safely

- Use LOC only as **one component** of a broader productivity view
- Healthy pattern: steady adds **plus** meaningful deletes
- Red flag: consistently high adds with almost no deletes over long periods
- Best use: compare **trends over time** for the same team, not individual-versus-individual contests

---

## 6. Composite KPIs — Bringing It All Together

| Composite KPI | Formula | Purpose | Target |
|---|---|---|---|
| **Adoption Rate** | `active_days / 28 × 100` | Is the user actually using Copilot? | `≥ 50%` |
| **Acceptance Quality** | `code_acceptance_activity_count / code_generation_activity_count × 100` | Are suggestions useful? | `25%–40%` |
| **Copilot Code Contribution %** | `loc_suggested_to_add_sum / loc_added_sum × 100` | How much output was suggestion-assisted? | `30%–70%` |
| **Estimated Time Saved** | `code_acceptances × 5 min / 60` | Directional time savings estimate | Varies |
| **Engagement Depth** | `Σ user_initiated_interaction_count` | How deeply are features being used (across **all** Copilot surfaces)? | `≥ 50` |
| **Feature Breadth** | Count of distinct features / modes used | Is the user moving beyond completions only? | `≥ 2` |
| **Team Health Score** | Weighted average of adoption, acceptance, engagement | Overall team readiness / maturity | Context-dependent |

### Example scoring model

```text
Team Health Score
= 40% Adoption
+ 30% Acceptance Quality
+ 20% Engagement Depth
+ 10% Feature Breadth
```

This is not an official GitHub formula; it is a practical reporting framework for customer conversations.

---

## 7. Health Classification System

The productivity report in this repository uses a **rule-based classification** to make customer conversations actionable.

| Profile | Color | Criteria | Action Required |
|---|---|---|---|
| **Power User** | 🟢 Green | `acceptance ≥ 30%`, `active_days ≥ 14`, `engagement_depth ≥ 50` | Celebrate and harvest best practices |
| **Healthy** | 🟢 Green | `acceptance ≥ 25%`, `active_days ≥ 7` | Maintain momentum |
| **Agent-Heavy** | 🟡 Yellow | `agent_interactions > chat_interactions` and `loc_added > loc_suggested × 2` | Valid advanced pattern; study it |
| **Chat-Focused** | 🟡 Yellow | Chat activity present, but `code_generations ≤ 5` | Encourage completions / edit workflows |
| **Moderate** | 🔵 Blue | Active, but below healthy thresholds | Targeted enablement on weak areas |
| **Low Usage** | 🔴 Red | `1–3 active days` and `< 20 interactions` | Workflow-integration coaching needed |
| **Needs Enablement** | 🔴 Red | `0 active days` or minimal meaningful usage | Training recommended |

```text
Classification order matters
┌──────────────────────────────────────────────────────┐
│ Needs Enablement → Low Usage → Power User →         │
│ Agent-Heavy → Chat-Focused → Healthy → Moderate     │
└──────────────────────────────────────────────────────┘
```

That ordering prevents advanced agent users from being incorrectly labeled as weak adopters simply because suggestion-style metrics do not tell the full story.

---

## 8. Common Patterns & Troubleshooting

### Q: Why is `code_acceptance_activity_count` higher than `code_generation_activity_count`?
**A:** These are different metrics. Generation counts **distinct output events**; acceptance counts **accept actions**. One generation can lead to multiple accept actions.

### Q: Why is `loc_added_sum` much higher than `loc_suggested_to_add_sum`?
**A:** `loc_suggested_to_add_sum` excludes direct agent/edit writes. `loc_added_sum` includes what was actually written into files. The gap usually reflects **agent contribution**.

### Q: A user has `active_days = 0` but still has a seat. Is that an error?
**A:** Usually no. It means the user is licensed but did not generate tracked activity in the 28-day window.

### Q: Should we compare LOC across developers?
**A:** No. LOC is a **volume metric**, not a quality metric. Compare trends within the same team over time.

### Q: What is a “good” acceptance rate?
**A:** Typically **25%–35%** is healthy. Context matters: language, framework, repository maturity, and task type all influence this.

### Q: How should we measure ROI?
**A:** Use three lenses together:
1. **Adoption:** Are licensed users actually using Copilot?
2. **Acceleration:** Are they accepting useful suggestions?
3. **Workflow maturity:** Are they using chat, agents, CLI, and review—not only inline completions?

### Q: Why do team totals sometimes not match organization totals?
**A:** Team-level metrics require joining user activity to daily team snapshots. Multi-team users can contribute to more than one team, and teams with fewer than five seated users are omitted from user-teams reports.

### Q: My report shows the same user listed under multiple orgs with **identical** numbers. Is the data wrong?
**A:** No — that's expected. The user-level NDJSON returns each user's **global** Copilot activity for every org they hold a seat in (see the warning in §2.2). The toolkit dedupes by `(user_login, day)` before computing the Team Summary and Unique Users sheet, so totals are accurate. Per-org rows are kept so you can see which orgs each user is enrolled in; treat them as membership rows, not independent activity rows.

### Q: `engagement_depth` shows 0 even though `features_used` says `chat, agent`.
**A:** This was a known issue and is now fixed.

**Why it happened:** the old formula was `engagement_depth = chat_interactions + agent_interactions`, where those columns sum **only** the `chat_panel_*_mode` counters. Those counters only fire when chat is used via the **IDE chat panel**. If the user uses chat through any other surface — inline chat in the editor, terminal chat, GitHub.com chat, agent edits in edit-mode — the boolean flags `used_chat` / `used_agent` are set but the panel-mode counters stay at zero, so `engagement_depth` was `0`.

**The fix:** `engagement_depth` is now sourced from `user_initiated_interaction_count` (= the `total_interactions` column). The docs define this as *"Number of explicit prompts sent to Copilot"* and it explicitly excludes opening chat, switching modes, shortcuts, and passive completions — making it the right cross-surface engagement signal.

**What still uses the panel-only counters:** the `chat_interactions` and `agent_interactions` columns are kept as breakdown columns (useful when populated) and the `Chat-Focused` / `Agent-Heavy` health profiles still use them. For users where panel-mode is always 0 you'll see those classifications less often even though Power User / Healthy / Moderate continue to work correctly.

### Q: `days_inactive` shows `0` for an active user even though their NDJSON activity was days ago. Is that wrong?
**A:** No — but it's measuring something subtle. There are **two** distinct "inactive" metrics in the report and they answer different questions:

| Column | What it measures | Source | Useful for |
|---|---|---|---|
| `days_inactive` | **Calendar days since the user's last Copilot use.** `0` = active today, `7` = last used a week ago, `"Never"` = seat never activated. | `seat.last_activity_at` (or NDJSON max-day fallback) compared to today. | "Is this user still actively using Copilot at all?" |
| `inactive_days_in_window` | **Days in the 28-day reporting window where the user did NOT prompt Copilot via NDJSON.** | `28 − active_days`. | "Of the last 28 days, how many did this user not actively prompt Copilot?" — the productivity-report-aligned metric. |

**Why both?** `seat.last_activity_at` is a real-time field that updates the moment a user opens an IDE with the Copilot extension loaded — even passive presence counts. So a user who opens their IDE today but only *actually prompts* Copilot 3 times out of 28 days will show `days_inactive = 0` and `inactive_days_in_window = 25`. The first one says "they have Copilot loaded today," the second says "they're rarely using it during the reporting window." Both are valid; the second is usually the one you want to highlight to customers for adoption / coaching conversations.

### Q: The script prints `Seats: 0 (status: ok)` for every org but NDJSON activity is present. What's wrong?
**A:** Nothing on the API side. `status: ok` means the seats endpoint returned **HTTP 200 with an empty list**, not an error. Three things can produce this:

1. **The activity is from users on Copilot Pro / Individual (personal subscription).** They show up in NDJSON because they're org members using personal Copilot, but the org has no org-assigned seats. `Seat Assigned Date` will be **correctly blank** for these users — there's no seat to read a `created_at` from. This is the most common cause when you're testing on your own personal-account orgs.
2. **The org is under enterprise-level seat management (Copilot Enterprise).** Org-level `/orgs/{org}/copilot/billing/seats` returns empty because the seats are assigned at the enterprise level. **Fix:** re-run with `--enterprise <real-enterprise-slug>`. The script now automatically:
   - Pre-fetches `/enterprises/{enterprise}/copilot/billing/seats`.
   - **Discovers enterprise-owned orgs from the seats response** (`seat.organization.login`) and adds them to the iteration list, even when `/enterprises/{enterprise}/organizations` is forbidden (the two endpoints have different access requirements).
   - **Builds a global seat map** (`login.lower()` → seat) so a user holding an enterprise-level seat granted by org A shows that seat's dates and plan_type in **every** org B row their activity appears in. This is the "Copilot Enterprise spillover" case — the user is a member of multiple orgs but the licensing seat lives in one.
   - Per-org seats still **win** over the global map when both exist (more specific source).
   - The `<slug>` is your enterprise account name (e.g. `acme-corp`), **not** your GitHub username.
3. **The org has Copilot Business but no users assigned yet.** Visit `https://github.com/organizations/<org>/settings/copilot/seat_management` to confirm.

The script now prints a loud `⚠ N org(s) still have 0 seats but show Copilot activity` block at the end of each run when this happens, with the same diagnosis.

### Q: `Seat Assigned Date` and `Last Activity Date` are blank for **some** users but not all (and the API didn't error).
**A:** Until v0.4 this happened silently when the user's GitHub login differed in casing between the seats endpoint and the user-level NDJSON. For example: NDJSON returned `pthangavel_pebin` while seats returned `Pthangavel_Pebin`. The old code did an exact-case dict lookup and silently dropped to `None` — making it look like the user had no seat record.

**Fix in current version:** seat lookups are now case-insensitive (we key the seat map by `login.lower()`). If you still see this, run with `--debug` — the script now prints how many seats had `assignee.login` and `created_at` populated, plus the first-seat field keys so you can spot any GitHub field-name changes.

### Q: `Seat Assigned Date` and `Last Activity Date` are blank for all users in one or more orgs.
**A:** The seats endpoint (`/orgs/{org}/copilot/billing/seats`) returned a non-success status. Common causes:

- **Token scope:** Personal access tokens need `manage_billing:copilot` (classic) or the equivalent fine-grained `Copilot Business → Read` permission.
- **Admin role:** You must be an **owner / billing manager** of the org. Even with the right scope, a member-role token will get HTTP 403.
- **No Copilot subscription on the org:** A 404 means the org has no Copilot Business/Enterprise plan.

When this happens the toolkit prints a `⚠ WARNING: seats fetch returned HTTP ...` line per affected org and a final summary.

**Backfill behaviour:** Even when seats fail, `Last Activity Date` is now backfilled from the user's **maximum NDJSON day** for users who appear in NDJSON, and `Days Inactive` is recomputed from it. This means active users will still have a populated last-activity column. `Seat Assigned Date` has no NDJSON equivalent and will remain blank until the seats API works.

**Caveat:** Users who are licensed but **inactive** (no NDJSON activity) will be missing from the report entirely when seats are unavailable — so Team Adoption Rate and Needs Enablement counts for those orgs are unreliable. Re-run with a token that has the right scope and admin role to fix this.

### Q: `chat_interactions` / `agent_interactions` show 0 even though `used_chat: true` / `used_agent: true`.
**A:** Possible causes:

1. **Field-name mismatch** — GitHub occasionally renames or restructures NDJSON fields. Run with `--debug` and inspect the printed `first record top-level keys` to confirm the field names match what the script expects (`chat_panel_ask_mode`, `chat_panel_edit_mode`, `chat_panel_plan_mode`, `chat_panel_custom_mode`, `chat_panel_agent_mode`).
2. **Chat used only via surfaces that don't populate panel-mode counters** — for example, inline chat suggestions in some IDEs may set `used_chat: true` without incrementing the panel-mode counts.
3. **Window has no chat days** — `used_chat: true` is sticky across the window; the counter may still be zero on the specific days returned.

### Q: How do I capture the raw API payloads for debugging?
**A:** Run the report with `--debug`:

```
python copilot_productivity_report.py --orgs my-org --debug
```

For each org the script will:

- Save the raw NDJSON download to `./debug_<org>_<timestamp>.ndjson`
- Save the raw seats JSON to `./debug_seats_<org>_<timestamp>.json`
- Print the first NDJSON record's top-level keys and any chat/agent-related fields

These files contain user-identifying data and are already in `.gitignore` (`debug_*.ndjson`, `debug_*.json`); delete them after you're done diagnosing.

---

## 9. Practical Calculation Guide

### Step 1 — Build the base user-day dataset

```text
One NDJSON row
= one user
× one day
× one organization or enterprise scope
```

### Step 1.5 — Report column name mapping

The productivity report uses shortened column names for readability. Here is the exact mapping between API fields and report columns:

| Report Column | API Field / Source | Type / Description |
|---|---|---|
| `code_generations` | `code_generation_activity_count` | Sum across 28 days |
| `code_acceptances` | `code_acceptance_activity_count` | Sum across 28 days |
| `loc_suggested` | `loc_suggested_to_add_sum` | Sum across 28 days |
| `loc_added` | `loc_added_sum` | Sum across 28 days |
| `loc_deleted` | `loc_deleted_sum` | Sum across 28 days |
| `active_days` | Count of distinct `day` records per user | Derived |
| `seat_assigned_date` | `/orgs/{org}/copilot/billing/seats` → `seats[].created_at` | Seat assignment date (`YYYY-MM-DD`) |
| `last_activity_date` | `/orgs/{org}/copilot/billing/seats` → `seats[].last_activity_at` | Last Copilot editor activity date; empty if never used |
| `days_inactive` | Derived from `last_activity_at` and the current date | **Calendar days since the user's last Copilot use.** `0` means they used Copilot today. `"Never"` if the seat has never been activated. Uses today as the floor so the result reflects calendar reality (GitHub's NDJSON is typically 1-2 days behind real-time). **Note:** `last_activity_at` from the seats endpoint reflects real-time IDE telemetry — it can be more recent than the NDJSON window end, and updates the moment the user opens an IDE with Copilot. If you want "how many days in the 28-day window did this user not actively prompt Copilot," use **`inactive_days_in_window`** instead. |
| `inactive_days_in_window` | `REPORT_DAYS − active_days` (clamped to ≥ 0) | **Days within the 28-day reporting window where the user did NOT have NDJSON-recorded Copilot prompts.** This is the productivity-report-aligned metric: a user with `active_days = 3` shows `inactive_days_in_window = 25`. Useful for spotting low-usage licensed users even when `days_inactive` is 0 (i.e. the seat is "active" via IDE telemetry but the user rarely prompts Copilot). |
| `adoption_rate_pct` | `active_days / 28 × 100` | Derived (%) |
| `acceptance_rate_pct` | `code_acceptances / code_generations × 100` | Derived (%) |
| `copilot_contribution_pct` | `min(loc_suggested / loc_added, 1.0) × 100` | Derived (%, capped at 100) |
| `net_loc_change` | `loc_added − loc_deleted` | Derived |
| `chat_interactions` | Sum of `chat_panel_ask_mode` + `edit_mode` + `plan_mode` + `custom_mode` | Derived (excludes agent mode) |
| `agent_interactions` | `chat_panel_agent_mode` | Sum across 28 days |
| `engagement_depth` | `Σ user_initiated_interaction_count` (= `total_interactions`); covers active prompts across all Copilot surfaces, not just the chat panel | Derived |
| `estimated_time_saved_hrs` | `code_acceptances × 5 / 60` | Derived (hours) |

> **Why `_pct`?** Columns ending in `_pct` are calculated **percentages**, not raw API values. They help interpret the data without manual math.

### Workbook sheet reference

The generated Excel workbook contains four customer-facing worksheets. Use the sheet that matches the level of analysis needed:

| Worksheet | What it contains | Best use | Key notes |
|---|---|---|---|
| **User Productivity** | One row per user per organization, including the new `seat_assigned_date`, `last_activity_date`, and `days_inactive` columns | Per-org analysis, org-specific follow-up, and validating how a user appears within each organization | A user who belongs to two organizations appears as two rows |
| **Unique Users** | One row per distinct `user_login` across all organizations; `organization` is renamed to `organizations` and contains a comma-separated list when needed | Customer-wide adoption and ROI conversations where users should not be double-counted | Volumetric metrics are summed; `active_days` uses the max across orgs capped at 28; rates are recomputed from merged totals; `seat_assigned_date` is earliest, `last_activity_date` is latest; health profile is reclassified; sorted by `engagement_depth` descending |
| **Needs Enablement** | Users from the deduped list where `health_profile == "Needs Enablement"`, including `user_login`, `organizations`, date/inactivity fields, activity counts, and `health_notes` | Coaching, training, and license-adoption follow-up | Sorted by `days_inactive` descending: users who have never used Copilot appear first, followed by the most stale users; if empty, the sheet shows `No users currently flagged for enablement` |
| **Team Summary** | Rollup KPIs, health distribution, top engaged users, and enablement counts | Executive summary and customer readout | Overview includes `Unique Users` and `Unique Active Users`; health distribution uses the deduped unique-user list; top users by engagement depth shows the top 10 unique users and excludes zero-engagement users; enablement detail is shown in the **Needs Enablement** sheet |

> [!NOTE]
> In **Team Summary**, `Unique Users` means distinct `user_login`, and `Unique Active Users` means distinct `user_login` with `active_days > 0`. The previous inline enablement list is intentionally replaced by a count and a pointer to the **Needs Enablement** worksheet.

### Step 2 — Derive user-level KPIs

```text
active_days              = count(distinct day)
adoption_rate_pct        = active_days / 28 * 100
acceptance_rate_pct      = code_acceptance_activity_count / code_generation_activity_count * 100
engagement_depth         = user_initiated_interaction_count  (= total_interactions)
feature_breadth          = count(used_chat, used_agent, used_cli, code_review flags set to true)
loc_changed_with_ai      = loc_added_sum + loc_deleted_sum
copilot_contribution_pct = min(loc_suggested_to_add_sum / loc_added_sum, 1.0) * 100
estimated_time_saved_hrs = code_acceptance_activity_count * 5 / 60
```

### Step 3 — Aggregate to team or org level

```text
Team totals
= sum(volume metrics)
= count(distinct users) for active-user metrics
= recompute rates from totals, not averages of averages
```

### Step 4 — Interpret patterns, not isolated numbers

```text
High adoption + healthy acceptance + broad feature use
= strong maturity signal

Low adoption + low interactions
= license without workflow integration

High LOC gap + strong agent usage
= advanced agentic development pattern
```

---

## 10. References

- [GitHub Copilot Usage Metrics — Official Docs](https://docs.github.com/en/enterprise-cloud@latest/copilot/reference/copilot-usage-metrics/copilot-usage-metrics)
- [Copilot Usage Metrics API](https://docs.github.com/en/rest/copilot/copilot-metrics)
- [REST API Endpoints for Copilot Usage Metrics](https://docs.github.com/en/rest/copilot/copilot-usage-metrics)
- [Example Schema for Copilot Usage Metrics](https://docs.github.com/en/enterprise-cloud@latest/copilot/reference/copilot-usage-metrics/example-schema)
- [Team-level Copilot Usage Metrics](https://docs.github.com/en/enterprise-cloud@latest/copilot/reference/copilot-usage-metrics/team-level-metrics)

---

## Final takeaway

```text
┌──────────────────────────────────────────────────────────────┐
│ The most important productivity truth about Copilot metrics │
├──────────────────────────────────────────────────────────────┤
│ Do not ask only: “How many lines did Copilot write?”        │
│ Ask instead:                                                 │
│   • Are people using it?                                     │
│   • Are the suggestions useful?                              │
│   • Has workflow behavior improved?                          │
│   • Are teams moving from simple chat to agentic execution?  │
└──────────────────────────────────────────────────────────────┘
```

That is the difference between **usage reporting** and **productivity insight**.