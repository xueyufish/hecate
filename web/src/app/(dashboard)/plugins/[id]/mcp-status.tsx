"use client";

import { useEffect, useState, useCallback } from "react";
import { api } from "@/lib/api-client";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { RefreshCw, Wifi, WifiOff, AlertTriangle, Loader2 } from "lucide-react";

interface MCPConnectionStatus {
  registered: boolean;
  name: string;
  endpoint: string;
  transport: string;
  workspace_id: string | null;
  circuit_state: string;
  reconnecting: boolean;
  pool: {
    active: number;
    idle: number;
    total: number;
    max: number;
    healthy: boolean;
  };
  tool_count: number;
  tools_cached: boolean;
}

const STATUS_CONFIG: Record<string, { color: string; icon: typeof Wifi; label: string }> = {
  healthy: { color: "bg-green-100 text-green-800", icon: Wifi, label: "Healthy" },
  unhealthy: { color: "bg-red-100 text-red-800", icon: WifiOff, label: "Unhealthy" },
  reconnecting: { color: "bg-yellow-100 text-yellow-800", icon: Loader2, label: "Reconnecting" },
  disconnected: { color: "bg-gray-100 text-gray-800", icon: WifiOff, label: "Disconnected" },
  circuit_open: { color: "bg-red-100 text-red-800", icon: AlertTriangle, label: "Circuit Open" },
};

function getStatusKey(status: MCPConnectionStatus): string {
  if (status.reconnecting) return "reconnecting";
  if (status.circuit_state === "open") return "circuit_open";
  if (!status.pool.healthy) return "unhealthy";
  if (status.pool.total > 0) return "healthy";
  return "disconnected";
}

export function MCPStatusPanel({ pluginName }: { pluginName: string }) {
  const [status, setStatus] = useState<MCPConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  const fetchStatus = useCallback(async () => {
    try {
      const data = await api.get<MCPConnectionStatus>(`/api/mcp/connections/${pluginName}`);
      setStatus(data);
    } catch {
      setStatus(null);
    } finally {
      setLoading(false);
    }
  }, [pluginName]);

  useEffect(() => {
    fetchStatus();
    const interval = setInterval(fetchStatus, 30000);
    return () => clearInterval(interval);
  }, [fetchStatus]);

  const handleReconnect = async () => {
    setActionLoading("reconnect");
    try {
      await api.post(`/api/mcp/connections/${pluginName}/reconnect`);
      setTimeout(fetchStatus, 1000);
    } catch {
      // ignore
    } finally {
      setActionLoading(null);
    }
  };

  const handleSync = async () => {
    setActionLoading("sync");
    try {
      await api.post(`/api/mcp/connections/${pluginName}/sync`);
      await fetchStatus();
    } catch {
      // ignore
    } finally {
      setActionLoading(null);
    }
  };

  if (loading) {
    return (
      <Card>
        <CardContent className="py-6 text-center text-muted-foreground">
          Loading MCP status...
        </CardContent>
      </Card>
    );
  }

  if (!status || !status.registered) {
    return null;
  }

  const statusKey = getStatusKey(status);
  const config = STATUS_CONFIG[statusKey];
  const StatusIcon = config.icon;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">MCP Connection</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Status</span>
          <Badge className={config.color}>
            <StatusIcon className="mr-1 h-3 w-3" />
            {config.label}
          </Badge>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Endpoint</span>
          <span className="max-w-48 truncate font-mono text-xs">{status.endpoint}</span>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Pool</span>
          <span className="text-sm">
            {status.pool.active} active / {status.pool.idle} idle / {status.pool.max} max
          </span>
        </div>

        <div className="flex items-center justify-between">
          <span className="text-muted-foreground">Tools</span>
          <span className="text-sm">{status.tool_count} cached</span>
        </div>

        {status.circuit_state !== "closed" && (
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Circuit</span>
            <Badge variant="outline" className="text-xs">
              {status.circuit_state}
            </Badge>
          </div>
        )}

        <div className="flex gap-2 pt-2">
          <Button
            size="sm"
            variant="outline"
            onClick={handleReconnect}
            disabled={actionLoading === "reconnect"}
          >
            <RefreshCw className={`mr-1 h-3 w-3 ${actionLoading === "reconnect" ? "animate-spin" : ""}`} />
            {actionLoading === "reconnect" ? "Reconnecting..." : "Reconnect"}
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={handleSync}
            disabled={actionLoading === "sync"}
          >
            <RefreshCw className={`mr-1 h-3 w-3 ${actionLoading === "sync" ? "animate-spin" : ""}`} />
            {actionLoading === "sync" ? "Syncing..." : "Sync Tools"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
