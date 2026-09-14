"use client";

// Channel manager tab (1.3.20): list / create / repoint publishing channels
// bound to an agent's published versions. API channels expose a stable
// invocation URL; IM channels route webhook instances; embed/webhook are
// unwired placeholders in v1.

import { useCallback, useEffect, useState } from "react";
import { Copy, Plus, Radio, Trash2 } from "lucide-react";
import { channelsApi } from "@/lib/api-client";
import type { ChannelEntry } from "@/lib/api-types";

interface ChannelManagerProps {
  agentId: string;
}

const CHANNEL_TYPES: ChannelEntry["type"][] = ["api", "im", "embed", "webhook"];

export function ChannelManager({ agentId }: ChannelManagerProps) {
  const [channels, setChannels] = useState<ChannelEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const [newType, setNewType] = useState<ChannelEntry["type"]>("api");
  const [newBindMode, setNewBindMode] = useState<ChannelEntry["bind_mode"]>("published");
  const [newPinnedVersion, setNewPinnedVersion] = useState("");
  const [newProvider, setNewProvider] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setChannels(await channelsApi.list(agentId));
    } catch {
      setError("Failed to load channels");
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const handleCreate = async () => {
    setError(null);
    try {
      await channelsApi.create({
        name: newName,
        type: newType,
        agent_id: agentId,
        bind_mode: newBindMode,
        pinned_version: newBindMode === "pinned" ? Number(newPinnedVersion) : null,
        config: newType === "im" ? { provider: newProvider } : {},
      });
      setCreating(false);
      setNewName("");
      setNewProvider("");
      setNewPinnedVersion("");
      await refresh();
    } catch (e) {
      const message = (e as { data?: { error?: { message?: string } } }).data?.error?.message;
      setError(message || "Create failed");
    }
  };

  const handleRepoint = async (channel: ChannelEntry, version: number) => {
    setError(null);
    try {
      await channelsApi.update(channel.id, {
        bind_mode: "pinned",
        pinned_version: version,
      });
      await refresh();
    } catch {
      setError("Repoint failed");
    }
  };

  const handleToggleStatus = async (channel: ChannelEntry) => {
    setError(null);
    try {
      await channelsApi.update(channel.id, {
        status: channel.status === "disabled" ? "active" : "disabled",
      });
      await refresh();
    } catch {
      setError("Status change failed");
    }
  };

  const handleDelete = async (channel: ChannelEntry) => {
    setError(null);
    try {
      await channelsApi.remove(channel.id);
      await refresh();
    } catch {
      setError("Delete failed");
    }
  };

  const invocationUrl = (channel: ChannelEntry) => {
    if (typeof window === "undefined") return "";
    return `${window.location.origin}/v1/channels/${channel.id}/chat/completions`;
  };

  const copyUrl = async (channel: ChannelEntry) => {
    try {
      await navigator.clipboard.writeText(invocationUrl(channel));
      setCopied(channel.id);
      setTimeout(() => setCopied(null), 1500);
    } catch {
      setError("Copy failed");
    }
  };

  return (
    <div className="space-y-3 rounded-md border p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Radio className="h-4 w-4" />
          <h2 className="text-sm font-semibold">渠道管理</h2>
        </div>
        <button
          onClick={() => setCreating((v) => !v)}
          className="rounded-md bg-primary px-2 py-1 text-xs text-primary-foreground hover:bg-primary/90"
        >
          <Plus className="mr-1 inline h-3 w-3" />
          新建渠道
        </button>
      </div>

      {creating && (
        <div className="space-y-2 rounded-md border bg-muted/40 p-3">
          <input
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
            placeholder="渠道名称"
            className="w-full rounded-md border px-2 py-1 text-sm"
          />
          <div className="flex items-center gap-2 text-sm">
            <select
              value={newType}
              onChange={(e) => setNewType(e.target.value as ChannelEntry["type"])}
              className="rounded-md border px-2 py-1"
            >
              {CHANNEL_TYPES.map((t) => (
                <option key={t} value={t} disabled={t === "embed" || t === "webhook"}>
                  {t}
                  {t === "embed" || t === "webhook" ? "（占位）" : ""}
                </option>
              ))}
            </select>
            <select
              value={newBindMode}
              onChange={(e) => setNewBindMode(e.target.value as ChannelEntry["bind_mode"])}
              className="rounded-md border px-2 py-1"
            >
              <option value="published">追踪最新发布</option>
              <option value="pinned">钉死版本</option>
            </select>
            {newBindMode === "pinned" && (
              <input
                value={newPinnedVersion}
                onChange={(e) => setNewPinnedVersion(e.target.value)}
                placeholder="版本号"
                className="w-20 rounded-md border px-2 py-1"
              />
            )}
            {newType === "im" && (
              <input
                value={newProvider}
                onChange={(e) => setNewProvider(e.target.value)}
                placeholder="IM provider (feishu/slack)"
                className="w-40 rounded-md border px-2 py-1"
              />
            )}
          </div>
          <button
            onClick={handleCreate}
            className="rounded-md bg-primary px-3 py-1 text-xs text-primary-foreground hover:bg-primary/90"
          >
            创建
          </button>
        </div>
      )}

      {error && (
        <div className="rounded-md border border-red-300 bg-red-50 px-3 py-2 text-xs text-red-700">
          {error}
        </div>
      )}

      {loading ? (
        <div className="text-muted-foreground text-sm">Loading channels...</div>
      ) : channels.length === 0 ? (
        <div className="text-muted-foreground text-sm">
          尚无渠道。创建渠道前需先发布一个版本。
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-muted-foreground">
              <th className="py-1">名称</th>
              <th className="py-1">类型</th>
              <th className="py-1">绑定</th>
              <th className="py-1">状态</th>
              <th className="py-1 text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {channels.map((c) => (
              <tr key={c.id} className="border-t">
                <td className="py-1">{c.name}</td>
                <td className="py-1">{c.type}</td>
                <td className="py-1 text-xs">
                  {c.bind_mode === "pinned" ? (
                    <span>
                      钉死 v
                      <input
                        type="number"
                        defaultValue={c.pinned_version ?? undefined}
                        min={1}
                        className="w-14 rounded border px-1"
                        onBlur={(e) => {
                          const v = Number(e.target.value);
                          if (v > 0 && v !== c.pinned_version) void handleRepoint(c, v);
                        }}
                      />
                    </span>
                  ) : (
                    <span>追踪最新发布</span>
                  )}
                </td>
                <td className="py-1 text-xs">
                  {c.status === "active" ? (
                    <span className="text-green-700">active</span>
                  ) : (
                    <span className="text-amber-600">{c.status}</span>
                  )}
                </td>
                <td className="py-1 text-right">
                  <div className="flex items-center justify-end gap-1">
                    {c.type === "api" && c.status === "active" && (
                      <button
                        onClick={() => copyUrl(c)}
                        className="rounded border px-1.5 py-0.5 text-xs hover:bg-muted"
                        title={invocationUrl(c)}
                      >
                        <Copy className="mr-0.5 inline h-3 w-3" />
                        {copied === c.id ? "已复制" : "调用地址"}
                      </button>
                    )}
                    <button
                      onClick={() => handleToggleStatus(c)}
                      className="rounded border px-1.5 py-0.5 text-xs hover:bg-muted"
                    >
                      {c.status === "disabled" ? "启用" : "停用"}
                    </button>
                    <button
                      onClick={() => handleDelete(c)}
                      className="rounded border px-1.5 py-0.5 text-xs hover:bg-muted"
                      title="删除渠道"
                    >
                      <Trash2 className="inline h-3 w-3" />
                    </button>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
