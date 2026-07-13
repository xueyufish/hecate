---
name: openspec-archive-change
description: Archive a completed change in the experimental workflow. Use when the user wants to finalize and archive a change after implementation is complete.
license: MIT
compatibility: Requires openspec CLI.
metadata:
  author: openspec
  version: "1.0"
  generatedBy: "1.3.1"
---

Archive a completed change in the experimental workflow.

**Input**: Optionally specify a change name. If omitted, check if it can be inferred from conversation context. If vague or ambiguous you MUST prompt for available changes.

**Steps**

1. **If no change name provided, prompt for selection**

   Run `openspec list --json` to get available changes. Use the **AskUserQuestion tool** to let the user select.

   Show only active changes (not already archived).
   Include the schema used for each change if available.

   **IMPORTANT**: Do NOT guess or auto-select a change. Always let the user choose.

2. **Check all status in one call**

   Run a SINGLE bash command that collects all status information at once:

   ```bash
   echo "=== ARTIFACTS ===" && openspec status --change "<name>" --json 2>&1 && echo "=== TASKS ===" && grep -c '^\- \[ \]' openspec/changes/<name>/tasks.md 2>&1; echo "---"; grep -c '^\- \[x\]' openspec/changes/<name>/tasks.md 2>&1 && echo "=== SPECS ===" && ls openspec/changes/<name>/specs/ 2>&1 && echo "=== MAIN SPECS ===" && ls openspec/specs/ 2>&1 | grep -i "<capability>"
   ```

   Parse the combined output to understand:
   - Artifact completion status (all done or which are incomplete)
   - Task counts (complete vs incomplete)
   - Delta spec existence and whether main spec already exists

   **If any artifacts are not `done` OR incomplete tasks found:**
   - Display warning listing incomplete artifacts and task count
   - Use **AskUserQuestion tool** to confirm user wants to proceed
   - Proceed if user confirms

3. **Sync delta specs if needed**

   If delta specs exist and main spec doesn't exist yet:
   - Copy delta spec to main spec in ONE bash call
   - Proceed to archive

   If main spec already exists and is identical:
   - Skip sync, proceed to archive

4. **Update catalog and roadmap + archive — batch reads then edits**

   Read ALL files needed for catalog/roadmap updates in PARALLEL:
   - `docs/features/feature-catalog.md` (relevant lines)
   - `docs/features/roadmap.md` (relevant lines)
   - `openspec/changes/<name>/proposal.md` (to identify affected features)

   Then perform ALL edits in sequence (no re-reads between edits):
   - Edit feature-catalog.md (mark ✅, update descriptions)
   - Edit roadmap.md (mark ✅, update P3 count, update milestone checkboxes)

   Then perform the archive:
   ```bash
   mkdir -p openspec/changes/archive && mv openspec/changes/<name> openspec/changes/archive/YYYY-MM-DD-<name>
   ```

   **Check if target already exists:**
   - If yes: Fail with error, suggest renaming existing archive or using different date
   - If no: Move the change directory to archive

5. **Display summary**

    Show archive completion summary including:
    - Change name
    - Schema that was used
    - Archive location
    - Whether specs were synced (if applicable)
    - Whether catalog/roadmap were updated (if applicable)
    - Note about any warnings (incomplete artifacts/tasks)

**Output On Success**

```
## Archive Complete

**Change:** <change-name>
**Schema:** <schema-name>
**Archived to:** openspec/changes/archive/YYYY-MM-DD-<name>/
**Specs:** ✓ Synced to main specs (or "No delta specs" or "Sync skipped")
**Catalog & Roadmap:** ✓ Updated (or "Already up to date")

All artifacts complete. All tasks complete.
```

**Guardrails**
- Always prompt for change selection if not provided
- Use artifact graph (openspec status --json) for completion checking
- Don't block archive on warnings - just inform and confirm
- Preserve .openspec.yaml when moving to archive (it moves with the directory)
- Show clear summary of what happened
- If sync is requested, use openspec-sync-specs approach (agent-driven)
- If delta specs exist, always run the sync assessment and show the combined summary before prompting
- Always check and update `docs/features/feature-catalog.md` and `docs/features/roadmap.md` before archiving — this is MANDATORY, not optional
- **Minimize tool calls**: Combine related bash commands into one call; batch parallel reads; avoid re-reading files between edits
