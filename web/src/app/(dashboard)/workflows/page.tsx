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
import { Share2, Plus } from "lucide-react";

interface Workflow {
  id: string;
  name: string;
  description: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export default function WorkflowsPage() {
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<{ items: Workflow[]; total: number }>("/api/workflows")
      .then((res) => setWorkflows(res.items || []))
      .catch(() => setWorkflows([]))
      .finally(() => setLoading(false));
  }, []);

  if (loading) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Share2 className="h-6 w-6" />
          <h1 className="text-2xl font-bold">Workflows</h1>
        </div>
        <Link href="/workflows/new">
          <Button>
            <Plus className="mr-2 h-4 w-4" />
            New Workflow
          </Button>
        </Link>
      </div>

      {workflows.length === 0 ? (
        <div className="text-muted-foreground">No workflows yet</div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Version</TableHead>
              <TableHead>Created</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {workflows.map((wf) => (
              <TableRow key={wf.id}>
                <TableCell className="font-medium">{wf.name}</TableCell>
                <TableCell>v{wf.version}</TableCell>
                <TableCell>
                  {new Date(wf.created_at).toLocaleDateString("zh-CN")}
                </TableCell>
                <TableCell>
                  <Link
                    href={`/workflows/${wf.id}`}
                    className="text-sm text-blue-600 hover:underline"
                  >
                    Edit
                  </Link>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
