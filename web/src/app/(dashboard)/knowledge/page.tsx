"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Database, Plus } from "lucide-react";

interface KB {
  id: string;
  name: string;
  description: string;
  document_count: number;
  created_at: string;
}

export default function KnowledgePage() {
  const [kbs, setKBs] = useState<KB[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<KB[]>("/api/knowledge-bases")
      .then(setKBs)
      .catch(() => setKBs([]))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="text-muted-foreground">加载中...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">知识库</h1>
        <Link href="/knowledge/new">
          <Button>
            <Plus className="mr-2 h-4 w-4" />
            创建知识库
          </Button>
        </Link>
      </div>

      {kbs.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16">
          <Database className="mb-4 h-12 w-12 text-muted-foreground" />
          <p className="mb-4 text-muted-foreground">
            还没有知识库，点击创建第一个
          </p>
          <Link href="/knowledge/new">
            <Button>创建知识库</Button>
          </Link>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>名称</TableHead>
              <TableHead>文档数</TableHead>
              <TableHead>创建时间</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {kbs.map((kb) => (
              <TableRow key={kb.id}>
                <TableCell>
                  <Link
                    href={`/knowledge/${kb.id}`}
                    className="font-medium hover:underline"
                  >
                    {kb.name}
                  </Link>
                </TableCell>
                <TableCell>{kb.document_count ?? 0}</TableCell>
                <TableCell>
                  {new Date(kb.created_at).toLocaleDateString("zh-CN")}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
