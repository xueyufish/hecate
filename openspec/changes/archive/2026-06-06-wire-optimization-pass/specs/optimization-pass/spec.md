## MODIFIED Requirements

### Requirement: GraphCompiler accepts optional optimization passes
GraphCompiler SHALL accept an optional `passes: list[OptimizationPass] | None = None` parameter in its constructor, defaulting to an empty list. After validation and CompiledGraph construction, GraphCompiler SHALL apply each pass sequentially via `optimize()`, passing the output of one pass as input to the next.

#### Scenario: Default no optimization
- **WHEN** GraphCompiler is created without passes parameter
- **THEN** it SHALL compile without optimization (current behavior — empty pass list)

#### Scenario: Single optimization pass
- **WHEN** GraphCompiler is created with `passes=[DeadNodeElimination()]`
- **THEN** it SHALL apply DeadNodeElimination.optimize() after validation and return the optimized CompiledGraph

#### Scenario: Multiple optimization passes in pipeline
- **WHEN** GraphCompiler is created with `passes=[DeadNodeElimination(), ParallelBranchDetection()]`
- **THEN** it SHALL apply DeadNodeElimination.optimize() first, then pass the result to ParallelBranchDetection.optimize()

#### Scenario: Pass ordering is preserved
- **WHEN** passes=[P, Q] where P and Q are OptimizationPass implementations
- **THEN** P.optimize() SHALL be called before Q.optimize()
