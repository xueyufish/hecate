"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { ClipboardList, Plus, Trash2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import {
  AnnotationEntryPayload,
  AnnotationItemDetail,
  AnnotationMetricDef,
  AnnotationQueue,
  AnnotationQueueItem,
  evaluationApi,
} from "@/lib/api-client";

type FormState = Record<
  string,
  { value: string; valueLabel: string; override: boolean; reasonCode: string; justification: string }
>;

function emptyFormFor(detail: AnnotationItemDetail | null): FormState {
  const state: FormState = {};
  if (!detail) return state;
  for (const def of detail.queue.metric_defs) {
    const suggestion = detail.suggestions.find((s) => s.metric_name === def.name);
    state[def.name] = {
      value: def.data_type === "numeric" && suggestion ? String(suggestion.value) : "",
      valueLabel: def.data_type !== "numeric" && suggestion ? suggestion.reasoning ?? "" : "",
      override: false,
      reasonCode: "",
      justification: "",
    };
  }
  return state;
}

/** One annotation input control, rendered by the metric's declared data type. */
function MetricInput({
  def,
  state,
  suggestion,
  onChange,
}: {
  def: AnnotationMetricDef;
  state: FormState[string];
  suggestion?: { value: number; source: string; reasoning: string | null };
  onChange: (patch: Partial<FormState[string]>) => void;
}) {
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Label className="text-xs font-medium">{def.name}</Label>
        {suggestion && (
          <span className="text-xs text-muted-foreground" title={suggestion.reasoning ?? ""}>
            machine: {suggestion.value} ({suggestion.source})
          </span>
        )}
      </div>
      {def.data_type === "numeric" && (
        <Input
          type="number"
          step="0.01"
          min={def.min}
          max={def.max}
          value={state.value}
          onChange={(e) => onChange({ value: e.target.value })}
          placeholder={`[${def.min ?? "-inf"}, ${def.max ?? "inf"}]`}
        />
      )}
      {def.data_type === "categorical" && (
        <Select value={state.valueLabel} onValueChange={(v) => onChange({ valueLabel: v })}>
          <SelectTrigger>
            <SelectValue placeholder="pick a label" />
          </SelectTrigger>
          <SelectContent>
            {(def.categories ?? []).map((c) => (
              <SelectItem key={c} value={c}>
                {c}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      )}
      {def.data_type === "boolean" && (
        <Select value={state.valueLabel} onValueChange={(v) => onChange({ valueLabel: v })}>
          <SelectTrigger>
            <SelectValue placeholder="true / false" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="true">true</SelectItem>
            <SelectItem value="false">false</SelectItem>
          </SelectContent>
        </Select>
      )}
      <label className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <input
          type="checkbox"
          checked={state.override}
          onChange={(e) => onChange({ override: e.target.checked })}
        />
        override a machine score
      </label>
      {state.override && (
        <div className="space-y-1 rounded-md border p-2">
          <Input
            value={state.reasonCode}
            onChange={(e) => onChange({ reasonCode: e.target.value })}
            placeholder="reason code (e.g. judge_too_harsh)"
          />
          <Textarea
            value={state.justification}
            onChange={(e) => onChange({ justification: e.target.value })}
            placeholder="justification (required for overrides)"
            rows={2}
          />
        </div>
      )}
    </div>
  );
}

/** Queue creation form with dynamic metric definitions. */
function CreateQueueForm({ onCreated }: { onCreated: () => void }) {
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [defs, setDefs] = useState<AnnotationMetricDef[]>([
    { name: "helpfulness", data_type: "numeric", min: 0, max: 1 },
  ]);
  const [error, setError] = useState<string | null>(null);

  const updateDef = (index: number, patch: Partial<AnnotationMetricDef>) => {
    setDefs(defs.map((d, i) => (i === index ? { ...d, ...patch } : d)));
  };

  const submit = async () => {
    setError(null);
    try {
      await evaluationApi.createAnnotationQueue({
        name,
        instructions: instructions || undefined,
        metric_defs: defs,
      });
      setName("");
      setInstructions("");
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to create queue");
    }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm font-medium">New annotation queue</CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        <Input value={name} onChange={(e) => setName(e.target.value)} placeholder="queue name" />
        <Input
          value={instructions}
          onChange={(e) => setInstructions(e.target.value)}
          placeholder="reviewer instructions (optional)"
        />
        {defs.map((def, index) => (
          <div key={index} className="flex flex-wrap items-center gap-2">
            <Input
              className="w-40"
              value={def.name}
              onChange={(e) => updateDef(index, { name: e.target.value })}
              placeholder="metric name"
            />
            <Select value={def.data_type} onValueChange={(v) => updateDef(index, { data_type: v as AnnotationMetricDef["data_type"] })}>
              <SelectTrigger className="w-32">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="numeric">numeric</SelectItem>
                <SelectItem value="categorical">categorical</SelectItem>
                <SelectItem value="boolean">boolean</SelectItem>
              </SelectContent>
            </Select>
            {def.data_type === "numeric" && (
              <>
                <Input
                  className="w-20"
                  type="number"
                  value={def.min ?? 0}
                  onChange={(e) => updateDef(index, { min: Number(e.target.value) })}
                />
                <Input
                  className="w-20"
                  type="number"
                  value={def.max ?? 1}
                  onChange={(e) => updateDef(index, { max: Number(e.target.value) })}
                />
              </>
            )}
            {def.data_type === "categorical" && (
              <Input
                className="flex-1"
                value={(def.categories ?? []).join(",")}
                onChange={(e) => updateDef(index, { categories: e.target.value.split(",").filter(Boolean) })}
                placeholder="comma-separated categories"
              />
            )}
            <Button variant="ghost" size="sm" onClick={() => setDefs(defs.filter((_, i) => i !== index))}>
              <Trash2 className="h-3 w-3" />
            </Button>
          </div>
        ))}
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => setDefs([...defs, { name: "", data_type: "numeric", min: 0, max: 1 }])}
          >
            <Plus className="mr-1 h-3 w-3" /> metric
          </Button>
          <Button size="sm" onClick={submit} disabled={!name.trim()}>
            Create queue
          </Button>
          {error && <span className="text-xs text-destructive">{error}</span>}
        </div>
      </CardContent>
    </Card>
  );
}

/** Intake dialog: paste trace IDs or pull low-score samples from an online task. */
function AddTracesForm({
  queueId,
  onAdded,
}: {
  queueId: string;
  onAdded: (message: string) => void;
}) {
  const [traceIds, setTraceIds] = useState("");
  const [taskId, setTaskId] = useState("");
  const [metricName, setMetricName] = useState("");
  const [maxScore, setMaxScore] = useState("0.5");
  const [limit, setLimit] = useState("50");

  const addManual = async () => {
    const ids = traceIds
      .split(/[\s,]+/)
      .filter(Boolean)
      .map((id) => id.trim());
    if (ids.length === 0) return;
    const result = await evaluationApi.addAnnotationQueueItems(queueId, ids);
    setTraceIds("");
    onAdded(`added ${result.created}, already present ${result.already_present.length}, rejected ${result.rejected.length}`);
  };

  const addFromTask = async () => {
    if (!taskId.trim()) return;
    const result = await evaluationApi.addAnnotationQueueItemsFromTask(queueId, {
      task_id: taskId.trim(),
      metric_name: metricName.trim() || undefined,
      max_score: maxScore ? Number(maxScore) : undefined,
      limit: limit ? Number(limit) : undefined,
    });
    onAdded(`added ${result.created} of ${result.candidates} candidates (skipped ${result.skipped_existing})`);
  };

  return (
    <div className="space-y-3 rounded-md border p-3">
      <div className="space-y-1">
        <Label className="text-xs">Trace IDs (one per line, up to 100)</Label>
        <Textarea value={traceIds} onChange={(e) => setTraceIds(e.target.value)} rows={3} />
        <Button size="sm" variant="outline" onClick={addManual}>
          Add traces
        </Button>
      </div>
      <div className="space-y-1 border-t pt-3">
        <Label className="text-xs">From online task (low-score samples)</Label>
        <div className="flex flex-wrap items-center gap-2">
          <Input className="w-56" value={taskId} onChange={(e) => setTaskId(e.target.value)} placeholder="task id" />
          <Input className="w-32" value={metricName} onChange={(e) => setMetricName(e.target.value)} placeholder="metric" />
          <Input className="w-24" type="number" step="0.1" value={maxScore} onChange={(e) => setMaxScore(e.target.value)} placeholder="max score" />
          <Input className="w-20" type="number" value={limit} onChange={(e) => setLimit(e.target.value)} placeholder="limit" />
          <Button size="sm" variant="outline" onClick={addFromTask}>
            Add from task
          </Button>
        </div>
      </div>
    </div>
  );
}

/** Review workbench: trace projection + suggestion-prefilled annotation form. */
function Workbench({
  queueId,
  items,
  currentIndex,
  onNavigated,
}: {
  queueId: string;
  items: AnnotationQueueItem[];
  currentIndex: number;
  onNavigated: (index: number) => void;
}) {
  const item = items[currentIndex];
  const [detail, setDetail] = useState<AnnotationItemDetail | null>(null);
  const [form, setForm] = useState<FormState>({});
  const [message, setMessage] = useState<string | null>(null);

  const load = useCallback(
    async (target: AnnotationQueueItem, claim: boolean) => {
      setMessage(null);
      if (claim && target.status === "pending") {
        try {
          await evaluationApi.claimAnnotationQueueItem(queueId, target.id);
        } catch {
          // claimed by someone else or assignment rules — still viewable
        }
      }
      try {
        const d = await evaluationApi.getAnnotationQueueItemDetail(queueId, target.id);
        setDetail(d);
        setForm(emptyFormFor(d));
      } catch {
        setDetail(null);
      }
    },
    [queueId]
  );

  useEffect(() => {
    if (item) {
      load(item, true);
    } else {
      setDetail(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.id]);

  const submit = async () => {
    if (!detail) return;
    const annotations: AnnotationEntryPayload[] = detail.queue.metric_defs.map((def) => {
      const state = form[def.name];
      const entry: AnnotationEntryPayload = { metric_name: def.name };
      if (def.data_type === "numeric") entry.value = Number(state.value);
      else entry.value_label = state.valueLabel;
      if (state.override) {
        entry.overrides_score_id = detail.suggestions.find((s) => s.metric_name === def.name)?.score_id;
        entry.reason_code = state.reasonCode;
        entry.justification = state.justification;
      }
      return entry;
    });
    try {
      await evaluationApi.submitAnnotationQueueItem(queueId, detail.item.id, annotations);
      // Refreshing the pending list advances to the next pending item.
      setMessage("submitted");
      onNavigated(-1);
    } catch (e) {
      setMessage(e instanceof Error ? e.message : "submit failed");
    }
  };

  const skip = async () => {
    if (!detail) return;
    await evaluationApi.skipAnnotationQueueItem(queueId, detail.item.id);
    onNavigated(-1);
  };

  if (!detail) {
    return <div className="py-8 text-center text-sm text-muted-foreground">No item selected.</div>;
  }

  const messages = detail.projection?.messages ?? [];
  const toolCalls = detail.projection?.tool_calls ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <Badge variant="outline">{detail.item.status}</Badge>
          <span className="font-mono">trace {detail.item.target_id.slice(0, 8)}</span>
          <span>
            item {currentIndex + 1} / {items.length}
          </span>
        </div>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="sm"
            disabled={currentIndex === 0}
            onClick={() => onNavigated(currentIndex - 1)}
          >
            Prev
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={currentIndex + 1 >= items.length}
            onClick={() => onNavigated(currentIndex + 1)}
          >
            Next
          </Button>
          <Button variant="outline" size="sm" onClick={skip}>
            Skip
          </Button>
          <Button size="sm" onClick={submit}>
            Submit
          </Button>
        </div>
      </div>
      {message && <div className="text-xs text-muted-foreground">{message}</div>}

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Conversation</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {messages.length > 0 ? (
            messages.map((m, i) => (
              <div
                key={i}
                className={`max-w-[85%] rounded-md px-3 py-2 text-sm ${
                  m.role === "user" ? "bg-muted" : "bg-primary/10 ml-auto"
                }`}
              >
                <div className="mb-0.5 text-xs font-medium text-muted-foreground">{m.role}</div>
                {String(m.content)}
              </div>
            ))
          ) : (
            <div className="text-xs text-muted-foreground">
              No projected conversation for this trace window.
            </div>
          )}
          {toolCalls.length > 0 && (
            <div className="rounded-md border p-2 text-xs text-muted-foreground">
              <div className="mb-1 font-medium">Tool calls</div>
              {toolCalls.map((t, i) => (
                <div key={i} className="font-mono">
                  {t.name}({typeof t.args === "string" ? t.args : JSON.stringify(t.args)})
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm font-medium">Annotations</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          {detail.queue.instructions && (
            <div className="rounded-md bg-muted p-2 text-xs text-muted-foreground">{detail.queue.instructions}</div>
          )}
          {detail.queue.metric_defs.map((def) => (
            <MetricInput
              key={def.name}
              def={def}
              state={form[def.name]}
              suggestion={detail.suggestions.find((s) => s.metric_name === def.name)}
              onChange={(patch) => setForm({ ...form, [def.name]: { ...form[def.name], ...patch } })}
            />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

export function AnnotationsView() {
  const [queues, setQueues] = useState<AnnotationQueue[]>([]);
  const [selectedQueueId, setSelectedQueueId] = useState<string | null>(null);
  const [items, setItems] = useState<AnnotationQueueItem[]>([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [showCreate, setShowCreate] = useState(false);
  const [showIntake, setShowIntake] = useState(false);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);

  const selectedQueue = useMemo(() => queues.find((q) => q.id === selectedQueueId) ?? null, [queues, selectedQueueId]);
  const pendingItems = useMemo(() => items.filter((i) => i.status !== "completed" && i.status !== "skipped"), [items]);

  const refreshQueues = useCallback(async () => {
    try {
      const res = await evaluationApi.listAnnotationQueues();
      setQueues(res.items);
      setSelectedQueueId((current) => current ?? res.items[0]?.id ?? null);
    } catch {
      setQueues([]);
    }
  }, []);

  const refreshItems = useCallback(async () => {
    if (!selectedQueueId) return;
    try {
      const res = await evaluationApi.listAnnotationQueueItems(selectedQueueId);
      setItems(res.items);
      setCurrentIndex(0);
    } catch {
      setItems([]);
    }
  }, [selectedQueueId]);

  useEffect(() => {
    refreshQueues();
  }, [refreshQueues]);
  useEffect(() => {
    refreshItems();
  }, [refreshItems]);

  const navigate = (index: number) => {
    if (index === -1) {
      refreshItems();
      return;
    }
    setCurrentIndex(index);
  };

  const pushToDataset = async () => {
    if (!selectedQueueId) return;
    const name = window.prompt("Push completed annotations to dataset named:");
    if (!name) return;
    try {
      const result = await evaluationApi.pushAnnotationQueueToDataset(selectedQueueId, { dataset_name: name });
      setStatusMessage(`pushed ${result.created} items (skipped ${result.skipped}) to ${result.dataset_id.slice(0, 8)}`);
    } catch (e) {
      setStatusMessage(e instanceof Error ? e.message : "push failed");
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex flex-wrap gap-2">
          {queues.map((q) => (
            <button
              key={q.id}
              className={`rounded-md border px-3 py-2 text-left text-sm ${
                q.id === selectedQueueId ? "border-primary" : "text-muted-foreground"
              }`}
              onClick={() => {
                setSelectedQueueId(q.id);
                setShowIntake(false);
              }}
            >
              <div className="flex items-center gap-2 font-medium">
                <ClipboardList className="h-3.5 w-3.5" />
                {q.name}
              </div>
              <div className="mt-1 flex gap-1.5 text-xs text-muted-foreground">
                {(["pending", "claimed", "completed", "skipped"] as const).map((s) => (
                  <Badge key={s} variant="outline" className="text-[10px]">
                    {s} {q.counts?.[s] ?? 0}
                  </Badge>
                ))}
              </div>
              {q.metric_defs.length > 0 && (
                <div className="mt-1 text-xs text-muted-foreground">
                  {q.metric_defs.map((d) => d.name).join(" · ")}
                </div>
              )}
            </button>
          ))}
          <Button variant="outline" size="sm" onClick={() => setShowCreate(!showCreate)}>
            <Plus className="mr-1 h-3 w-3" /> New queue
          </Button>
        </div>
      </div>

      {showCreate && (
        <CreateQueueForm
          onCreated={() => {
            setShowCreate(false);
            refreshQueues();
          }}
        />
      )}

      {selectedQueue && (
        <>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => setShowIntake(!showIntake)}>
              Add traces
            </Button>
            <Button variant="outline" size="sm" onClick={pushToDataset}>
              Push completed to dataset
            </Button>
            {statusMessage && <span className="text-xs text-muted-foreground">{statusMessage}</span>}
          </div>
          {showIntake && (
            <AddTracesForm
              queueId={selectedQueue.id}
              onAdded={(message) => {
                setStatusMessage(message);
                setShowIntake(false);
                refreshItems();
              }}
            />
          )}
          {pendingItems.length > 0 ? (
            <Workbench
              queueId={selectedQueue.id}
              items={pendingItems.length > 0 ? pendingItems : items}
              currentIndex={Math.min(currentIndex, Math.max(pendingItems.length - 1, 0))}
              onNavigated={navigate}
            />
          ) : (
            <Card>
              <CardContent className="py-8 text-center text-sm text-muted-foreground">
                No items waiting for review in this queue. Add traces or pull low-score samples from an
                online task.
              </CardContent>
            </Card>
          )}
        </>
      )}

      {queues.length === 0 && (
        <Card>
          <CardContent className="py-8 text-center text-sm text-muted-foreground">
            No annotation queues yet. Create one to start reviewing production traces.
          </CardContent>
        </Card>
      )}
    </div>
  );
}
