"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api-client";
import { groupModelsByProvider, ModelGroup, ModelOption } from "@/lib/model-grouping";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Play, History, Trash2, RotateCcw } from "lucide-react";

interface TestResult {
  content: string;
  model: string;
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
  latency: { ttft: number; total: number };
}

interface TestHistoryEntry {
  id: string;
  model: string;
  prompt: string;
  systemPrompt: string;
  temperature: number;
  maxTokens: number;
  response: string;
  timestamp: number;
  latency: { ttft: number; total: number };
  usage: { prompt_tokens: number; completion_tokens: number; total_tokens: number };
}

const HISTORY_KEY = "hecate_model_debug_history";
const MAX_HISTORY = 10;

function loadHistory(): TestHistoryEntry[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

function saveHistory(entries: TestHistoryEntry[]) {
  localStorage.setItem(HISTORY_KEY, JSON.stringify(entries.slice(0, MAX_HISTORY)));
}

function truncate(str: string, max: number) {
  return str.length > max ? str.slice(0, max) + "..." : str;
}

function getErrorSuggestion(error: string): string {
  const lower = error.toLowerCase();
  if (lower.includes("unauthorized") || lower.includes("api_key") || lower.includes("401")) {
    return "Check your API key in provider settings";
  }
  if (lower.includes("not_found") || lower.includes("404")) {
    return "Model not available — verify the model ID and provider status";
  }
  if (lower.includes("rate_limit") || lower.includes("429")) {
    return "Rate limited — wait a moment and try again";
  }
  if (lower.includes("timeout")) {
    return "Request timed out — try reducing max_tokens or check provider status";
  }
  if (lower.includes("network") || lower.includes("fetch")) {
    return "Network error — check your connection";
  }
  return "";
}

export default function ModelDebugPage() {
  const [modelGroups, setModelGroups] = useState<ModelGroup[]>([]);
  const [selectedModel, setSelectedModel] = useState("");
  const [systemPrompt, setSystemPrompt] = useState("");
  const [prompt, setPrompt] = useState("你好，请简短回复");
  const [temperature, setTemperature] = useState(0.7);
  const [maxTokens, setMaxTokens] = useState(100);
  const [streaming, setStreaming] = useState(true);
  const [testing, setTesting] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const [error, setError] = useState("");
  const [errorSuggestion, setErrorSuggestion] = useState("");
  const [streamContent, setStreamContent] = useState("");
  const [history, setHistory] = useState<TestHistoryEntry[]>([]);
  const [showHistory, setShowHistory] = useState(false);

  useEffect(() => {
    api
      .get<{ data: ModelOption[] }>("/v1/models")
      .then((res) => setModelGroups(groupModelsByProvider(res.data || [])))
      .catch(() => setModelGroups([]));
    setHistory(loadHistory());
  }, []);

  const saveToHistory = useCallback((result: TestResult) => {
    const entry: TestHistoryEntry = {
      id: Date.now().toString(),
      model: selectedModel,
      prompt: truncate(prompt, 100),
      systemPrompt: truncate(systemPrompt, 100),
      temperature,
      maxTokens,
      response: truncate(result.content, 500),
      timestamp: Date.now(),
      latency: result.latency,
      usage: result.usage,
    };
    const updated = [entry, ...history].slice(0, MAX_HISTORY);
    setHistory(updated);
    saveHistory(updated);
  }, [selectedModel, prompt, systemPrompt, temperature, maxTokens, history]);

  const handleTest = useCallback(async () => {
    if (!selectedModel || !prompt.trim()) return;
    setTesting(true);
    setResult(null);
    setError("");
    setErrorSuggestion("");
    setStreamContent("");

    const messages: { role: string; content: string }[] = [];
    if (systemPrompt.trim()) {
      messages.push({ role: "system", content: systemPrompt.trim() });
    }
    messages.push({ role: "user", content: prompt.trim() });

    const startTime = Date.now();
    let ttft = 0;

    try {
      if (streaming) {
        let content = "";
        for await (const token of api.stream("/v1/chat/completions", {
          model: selectedModel,
          messages,
        })) {
          if (!ttft) ttft = Date.now() - startTime;
          content += token;
          setStreamContent(content);
        }
        const totalTime = Date.now() - startTime;
        const finalResult: TestResult = {
          content,
          model: selectedModel,
          usage: { prompt_tokens: 0, completion_tokens: 0, total_tokens: 0 },
          latency: { ttft, total: totalTime },
        };
        setResult(finalResult);
        saveToHistory(finalResult);
      } else {
        const res = await api.post<TestResult>("/v1/chat/completions", {
          model: selectedModel,
          messages,
          temperature,
          max_tokens: maxTokens,
          stream: false,
        });
        const totalTime = Date.now() - startTime;
        const finalResult = { ...res, latency: { ttft: totalTime, total: totalTime } };
        setResult(finalResult);
        saveToHistory(finalResult);
      }
    } catch (e: unknown) {
      const msg = e && typeof e === "object" && "error" in e
        ? (e as { error: { message: string } }).error.message
        : "测试失败";
      setError(msg);
      setErrorSuggestion(getErrorSuggestion(msg));
    } finally {
      setTesting(false);
    }
  }, [selectedModel, prompt, systemPrompt, temperature, maxTokens, streaming, saveToHistory]);

  const loadFromHistory = (entry: TestHistoryEntry) => {
    setSelectedModel(entry.model);
    setPrompt(entry.prompt.replace("...", ""));
    setSystemPrompt(entry.systemPrompt.replace("...", ""));
    setTemperature(entry.temperature);
    setMaxTokens(entry.maxTokens);
    setShowHistory(false);
  };

  const clearHistory = () => {
    if (confirm("Clear all test history?")) {
      setHistory([]);
      localStorage.removeItem(HISTORY_KEY);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold">模型调试</h1>
        <Button variant="outline" size="sm" onClick={() => setShowHistory(!showHistory)}>
          <History className="mr-1 h-4 w-4" />
          History ({history.length})
        </Button>
      </div>

      {showHistory && (
        <Card>
          <CardHeader className="pb-2">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm">Test History</CardTitle>
              <Button variant="ghost" size="sm" onClick={clearHistory}>
                <Trash2 className="mr-1 h-3 w-3" />
                Clear
              </Button>
            </div>
          </CardHeader>
          <CardContent>
            {history.length === 0 ? (
              <p className="text-sm text-muted-foreground">No history yet</p>
            ) : (
              <div className="space-y-2 max-h-60 overflow-y-auto">
                {history.map((entry) => (
                  <div
                    key={entry.id}
                    className="flex items-center justify-between rounded border p-2 text-xs hover:bg-muted cursor-pointer"
                    onClick={() => loadFromHistory(entry)}
                  >
                    <div className="flex-1 min-w-0">
                      <p className="font-medium truncate">{entry.model}</p>
                      <p className="text-muted-foreground truncate">{entry.prompt}</p>
                    </div>
                    <div className="text-right text-muted-foreground ml-2 shrink-0">
                      <p>{new Date(entry.timestamp).toLocaleTimeString()}</p>
                      <p>{entry.latency.total}ms</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      )}

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
            <Label>System Prompt (optional)</Label>
            <Textarea
              value={systemPrompt}
              onChange={(e) => setSystemPrompt(e.target.value)}
              rows={2}
              placeholder="You are a helpful assistant..."
            />
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

          <div className="flex items-center gap-4">
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={streaming}
                onChange={(e) => setStreaming(e.target.checked)}
                className="h-4 w-4 rounded border"
              />
              Streaming
            </label>
            <Button
              onClick={handleTest}
              disabled={testing || !selectedModel || !prompt.trim()}
            >
              <Play className="mr-2 h-4 w-4" />
              {testing ? "测试中..." : "运行测试"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {error && (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="pt-6 space-y-2">
            <p className="text-sm text-red-600">{error}</p>
            {errorSuggestion && (
              <p className="text-xs text-red-500">{errorSuggestion}</p>
            )}
            {error.toLowerCase().includes("network") && (
              <Button variant="outline" size="sm" onClick={handleTest}>
                <RotateCcw className="mr-1 h-3 w-3" />
                Retry
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      {testing && streaming && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Streaming...</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="rounded border bg-muted p-4 text-sm whitespace-pre-wrap min-h-[60px]">
              {streamContent || <span className="animate-pulse">Waiting for response...</span>}
            </div>
          </CardContent>
        </Card>
      )}

      {result && !testing && (
        <Card>
          <CardHeader>
            <CardTitle>测试结果</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-4 text-xs text-muted-foreground">
              <span>Model: {result.model}</span>
              <span>TTFT: {result.latency.ttft}ms</span>
              <span>Total: {result.latency.total}ms</span>
            </div>

            <div>
              <Label>响应内容</Label>
              <div className="mt-1 rounded-md border bg-muted p-4 text-sm whitespace-pre-wrap">
                {result.content}
              </div>
            </div>

            {result.usage.total_tokens > 0 && (
              <div className="space-y-2">
                <div className="flex gap-4 text-xs text-muted-foreground">
                  <span>Prompt: {result.usage.prompt_tokens}</span>
                  <span>Completion: {result.usage.completion_tokens}</span>
                  <span>Total: {result.usage.total_tokens}</span>
                </div>
                <div className="h-3 w-full flex rounded-full overflow-hidden bg-muted">
                  <div
                    className="bg-blue-500 h-full"
                    style={{
                      width: `${(result.usage.prompt_tokens / result.usage.total_tokens) * 100}%`,
                    }}
                  />
                  <div
                    className="bg-green-500 h-full"
                    style={{
                      width: `${(result.usage.completion_tokens / result.usage.total_tokens) * 100}%`,
                    }}
                  />
                </div>
              </div>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}
