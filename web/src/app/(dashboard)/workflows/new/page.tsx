"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { ArrowLeft } from "lucide-react";
import Link from "next/link";

export default function NewWorkflowPage() {
  const router = useRouter();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [dslText, setDslText] = useState(
    JSON.stringify(
      {
        version: "1.0",
        name: "",
        state: {
          messages: { type: "topic", default: [] },
          context: { type: "last_value", default: "" },
        },
        nodes: {},
        edges: [],
        entry: "",
      },
      null,
      2
    )
  );
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit() {
    if (!name.trim()) {
      setError("请输入工作流名称");
      return;
    }

    let graphDsl: unknown;
    try {
      graphDsl = JSON.parse(dslText);
    } catch {
      setError("Graph DSL JSON 格式错误");
      return;
    }

    setSubmitting(true);
    setError("");

    try {
      await api.post("/api/workflows", {
        name: name.trim(),
        description: description.trim(),
        graph_dsl: graphDsl,
      });
      router.push("/workflows");
    } catch (err: unknown) {
      const apiErr = err as { error?: { message?: string } };
      setError(apiErr.error?.message || "创建失败");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <Link href="/workflows">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="mr-1 h-4 w-4" />
            返回
          </Button>
        </Link>
        <h1 className="text-2xl font-bold">新建工作流</h1>
      </div>

      <div className="max-w-2xl space-y-4">
        <div>
          <label className="mb-1 block text-sm font-medium">名称</label>
          <input
            type="text"
            className="w-full rounded-md border px-3 py-2 text-sm"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="工作流名称"
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">描述</label>
          <textarea
            className="w-full rounded-md border px-3 py-2 text-sm"
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="工作流描述（可选）"
          />
        </div>

        <div>
          <label className="mb-1 block text-sm font-medium">Graph DSL</label>
          <textarea
            className="w-full rounded-md border px-3 py-2 font-mono text-xs"
            rows={16}
            value={dslText}
            onChange={(e) => setDslText(e.target.value)}
          />
        </div>

        {error && (
          <div className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-600">
            {error}
          </div>
        )}

        <Button onClick={handleSubmit} disabled={submitting}>
          {submitting ? "创建中..." : "创建"}
        </Button>
      </div>
    </div>
  );
}
