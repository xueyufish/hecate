"use client";

/** Known-bad item marking UI (7.3c).

Renders a row of actions per dataset item to mark it known-bad or clear the
mark. Marking requires a non-empty reason; the action surfaces a small
modal-like inline form so the reason is captured at the moment of marking
(no surprise audits). State updates flow through ``evaluationApi``;
component owns the local reason draft and ``busy`` lock.
*/

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { evaluationApi } from "@/lib/api-client";

export interface ItemMarkingProps {
  datasetId: string;
  itemId: string;
  knownBad: boolean;
  knownBadReason: string | null;
  onChange: () => void | Promise<void>;
}

export function ItemMarkingControls({
  datasetId,
  itemId,
  knownBad,
  knownBadReason,
  onChange,
}: ItemMarkingProps) {
  const [editing, setEditing] = useState(false);
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const mark = async () => {
    if (!reason.trim()) {
      setError("Reason is required to mark an item known-bad.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await evaluationApi.markItemKnownBad(datasetId, itemId, {
        known_bad: true,
        known_bad_reason: reason.trim(),
      });
      setReason("");
      setEditing(false);
      await onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const clearMark = async () => {
    setBusy(true);
    setError(null);
    try {
      await evaluationApi.markItemKnownBad(datasetId, itemId, { known_bad: false });
      await onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  if (knownBad) {
    return (
      <div className="flex flex-col gap-1 text-xs">
        <Badge variant="destructive">known-bad</Badge>
        {knownBadReason && (
          <span className="text-muted-foreground">reason: {knownBadReason}</span>
        )}
        <Button
          size="sm"
          variant="outline"
          onClick={clearMark}
          disabled={busy}
        >
          {busy ? "Clearing…" : "Clear mark"}
        </Button>
        {error && <span className="text-destructive">{error}</span>}
      </div>
    );
  }

  if (!editing) {
    return (
      <div className="flex flex-col gap-1">
        <Button
          size="sm"
          variant="outline"
          onClick={() => setEditing(true)}
          disabled={busy}
        >
          Mark known-bad
        </Button>
        {error && <span className="text-xs text-destructive">{error}</span>}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={`reason-${itemId}`} className="text-xs">
        Reason
      </Label>
      <Input
        id={`reason-${itemId}`}
        placeholder="e.g. stale expected_answer"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
      />
      <div className="flex gap-1">
        <Button size="sm" onClick={mark} disabled={busy || !reason.trim()}>
          {busy ? "Saving…" : "Confirm mark"}
        </Button>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setEditing(false);
            setReason("");
            setError(null);
          }}
          disabled={busy}
        >
          Cancel
        </Button>
      </div>
      {error && <span className="text-xs text-destructive">{error}</span>}
    </div>
  );
}