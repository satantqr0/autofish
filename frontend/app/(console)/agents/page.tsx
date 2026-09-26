"use client";

import { Bot, BrainCircuit, CheckCircle2, CircleAlert, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { AISettingsResponse, OperationsFacts, RuntimeReadiness } from "@/lib/types";

export default function AgentsPage() {
  const [facts, setFacts] = useState<OperationsFacts | null>(null);
  const [readiness, setReadiness] = useState<RuntimeReadiness | null>(null);
  const [settings, setSettings] = useState<AISettingsResponse | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextFacts, nextReadiness, nextSettings] = await Promise.all([
        apiFetch<OperationsFacts>("/api/v1/operations/facts"),
        apiFetch<RuntimeReadiness>("/api/v1/dashboard/readiness"),
        apiFetch<AISettingsResponse>("/api/v1/ai-settings"),
      ]);
      setFacts(nextFacts);
      setReadiness(nextReadiness);
      setSettings(nextSettings);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "AI Agent 状态加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <>
      <div className="page-heading">
        <div><div className="eyebrow">AI DECISIONS · READ ONLY</div><h2>AI Agent</h2><p>展示实际任务路由、模型可用性与已落库决策，不用概念卡片冒充运行状态。</p></div>
        <div className="heading-actions"><Link className="secondary-button" href="/model-settings">配置模型</Link><button aria-label="刷新 AI Agent 状态" className="icon-button" onClick={() => void load()} type="button"><RefreshCw className={loading ? "spin" : ""} size={17} /></button></div>
      </div>
      {error ? <div className="card empty-state"><CircleAlert size={27} /><b>AI Agent 状态不可用</b><span>{error}</span><button className="secondary-button" onClick={() => void load()} type="button">重试</button></div> : null}
      {!error && loading ? <div className="card empty-state"><RefreshCw className="spin" size={25} /><span>正在核对模型与决策事实</span></div> : null}
      {!error && facts && readiness && settings ? (
        <>
          <section className="summary-grid" style={{ marginBottom: 12 }}>
            <article className="card summary-card"><span>模型总开关</span><strong style={{ color: settings.runtime.enabled ? "var(--success)" : "var(--warning)" }}>{settings.runtime.enabled ? "已启用" : "已关闭"}</strong><small>主服务：{settings.runtime.primary_provider}</small></article>
            <article className="card summary-card"><span>已落库决策</span><strong>{facts.agents.total_decisions}</strong><small>每条保留输入哈希与原因</small></article>
            <article className="card summary-card"><span>就绪路由</span><strong>{readiness.ai.routes.filter((item) => item.ready).length} / {readiness.ai.routes.length}</strong><small>文案、视觉质检、精品图</small></article>
          </section>
          <section className="card" style={{ marginBottom: 12 }}>
            <div className="panel-heading"><h3>任务路由</h3><span className={`tag ${readiness.ai.enabled ? "green" : "orange"}`}>{readiness.ai.enabled ? "运行中" : "已停止"}</span></div>
            <div className="table-wrap"><table><thead><tr><th>任务</th><th>服务</th><th>状态</th><th>说明</th></tr></thead><tbody>{readiness.ai.routes.map((route) => <tr key={route.task}><td><b>{route.label}</b><br /><small className="muted">{route.task}</small></td><td>{route.provider ?? "人工"}</td><td><span className={`tag ${route.ready ? "green" : "orange"}`}>{route.ready ? "就绪" : "阻塞"}</span></td><td className="readable-cell">{route.message}</td></tr>)}</tbody></table></div>
          </section>
          <section className="card">
            <div className="panel-heading"><h3>最近 AI 决策</h3><BrainCircuit size={18} /></div>
            {facts.agents.recent.length ? <div className="table-wrap"><table><thead><tr><th>Agent</th><th>模型</th><th>置信度</th><th>原因摘要</th><th>时间</th></tr></thead><tbody>{facts.agents.recent.map((item) => <tr key={item.id}><td><b>{item.agent}</b><br /><small className="muted">#{item.id}</small></td><td>{item.provider} / {item.model}</td><td>{Number(item.confidence).toLocaleString("zh-CN", { style: "percent", maximumFractionDigits: 1 })}</td><td className="readable-cell">{item.reason_summary}</td><td>{new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}</td></tr>)}</tbody></table></div> : <div className="empty-state"><Bot size={27} /><b>还没有真实 AI 决策</b><span>系统不会生成演示记录；运行一次自主草稿或客服建议后，这里会显示可审计事实。</span><Link className="secondary-button" href="/autonomous"><CheckCircle2 size={15} />查看自主上架</Link></div>}
          </section>
        </>
      ) : null}
    </>
  );
}
