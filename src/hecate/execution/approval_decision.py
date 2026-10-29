"""Human approval decisions for durable platform approval waits.

One decision closes the loop in two durable phases:

1. **Decision** — after server-side authorization (active workspace, task
   visibility, ADMIN role enforced at the API, and the self-approval ban
   against the task's persisted initiator), the ledger's CAS ``decide``
   transitions the binding exactly once and the decision operation row
   (unique ``operation_id``) commits in the same transaction. Replays
   return the recorded facts; a different request under the same
   operation id conflicts.
2. **Resume** — a ``resume`` command with a deterministic id derived
   from the operation id drives the existing wake gate and single-use
   token. A crash between the phases leaves the operation row with no
   resume receipt; the named recovery consumer re-issues the resume from
   the stored row — never from request data — and the command recorder
   plus the token CAS make that replay safe.

Fact separation: ``applied`` on the resume receipt says the platform
accepted the recorded decision as a wake; the decision facts say what
was decided and by whom. Neither is task success.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from hecate.contracts.execution.durable import CommandState, ControlCommandKind
from hecate.models.approval import ApprovalAuthorityModel, ApprovalDecisionModel

logger = logging.getLogger(__name__)

APPROVAL_DECISION_RESUME_PREFIX = "approval-resume-"


class ApprovalDecisionError(Exception):
    """Structured decision failure surfaced as a 4xx by the API layer."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def resume_command_id_for(operation_id: str) -> str:
    """The deterministic resume identity for one decision operation."""

    return f"{APPROVAL_DECISION_RESUME_PREFIX}{hashlib.sha256(operation_id.encode()).hexdigest()}"


