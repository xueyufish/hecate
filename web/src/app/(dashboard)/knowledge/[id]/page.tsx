"use client";

import { useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api-client";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Upload, Globe } from "lucide-react";

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
  pending: { label: "Pending", variant: "outline" },
  parsing: { label: "Parsing", variant: "secondary" },
  completed: { label: "Completed", variant: "default" },
  failed: { label: "Failed", variant: "destructive" },
};

export default function KnowledgeDetailPage() {
  const params = useParams();
  const kbId = params.id as string;
  const [kb, setKB] = useState<KB | null>(null);
  const [docs, setDocs] = useState<Document[]>([]);
  const [uploading, setUploading] = useState(false);
  const [urls, setUrls] = useState("");
  const [crawling, setCrawling] = useState(false);
  const [crawlResult, setCrawlResult] = useState<{ success: number; failed: number; total: number } | null>(null);
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
      alert("Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  const handleCrawl = async () => {
    if (!urls.trim()) return;
    setCrawling(true);
    setCrawlResult(null);
    try {
      const urlList = urls.split("\n").map((u) => u.trim()).filter(Boolean);
      const payload = urlList.length === 1 ? { url: urlList[0] } : { urls: urlList };
      const result = await api.post<{ success: number; failed: number; total: number }>(
        `/api/knowledge-bases/${kbId}/urls`,
        payload,
      );
      setCrawlResult(result);
      setUrls("");
      await loadDocs();
    } catch {
      alert("Crawl failed");
    } finally {
      setCrawling(false);
    }
  };

  if (!kb) {
    return <div className="text-muted-foreground">Loading...</div>;
  }

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">{kb.name}</h1>

      <div className="rounded-lg border border-dashed p-8 text-center">
        <Upload className="mx-auto mb-3 h-8 w-8 text-muted-foreground" />
        <p className="mb-3 text-sm text-muted-foreground">
          Supports PDF, DOCX, TXT, MD formats
        </p>
        <Button
          variant="outline"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
        >
          {uploading ? "Uploading..." : "Upload Document"}
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

      <div className="rounded-lg border p-6 space-y-4">
        <div className="flex items-center gap-2">
          <Globe className="h-5 w-5 text-muted-foreground" />
          <h2 className="text-lg font-medium">Crawl from Web</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          Enter URLs, the system will automatically crawl and add to knowledge base
        </p>
        <div className="space-y-2">
          <Label htmlFor="urls">URLs (one per line, batch supported)</Label>
          <Textarea
            id="urls"
            value={urls}
            onChange={(e) => setUrls(e.target.value)}
            rows={3}
            placeholder="https://example.com/article&#10;https://example.com/docs"
          />
        </div>
        <div className="flex items-center gap-4">
          <Button onClick={handleCrawl} disabled={crawling || !urls.trim()}>
            {crawling ? "Crawling..." : "Start Crawling"}
          </Button>
          {crawlResult && (
            <span className="text-sm text-muted-foreground">
              Success: {crawlResult.success}, Failed: {crawlResult.failed}, Total: {crawlResult.total}
            </span>
          )}
        </div>
      </div>

      {docs.length > 0 && (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Filename</TableHead>
              <TableHead>Size</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Chunks</TableHead>
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
