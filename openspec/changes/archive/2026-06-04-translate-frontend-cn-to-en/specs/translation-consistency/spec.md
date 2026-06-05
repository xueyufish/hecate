## ADDED Requirements

### Requirement: All Chinese UI text translated to English
The frontend codebase SHALL contain zero Chinese characters in UI-facing strings (labels, placeholders, alerts, status messages).

#### Scenario: No Chinese characters in source files
- **WHEN** searching `web/src/` for Chinese characters (Unicode range `\u4e00-\u9fff`)
- **THEN** zero matches SHALL be found in `.ts` and `.tsx` files

### Requirement: Consistent terminology across files
The same Chinese term SHALL be translated to the same English term in all files.

#### Scenario: Terminology consistency
- **WHEN** comparing translations of the same Chinese term across different files
- **THEN** all instances SHALL use identical English wording

### Requirement: Edge label logic preserved
The handoff edge label SHALL be consistently translated and all equality checks updated.

#### Scenario: Handoff edge detection works after translation
- **WHEN** a handoff edge is created with the new English label
- **THEN** all equality checks (`===` comparisons) in `canvas-area.tsx` and `dsl-bridge.ts` SHALL match the new label
