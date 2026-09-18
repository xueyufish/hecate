## ADDED Requirements

### Requirement: Citation badge display in studio

The citation map recorded on assistant messages (cited and unresolved chunk markers) SHALL be surfaced in studio session conversation and replay views as a badge display on the message: the cited marker count, the unresolved marker count, and the individual marker identifiers on demand. Unresolved markers SHALL be visually distinguished from resolved ones. Messages without citation metadata SHALL render without badges. The display is read-only: no scoring state, highlighting, or disposition information is included in this stage.

#### Scenario: Message with citations renders badge

- **WHEN** a studio session or replay view renders an assistant message that carries citation metadata
- **THEN** the message SHALL display a citation badge with the cited count, and the individual marker identifiers SHALL be viewable on demand

#### Scenario: Unresolved markers distinguished

- **WHEN** a citation map contains unresolved markers (never issued in the session)
- **THEN** those markers SHALL be visually distinguished from resolved markers in the badge detail

#### Scenario: Message without citations renders nothing

- **WHEN** an assistant message has no citation metadata (provenance disabled for its node)
- **THEN** no citation badge SHALL be rendered for that message
