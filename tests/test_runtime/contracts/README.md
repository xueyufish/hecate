"""Contract tests for infrastructure swapability (runtime-pluggability).

Each contract module parameterizes the same behavior assertions over a
production implementation and a hand-written contract fake. If a new
implementation satisfies the contracts, it can replace the existing one
without touching any Worker — that is the swapability guarantee.

README rule: any new infrastructure implementation MUST be registered in
the parameter lists here (or in a sibling contract module).
"""
