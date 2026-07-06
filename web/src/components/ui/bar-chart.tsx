"use client";

import {
  BarChart as RechartsBarChart,
  Bar,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

interface DataPoint {
  name: string;
  [key: string]: string | number;
}

interface BarChartProps {
  data: DataPoint[];
  bars: { dataKey: string; color: string; name?: string }[];
  xAxisKey?: string;
  height?: number;
  layout?: "horizontal" | "vertical";
}

export function BarChart({
  data,
  bars,
  xAxisKey = "name",
  height = 300,
  layout = "horizontal",
}: BarChartProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RechartsBarChart
        data={data}
        layout={layout}
        margin={{ top: 5, right: 20, left: 10, bottom: 5 }}
      >
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        {layout === "horizontal" ? (
          <>
            <XAxis dataKey={xAxisKey} className="text-xs" tick={{ fontSize: 12 }} />
            <YAxis className="text-xs" tick={{ fontSize: 12 }} />
          </>
        ) : (
          <>
            <XAxis type="number" className="text-xs" tick={{ fontSize: 12 }} />
            <YAxis dataKey={xAxisKey} type="category" className="text-xs" tick={{ fontSize: 12 }} />
          </>
        )}
        <Tooltip
          contentStyle={{
            backgroundColor: "hsl(var(--card))",
            border: "1px solid hsl(var(--border))",
            borderRadius: "6px",
            fontSize: "12px",
          }}
        />
        <Legend />
        {bars.map((bar) => (
          <Bar
            key={bar.dataKey}
            dataKey={bar.dataKey}
            fill={bar.color}
            name={bar.name || bar.dataKey}
            radius={[4, 4, 0, 0]}
          />
        ))}
      </RechartsBarChart>
    </ResponsiveContainer>
  );
}
