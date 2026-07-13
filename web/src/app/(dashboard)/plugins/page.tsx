"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { api } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Puzzle, CheckCircle, XCircle, AlertTriangle, Globe, Building2 } from "lucide-react";

interface Plugin {
  id: string;
  name: string;
  type: string;
  version: string;
  status: string;
  entry: string;
  manifest_: Record<string, unknown>;
  config: Record<string, unknown>;
  workspace_id: string | null;
}

const STATUS_STYLES: Record<string, string> = {
  enabled: "bg-green-100 text-green-800",
  installed: "bg-gray-100 text-gray-800",
  disabled: "bg-gray-100 text-gray-800",
  error: "bg-red-100 text-red-800",
};

const STATUS_ICONS: Record<string, typeof CheckCircle> = {
  enabled: CheckCircle,
  disabled: XCircle,
  error: AlertTriangle,
};

export default function PluginsPage() {
  const [plugins, setPlugins] = useState<Plugin[]>([]);
  const [loading, setLoading] = useState(true);

  const fetchPlugins = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.get<Plugin[]>("/api/plugins");
      setPlugins(data);
    } catch {
      setPlugins([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchPlugins();
  }, [fetchPlugins]);

  const toggleStatus = async (plugin: Plugin) => {
    const action = plugin.status === "enabled" ? "disable" : "enable";
    try {
      await api.post(`/api/plugins/${plugin.id}/${action}`);
      await fetchPlugins();
    } catch {
      // ignore
    }
  };

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold">Plugins</h1>
          <p className="text-muted-foreground">Manage installed plugins and their configurations</p>
        </div>
        <div className="flex items-center gap-2">
          <input
            type="file"
            accept=".hecate-plugin"
            className="hidden"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              const formData = new FormData();
              formData.append("file", file);
              try {
                await fetch("/api/plugins/upload", { method: "POST", body: formData });
                fetchPlugins();
              } catch {
                // ignore
              }
              e.target.value = "";
            }}
            id="plugin-upload"
          />
          <Button
            variant="outline"
            size="sm"
            onClick={() => document.getElementById("plugin-upload")?.click()}
          >
            Upload Plugin
          </Button>
          <Link href="/plugins/create">
            <Button size="sm">Create Plugin</Button>
          </Link>
          <Button variant="outline" size="sm" onClick={fetchPlugins}>
            Refresh
          </Button>
        </div>
      </div>

      {loading ? (
        <div className="py-12 text-center text-muted-foreground">Loading plugins...</div>
      ) : plugins.length === 0 ? (
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground">
            <Puzzle className="mx-auto mb-2 h-8 w-8" />
            <p>No plugins installed</p>
            <p className="mt-1 text-xs">Place plugin packages in the plugins/ directory to get started</p>
          </CardContent>
        </Card>
      ) : (
        <div className="rounded-md border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b bg-muted/50">
                <th className="px-4 py-3 text-left font-medium">Name</th>
                <th className="px-4 py-3 text-left font-medium">Type</th>
                <th className="px-4 py-3 text-left font-medium">Version</th>
                <th className="px-4 py-3 text-left font-medium">Status</th>
                <th className="px-4 py-3 text-left font-medium">Scope</th>
                <th className="px-4 py-3 text-right font-medium">Actions</th>
              </tr>
            </thead>
            <tbody>
              {plugins.map((plugin) => {
                const Icon = STATUS_ICONS[plugin.status] || CheckCircle;
                return (
                  <tr key={plugin.id} className="border-b last:border-0">
                    <td className="px-4 py-3">
                      <Link href={`/plugins/${plugin.id}`} className="font-medium hover:underline">
                        {plugin.name}
                      </Link>
                    </td>
                    <td className="px-4 py-3 text-muted-foreground">{plugin.type}</td>
                    <td className="px-4 py-3 text-muted-foreground">{plugin.version}</td>
                    <td className="px-4 py-3">
                      <Badge className={STATUS_STYLES[plugin.status] || STATUS_STYLES.installed}>
                        <Icon className="mr-1 h-3 w-3" />
                        {plugin.status}
                      </Badge>
                    </td>
                    <td className="px-4 py-3">
                      {plugin.workspace_id ? (
                        <span className="inline-flex items-center gap-1 text-muted-foreground">
                          <Building2 className="h-3 w-3" /> Workspace
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-muted-foreground">
                          <Globe className="h-3 w-3" /> Platform
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-right">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => toggleStatus(plugin)}
                      >
                        {plugin.status === "enabled" ? "Disable" : "Enable"}
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
