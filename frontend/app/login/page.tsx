"use client";

import { ArrowRight, Fish, LockKeyhole, ShieldCheck, UserRound } from "lucide-react";
import { useRouter, useSearchParams } from "next/navigation";
import { FormEvent, Suspense, useState } from "react";

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState(searchParams.get("expired") ? "登录已过期，请重新登录" : "");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/session/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      if (!response.ok) {
        const body = (await response.json()) as { detail?: string };
        throw new Error(body.detail ?? "登录失败");
      }
      router.replace("/dashboard");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "登录失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="login-page">
      <section className="login-story">
        <div className="login-brand"><Fish size={31} /> AutoFish</div>
        <div className="login-story-copy">
          <div className="eyebrow light">OPERATIONS CONTROL PLANE</div>
          <h1>把重复工作交给系统，<br />把最终决定留给人。</h1>
          <p>商品、定价、任务和风险共用一套可追溯事实。真实平台连接默认关闭，任何验证与高风险动作都会转入人工队列。</p>
          <div className="login-proof">
            <span><ShieldCheck size={19} /> 最低售价硬校验</span>
            <span><LockKeyhole size={19} /> 全链路审计</span>
          </div>
        </div>
        <div className="login-grid" aria-hidden="true" />
      </section>
      <section className="login-panel">
        <form className="login-card" onSubmit={submit}>
          <div className="eyebrow">PRIVATE NAS CONSOLE</div>
          <h2>登录运营控制台</h2>
          <p className="muted">仅限授权操作员访问</p>
          <label>
            <span>用户名</span>
            <div className="input-with-icon"><UserRound size={17} /><input autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} required /></div>
          </label>
          <label>
            <span>密码</span>
            <div className="input-with-icon"><LockKeyhole size={17} /><input autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} required autoFocus /></div>
          </label>
          {error && <div className="form-error" role="alert">{error}</div>}
          <button className="primary-button login-submit" disabled={loading}>
            {loading ? "正在验证…" : "进入控制台"}<ArrowRight size={17} />
          </button>
          <div className="login-footnote"><span className="status-dot amber" />真实连接按授权启用，平台写操作默认关闭</div>
        </form>
      </section>
    </main>
  );
}

export default function LoginPage() {
  return <Suspense><LoginForm /></Suspense>;
}
