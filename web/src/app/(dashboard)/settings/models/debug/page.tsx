"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { groupModelsByProvider, ModelGroup, ModelOption } from "@/lib/model-grouping";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Play } from "lucide-react";

interface TestResult {
  content: string;
  model: string;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}

export default function ModelDebugPage() {
  const [modelGroups, setModelGroups] = useState<ModelGroup[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [prompt, setPrompt] = useState("你好，请简短回复");
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(100);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api
      .get<{ data: ModelOption[] }>("/v1/models")
      .then((res) => setModelGroups(groupModelsByProvider(res.data || [])))
      .catch(() => setModelGroups([]));
  }, []);

  const handleTest = async () => {
    if (!selectedModel || !prompt.trim()) return;
    setTesting(true);
    setResult(null);
    setError("");
    try {
      const res = await api.post<TestResult>("/api/models/test", {
        model_id: selectedModel,
        prompt: prompt.trim(),
        temperature,
        max_tokens: maxTokens,
      });
      setResult(res);
    } catch (e: unknown) {
      const msg = e && typeof e === "object" && "error" in e
        ? (e as { error: { message: string } }).error.message
        : "测试失败";
      setError(msg);
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-semibold">模型调试</h1>

      <Card>
        <CardHeader>
          <CardTitle>测试配置</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>选择模型</Label>
            <select
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              className="flex h-10 w-full rounded-md border bg-background px-3 py-2 text-sm"
            >
              <option value="">选择模型</option>
              {modelGroups.map((group) => (
                <optgroup
                  key={group.provider_name}
                  label={group.provider_display_name}
                >
                  {group.models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.id}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          <div className="space-y-2">
            <Label>测试提示词</Label>
            <Textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={3}
              placeholder="输入要测试的提示词..."
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Temperature: {temperature}</Label>
              <input
                type="range"
                min="0"
                max="2"
                step="0.1"
                value={temperature}
                onChange={(e) => setTemperature(parseFloat(e.target.value))}
                className="w-full"
              />
            </div>
            <div className="space-y-2">
              <Label>Max Tokens: {maxTokens}</Label>
              <input
                type="range"
                min="1"
                max="2000"
                step="10"
                value={maxTokens}
                onChange={(e) => setMaxTokens(parseInt(e.target.value))}
                className="w-full"
              />
            </div>
          </div>

          <Button
            onClick={handleTest}
            disabled={testing || !selectedModel || !prompt.trim()}
          >
            <Play className="mr-2 h-4 w-4" />
            {testing ? "测试中..." : "运行测试"}
          </Button>
        </CardContent>
      </Card>

      {error && (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="pt-6">
            <p className="text-sm text-red-600">{error}</p>
          </CardContent>
        </Card>
      )}

      {result && (
        <Card>
          <CardHeader>
            <CardTitle>测试结果</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div>
              <Label>模型</Label>
              <p className="text-sm">{result.model}</p>
            </div>
            <div>
              <Label>响应内容</Label>
              <div className="mt-1 rounded-md border bg-muted p-4 text-sm whitespace-pre-wrap">
                {result.content}
              </div>
            </div>
            <div className="flex gap-4 text-xs text-muted-foreground">
              <span>Prompt tokens: {result.usage.prompt_tokens}</span>
              <span>Completion tokens: {result.usage.completion_tokens}</span>
              <span>Total tokens: {result.usage.total_tokens}</span>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
