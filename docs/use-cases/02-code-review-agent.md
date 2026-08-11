# Use Case: Code Review Agent

> **30 minutes to build, halves PR turnaround**

A multi-agent system that reviews pull requests for security, style, and correctness, then aggregates findings into a single PR comment. Combines **MCP + Multi-Agent orchestration + Evaluation**.

---

## The scenario

A 50-engineer team merges ~200 PRs/week. Each PR waits 4-12 hours for human review. The team wants:

- **40%** reduction in reviewer load (bots catch the obvious stuff)
- **Consistent feedback** on security and style (no reviewer fatigue)
- **Aggregated findings** as a single PR comment (not 5 separate bot comments)

## The architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  Pull request opened                                              │
│         │                                                        │
│         │ GitHub webhook → Hecate workflow trigger               │
│         ▼                                                        │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  3 parallel reviewer agents (MCP filesystem + GitHub PR API) │ │
│  │                                                              │ │
│  │  ┌────────────┐  ┌────────────┐  ┌────────────┐             │ │
│  │  │  Security  │  │   Style    │  │ Correctness│             │ │
│  │  │  Reviewer  │  │  Reviewer  │  │  Reviewer  │             │ │
│  │  └─────┬──────┘  └─────┬──────┘  └─────┬──────┘             │ │
│  │        │              │              │                     │ │
│  └────────┼──────────────┼──────────────┼─────────────────────┘ │
│           └──────────────┼──────────────┘                       │
│                          ▼                                     │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │  Aggregator agent — deduplicates, prioritizes, formats       │ │
│  │  → single PR comment (no spam)                              │ │
│  └─────────────────────────┬───────────────────────────────────┘ │
│                            ▼                                   │
│              GitHub PR comment via MCP                          │
└──────────────────────────────────────────────────────────────────┘
```

**Features combined**:

- [MCP Tool Integration](../tutorials/03-mcp-integration.md) — GitHub MCP server for PR/filesystem access
- [Multi-Agent Orchestration](../tutorials/04-multi-agent.md) — 3 parallel reviewers + 1 aggregator
- [Evaluate an Agent](../tutorials/08-agent-evaluation.md) — regression-test the prompts to prevent quality drift

---

## 10-line tldr

```bash
# 1. Register GitHub MCP server (gives filesystem + PR tools)
curl -X POST http://localhost:8000/api/plugins/create \
  -d '{"manifest": {"name": "github", "type": "mcp", "entry": "mcp://https://mcp.github.com/sse"}}'

# 2. Create the workflow (paste JSON below)
curl -X POST http://localhost:8000/api/workflows/import -d @code-review-workflow.json

# 3. Wire up the GitHub webhook
curl -X POST https://api.github.com/repos/<owner>/<repo>/hooks \
  -d '{"events": ["pull_request"], "url": "https://hecate.internal/webhooks/github"}'
```

The `code-review-workflow.json` is the workflow definition shown in Step 3 below.

---

## The build

### Step 1 — Register the GitHub MCP server

GitHub provides an official MCP server at `https://mcp.github.com/sse` (or you can self-host [`github/github-mcp-server`](https://github.com/github/github-mcp-server)).

```bash
curl -X POST http://localhost:8000/api/plugins/create \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "manifest": {
      "name": "github",
      "version": "1.0.0",
      "type": "mcp",
      "entry": "mcp://https://mcp.github.com/sse"
    }
  }'
```

Enable it to fetch the AgentCard and discover tools:

```bash
curl -X POST http://localhost:8000/api/plugins/<PLUGIN_ID>/enable \
  -H "Authorization: Bearer dev-key-change-me"

hecate tool list --source mcp | grep github
# → mcp_github_get_pull_request         | mcp | Get PR details
# → mcp_github_get_pull_request_files   | mcp | List files in PR
# → mcp_github_get_file_contents        | mcp | Read file at ref
# → mcp_github_create_review_comment    | mcp | Post PR review comment
# → mcp_github_list_pull_request_reviews| mcp | List existing reviews
```

