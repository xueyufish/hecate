"use client";

import {
  LineChart as RechartsLineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from "recharts";

interface DataPoint {
  date: string;
  [key: string]: string | number;
}

interface LineChartProps {
  data: DataPoint[];
  lines: { dataKey: string; color: string; name?: string }[];
  xAxisKey?: string;
  height?: number;
  yAxisLabel?: string;
}

export function LineChart({
  data,
  lines,
  xAxisKey = "date",
  height = 300,
  yAxisLabel,
}: LineChartProps) {
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RechartsLineChart data={data} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
        <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
        <XAxis dataKey={xAxisKey} className="text-xs" tick={{ fontSize: 12 }} />
        <YAxis className="text-xs" tick={{ fontSize: 12 }} label={yAxisLabel ? { value: yAxisLabel, angle: -90, position: "insideLeft" } : undefined} />
        <Tooltip
          contentStyle={{
            backgroundColor: "hsl(var(--card))",
            border: "1px solid hsl(var(--border))",
            borderRadius: "6px",
            fontSize: "12px",
          }}
        />
        <Legend />
        {lines.map((line) => (
          <Line
            key={line.dataKey}
            type="monotone"
            dataKey={line.dataKey}
            stroke={line.color}
            name={line.name || line.dataKey}
            strokeWidth={2}
            dot={false}
          />
        ))}
      </RechartsLineChart>
    </ResponsiveContainer>
  );
}