def _request_digest(**facts: Any) -> str:
    payload = json.dumps(facts, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ApprovalDecisionService:
    """Decide, record and resume durable platform approval waits."""

    def __init__(
        self,
        db: AsyncSession,
        *,
        store: Any,
        recorder: Any,
        backend: str,
        ledger_source: str,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        worker: Any | None = None,
    ) -> None:
        self._db = db
        self._store = store
        self._recorder = recorder
        self._backend = backend
        self._ledger_source = ledger_source
        self._session_factory = session_factory
        # A bound worker makes the resume phase requeue only (the worker
        # cycle drives the re-dispatch); without one the wake spawns the
        # background dispatch exactly like submit does.
        self._worker = worker

    @classmethod
    def of(cls, service: Any) -> ApprovalDecisionService:
        """Compose from an existing TaskControlService's wiring."""

        return cls(
            service._db,
            store=service._store,
            recorder=service._recorder,
            backend=service._backend,
            ledger_source=service._ledger_source,
            session_factory=service._session_factory,
            worker=service._worker,
        )

    # -- query ----------------------------------------------------------------

    async def get_approval_view(
        self, workspace_id: uuid.UUID, task_id: uuid.UUID, *, decided_by: str | None = None, can_admin: bool = False
    ) -> dict[str, Any]:
        """The whitelisted approval view: wait summary, decision, resume receipt.

        Read-only: the contract summary names the parked tool, risk level
        and target resources — never the raw arguments — and the decision
        facts come from the operation record when present. Observation
        only; nothing here issues a resume.
        """
        from hecate.execution.task_control import command_receipt_summary

        t_ref, wait, task_input, _state = await self._bound_wait(workspace_id, task_id)
        raw_contract = wait.get("contract_ref")
        contract_ref = raw_contract if isinstance(raw_contract, dict) else {}
        decision_row = await self._decision_row_for_task(workspace_id, task_id, contract_ref.get("approval_ref"))
        view: dict[str, Any] = {
            "task_id": str(task_id),
            "workspace_id": str(workspace_id),
            "wait": {
                "consumed": wait.get("consumed") is True,
                "expires_at": wait.get("wait_expires_at"),
                "wait_token": wait.get("wait_token"),
            },
            "contract": {
                "approval_ref": contract_ref.get("approval_ref"),
                "tool_name": contract_ref.get("tool_name"),
                "risk_level": contract_ref.get("risk_level"),
                "target_resources": list(contract_ref.get("target_resources") or []),
                "invocation_id": contract_ref.get("invocation_id"),
            },
            "decision": None,
            "revision": None,
            "can_decide": False,
            "read_only_reason": "ADMIN_REQUIRED",
            "authority": None,
        }
        current = await asyncio.to_thread(self._store.get_task_state, t_ref)
        view["revision"] = current.revision if current else None
        if can_admin and decided_by:
            try:
                task = await self._get_task(workspace_id, task_id)
                self._enforce_not_self_approved(task_input, decided_by, task.initiator_ref)
                await self._validate_wait(
                    t_ref,
                    current,
                    wait,
                    task_input,
                    approval_ref=contract_ref.get("approval_ref"),
                    wait_token=wait.get("wait_token"),
                    expected_revision=view["revision"],
                )
                # Observation never re-anchors or otherwise writes authority.
                from hecate.enterprise.governance_ledgers import SqlApprovalLedger

                binding = await SqlApprovalLedger(self._db).get(contract_ref["approval_ref"])
                try:
                    self._validate_binding(binding, wait, task_input, task_id, trusted=False)
                except ApprovalDecisionError as exc:
                    if exc.code != "ALREADY_DECIDED":
                        raise
                    view["read_only_reason"] = exc.code
                else:
                    view["can_decide"] = True
                    view["read_only_reason"] = None
                view["authority"] = {
                    "judgment": binding.judgment.value,
                    "decided_by": binding.decided_by,
                    "expires_at": binding.expires_at.isoformat() if binding.expires_at else None,
                }
            except ApprovalDecisionError as exc:
                view["read_only_reason"] = exc.code
        if decision_row is not None:
            receipt = None
            if decision_row.resume_command_id:
                record = await self._receipt(decision_row.resume_command_id)
                receipt = command_receipt_summary(record) if record else None
            view["decision"] = {
                "operation_id": decision_row.operation_id,
                "decision": decision_row.decision,
                "reason": decision_row.reason,
                "decided_by": decision_row.decided_by,
                "created_at": decision_row.created_at.isoformat() if decision_row.created_at else None,
                "resume_command_id": decision_row.resume_command_id,
                "resume_receipt": receipt,
            }
        return view

    # -- decision -------------------------------------------------------------

    async def submit_decision(
        self,
        workspace_id: uuid.UUID,
        task_id: uuid.UUID,
        *,
        operation_id: str,
        approval_ref: str,
        decision: str,
        reason: str,
        wait_token: str,
        expected_revision: int | None,
        decided_by: str,
    ) -> dict[str, Any]:
        """Record the authoritative decision, then drive the resume phase.

        Authorization (workspace visibility, ADMIN, self-approval ban) runs
        before any authority query or write. Returns the decision facts and
        the resume receipt separately.
        """
        normalized = decision.strip().lower()
        if normalized not in ("approve", "reject"):
            raise ApprovalDecisionError("INVALID_DECISION", "decision must be approve or reject")
        if not operation_id.strip() or len(operation_id) > 200 or len(reason) > 2000:
            raise ApprovalDecisionError("INVALID_REQUEST", "invalid operation id or reason length")
        try:
            decided_by = str(uuid.UUID(decided_by))
        except (TypeError, ValueError) as exc:
            raise ApprovalDecisionError("INVALID_DECIDED_BY", "a verified human identity is required") from exc
        # Authorize the responsibility record before even looking up an
        # operation. Replay must remain possible after the original wait
        # has ended or a newer approval has parked on this same Task.
        task = await self._get_task(workspace_id, task_id)
        t_ref, wait, task_input, _state = await self._bound_wait(workspace_id, task_id)
        self._enforce_not_self_approved(task_input, decided_by, task.initiator_ref)
        digest = _request_digest(
            workspace_id=str(workspace_id),
            task_id=str(task_id),
            approval_ref=approval_ref,
            decision=normalized,
            reason=reason,
            decided_by=decided_by,
            wait_token=wait_token,
            expected_revision=expected_revision,
        )
        existing = await self._decision_row(workspace_id, task_id, operation_id)
        if existing is not None:
            return await self._resume_recorded(existing, digest)
        current = await asyncio.to_thread(self._store.get_task_state, t_ref)
        await self._validate_wait(
            t_ref,
            current,
            wait,
            task_input,
            approval_ref=approval_ref,
            wait_token=wait_token,
            expected_revision=expected_revision,
        )
        ledger = self._approval_ledger()
        try:
            # The row lock orders decision/revocation writers on PostgreSQL;
            # the ledger CAS remains the arbitration on SQLite as well.
            await self._db.execute(
                select(ApprovalAuthorityModel)
                .where(
                    ApprovalAuthorityModel.approval_ref == approval_ref,
                    ApprovalAuthorityModel.deleted.is_(False),
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            try:
                binding = await ledger.get(approval_ref)
            except ValueError as exc:
                raise ApprovalDecisionError("UNTRUSTED_TIME", "approval authority time cannot be verified") from exc
            self._validate_binding(binding, wait, task_input, task_id, trusted=True)
            try:
                binding = await ledger.decide(
                    approval_ref, approved=normalized == "approve", reason=reason, decided_by=decided_by
                )
            except ValueError as exc:
                raise ApprovalDecisionError(
                    "ALREADY_DECIDED", "another operation already decided this approval"
                ) from exc
            row = ApprovalDecisionModel(
                operation_id=operation_id,
                approval_ref=approval_ref,
                task_id=task_id,
                workspace_id=workspace_id,
                decision=normalized,
                reason=reason,
                decided_by=decided_by,
                request_digest=digest,
                wait_token=wait_token,
                expected_revision=expected_revision,
                resume_command_id=None,
            )
            self._db.add(row)
            await self._db.flush()
            # Authority and the immutable operation/audit facts share THIS
            # session, not a ledger factory's independent transaction.
            await self._db.commit()
        except (IntegrityError, ApprovalDecisionError) as exc:
            await self._db.rollback()
            raced = await self._decision_row(workspace_id, task_id, operation_id)
            if raced is not None:
                return await self._resume_recorded(raced, digest)
            if isinstance(exc, ApprovalDecisionError) and exc.code != "ALREADY_DECIDED":
                raise
            raise ApprovalDecisionError("ALREADY_DECIDED", "another operation already decided this approval") from exc
        except Exception:
            await self._db.rollback()
            raise
        return await self._resume_recorded(row, digest, replay=False)

    async def _resume_recorded(self, row: ApprovalDecisionModel, digest: str, *, replay: bool = True) -> dict[str, Any]:
        facts = await self._replay_or_conflict(row, digest)
        if not replay:
            facts["decision"].pop("replay", None)
        if row.resume_command_id is None:
            try:
                facts["resume"] = await self._issue_resume(row, row.task_id, row.workspace_id)
                facts["decision"]["resume_command_id"] = row.resume_command_id
            except Exception:
                await self._db.rollback()
                facts["resume"] = {"state": "pending_recovery"}
        return facts

    # -- recovery -------------------------------------------------------------

    async def resume_once(self) -> int:
        """Complete phase two for decisions whose resume never committed.

        The named recovery consumer: decisions with no resume receipt get
        the deterministic resume re-issued from the stored row. Receipt
        replay plus the single-use token make this safe to run alongside
        the API path. Returns the number of resumes processed.
        """

        from hecate.core.database import async_session_factory as default_factory

        factory = self._session_factory or default_factory
        # Each operation owns a session. One failed/poisoned row cannot
        # poison the next row's transaction or hold writes across a wake.
        async with factory() as db:
            ids = list(
                await db.scalars(
                    select(ApprovalDecisionModel.id)
                    .where(
                        ApprovalDecisionModel.resume_command_id.is_(None),
                        ApprovalDecisionModel.deleted.is_(False),
                    )
                    .order_by(ApprovalDecisionModel.updated_at, ApprovalDecisionModel.id)
                    .limit(32)
                )
            )
        processed = 0
        for identity in ids:
            async with factory() as db:
                row = await db.get(ApprovalDecisionModel, identity)
                if row is None or row.resume_command_id is not None or row.deleted:
                    continue
                service = ApprovalDecisionService(
                    db,
                    store=self._store,
                    recorder=self._recorder,
                    backend=self._backend,
                    ledger_source=self._ledger_source,
                    session_factory=factory,
                    worker=self._worker,
                )
                try:
                    await service._issue_resume(row, row.task_id, row.workspace_id)
                except Exception:
                    await db.rollback()
                    # Rotate failed rows behind untouched pending work so
                    # one bounded batch cannot starve every later decision.
                    await db.execute(
                        update(ApprovalDecisionModel)
                        .where(ApprovalDecisionModel.id == identity)
                        .values(updated_at=datetime.now(UTC))
                    )
                    await db.commit()
                    logger.warning("approval resume recovery failed for operation %s", identity, exc_info=True)
                    continue
                processed += 1
        return processed

    # -- internals ------------------------------------------------------------

    async def _bound_wait(
        self, workspace_id: uuid.UUID, task_id: uuid.UUID
    ) -> tuple[Any, dict[str, Any], dict[str, Any], str]:
        """The task's durable wait and input, bound to this workspace.

        Visibility runs through the workspace-scoped task read (foreign,
        missing and soft-deleted tasks are indistinguishable). Anything but
        a live ``waiting_approval`` wait with a contract is read-only.
        """
        from hecate.execution.task_control import UNRECORDED_LIFECYCLE

        await self._get_task(workspace_id, task_id)
        t_ref = self._task_ref_of(workspace_id, task_id)
        record = await asyncio.to_thread(self._store.get_task_state, t_ref)
        raw = (record.extra or {}).get("wait") if record else None
        wait = raw if isinstance(raw, dict) else {}
        task_input = await asyncio.to_thread(self._store.get_task_input, t_ref) or {}
        state = record.lifecycle_state.value if record else UNRECORDED_LIFECYCLE
        return t_ref, wait, task_input, state

    def _task_ref_of(self, workspace_id: uuid.UUID, task_id: uuid.UUID) -> Any:
        from hecate.execution.task_control import task_ref_of

        return task_ref_of(task_id)

    async def _get_task(self, workspace_id: uuid.UUID, task_id: uuid.UUID) -> Any:
        return await self._task_control()._get_task(workspace_id, task_id)

    async def _validate_wait(
        self,
        t_ref: Any,
        current: Any,
        wait: dict,
        task_input: dict,
        *,
        approval_ref: str,
        wait_token: str,
        expected_revision: int | None,
    ) -> None:
        from hecate.contracts.execution.durable import TaskLifecycleState
        from hecate.execution.approval_wait_expiration import ApprovalWaitExpirer

        if current is None or current.lifecycle_state is not TaskLifecycleState.WAITING_APPROVAL:
            raise ApprovalDecisionError("NOT_WAITING_APPROVAL", "the task is not awaiting an approval decision")
        if wait.get("wake_kind") != "resume" or wait.get("consumed") is not False:
            raise ApprovalDecisionError("UNSUPPORTED_WAIT", "the wait is consumed or unsupported")
        if not wait_token or wait_token != wait.get("wait_token"):
            raise ApprovalDecisionError("WAIT_TOKEN_MISMATCH", "the wait token does not match")
        if expected_revision is None or expected_revision != current.revision:
            raise ApprovalDecisionError("REVISION_CONFLICT", "the task revision no longer matches")
        contract = wait.get("contract_ref")
        if not isinstance(contract, dict) or contract.get("approval_ref") != approval_ref:
            raise ApprovalDecisionError("APPROVAL_REF_MISMATCH", "the current wait names another approval")
        if (
            not isinstance(contract.get("tool_name"), str)
            or not contract["tool_name"].strip()
            or not isinstance(contract.get("arguments_digest"), str)
            or len(contract["arguments_digest"]) != 64
            or not isinstance(contract.get("invocation_id"), str)
            or not contract["invocation_id"].strip()
            or not isinstance(contract.get("target_resources"), list)
            or any(not isinstance(resource, str) for resource in contract["target_resources"])
        ):
            raise ApprovalDecisionError("UNSUPPORTED_WAIT", "the parked action identity is incomplete")
        if not hasattr(self._store, "engine") or not await ApprovalWaitExpirer(
            self._store, self._session_factory
        )._is_local(self._db, t_ref, current):
            raise ApprovalDecisionError("UNSUPPORTED_WAIT", "only platform-local durable approvals are supported")
        self._check_wait_deadline(wait, datetime.now(UTC))

    @staticmethod
    def _check_wait_deadline(wait: dict[str, Any], now: datetime) -> None:
        raw = wait.get("wait_expires_at")
        try:
            deadline = datetime.fromisoformat(raw) if isinstance(raw, str) else None
            if deadline is None or deadline.tzinfo is None or now >= deadline:
                raise ValueError("expired or missing deadline")
        except ValueError as exc:
            raise ApprovalDecisionError("WAIT_EXPIRED", "the wait deadline is expired or unverifiable") from exc

    @staticmethod
    def _validate_binding(binding: Any, wait: dict, task_input: dict, task_id: uuid.UUID, *, trusted: bool) -> None:
        from hecate_governance.approval import ActionRequest, ApprovalJudgment, BindingCheck, check_binding
        from hecate_governance.clock import TimeAnchor, TrustedClock

        from hecate.execution.approval_wait import task_principal_fingerprint

        if binding is None:
            raise ApprovalDecisionError("AUTHORITY_UNAVAILABLE", "the approval authority is missing")
        now = datetime.now(UTC)
        if trusted:
            now, problem = TrustedClock().trusted_now(
                TimeAnchor(binding.anchor_wall, binding.anchor_monotonic, binding.clock_epoch)
            )
            if problem:
                raise ApprovalDecisionError("UNTRUSTED_TIME", "approval authority time cannot be verified")
        ApprovalDecisionService._check_wait_deadline(wait, now)
        contract = wait["contract_ref"]
        actor = task_input.get("user_id") or f"agent:{task_input.get('agent_id')}"
        request = ActionRequest(
            invocation_id=str(contract.get("invocation_id") or ""),
            tool_name=str(contract.get("tool_name") or ""),
            arguments_digest=str(contract.get("arguments_digest") or ""),
            target_resources=tuple(contract.get("target_resources") or ()),
            principal_fingerprint=task_principal_fingerprint(
                actor=str(actor), agent_principal=task_input.get("agent_id")
            ),
            task_scope=str(task_id),
        )
        verdict = check_binding(binding.model_copy(update={"judgment": ApprovalJudgment.APPROVED}), request, now=now)
        if (
            verdict.check is not BindingCheck.OK
            or binding.consumed_by_invocation_id is not None
            or set(request.target_resources) != set(binding.target_resources)
        ):
            raise ApprovalDecisionError("BINDING_INVALID", "the approval is revoked, expired, consumed or mismatched")
        if binding.judgment is not ApprovalJudgment.PENDING:
            raise ApprovalDecisionError("ALREADY_DECIDED", "the approval has already been decided")

    def _task_control(self) -> Any:
        from hecate.execution.task_control import TaskControlService

        return TaskControlService(
            self._db,
            store=self._store,
            recorder=self._recorder,
            backend=self._backend,
            ledger_source=self._ledger_source,
            session_factory=self._session_factory,
            worker=self._worker,
        )

    def _approval_ledger(self) -> Any:
        from hecate.enterprise.approval_wait_ledger import TaskApprovalLedger

        return TaskApprovalLedger(self._db)

    @staticmethod
    def _enforce_not_self_approved(task_input: dict[str, Any], decided_by: str, chain: Any = None) -> None:
        """Resolve responsibility from matching persisted identity records."""
        if not isinstance(chain, dict) or "initiator" not in chain or "user_id" not in task_input:
            raise ApprovalDecisionError("INITIATOR_UNPARSEABLE", "the initiating identity is unverifiable")
        raw = task_input["user_id"]
        try:
            initiator = str(uuid.UUID(str(raw))) if raw is not None else None
            if initiator != chain["initiator"] or not chain.get("principal_id") or not task_input.get("agent_id"):
                raise ValueError("identity records disagree")
        except (TypeError, ValueError) as exc:
            raise ApprovalDecisionError("INITIATOR_UNPARSEABLE", "the initiating identity is unverifiable") from exc
        if decided_by in (initiator, chain["principal_id"], task_input["agent_id"]):
            raise ApprovalDecisionError(
                "SELF_APPROVAL", "the initiating human or proposal Agent cannot decide its own approval"
            )

    async def _decision_row_for_task(
        self, workspace_id: uuid.UUID, task_id: uuid.UUID, approval_ref: str | None
    ) -> ApprovalDecisionModel | None:
        if not approval_ref:
            return None
        result = await self._db.execute(
            select(ApprovalDecisionModel)
            .where(
                ApprovalDecisionModel.workspace_id == workspace_id,
                ApprovalDecisionModel.task_id == task_id,
                ApprovalDecisionModel.approval_ref == approval_ref,
                ApprovalDecisionModel.deleted.is_(False),
            )
            .order_by(ApprovalDecisionModel.created_at.desc(), ApprovalDecisionModel.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _decision_row(
        self, workspace_id: uuid.UUID, task_id: uuid.UUID, operation_id: str
    ) -> ApprovalDecisionModel | None:
        result = await self._db.execute(
            select(ApprovalDecisionModel).where(
                ApprovalDecisionModel.operation_id == operation_id,
                ApprovalDecisionModel.deleted.is_(False),
            )
        )
        row = result.scalar_one_or_none()
        if row is not None and (row.workspace_id != workspace_id or row.task_id != task_id):
            raise ApprovalDecisionError(
                "OPERATION_BOUND_ELSEWHERE",
                "the operation id is registered against a different task",
            )
        return row

    async def _replay_or_conflict(self, row: ApprovalDecisionModel, digest: str) -> dict[str, Any]:
        if row.request_digest != digest:
            raise ApprovalDecisionError(
                "OPERATION_CONFLICT",
                "the operation id is bound to a different decision, reason or identity",
            )
        resume: dict[str, Any] | None = None
        if row.resume_command_id:
            from hecate.execution.task_control import command_receipt_summary

            record = await self._receipt(row.resume_command_id)
            resume = command_receipt_summary(record) if record else None
        return {
            "decision": {
                "operation_id": row.operation_id,
                "decision": row.decision,
                "reason": row.reason,
                "decided_by": row.decided_by,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "resume_command_id": row.resume_command_id,
                "replay": True,
            },
            "resume": resume,
        }

    async def _issue_resume(
        self, row: ApprovalDecisionModel, task_id: uuid.UUID, workspace_id: uuid.UUID
    ) -> dict[str, Any]:
        """Phase two: the deterministic resume command for a recorded decision.

        Any failure here leaves ``resume_command_id`` NULL — the durable
        recoverable state the named consumer heals — and never un-decides
        the committed judgment.
        """
        from hecate.execution.task_control import command_receipt_summary

        service = self._task_control()
        issued = await service.issue_command(
            workspace_id=workspace_id,
            task_id=task_id,
            kind=ControlCommandKind.RESUME,
            command_id=resume_command_id_for(row.operation_id),
            issuer=f"approval-decision:{row.decided_by}",
            payload={"approval_ref": row.approval_ref},
            payload_schema_ref="urn:hecate:approval-resume/0",
            expected_revision=row.expected_revision,
            detail_ns={"wait_token": row.wait_token},
        )
        record = issued.record
        if row.resume_command_id != record.command_id:
            row.resume_command_id = record.command_id
            if record.state not in (CommandState.APPLIED, CommandState.REJECTED, CommandState.EXPIRED):
                row.resume_command_id = None
            await self._db.flush()
        await self._db.commit()
        receipt = command_receipt_summary(record)
        if record.state is CommandState.REJECTED:
            # The receipt records why (expired token, cancelled task, moved
            # revision). The decision stays recorded; no retry loop — a
            # rejected resume converges through the existing paths.
            receipt["detail"] = issued.detail
        return receipt

    async def _receipt(self, command_id: str) -> Any:
        import asyncio

        return await asyncio.to_thread(self._recorder.get, command_id)


class ApprovalResumeRecovery:
    """The named worker consumer completing phased approval decisions.

    Composed with the approval-wait expirer in the platform wiring: each
    worker cycle first converges expired waits, then completes decision
    operations whose resume phase never committed. The expirer callback is
    preserved untouched.
    """

    def __init__(
        self, store: Any, session_factory: async_sessionmaker[AsyncSession] | None, *, worker: Any = None
    ) -> None:
        self._store = store
        self._session_factory = session_factory
        self._worker = worker

    async def resume_once(self) -> int:
        """Complete pending resumes using fresh sessions and the bound worker."""
        from hecate.core.database import async_session_factory as default_factory

        factory = self._session_factory or default_factory
        async with factory() as db:
            service = ApprovalDecisionService(
                db,
                store=self._store,
                recorder=self._store,
                backend="postgres",
                ledger_source="core",
                session_factory=factory,
                worker=self._worker,
            )
            return await service.resume_once()
