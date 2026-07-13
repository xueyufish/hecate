"use client";

import Link from "next/link";
import { useAuth } from "@/lib/auth";
import { Bot, Database, LogOut, Settings, Share2, Activity, DollarSign, Gauge, Users, MessageSquare, Puzzle } from "lucide-react";

export function Sidebar() {
  const { userEmail, logout } = useAuth();

  return (
    <aside className="flex h-screen w-60 flex-col border-r bg-muted/30">
      <div className="flex items-center gap-2 border-b px-4 py-4">
        <Bot className="h-6 w-6" />
        <span className="text-lg font-semibold">Hecate</span>
      </div>
      <nav className="flex-1 space-y-1 p-2">
        <Link
          href="/agents"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted"
        >
          <Bot className="h-4 w-4" />
          Agents
        </Link>
        <Link
          href="/workflows"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted"
        >
          <Share2 className="h-4 w-4" />
          Workflows
        </Link>
        <Link
          href="/knowledge"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted"
        >
          <Database className="h-4 w-4" />
          Knowledge
        </Link>
        <Link
          href="/settings/models"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted"
        >
          <Settings className="h-4 w-4" />
          Settings
        </Link>
        <Link
          href="/settings/models/monitoring"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted"
        >
          <Activity className="h-4 w-4" />
          Monitoring
        </Link>
        <Link
          href="/settings/models/cost-analysis"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted"
        >
          <DollarSign className="h-4 w-4" />
          Cost Analysis
        </Link>
        <Link
          href="/ops-center"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted"
        >
          <Gauge className="h-4 w-4" />
          Ops Center
        </Link>
        <Link
          href="/ops-center/agents"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted"
        >
          <Users className="h-4 w-4" />
          Agent Health
        </Link>
        <Link
          href="/ops-center/conversations"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted"
        >
          <MessageSquare className="h-4 w-4" />
          Conversations
        </Link>
        <Link
          href="/plugins"
          className="flex items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-muted"
        >
          <Puzzle className="h-4 w-4" />
          Plugins
        </Link>
      </nav>
      <div className="border-t p-3">
        <div className="mb-2 truncate text-xs text-muted-foreground">
          {userEmail}
        </div>
        <button
          onClick={logout}
          className="flex w-full items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <LogOut className="h-4 w-4" />
          Logout
        </button>
      </div>
    </aside>
  );
}
