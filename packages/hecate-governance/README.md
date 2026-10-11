# hecate-governance

Execution governance primitives shared by the platform control plane and the
standalone runner host (`hecate-runner`):

- `ActionPrincipal` — verified identity envelope (actor, agent principal,
  on-behalf-of, delegation, data domains, deployment scope). Derived only from
  server-verified credentials or trusted host stamps; self-asserted request
  fields never enter the envelope.
- Invocation identity — logical-call-scoped `invocation_id` generated at the
  dispatch boundary, stable across attempts/restarts, persisted with its
  arguments digest and principal fingerprint; same id with different digest or
  principal is a conflict.
- Controlled action records — tool, arguments digest, target resources, side
  effect class, decision, approval reference and execution receipt vocabulary
  with distinct error categories (schema invalid / business rejected /
  technical failure / outcome unknown / policy denied).

The package intentionally depends only on `pydantic` and the standard library:
it must be installable by the standalone host without the platform
application, ORM or any backend SDK. Platform-side policy implementations
remain in `src/hecate/tools/policy/`; runner-side local profiles remain in
`packages/hecate-runner`.
