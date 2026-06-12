"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import type { CollaborationPattern, PatternDefinition } from "@/lib/workflow-types";
import { Plus, Trash2 } from "lucide-react";

interface PatternConfigDialogProps {
  open: boolean;
  pattern: PatternDefinition | null;
  onGenerate: (patternId: CollaborationPattern, config: Record<string, unknown>) => void;
  onClose: () => void;
}

interface StageItem {
  id: string;
  model: string;
  system_prompt: string;
}

interface AgentItem {
  id: string;
  model: string;
  system_prompt: string;
}

function makeStage(i: number): StageItem {
  return { id: `stage_${i}`, model: "gpt-4o", system_prompt: "" };
}

function makeAgent(prefix: string, i: number): AgentItem {
  return { id: `${prefix}_${i}`, model: "gpt-4o", system_prompt: "" };
}

export function PatternConfigDialog({ open, pattern, onGenerate, onClose }: PatternConfigDialogProps) {
  const [stages, setStages] = useState<StageItem[]>([makeStage(0), makeStage(1)]);
  const [workers, setWorkers] = useState<AgentItem[]>([makeAgent("worker", 0), makeAgent("worker", 1)]);
  const [specialists, setSpecialists] = useState<AgentItem[]>([makeAgent("spec", 0), makeAgent("spec", 1)]);
  const [participants, setParticipants] = useState<AgentItem[]>([makeAgent("p", 0), makeAgent("p", 1)]);
  const [coordinator, setCoordinator] = useState({ model: "gpt-4o", system_prompt: "" });
  const [aggregator, setAggregator] = useState({ model: "gpt-4o", system_prompt: "" });
  const [router, setRouter] = useState({ id: "router", model: "gpt-4o", system_prompt: "" });
  const [proposer, setProposer] = useState({ model: "gpt-4o", system_prompt: "" });
  const [responder, setResponder] = useState({ model: "gpt-4o", system_prompt: "" });
  const [debaterA, setDebaterA] = useState({ model: "gpt-4o", system_prompt: "" });
  const [debaterB, setDebaterB] = useState({ model: "gpt-4o", system_prompt: "" });
  const [judge, setJudge] = useState({ model: "", system_prompt: "" });
  const [maxRounds, setMaxRounds] = useState(5);
  const [rounds, setRounds] = useState(3);

  if (!open || !pattern) return null;

  const currentPattern = pattern;

  function handleGenerate() {
    const pid = currentPattern.id;
    let config: Record<string, unknown> = {};

    switch (pid) {
      case "sequential":
        config = { stages: stages.map((s) => ({ id: s.id, model: s.model, system_prompt: s.system_prompt })) };
        break;
      case "parallel":
        config = {
          coordinator: { model: coordinator.model, system_prompt: coordinator.system_prompt },
          workers: workers.map((w) => ({ model: w.model, system_prompt: w.system_prompt })),
          aggregator: { model: aggregator.model, system_prompt: aggregator.system_prompt },
        };
        break;
      case "handoff":
        config = {
          router: { id: router.id, model: router.model, system_prompt: router.system_prompt },
          specialists: specialists.map((s) => ({ id: s.id, model: s.model, system_prompt: s.system_prompt })),
        };
        break;
      case "broadcast":
        config = {
          participants: participants.map((p) => ({ id: p.id, model: p.model, system_prompt: p.system_prompt })),
        };
        break;
      case "negotiation":
        config = {
          proposer: { model: proposer.model, system_prompt: proposer.system_prompt },
          responder: { model: responder.model, system_prompt: responder.system_prompt },
          max_rounds: maxRounds,
        };
        break;
      case "debate":
        config = {
          debater_a: { model: debaterA.model, system_prompt: debaterA.system_prompt },
          debater_b: { model: debaterB.model, system_prompt: debaterB.system_prompt },
          ...(judge.model ? { judge: { model: judge.model, system_prompt: judge.system_prompt } } : {}),
          rounds,
        };
        break;
    }

    onGenerate(pid, config);
  }

  function renderAgentList(
    items: AgentItem[],
    setItems: (items: AgentItem[]) => void,
    prefix: string,
  ) {
    return (
      <div className="space-y-2">
        {items.map((item, i) => (
          <div key={i} className="flex gap-2">
            <Input
              placeholder="ID"
              value={item.id}
              onChange={(e) => {
                const next = [...items];
                next[i] = { ...next[i], id: e.target.value };
                setItems(next);
              }}
              className="w-24"
            />
            <Input
              placeholder="Model"
              value={item.model}
              onChange={(e) => {
                const next = [...items];
                next[i] = { ...next[i], model: e.target.value };
                setItems(next);
              }}
              className="w-24"
            />
            <Textarea
              placeholder="System prompt"
              value={item.system_prompt}
              onChange={(e) => {
                const next = [...items];
                next[i] = { ...next[i], system_prompt: e.target.value };
                setItems(next);
              }}
              className="flex-1"
              rows={1}
            />
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setItems(items.filter((_, j) => j !== i))}
              disabled={items.length <= 2}
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        ))}
        <Button
          variant="outline"
          size="sm"
          onClick={() => setItems([...items, makeAgent(prefix, items.length)])}
        >
          <Plus className="mr-1 h-3 w-3" /> Add
        </Button>
      </div>
    );
  }

  function renderConfigForm() {
    switch (currentPattern.id) {
      case "sequential":
        return (
          <div className="space-y-3">
            <Label>Stages</Label>
            {stages.map((stage, i) => (
              <div key={i} className="flex gap-2">
                <Input
                  placeholder="Stage ID"
                  value={stage.id}
                  onChange={(e) => {
                    const next = [...stages];
                    next[i] = { ...next[i], id: e.target.value };
                    setStages(next);
                  }}
                  className="w-24"
                />
                <Input
                  placeholder="Model"
                  value={stage.model}
                  onChange={(e) => {
                    const next = [...stages];
                    next[i] = { ...next[i], model: e.target.value };
                    setStages(next);
                  }}
                  className="w-24"
                />
                <Textarea
                  placeholder="System prompt"
                  value={stage.system_prompt}
                  onChange={(e) => {
                    const next = [...stages];
                    next[i] = { ...next[i], system_prompt: e.target.value };
                    setStages(next);
                  }}
                  className="flex-1"
                  rows={1}
                />
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setStages(stages.filter((_, j) => j !== i))}
                  disabled={stages.length <= 2}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
            ))}
            <Button
              variant="outline"
              size="sm"
              onClick={() => setStages([...stages, makeStage(stages.length)])}
            >
              <Plus className="mr-1 h-3 w-3" /> Add Stage
            </Button>
          </div>
        );

      case "parallel":
        return (
          <div className="space-y-3">
            <div>
              <Label>Coordinator</Label>
              <div className="mt-1 flex gap-2">
                <Input placeholder="Model" value={coordinator.model} onChange={(e) => setCoordinator({ ...coordinator, model: e.target.value })} className="w-32" />
                <Textarea placeholder="System prompt" value={coordinator.system_prompt} onChange={(e) => setCoordinator({ ...coordinator, system_prompt: e.target.value })} className="flex-1" rows={1} />
              </div>
            </div>
            <div>
              <Label>Workers</Label>
              {renderAgentList(workers, setWorkers, "worker")}
            </div>
            <div>
              <Label>Aggregator</Label>
              <div className="mt-1 flex gap-2">
                <Input placeholder="Model" value={aggregator.model} onChange={(e) => setAggregator({ ...aggregator, model: e.target.value })} className="w-32" />
                <Textarea placeholder="System prompt" value={aggregator.system_prompt} onChange={(e) => setAggregator({ ...aggregator, system_prompt: e.target.value })} className="flex-1" rows={1} />
              </div>
            </div>
          </div>
        );

      case "handoff":
        return (
          <div className="space-y-3">
            <div>
              <Label>Router</Label>
              <div className="mt-1 flex gap-2">
                <Input placeholder="Router ID" value={router.id} onChange={(e) => setRouter({ ...router, id: e.target.value })} className="w-24" />
                <Input placeholder="Model" value={router.model} onChange={(e) => setRouter({ ...router, model: e.target.value })} className="w-24" />
                <Textarea placeholder="System prompt" value={router.system_prompt} onChange={(e) => setRouter({ ...router, system_prompt: e.target.value })} className="flex-1" rows={1} />
              </div>
            </div>
            <div>
              <Label>Specialists</Label>
              {renderAgentList(specialists, setSpecialists, "spec")}
            </div>
          </div>
        );

      case "broadcast":
        return (
          <div className="space-y-3">
            <Label>Participants</Label>
            {renderAgentList(participants, setParticipants, "p")}
          </div>
        );

      case "negotiation":
        return (
          <div className="space-y-3">
            <div>
              <Label>Proposer</Label>
              <div className="mt-1 flex gap-2">
                <Input placeholder="Model" value={proposer.model} onChange={(e) => setProposer({ ...proposer, model: e.target.value })} className="w-32" />
                <Textarea placeholder="System prompt" value={proposer.system_prompt} onChange={(e) => setProposer({ ...proposer, system_prompt: e.target.value })} className="flex-1" rows={1} />
              </div>
            </div>
            <div>
              <Label>Responder</Label>
              <div className="mt-1 flex gap-2">
                <Input placeholder="Model" value={responder.model} onChange={(e) => setResponder({ ...responder, model: e.target.value })} className="w-32" />
                <Textarea placeholder="System prompt" value={responder.system_prompt} onChange={(e) => setResponder({ ...responder, system_prompt: e.target.value })} className="flex-1" rows={1} />
              </div>
            </div>
            <div>
              <Label>Max Rounds</Label>
              <Input type="number" value={maxRounds} onChange={(e) => setMaxRounds(Number(e.target.value))} className="w-24" min={1} max={20} />
            </div>
          </div>
        );

      case "debate":
        return (
          <div className="space-y-3">
            <div>
              <Label>Debater A</Label>
              <div className="mt-1 flex gap-2">
                <Input placeholder="Model" value={debaterA.model} onChange={(e) => setDebaterA({ ...debaterA, model: e.target.value })} className="w-32" />
                <Textarea placeholder="System prompt" value={debaterA.system_prompt} onChange={(e) => setDebaterA({ ...debaterA, system_prompt: e.target.value })} className="flex-1" rows={1} />
              </div>
            </div>
            <div>
              <Label>Debater B</Label>
              <div className="mt-1 flex gap-2">
                <Input placeholder="Model" value={debaterB.model} onChange={(e) => setDebaterB({ ...debaterB, model: e.target.value })} className="w-32" />
                <Textarea placeholder="System prompt" value={debaterB.system_prompt} onChange={(e) => setDebaterB({ ...debaterB, system_prompt: e.target.value })} className="flex-1" rows={1} />
              </div>
            </div>
            <div>
              <Label>Judge (optional)</Label>
              <div className="mt-1 flex gap-2">
                <Input placeholder="Model (leave empty for no judge)" value={judge.model} onChange={(e) => setJudge({ ...judge, model: e.target.value })} className="w-32" />
                <Textarea placeholder="System prompt" value={judge.system_prompt} onChange={(e) => setJudge({ ...judge, system_prompt: e.target.value })} className="flex-1" rows={1} />
              </div>
            </div>
            <div>
              <Label>Rounds</Label>
              <Input type="number" value={rounds} onChange={(e) => setRounds(Number(e.target.value))} className="w-24" min={1} max={20} />
            </div>
          </div>
        );
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40">
      <div className="w-full max-w-lg rounded-lg bg-white p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-lg font-semibold">Configure: {pattern.name}</h2>
          <button onClick={onClose} className="rounded-md p-1 text-gray-400 hover:text-gray-600">
            ✕
          </button>
        </div>
        <p className="mb-4 text-sm text-gray-500">{currentPattern.description}</p>
        {renderConfigForm()}
        <div className="mt-6 flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleGenerate}>Generate</Button>
        </div>
      </div>
    </div>
  );
}
