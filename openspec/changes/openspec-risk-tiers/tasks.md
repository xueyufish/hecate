# Tasks

## 1. Define the policy

- [x] 1.1 Update `AGENTS.md` with conservative lightweight/full criteria, minimum evidence, and proportional archive review; review the risk boundary against existing repository gates.
- [x] 1.2 Update the development workflow guide with the decision table, examples, escalation rule, schema selection, and archive expectations; verify the documented local links.

## 2. Make the lightweight flow executable

- [x] 2.1 Add the project-local `lightweight` schema with proposal and tasks templates, preserving `spec-driven` as the default; pass `openspec schema validate lightweight --verbose`.
- [x] 2.2 Update `openspec-propose` to classify changes from repository policy and choose the lightweight schema only when all criteria are met; review its schema-selection instructions.
- [x] 2.3 Create and strictly validate a smoke change through the lightweight schema, including the intentional `skip_specs` declaration; remove the temporary smoke change afterwards.

## 3. Verify

- [x] 3.1 Run applicable docs and OpenSpec validation checks, record exact commands/results, and confirm the main worktree remains untouched.
