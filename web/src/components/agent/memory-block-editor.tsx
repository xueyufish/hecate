"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

interface MemoryBlock {
  id: string;
  agent_id: string;
  label: string;
  content: string;
  position: number;
  limit: number;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

interface MemoryBlockEditorProps {
  agentId: string;
}

const TEMPLATES = [
  { label: "persona", contentHint: "You are a helpful assistant that...", position: 0, limit: 2000 },
  { label: "user_profile", contentHint: "The user prefers...", position: 1, limit: 1000 },
  { label: "domain_context", contentHint: "This agent operates in the domain of...", position: 2, limit: 2000 },
  { label: "task_tracker", contentHint: "Current task: ... Progress: ...", position: 3, limit: 1500 },
];

export function MemoryBlockEditor({ agentId }: MemoryBlockEditorProps) {
  const [blocks, setBlocks] = useState<MemoryBlock[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editContent, setEditContent] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);
  const [addForm, setAddForm] = useState({ label: "", content: "", position: 0, limit: 2000 });
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set());
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchBlocks = useCallback(async () => {
    try {
      setLoading(true);
      setError(null);
      const result = await api.get<MemoryBlock[]>(`/api/agents/${agentId}/memory-blocks`);
      setBlocks(result);
    } catch (err: unknown) {
      if (err && typeof err === "object" && "error" in err) {
        setError((err as { error: { message: string } }).error.message);
      } else {
        setError("Failed to load memory blocks");
      }
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    fetchBlocks();
  }, [fetchBlocks]);

  const handleUpdate = async (blockId: string) => {
    try {
      setActionLoading(blockId);
      await api.put(`/api/agents/${agentId}/memory-blocks/${blockId}`, { content: editContent });
      setEditingId(null);
      await fetchBlocks();
    } catch (err: unknown) {
      if (err && typeof err === "object" && "error" in err) {
        setError((err as { error: { message: string } }).error.message);
      } else {
        setError("Failed to update block");
      }
    } finally {
      setActionLoading(null);
    }
  };

  const handleDelete = async (blockId: string) => {
    if (!confirm("Are you sure you want to delete this memory block?")) return;
    try {
      setActionLoading(blockId);
      await api.delete(`/api/agents/${agentId}/memory-blocks/${blockId}`);
      await fetchBlocks();
    } catch (err: unknown) {
      if (err && typeof err === "object" && "error" in err) {
        setError((err as { error: { message: string } }).error.message);
      } else {
        setError("Failed to delete block");
      }
    } finally {
      setActionLoading(null);
    }
  };

  const handleAddFromTemplate = async (template: typeof TEMPLATES[number]) => {
    try {
      setActionLoading(`template-${template.label}`);
      setError(null);
      await api.post(`/api/agents/${agentId}/memory-blocks`, {
        label: template.label,
        content: template.contentHint,
        position: template.position,
        limit: template.limit,
      });
      await fetchBlocks();
    } catch (err: unknown) {
      if (err && typeof err === "object" && "error" in err) {
        const apiErr = err as { error: { code: string; message: string } };
        if (apiErr.error.code === "CONFLICT") {
          setError(`Block "${template.label}" already exists`);
        } else {
          setError(apiErr.error.message);
        }
      } else {
        setError("Failed to create block from template");
      }
    } finally {
      setActionLoading(null);
    }
  };

  const handleAddCustom = async () => {
    if (!addForm.label.trim()) {
      setError("Label is required");
      return;
    }
    try {
      setActionLoading("add-custom");
      setError(null);
      await api.post(`/api/agents/${agentId}/memory-blocks`, addForm);
      setShowAddForm(false);
      setAddForm({ label: "", content: "", position: 0, limit: 2000 });
      await fetchBlocks();
    } catch (err: unknown) {
      if (err && typeof err === "object" && "error" in err) {
        const apiErr = err as { error: { code: string; message: string } };
        if (apiErr.error.code === "CONFLICT") {
          setError(`Block "${addForm.label}" already exists`);
        } else {
          setError(apiErr.error.message);
        }
      } else {
        setError("Failed to create block");
      }
    } finally {
      setActionLoading(null);
    }
  };

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  if (loading) {
    return <div className="text-sm text-muted-foreground">Loading memory blocks...</div>;
  }

  return (
    <div className="space-y-4">
      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-4 py-3 text-sm text-red-800">
          {error}
          <button onClick={() => setError(null)} className="ml-2 text-red-600 hover:text-red-800">
            Dismiss
          </button>
        </div>
      )}

      {/* Templates */}
      <div className="space-y-2">
        <Label>Quick Add Templates</Label>
        <div className="flex flex-wrap gap-2">
          {TEMPLATES.map((t) => (
            <Button
              key={t.label}
              variant="outline"
              size="sm"
              onClick={() => handleAddFromTemplate(t)}
              disabled={actionLoading === `template-${t.label}` || blocks.some((b) => b.label === t.label)}
            >
              {t.label}
            </Button>
          ))}
        </div>
      </div>

      {/* Existing Blocks */}
      {blocks.length === 0 && !showAddForm && (
        <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
          No memory blocks configured. Add a template or create a custom block.
        </div>
      )}

      {blocks.map((block) => (
        <Card key={block.id}>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-medium">{block.label}</CardTitle>
              <div className="flex items-center gap-1">
                <span className="text-xs text-muted-foreground">pos:{block.position} limit:{block.limit}</span>
                {editingId !== block.id && (
                  <>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => {
                        setEditingId(block.id);
                        setEditContent(block.content);
                      }}
                    >
                      Edit
                    </Button>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleDelete(block.id)}
                      disabled={actionLoading === block.id}
                    >
                      Delete
                    </Button>
                  </>
                )}
              </div>
            </div>
          </CardHeader>
          <CardContent>
            {editingId === block.id ? (
              <div className="space-y-2">
                <Textarea
                  value={editContent}
                  onChange={(e) => setEditContent(e.target.value)}
                  rows={4}
                  className="w-full"
                />
                <div className="flex gap-2">
                  <Button
                    size="sm"
                    onClick={() => handleUpdate(block.id)}
                    disabled={actionLoading === block.id}
                  >
                    {actionLoading === block.id ? "Saving..." : "Save"}
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setEditingId(null)}
                  >
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
              <div>
                <p className="whitespace-pre-wrap text-sm">
                  {expandedIds.has(block.id)
                    ? block.content
                    : block.content.length > 100
                      ? block.content.slice(0, 100) + "..."
                      : block.content || "(empty)"}
                </p>
                {block.content.length > 100 && (
                  <button
                    onClick={() => toggleExpand(block.id)}
                    className="mt-1 text-xs text-blue-600 hover:text-blue-800"
                  >
                    {expandedIds.has(block.id) ? "Show less" : "Show more"}
                  </button>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      ))}

      {/* Add Custom Block */}
      {showAddForm ? (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Add Custom Block</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="space-y-1">
              <Label htmlFor="block-label">Label *</Label>
              <Input
                id="block-label"
                value={addForm.label}
                onChange={(e) => setAddForm((f) => ({ ...f, label: e.target.value }))}
                maxLength={100}
                placeholder="e.g., domain_context"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="block-content">Content</Label>
              <Textarea
                id="block-content"
                value={addForm.content}
                onChange={(e) => setAddForm((f) => ({ ...f, content: e.target.value }))}
                rows={3}
                maxLength={50000}
              />
            </div>
            <div className="flex gap-4">
              <div className="space-y-1">
                <Label htmlFor="block-position">Position</Label>
                <Input
                  id="block-position"
                  type="number"
                  value={addForm.position}
                  onChange={(e) => setAddForm((f) => ({ ...f, position: parseInt(e.target.value) || 0 }))}
                  min={0}
                  className="w-24"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="block-limit">Token Limit</Label>
                <Input
                  id="block-limit"
                  type="number"
                  value={addForm.limit}
                  onChange={(e) => setAddForm((f) => ({ ...f, limit: parseInt(e.target.value) || 2000 }))}
                  min={1}
                  className="w-32"
                />
              </div>
            </div>
            <div className="flex gap-2">
              <Button
                size="sm"
                onClick={handleAddCustom}
                disabled={!addForm.label.trim() || actionLoading === "add-custom"}
              >
                {actionLoading === "add-custom" ? "Creating..." : "Create Block"}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setShowAddForm(false);
                  setAddForm({ label: "", content: "", position: 0, limit: 2000 });
                }}
              >
                Cancel
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : (
        <Button variant="outline" onClick={() => setShowAddForm(true)}>
          Add Custom Block
        </Button>
      )}
    </div>
  );
}
