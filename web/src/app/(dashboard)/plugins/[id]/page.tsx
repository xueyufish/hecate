"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ArrowLeft, CheckCircle, XCircle, AlertTriangle, Save } from "lucide-react";
import { MCPStatusPanel } from "./mcp-status";

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

interface ConfigField {
  key: string;
  type: string;
  description?: string;
  required?: boolean;
  secret?: boolean;
  enum?: string[];
  minimum?: number;
  maximum?: number;
  default?: unknown;
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

function parseConfigSchema(schema: Record<string, unknown> | null | undefined): ConfigField[] {
  if (!schema || typeof schema !== "object") return [];
  const properties = (schema.properties as Record<string, Record<string, unknown>>) || {};
  const required = (schema.required as string[]) || [];

  return Object.entries(properties).map(([key, prop]) => ({
    key,
    type: (prop.type as string) || "string",
    description: prop.description as string | undefined,
    required: required.includes(key),
    secret: (prop.secret as boolean) || (prop.format as string) === "password",
    enum: prop.enum as string[] | undefined,
    minimum: prop.minimum as number | undefined,
    maximum: prop.maximum as number | undefined,
    default: prop.default,
  }));
}

export default function PluginDetailPage() {
  const params = useParams();
  const pluginId = params.id as string;
  const [plugin, setPlugin] = useState<Plugin | null>(null);
  const [configValues, setConfigValues] = useState<Record<string, unknown>>({});
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState<string | null>(null);

  const fetchPlugin = useCallback(async () => {
    try {
      const data = await api.get<Plugin>(`/api/plugins/${pluginId}`);
      setPlugin(data);
      setConfigValues(data.config || {});
    } catch {
      setPlugin(null);
    }
  }, [pluginId]);

  useEffect(() => {
    fetchPlugin();
  }, [fetchPlugin]);

  const toggleStatus = async () => {
    if (!plugin) return;
    const action = plugin.status === "enabled" ? "disable" : "enable";
    try {
      await api.post(`/api/plugins/${plugin.id}/${action}`);
      await fetchPlugin();
    } catch {
      // ignore
    }
  };

  const saveConfig = async () => {
    if (!plugin) return;
    setSaving(true);
    setSaveMsg(null);
    try {
      await api.put(`/api/plugins/${plugin.id}/config`, { config: configValues });
      setSaveMsg("Configuration saved");
      setTimeout(() => setSaveMsg(null), 3000);
    } catch {
      setSaveMsg("Failed to save configuration");
    } finally {
      setSaving(false);
    }
  };

  const configSchema = plugin?.manifest_?.config_schema as Record<string, unknown> | undefined;
  const configFields = parseConfigSchema(configSchema);
  const permissions = (plugin?.manifest_?.permissions as string[]) || [];
  const Icon = plugin ? STATUS_ICONS[plugin.status] || CheckCircle : CheckCircle;

  if (!plugin) {
    return (
      <div className="container mx-auto p-6">
        <div className="py-12 text-center text-muted-foreground">Loading plugin...</div>
      </div>
    );
  }

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center gap-4">
        <Link href="/plugins" className="text-muted-foreground hover:text-foreground">
          <ArrowLeft className="h-4 w-4" />
        </Link>
        <div className="flex-1">
          <h1 className="text-2xl font-bold">{plugin.name}</h1>
          <p className="text-muted-foreground">{(plugin.manifest_?.description as string) || plugin.type}</p>
        </div>
        <Button variant="outline" size="sm" onClick={toggleStatus}>
          {plugin.status === "enabled" ? "Disable" : "Enable"}
        </Button>
        {plugin.workspace_id !== null || !plugin.entry.startsWith("python:hecate.") ? (
          <Button
            variant="destructive"
            size="sm"
            onClick={async () => {
              try {
                await fetch(`/api/plugins/${plugin.id}`, { method: "DELETE" });
                window.location.href = "/plugins";
              } catch {
                // ignore
              }
            }}
          >
            Uninstall
          </Button>
        ) : null}
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        {/* Manifest Info */}
        <Card>
          <CardHeader>
            <CardTitle className="text-sm">Plugin Information</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">Type</span>
              <span>{plugin.type}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Version</span>
              <span>{plugin.version}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Status</span>
              <Badge className={STATUS_STYLES[plugin.status] || STATUS_STYLES.installed}>
                <Icon className="mr-1 h-3 w-3" />
                {plugin.status}
              </Badge>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">Entry</span>
              <span className="max-w-48 truncate font-mono text-xs">{plugin.entry}</span>
            </div>
            {permissions.length > 0 && (
              <div>
                <span className="text-muted-foreground">Permissions</span>
                <div className="mt-1 flex flex-wrap gap-1">
                  {permissions.map((p) => (
                    <Badge key={p} variant="outline" className="text-xs">{p}</Badge>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* MCP Status Panel — only for mcp:// plugins */}
        {plugin.entry.startsWith("mcp://") && (
          <MCPStatusPanel pluginName={plugin.name} />
        )}

        {/* Config Form */}
        {configFields.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle className="text-sm">Configuration</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              {configFields.map((field) => (
                <div key={field.key} className="space-y-1">
                  <Label htmlFor={field.key} className="text-sm">
                    {field.description || field.key}
                    {field.required && <span className="ml-1 text-red-500">*</span>}
                  </Label>
                  {field.enum ? (
                    <select
                      id={field.key}
                      className="w-full rounded-md border bg-background px-3 py-2 text-sm"
                      value={(configValues[field.key] as string) || ""}
                      onChange={(e) => setConfigValues({ ...configValues, [field.key]: e.target.value })}
                    >
                      <option value="">Select...</option>
                      {field.enum.map((opt) => (
                        <option key={opt} value={opt}>{opt}</option>
                      ))}
                    </select>
                  ) : field.type === "boolean" ? (
                    <div className="flex items-center gap-2">
                      <input
                        id={field.key}
                        type="checkbox"
                        checked={(configValues[field.key] as boolean) || false}
                        onChange={(e) => setConfigValues({ ...configValues, [field.key]: e.target.checked })}
                      />
                      <span className="text-xs text-muted-foreground">Enabled</span>
                    </div>
                  ) : field.type === "number" || field.type === "integer" ? (
                    <Input
                      id={field.key}
                      type="number"
                      min={field.minimum}
                      max={field.maximum}
                      value={(configValues[field.key] as number) ?? ""}
                      onChange={(e) => setConfigValues({ ...configValues, [field.key]: Number(e.target.value) })}
                    />
                  ) : (
                    <Input
                      id={field.key}
                      type={field.secret ? "password" : "text"}
                      value={(configValues[field.key] as string) || ""}
                      onChange={(e) => setConfigValues({ ...configValues, [field.key]: e.target.value })}
                    />
                  )}
                </div>
              ))}
              <div className="flex items-center gap-2 pt-2">
                <Button size="sm" onClick={saveConfig} disabled={saving}>
                  <Save className="mr-1 h-3 w-3" />
                  {saving ? "Saving..." : "Save Configuration"}
                </Button>
                {saveMsg && (
                  <span className={`text-xs ${saveMsg.includes("Failed") ? "text-red-600" : "text-green-600"}`}>
                    {saveMsg}
                  </span>
                )}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}
