"use client";

import Link from "next/link";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { LifecycleBadge } from "@/components/tasks/state-badge";
import type { TaskSummary } from "@/lib/task-types";

function createdLabel(created_at: string | null): string {
  if (!created_at) return "未记录";
  const date = new Date(created_at);
  return Number.isNaN(date.getTime()) ? created_at : date.toLocaleString();
}

export function TaskListTable({ tasks }: { tasks: TaskSummary[] }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>目标</TableHead>
          <TableHead>生命周期状态</TableHead>
          <TableHead>创建时间</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {tasks.map((task) => (
          <TableRow key={task.task_id} data-testid="task-row">
            <TableCell>
              <Link
                href={`/ops-center/tasks/${task.task_id}`}
                className="font-medium hover:underline"
              >
                {task.goal || "（无目标摘要）"}
              </Link>
            </TableCell>
            <TableCell>
              <LifecycleBadge state={task.lifecycle_state} />
            </TableCell>
            <TableCell>{createdLabel(task.created_at)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
