"use client";

import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { replayApi, type SessionDetail } from "@/lib/api-client";
import { ReplayView } from "@/components/replay/replay-view";

interface Props {
  params: { id: string };
}

export default function ConversationDetailPage({ params }: Props) {
  const sessionId = params.id;
  const [session, setSession] = useState<SessionDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const s = await replayApi.getSession(sessionId);
        if (!cancelled) setSession(s);
      } catch (e) {
        if (!cancelled) setError((e as Error).message ?? "load failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId]);

  if (error) {
    return (
      <div className="p-6">
        <Card>
          <CardContent className="pt-6 text-red-600">{error}</CardContent>
        </Card>
      </div>
    );
  }
  if (!session) {
    return <div className="p-6 text-gray-500">Loading…</div>;
  }

  const hasLog = session.log_version > 0;

  return (
    <div className="p-6 space-y-6">
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold">Session {sessionId.slice(0, 8)}</h1>
        <div className="flex gap-2 text-sm text-gray-500">
          <Badge>{session.status}</Badge>
          <span>log_version={session.log_version}</span>
        </div>
      </header>

      {hasLog ? (
        <ReplayView sessionId={sessionId} />
      ) : (
        <Card>
          <CardHeader>
            <CardTitle>No event log</CardTitle>
          </CardHeader>
          <CardContent className="text-sm text-gray-500">
            This session has no execution log entries (path A / path C calls are not recorded).
          </CardContent>
        </Card>
      )}
    </div>
  );
}