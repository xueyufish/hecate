"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Upload } from "lucide-react";

interface Document {
  id: string;
  filename: string;
  file_size: number;
  content_type: string;
  parsing_status: "pending" | "parsing" | "completed" | "failed";
  parsing_error: string | null;
  chunk_count: number;
}

interface KB {
  id: string;
  name: string;
}

const statusMap: Record<string, { label: string; variant: "default" | "secondary" | "destructive" | "outline" }> = {
  pending: { label: "等待中", variant: "outline" },
  parsing: { label: "解析中", variant: "secondary" },
  completed: { label: "已完成", variant: "default" },
  failed: { label: "失败", variant: "destructive" },
};

export default function KnowledgeDetailPage() {
  const params = useParams();
  const kbId = params.id as string;
  const [kb, setKB] = useState<KB | null>(null);
  const [docs, setDocs] = useState<Document[]>([]);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.get<KB>(`/api/knowledge-bases/${kbId}`).then(setKB).catch(() => {});
    loadDocs();
  }, [kbId]);

  const loadDocs = async () => {
    try {
      const kbData = await api.get<{ documents?: Document[] }>(
        `/api/knowledge-bases/${kbId}`
      );
      setDocs(kbData.documents || []);
    } catch {
      setDocs([]);
    }
  };

  const handleUpload = async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setUploading(true);
    try {
      for (const file of Array.from(files)) {
        await api.upload(`/api/knowledge-bases/${kbId}/upload`, file);
      }
      await loadDocs();
    } catch {
      alert("上传失败");
    } finally {
      setUploading(false);
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  if (!kb) {
    return <div className="text-muted-foreground">加载中...</div>;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{kb.name}</h1>

      <div className="rounded-lg border border-dashed p-8 text-center">
        <Upload className="mx-auto mb-3 h-8 w-8 text-muted-foreground" />
        <p className="mb-3 text-sm text-muted-foreground">
          支持 PDF, DOCX, TXT, MD 格式
        </p>
        <Button
          variant="outline"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
        >
          {uploading ? "上传中..." : "上传文档"}
        </Button>
        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.txt,.md"
          multiple
          className="hidden"
          onChange={(e) => handleUpload(e.target.files)}
        />
      </div>

      {docs.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>文件名</TableHead>
              <TableHead>大小</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>分块数</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {docs.map((doc) => (
              <TableRow key={doc.id}>
                <TableCell>{doc.filename}</TableCell>
                <TableCell>{formatSize(doc.file_size)}</TableCell>
                <TableCell>
                  <Badge variant={statusMap[doc.parsing_status]?.variant || "outline"}>
                    {statusMap[doc.parsing_status]?.label || doc.parsing_status}
                  </Badge>
                  {doc.parsing_status === "failed" && doc.parsing_error && (
                    <p className="mt-1 text-xs text-red-500">{doc.parsing_error}</p>
                  )}
                </TableCell>
                <TableCell>{doc.chunk_count}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