For a deeper dive on MCP, see [MCP Tool Integration](../tutorials/03-mcp-integration.md).

### Step 2 — Create the three reviewer agents

Each agent has a tightly scoped persona and reads only the files it needs.

#### Security Reviewer

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Security Reviewer",
    "persona": "You are a security engineer. Review the diff for: SQL injection, XSS, SSRF, auth/authz bugs, secret leakage, unsafe deserialization, path traversal, command injection. Be specific: cite file path + line + offending code. Skip non-issues (style, naming, etc.) — those are for other reviewers.",
    "model_config": {"model": "gpt-4o", "temperature": 0.1},
    "tools": ["mcp_github_get_pull_request_files", "mcp_github_get_file_contents"]
  }'
```

#### Style Reviewer

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Style Reviewer",
    "persona": "You review code for maintainability: naming clarity, function size, duplication, missing tests for new logic, unclear error handling. Skip security and correctness — other reviewers handle those.",
    "model_config": {"model": "gpt-4o-mini", "temperature": 0.2},
    "tools": ["mcp_github_get_pull_request_files", "mcp_github_get_file_contents"]
  }'
```

#### Correctness Reviewer

```bash
curl -X POST http://localhost:8000/api/agents \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Correctness Reviewer",
    "persona": "You review code for correctness: edge cases, off-by-one errors, race conditions, error swallowing, missing return values, type mismatches. Be conservative: only flag what is clearly wrong, not what is debatable.",
    "model_config": {"model": "gpt-4o", "temperature": 0.1},
    "tools": ["mcp_github_get_pull_request_files", "mcp_github_get_file_contents"]
  }'
```

Save each `id` as `SECURITY_ID`, `STYLE_ID`, `CORRECTNESS_ID`.

### Step 3 — Create the workflow

Build the workflow either in the [Visual Canvas](../tutorials/11-visual-canvas.md) or via JSON:

```bash
cat > code-review-workflow.json <<'EOF'
{
  "name": "code-review",
  "entry": "fanout",
  "nodes": [
    {
      "id": "fanout",
      "type": "router",
      "config": {"fan_out_to": ["security", "style", "correctness"]}
    },
    {
      "id": "security",
      "type": "agent",
      "agent_id": "SECURITY_ID",
      "input_mapping": {
        "files": "$workflow.input.pull_request.files",
        "diff": "$workflow.input.pull_request.diff"
      }
    },
    {
      "id": "style",
      "type": "agent",
      "agent_id": "STYLE_ID",
      "input_mapping": {
        "files": "$workflow.input.pull_request.files",
        "diff": "$workflow.input.pull_request.diff"
      }
    },
    {
      "id": "correctness",
      "type": "agent",
      "agent_id": "CORRECTNESS_ID",
      "input_mapping": {
        "files": "$workflow.input.pull_request.files",
        "diff": "$workflow.input.pull_request.diff"
      }
    },
    {
      "id": "aggregator",
      "type": "agent",
      "persona": "You are an aggregator. You receive findings from three reviewers (security, style, correctness). Your job: (1) deduplicate findings that overlap, (2) prioritize them — security > correctness > style, (3) format as a single PR comment with sections per severity, (4) skip nitpicks. Output: a Markdown comment body.",
      "model_config": {"model": "gpt-4o-mini", "temperature": 0.2},
      "tools": ["mcp_github_create_review_comment"]
    }
  ],
  "edges": [
    {"from": "fanout", "to": "security"},
    {"from": "fanout", "to": "style"},
    {"from": "fanout", "to": "correctness"},
    {"from": "security", "to": "aggregator", "merge": "concat"},
    {"from": "style", "to": "aggregator", "merge": "concat"},
    {"from": "correctness", "to": "aggregator", "merge": "concat"}
  ]
}
EOF

# Substitute the agent IDs
sed -i "s/SECURITY_ID/$SECURITY_ID/; s/STYLE_ID/$STYLE_ID/; s/CORRECTNESS_ID/$CORRECTNESS_ID/" \
  code-review-workflow.json

# Import
curl -X POST http://localhost:8000/api/workflows/import \
  -H "Authorization: Bearer dev-key-change-me" \
  -H "Content-Type: application/json" \
  -d @code-review-workflow.json
```

