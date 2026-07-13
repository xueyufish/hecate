"use client";

import { useState } from "react";
import Link from "next/link";
import { api } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ArrowLeft, Save } from "lucide-react";

const PLUGIN_TYPES = [
  { value: "tool", label: "Tool Plugin" },
  { value: "trigger", label: "Trigger Plugin" },
];

export default function CreatePluginPage() {
  const [name, setName] = useState("");
  const [type, setType] = useState("tool");
  const [description, setDescription] = useState("");
  const [apiUrl, setApiUrl] = useState("");
  const [webhookPath, setWebhookPath] = useState("");
  const [cronExpr, setCronExpr] = useState("");
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const handleSave = async () => {
    setSaving(true);
    setMsg(null);
    try {
      const manifest: Record<string, unknown> = {
        name,
        type,
        description,
      };
      if (type === "tool" && apiUrl) {
        manifest.api_endpoint = apiUrl;
      }
      if (type === "trigger") {
        if (webhookPath) manifest.webhook_path = webhookPath;
        if (cronExpr) manifest.cron = cronExpr;
      }
      await api.post("/api/plugins/create", { manifest });
      setMsg("Plugin created");
      setTimeout(() => setMsg(null), 3000);
    } catch {
      setMsg("Failed to create plugin");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center gap-4">
        <Link href="/plugins" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <h1 className="text-2xl font-bold">Create Plugin</h1>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-sm">Plugin Details</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <Label htmlFor="name">Plugin Name</Label>
            <Input
              id="name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-web-search"
            />
          </div>

          <div className="space-y-1">
            <Label htmlFor="type">Plugin Type</Label>
            <select
              id="type"
              className="w-full rounded-md border bg-background px-3 py-2 text-sm"
              value={type}
              onChange={(e) => setType(e.target.value)}
            >
              {PLUGIN_TYPES.map((t) => (
                <option key={t.value} value={t.value}>{t.label}</option>
              ))}
            </select>
          </div>

          <div className="space-y-1">
            <Label htmlFor="desc">Description</Label>
            <Input
              id="desc"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Search the web for information"
            />
          </div>

          {type === "tool" && (
            <div className="space-y-1">
              <Label htmlFor="apiUrl">API Endpoint URL (optional)</Label>
              <Input
                id="apiUrl"
                value={apiUrl}
                onChange={(e) => setApiUrl(e.target.value)}
                placeholder="https://api.example.com/search"
              />
            </div>
          )}

          {type === "trigger" && (
            <>
              <div className="space-y-1">
                <Label htmlFor="webhookPath">Webhook Path (optional)</Label>
                <Input
                  id="webhookPath"
                  value={webhookPath}
                  onChange={(e) => setWebhookPath(e.target.value)}
                  placeholder="/trigger/my-webhook"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="cron">Cron Expression (optional)</Label>
                <Input
                  id="cron"
                  value={cronExpr}
                  onChange={(e) => setCronExpr(e.target.value)}
                  placeholder="0 */6 * * *"
                />
              </div>
            </>
          )}

          <div className="flex items-center gap-2 pt-2">
            <Button size="sm" onClick={handleSave} disabled={saving || !name}>
              <Save className="mr-1 h-3 w-3" />
              {saving ? "Creating..." : "Create Plugin"}
            </Button>
            {msg && (
              <span className={`text-xs ${msg.includes("Failed") ? "text-red-600" : "text-green-600"}`}>
                {msg}
              </span>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
