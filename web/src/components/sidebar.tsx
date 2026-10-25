"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useAuth } from "@/lib/auth";
import { api } from "@/lib/api-client";
import { Bot, Brain, ClipboardCheck, Database, ListTodo, LogOut, PanelLeftClose, PanelLeftOpen, Settings, Share2, Activity, DollarSign, Gauge, Users, MessageSquare, Puzzle } from "lucide-react";

const STORAGE_KEY = "hecate.sidebar.collapsed";

export function Sidebar() {
  const { userEmail, logout } = useAuth();
  // Memory Center is editor+; viewers never see the entry. The probe is
  // the cheapest governance GET — a 403 hides the link.
  const [canGovern, setCanGovern] = useState(false);
  // Persisted so the demo can keep the rail collapsed across navigation.
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    setCollapsed(window.localStorage.getItem(STORAGE_KEY) === "1");
    api
      .get("/api/memory/governance/stats")
      .then(() => setCanGovern(true))
      .catch(() => setCanGovern(false));
  }, []);

  const toggleCollapsed = () => {
    setCollapsed((prev) => {
      window.localStorage.setItem(STORAGE_KEY, prev ? "0" : "1");
      return !prev;
    });
  };

  const navItems: { href: string; label: string; icon: React.ReactNode; visible?: boolean }[] = [
    { href: "/agents", label: "Agents", icon: <Bot className="h-4 w-4" /> },
    { href: "/memory", label: "Memory", icon: <Brain className="h-4 w-4" />, visible: canGovern },
    { href: "/workflows", label: "Workflows", icon: <Share2 className="h-4 w-4" /> },
    { href: "/knowledge", label: "Knowledge", icon: <Database className="h-4 w-4" /> },
    { href: "/settings/models", label: "Settings", icon: <Settings className="h-4 w-4" /> },
    { href: "/settings/models/monitoring", label: "Monitoring", icon: <Activity className="h-4 w-4" /> },
    { href: "/settings/models/cost-analysis", label: "Cost Analysis", icon: <DollarSign className="h-4 w-4" /> },
    { href: "/ops-center", label: "Ops Center", icon: <Gauge className="h-4 w-4" /> },
    { href: "/ops-center/tasks", label: "Tasks", icon: <ListTodo className="h-4 w-4" /> },
    { href: "/ops-center/agents", label: "Agent Health", icon: <Users className="h-4 w-4" /> },
    { href: "/ops-center/conversations", label: "Conversations", icon: <MessageSquare className="h-4 w-4" /> },
    { href: "/ops-center/evaluation", label: "Evaluation", icon: <ClipboardCheck className="h-4 w-4" /> },
    { href: "/plugins", label: "Plugins", icon: <Puzzle className="h-4 w-4" /> },
  ];

  return (
    <aside
      className={`flex h-screen flex-col border-r bg-muted/30 transition-all ${
        collapsed ? "w-14" : "w-60"
      }`}
    >
      <div
        className={`flex items-center border-b px-2 py-4 ${
          collapsed ? "justify-center" : "gap-2 px-4"
        }`}
      >
        <button
          onClick={toggleCollapsed}
          title={collapsed ? "展开菜单" : "收起菜单"}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="rounded-md p-1 hover:bg-muted"
        >
          {collapsed ? (
            <PanelLeftOpen className="h-5 w-5" />
          ) : (
            <PanelLeftClose className="h-5 w-5" />
          )}
        </button>
        {!collapsed && (
          <>
            <Bot className="h-6 w-6" />
            <span className="text-lg font-semibold">Hecate</span>
          </>
        )}
      </div>
      <nav className="flex-1 space-y-1 p-2">
        {navItems
          .filter((item) => item.visible !== false)
          .map((item) => (
            <Link
              key={item.href}
              href={item.href}
              title={collapsed ? item.label : undefined}
              data-testid={item.href === "/memory" ? "nav-memory" : undefined}
              className={`flex items-center rounded-md px-3 py-2 text-sm hover:bg-muted ${
                collapsed ? "justify-center px-0" : "gap-3"
              }`}
            >
              {item.icon}
              {!collapsed && item.label}
            </Link>
          ))}
      </nav>
      <div className={`border-t p-3 ${collapsed ? "px-2" : ""}`}>
        {!collapsed && (
          <div className="mb-2 truncate text-xs text-muted-foreground">
            {userEmail}
          </div>
        )}
        <button
          onClick={logout}
          title={collapsed ? "Logout" : undefined}
          className={`flex w-full items-center rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-muted hover:text-foreground ${
            collapsed ? "justify-center px-0" : "gap-2"
          }`}
        >
          <LogOut className="h-4 w-4" />
          {!collapsed && "Logout"}
        </button>
      </div>
    </aside>
  );
}
