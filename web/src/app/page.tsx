import Link from "next/link";

export default function HomePage() {
  return (
    <div className="flex min-h-screen flex-col items-center justify-center gap-6">
      <h1 className="text-3xl font-bold">Hecate Agent 平台</h1>
      <p className="text-muted-foreground">企业级自托管 Agent 平台</p>
      <div className="flex gap-4">
        <Link
          href="/login"
          className="rounded-md bg-foreground px-6 py-2 text-background"
        >
          登录
        </Link>
        <Link
          href="/register"
          className="rounded-md border px-6 py-2"
        >
          注册
        </Link>
      </div>
    </div>
  );
}
