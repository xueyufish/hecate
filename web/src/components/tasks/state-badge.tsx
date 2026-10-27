"use client";

import { Badge } from "@/components/ui/badge";
import {
  acceptanceLabel,
  lifecycleLabel,
  outcomeLabel,
  recoveryStateLabel,
} from "@/lib/task-types";

const LIFECYCLE_TONES: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  succeeded: "secondary",
  failed: "destructive",
  reconciliation_required: "destructive",
  unrecorded: "outline",
  cancelled: "outline",
};

export function LifecycleBadge({ state }: { state: string | null | undefined }) {
  const raw = state ?? "unrecorded";
  return <Badge variant={LIFECYCLE_TONES[raw] ?? "default"}>{lifecycleLabel(raw)}</Badge>;
}

const RECOVERY_TONES: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  outcome_unknown: "destructive",
  store_unavailable: "destructive",
  never_started: "outline",
};

export function RecoveryStateBadge({ state }: { state: string | null | undefined }) {
  const raw = state ?? "";
  if (!raw) return <Badge variant="outline">{recoveryStateLabel(raw)}</Badge>;
  return <Badge variant={RECOVERY_TONES[raw] ?? "default"}>{recoveryStateLabel(raw)}</Badge>;
}

const OUTCOME_TONES: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  succeeded: "secondary",
  failed: "destructive",
};

export function OutcomeBadge({ outcome }: { outcome: string | null | undefined }) {
  const raw = outcome ?? "";
  if (!raw) return <Badge variant="outline">{outcomeLabel(raw)}</Badge>;
  return <Badge variant={OUTCOME_TONES[raw] ?? "default"}>{outcomeLabel(raw)}</Badge>;
}

const ACCEPTANCE_TONES: Record<string, "default" | "secondary" | "destructive" | "outline"> = {
  passed: "secondary",
  acceptance_failed: "destructive",
  acceptance_unverified: "outline",
  not_declared: "outline",
  uninterpretable: "destructive",
};

export function AcceptanceBadge({ status }: { status: string | null | undefined }) {
  const raw = status ?? "";
  if (!raw) return <Badge variant="outline">{acceptanceLabel(raw)}</Badge>;
  return <Badge variant={ACCEPTANCE_TONES[raw] ?? "default"}>{acceptanceLabel(raw)}</Badge>;
}
