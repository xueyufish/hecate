## ADDED Requirements

### Requirement: OptimizationPass ABC defines pluggable graph optimization
The engine SHALL define an `OptimizationPass` ABC in `engine/optimization.py` with abstract method: `optimize(graph: CompiledGraph) -> CompiledGraph`.

#### Scenario: Optimize returns new graph
- **WHEN** `optimize(graph)` is called with a CompiledGraph
- **THEN** it SHALL return a new CompiledGraph (not modify the input)

### Requirement: DeadNodeElimination removes unreachable nodes
A `DeadNodeElimination` SHALL implement OptimizationPass by removing nodes not reachable from the entry point.

#### Scenario: Remove unreachable nodes
- **WHEN** a graph has nodes A (entry), B, C where C is unreachable
- **THEN** the optimized graph SHALL contain only A and B

#### Scenario: Preserve reachable nodes
- **WHEN** a graph has all nodes reachable
- **THEN** the optimized graph SHALL be identical to the input

#### Scenario: No entry point
- **WHEN** a graph has no entry point
- **THEN** the optimized graph SHALL be identical to the input (no elimination possible)

### Requirement: ParallelBranchDetection marks independent branches
A `ParallelBranchDetection` SHALL implement OptimizationPass by detecting branches that can execute in parallel and marking them in graph metadata.

#### Scenario: Detect parallel branches
- **WHEN** a graph has entry node A with edges to B and C (no dependencies between B and C)
- **THEN** the optimized graph SHALL have metadata["parallel_branches"] containing [B, C]

#### Scenario: No parallel branches
- **WHEN** a graph is linear (A→B→C)
- **THEN** the optimized graph SHALL have no parallel_branches metadata

### Requirement: GraphCompiler accepts optional optimization passes
GraphCompiler SHALL accept an optional `passes` parameter in its constructor, defaulting to empty list.

#### Scenario: Default no optimization
- **WHEN** GraphCompiler is created without passes parameter
- **THEN** it SHALL compile without optimization (current behavior)

#### Scenario: With optimization passes
- **WHEN** GraphCompiler is created with passes=[DeadNodeElimination()]
- **THEN** it SHALL apply the passes after validation
