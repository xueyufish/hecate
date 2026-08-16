"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { replayApi, type ReplayStateResponse } from "@/lib/api-client";

interface Props {
  sessionId: string;
  atVersion: number;
}

export function StateInspector({ sessionId, atVersion }: Props) {
  const [data, setData] = useState<ReplayStateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setError(null);
    setData(null);
    (async () => {
      try {
        const resp = await replayApi.getReplayState(sessionId, atVersion);
        if (!cancelled) setData(resp);
      } catch (e: unknown) {
        const err = e as { error?: { code?: string } };
        if (!cancelled) setError(err?.error?.code ?? "load_failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId, atVersion]);

  return (
    <Card>
      <CardHeader>
        <CardTitle>State Inspector (v{atVersion})</CardTitle>
      </CardHeader>
      <CardContent>
        {error && <div className="text-sm text-red-600">{error}</div>}
        {data && (
          <div className="space-y-2 text-sm">
            <div className="flex gap-3">
              <span className="text-gray-500">effective_version:</span>
              <span>{data.effective_version}</span>
              {data.fell_back && (
                <span className="text-amber-600 text-xs">
                  (fell back from v{data.requested_version})
                </span>
              )}
            </div>
            <div className="flex gap-3">
              <span className="text-gray-500">commit_points:</span>
              <span>{data.commit_points.join(", ") || "(none)"}</span>
            </div>
            <div>
              <div className="text-xs text-gray-500 mb-1">messages ({data.messages.length})</div>
              <pre className="text-xs bg-gray-50 p-2 rounded overflow-auto max-h-48">
                {JSON.stringify(data.messages, null, 2)}
              </pre>
            </div>
            <div>
              <div className="text-xs text-gray-500 mb-1">channel_state</div>
              <pre className="text-xs bg-gray-50 p-2 rounded overflow-auto max-h-48">
                {JSON.stringify(data.channel_state, null, 2)}
              </pre>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}