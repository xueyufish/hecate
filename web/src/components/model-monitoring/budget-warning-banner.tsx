"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { Alert, AlertDescription } from "@/components/ui/alert";
import { AlertTriangle } from "lucide-react";

interface BudgetStatus {
  limit_amount: number;
  spent_amount: number;
  utilization_pct: number;
  status_band: string;
}

export function BudgetWarningBanner() {
  const [warning, setWarning] = useState<string | null>(null);

  useEffect(() => {
    const checkBudget = async () => {
      try {
        const budgets = await api.get<{ id: string; scope: string; limit_amount: number }[]>(
          "/api/models/cost/budgets"
        );
        const budgetList = Array.isArray(budgets) ? budgets : [];
        for (const budget of budgetList) {
          try {
            const status = await api.get<BudgetStatus>(`/api/models/cost/budgets/${budget.id}/status`);
            if (status && status.utilization_pct >= 80) {
              setWarning(
                `Budget ${budget.scope}: $${status.spent_amount.toFixed(2)} / $${status.limit_amount.toFixed(2)} (${status.utilization_pct.toFixed(0)}% used)`
              );
              return;
            }
          } catch {
            // skip
          }
        }
        setWarning(null);
      } catch {
        setWarning(null);
      }
    };
    checkBudget();
  }, []);

  if (!warning) return null;

  return (
    <Alert variant="destructive" className="mb-4">
      <AlertTriangle className="h-4 w-4" />
      <AlertDescription className="flex items-center justify-between">
        <span>{warning}</span>
        <a href="/settings/models/cost-analysis" className="underline text-sm font-medium">
          View Details
        </a>
      </AlertDescription>
    </Alert>
  );
}
