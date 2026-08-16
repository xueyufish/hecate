"use client";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ReplayEvent } from "@/lib/api-client";

interface Props {
  event: ReplayEvent | null;
}

export function EventDetail({ event }: Props) {
  if (!event) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Event Detail</CardTitle>
        </CardHeader>
        <CardContent className="text-sm text-gray-500">
          Select an event from the timeline.
        </CardContent>
      </Card>
    );
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle>Event Detail</CardTitle>
      </CardHeader>
      <CardContent>
        <dl className="space-y-1 text-sm">
          <div className="flex justify-between">
            <dt className="text-gray-500">type</dt>
            <dd>{event.event_type}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-gray-500">version</dt>
            <dd>{event.version}</dd>
          </div>
          <div className="flex justify-between">
            <dt className="text-gray-500">superstep</dt>
            <dd>{event.superstep}</dd>
          </div>
          {event.node_id && (
            <div className="flex justify-between">
              <dt className="text-gray-500">node</dt>
              <dd className="font-mono">{event.node_id}</dd>
            </div>
          )}
          <div className="flex justify-between">
            <dt className="text-gray-500">timestamp</dt>
            <dd>{event.timestamp}</dd>
          </div>
        </dl>
        <div className="mt-3">
          <div className="text-xs text-gray-500 mb-1">payload</div>
          <pre className="text-xs bg-gray-50 p-2 rounded overflow-auto max-h-64">
            {JSON.stringify(event.payload, null, 2)}
          </pre>
        </div>
      </CardContent>
    </Card>
  );
}