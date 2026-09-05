"use client";

import {
  createContext,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Check, ChevronDown } from "lucide-react";

import { cn } from "@/lib/utils";

// Lightweight shadcn-compatible Select built without Radix (the project
// has no radix deps). Covers the compound API the dashboard pages use:
// Select / SelectTrigger / SelectValue / SelectContent / SelectItem.

interface SelectContextValue {
  value: string;
  onValueChange: (value: string) => void;
  open: boolean;
  setOpen: (open: boolean) => void;
  registerItem: (value: string, label: string) => void;
  itemLabels: Map<string, string>;
}

const SelectContext = createContext<SelectContextValue | null>(null);

interface SelectProps {
  /** Controlled value. Omit for uncontrolled usage (internal state). */
  value?: string;
  onValueChange: (value: string) => void;
  children: ReactNode;
  disabled?: boolean;
}

export function Select({ value, onValueChange, children, disabled }: SelectProps) {
  const [open, setOpen] = useState(false);
  const [internalValue, setInternalValue] = useState("");
  const [itemLabels, setItemLabels] = useState<Map<string, string>>(new Map());
  const rootRef = useRef<HTMLDivElement>(null);
  const effectiveValue = value ?? internalValue;

  const handleValueChange = (next: string) => {
    if (value === undefined) setInternalValue(next);
    onValueChange(next);
  };

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const registerItem = (itemValue: string, label: string) => {
    setItemLabels((prev) => {
      if (prev.get(itemValue) === label) return prev;
      const next = new Map(prev);
      next.set(itemValue, label);
      return next;
    });
  };

  return (
    <SelectContext.Provider
      value={{ value: effectiveValue, onValueChange: handleValueChange, open, setOpen, registerItem, itemLabels }}
    >
      <div
        ref={rootRef}
        className={cn("relative inline-block", disabled && "pointer-events-none opacity-50")}
        data-disabled={disabled ? "" : undefined}
      >
        {children}
      </div>
    </SelectContext.Provider>
  );
}

export function SelectTrigger({ className, children }: { className?: string; children?: ReactNode }) {
  const ctx = useContext(SelectContext);
  if (!ctx) throw new Error("SelectTrigger must be used within <Select>");
  return (
    <button
      type="button"
      onClick={() => ctx.setOpen(!ctx.open)}
      className={cn(
        "flex h-9 w-full items-center justify-between gap-2 rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm focus:outline-none focus:ring-1 focus:ring-ring",
        className,
      )}
    >
      {children}
      <ChevronDown className="h-4 w-4 shrink-0 opacity-50" />
    </button>
  );
}

export function SelectValue({ placeholder }: { placeholder?: string }) {
  const ctx = useContext(SelectContext);
  if (!ctx) throw new Error("SelectValue must be used within <Select>");
  const label = ctx.itemLabels.get(ctx.value);
  return <span className="truncate">{label ?? placeholder ?? ctx.value}</span>;
}

export function SelectContent({ className, children }: { className?: string; children: ReactNode }) {
  const ctx = useContext(SelectContext);
  if (!ctx) throw new Error("SelectContent must be used within <Select>");
  // Always mounted so SelectItem labels register for SelectValue even
  // while the dropdown is closed.
  return (
    <div
      className={cn(
        "absolute left-0 z-50 mt-1 min-w-[8rem] rounded-md border bg-popover p-1 text-popover-foreground shadow-md",
        ctx.open ? "block" : "hidden",
        className,
      )}
    >
      {children}
    </div>
  );
}

interface SelectItemProps {
  value: string;
  className?: string;
  children: ReactNode;
}

export function SelectItem({ value, className, children }: SelectItemProps) {
  const ctx = useContext(SelectContext);
  if (!ctx) throw new Error("SelectItem must be used within <Select>");
  const label = typeof children === "string" ? children : value;

  useEffect(() => {
    ctx.registerItem(value, label);
  }, [ctx, value, label]);

  const selected = ctx.value === value;
  return (
    <div
      role="option"
      aria-selected={selected}
      onClick={() => {
        ctx.onValueChange(value);
        ctx.setOpen(false);
      }}
      className={cn(
        "relative flex w-full cursor-pointer select-none items-center rounded-sm py-1.5 pl-2 pr-8 text-sm outline-none hover:bg-accent hover:text-accent-foreground",
        selected && "font-medium",
        className,
      )}
    >
      {children}
      {selected && <Check className="absolute right-2 h-4 w-4" />}
    </div>
  );
}
