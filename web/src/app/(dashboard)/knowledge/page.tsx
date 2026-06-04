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
    return <div className="text-muted-foreground">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">Knowledge Bases</h1>
        <Link href="/knowledge/new">
          <Button>
            <Plus className="mr-2 h-4 w-4" />
            Create Knowledge Base
          </Button>
        </Link>
      </div>

      {kbs.length === 0 ? (
        <div className="flex flex-col items-center justify-center rounded-lg border border-dashed py-16">
          <Database className="mb-4 h-12 w-12 text-muted-foreground" />
          <p className="mb-4 text-muted-foreground">
            No knowledge bases yet, click to create your first
          </p>
          <Link href="/knowledge/new">
            <Button>Create Knowledge Base</Button>
          </Link>
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Documents</TableHead>
              <TableHead>Created</TableHead>
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
                  {new Date(kb.created_at).toLocaleDateString("en-US")}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
