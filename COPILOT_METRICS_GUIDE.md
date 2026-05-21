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
| Engagement Depth | `Σ(chat_interactions + agent_interactions)` | Varies | Depth of real usage, not just seat possession |
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
| **Engagement Depth** | `chat_interactions + agent_interactions` | How deeply are features being used? | `≥ 20` |
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

---

## 9. Practical Calculation Guide

### Step 1 — Build the base user-day dataset

```text
One NDJSON row
= one user
× one day
× one organization or enterprise scope
```

### Step 2 — Derive user-level KPIs

```text
active_days              = count(distinct day)
adoption_rate_pct        = active_days / 28 * 100
acceptance_rate_pct      = code_acceptance_activity_count / code_generation_activity_count * 100
engagement_depth         = chat_interactions + agent_interactions
feature_breadth          = count(used_chat, used_agent, used_cli, code_review flags set to true)
loc_changed_with_ai      = loc_added_sum + loc_deleted_sum
copilot_contribution_pct = min(loc_suggested_to_add_sum / loc_added_sum, 1.0) * 100
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