For the full DSL reference, see [Graph DSL](../reference/graph-dsl.md).

### Step 4 — Wire up the GitHub webhook

```bash
curl -X POST https://api.github.com/repos/<owner>/<repo>/hooks \
  -H "Authorization: Bearer <GITHUB_TOKEN>" \
  -H "Accept: application/vnd.github+json" \
  -d '{
    "name": "web",
    "events": ["pull_request"],
    "config": {
      "url": "https://hecate.internal/webhooks/github",
      "content_type": "json",
      "secret": "<HEX_SHARED_SECRET>"
    }
  }'
```

When a PR is opened/synchronized, GitHub POSTs to Hecate's webhook, which triggers the workflow.

For a webhook implementation reference, see [Enable MCP Server](../how-to/enable-mcp-server.md).

---

## The evaluation

Track three metrics:

### 1. **Catch rate**

How often does the bot find a real issue (security or correctness) that humans would also flag?

```bash
hecate eval run \
  --dataset code-review-eval \
  --evaluator overlap-with-human-review \
  --workflow code-review
```

Run this against a held-out set of 50 historical PRs with known human reviews. Target: **>70%** overlap on security findings; **>50%** on style findings.

### 2. **False-positive rate**

How often does the bot flag something humans consider a non-issue? Built-in evaluator:

```bash
hecate eval run \
  --dataset code-review-eval \
  --evaluator precision \
  --workflow code-review
```

Target: **<20%** false positives on style; **<5%** on security.

### 3. **Reviewer time saved**

```sql
SELECT
  AVG(time_to_first_review) AS avg_review_time,
  AVG(time_to_merge) AS avg_merge_time,
  COUNT(*) FILTER (WHERE first_reviewer = 'hecate-bot') AS bot_first
FROM pull_requests
WHERE created_at > NOW() - INTERVAL '30 days';
```

Target: **time_to_first_review drops to <15 minutes** (bot reviews instantly); **reviewer-load drops 40%**.

For evaluation patterns, see [Evaluate an Agent](../tutorials/08-agent-evaluation.md).

---

## Adapt it

| Variation | Change |
|---|---|
| **Per-language reviewers** | Add a router that picks reviewers based on the file extension (Python → py-reviewer, TS → ts-reviewer) |
| **Auto-fix suggestions** | Bind a `mcp_github_create_review_comment` + `mcp_github_suggest_changes` pair so the bot can propose diffs |
| **Severity escalation** | For `severity: high` findings, page on-call via PagerDuty MCP; for `severity: low`, just comment |
| **Custom company rules** | Add a 4th reviewer with your internal style guide as the persona + a knowledge base of past review comments |
| **Learning loop** | Add an evaluation dataset of past PRs; have the aggregator cite "this matches issue #N from past reviews" so engineers can search precedents |

---

## When NOT to use this pattern

- **First-pass review for junior engineers** — they need to learn by reviewing themselves
- **Architecture-level decisions** — multi-file refactors, library upgrades — out of scope for diff reviewers
- **Subjective design feedback** — "should this be a microservice?" needs human judgment

---

## What's next

- **[Customer Support Bot](01-customer-support-bot.md)** — similar pattern for tickets
- **[Research Team](03-research-team.md)** — multi-agent collaboration for research
- **[Multi-Agent Orchestration deep dive](../tutorials/04-multi-agent.md)**
- **[Evaluate an Agent](../tutorials/08-agent-evaluation.md)** — how to prevent prompt regressions