"use client";

/** Dataset versions panel (7.3b).

Lists the dataset's frozen named versions, lets the user freeze the current
live items as a new version, soft-delete a version, and inspect the diff
against the live state (or another version). Checkout is destructive — the
UI surfaces a confirmation step before the call.
*/

import { useCallback, useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { evaluationApi, type DatasetVersionDiffResult, type DatasetVersionListEntry } from "@/lib/api-client";

interface DatasetVersionsPanelProps {
  datasetId: string;
}

export function DatasetVersionsPanel({ datasetId }: DatasetVersionsPanelProps) {
  const [versions, setVersions] = useState<DatasetVersionListEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [newName, setNewName] = useState("");
  const [diffTarget, setDiffTarget] = useState<string>("live");
  const [activeDiff, setActiveDiff] = useState<DatasetVersionDiffResult | null>(null);
  const [busy, setBusy] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await evaluationApi.listDatasetVersions(datasetId, { page_size: 100 });
      setVersions(res.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [datasetId]);

  useEffect(() => {
    reload();
  }, [reload]);

  const createVersion = async () => {
    if (!newName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await evaluationApi.createDatasetVersion(datasetId, { name: newName.trim() });
      setNewName("");
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const deleteVersion = async (versionId: string) => {
    if (!confirm("Soft-delete this version? Its name stays reserved.")) return;
    setBusy(true);
    setError(null);
    try {
      await evaluationApi.deleteDatasetVersion(datasetId, versionId);
      await reload();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const checkout = async (versionId: string, versionName: string) => {
    if (
      !confirm(
        `Restore the frozen items of version "${versionName}" as the dataset's live items? This is destructive — the current live items will be soft-deleted.`,
      )
    ) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const result = await evaluationApi.checkoutDatasetVersion(datasetId, versionId);
      alert(
        `Checked out version "${versionName}": added ${result.added}, removed ${result.removed}, changed ${result.changed}, live items after ${result.live_items_after}.`,
      );
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  const showDiff = async (versionId: string) => {
    setBusy(true);
    setError(null);
    try {
      const result = await evaluationApi.diffDatasetVersion(datasetId, versionId, diffTarget);
      setActiveDiff(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle>Dataset versions</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-end gap-2">
          <div className="flex-1">
            <Label htmlFor="new-version-name" className="text-sm">
              Freeze live items as
            </Label>
            <Input
              id="new-version-name"
              placeholder="e.g. v3.0"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
            />
          </div>
          <Button onClick={createVersion} disabled={busy || !newName.trim()}>
            Freeze version
          </Button>
        </div>

        {error && <div className="text-sm text-destructive">{error}</div>}
        {loading ? (
          <div className="text-sm text-muted-foreground">Loading versions…</div>
        ) : versions.length === 0 ? (
          <div className="text-sm text-muted-foreground">
            No versions yet — freeze the current items to start tracking changes.
          </div>
        ) : (
          <div className="space-y-2">
            {versions.map((v) => (
              <div
                key={v.id}
                className="flex items-center justify-between rounded border p-2 text-sm"
              >
                <div>
                  <div className="font-medium">{v.name}</div>
                  <div className="text-xs text-muted-foreground">
                    {v.content_hash.slice(0, 12)}… · {new Date(v.created_at).toLocaleString()}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => showDiff(v.id)}
                    disabled={busy}
                  >
                    Diff
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => checkout(v.id, v.name)}
                    disabled={busy}
                  >
                    Checkout
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => deleteVersion(v.id)}
                    disabled={busy}
                  >
                    Delete
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}

        <div className="border-t pt-3">
          <div className="mb-2 flex items-center gap-2 text-sm">
            <Label htmlFor="diff-against">Diff target</Label>
            <select
              id="diff-against"
              className="rounded border px-2 py-1 text-sm"
              value={diffTarget}
              onChange={(e) => setDiffTarget(e.target.value)}
            >
              <option value="live">Live dataset</option>
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </div>

          {activeDiff && (
            <DiffView diff={activeDiff} />
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function DiffView({ diff }: { diff: DatasetVersionDiffResult }) {
  return (
    <div className="space-y-3 text-sm">
      <div className="grid grid-cols-2 gap-2 text-xs text-muted-foreground">
        <div>
          Base: {diff.base.kind} <Badge variant="outline">{diff.base.name}</Badge>
        </div>
        <div>
          Target: {diff.target.kind}
          {"name" in diff.target && diff.target.name ? (
            <Badge variant="outline" className="ml-1">
              {diff.target.name}
            </Badge>
          ) : null}
        </div>
      </div>
      <DiffCategory title="Added" entries={diff.added} />
      <DiffCategory title="Removed" entries={diff.removed} />
      <div className="rounded border p-2">
        <div className="font-medium">Changed ({diff.changed.length})</div>
        {diff.changed.length === 0 ? (
          <div className="text-xs text-muted-foreground">No item-level changes.</div>
        ) : (
          <ul className="mt-2 space-y-1 text-xs">
            {diff.changed.map((change) => (
              <li key={change.item_id}>
                <code className="mr-2">{change.item_id.slice(0, 8)}…</code>
                {Object.entries(change.fields).map(([field, vals]) => (
                  <span key={field} className="ml-2">
                    {field}: {String(JSON.stringify(vals.base)).slice(0, 30)} →{" "}
                    {String(JSON.stringify(vals.target)).slice(0, 30)}
                  </span>
                ))}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function DiffCategory({ title, entries }: { title: string; entries: Array<Record<string, unknown>> }) {
  return (
    <div className="rounded border p-2">
      <div className="font-medium">
        {title} ({entries.length})
      </div>
      {entries.length === 0 ? (
        <div className="text-xs text-muted-foreground">None.</div>
      ) : (
        <ul className="mt-1 space-y-1 text-xs">
          {entries.map((entry, idx) => (
            <li key={idx}>
              <code className="mr-2">{String(entry.id).slice(0, 8)}…</code>
              {String(entry.query ?? "").slice(0, 80)}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}