"use client";

import { Activity, CircleAlert, ClipboardList, RefreshCw, ShieldAlert, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { OperationsFacts } from "@/lib/types";

type RiskTab = "manual" | "failures" | "controls" | "aftersales" | "audit";

export default function RiskPage() {
  const [facts, setFacts] = useState<OperationsFacts | null>(null);
  const [tab, setTab] = useState<RiskTab>("manual");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setFacts(await apiFetch<OperationsFacts>("/api/v1/operations/facts"));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "风险事实加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">RISK & MANUAL CONTROL</div><h2>风险与人工</h2><p>把待人工事项、失败任务、自动化开关、售后和审计证据放在一个可处理页面。</p></div><div className="heading-actions"><Link className="secondary-button" href="/settings">调整控制策略</Link><button aria-label="刷新风险事实" className="icon-button" onClick={() => void load()} type="button"><RefreshCw className={loading ? "spin" : ""} size={17} /></button></div></div>
      {error ? <div className="card empty-state"><CircleAlert size={27} /><b>风险事实不可用</b><span>{error}</span><button className="secondary-button" onClick={() => void load()} type="button">重试</button></div> : null}
      {!error && loading ? <div className="card empty-state"><RefreshCw className="spin" size={25} /><span>正在汇总人工与审计事实</span></div> : null}
      {!error && !loading && facts ? (
        <>
          <section className="summary-grid" style={{ marginBottom: 12 }}><article className="card summary-card"><span>待人工</span><strong>{facts.risk.open_manual_tasks.length}</strong><small>按优先级排序</small></article><article className="card summary-card"><span>失败 / 转人工任务</span><strong>{facts.risk.failed_jobs.length}</strong><small>最近 50 条</small></article><article className="card summary-card"><span>售后事项</span><strong>{facts.risk.after_sales.length}</strong><small>退款与争议保持人工门禁</small></article><article className="card summary-card"><span>已启用控制</span><strong>{facts.risk.controls.filter((item) => item.enabled).length} / {facts.risk.controls.length}</strong><small>全局与分项开关</small></article></section>
          <section className="card">
            <div className="workspace-tabs" role="tablist">
              <button aria-selected={tab === "manual"} className={tab === "manual" ? "active" : ""} onClick={() => setTab("manual")} role="tab" type="button"><ShieldAlert size={16} />人工队列 <b>{facts.risk.open_manual_tasks.length}</b></button>
              <button aria-selected={tab === "failures"} className={tab === "failures" ? "active" : ""} onClick={() => setTab("failures")} role="tab" type="button"><CircleAlert size={16} />失败任务 <b>{facts.risk.failed_jobs.length}</b></button>
              <button aria-selected={tab === "controls"} className={tab === "controls" ? "active" : ""} onClick={() => setTab("controls")} role="tab" type="button"><ShieldCheck size={16} />安全控制 <b>{facts.risk.controls.length}</b></button>
              <button aria-selected={tab === "aftersales"} className={tab === "aftersales" ? "active" : ""} onClick={() => setTab("aftersales")} role="tab" type="button"><ClipboardList size={16} />售后 <b>{facts.risk.after_sales.length}</b></button>
              <button aria-selected={tab === "audit"} className={tab === "audit" ? "active" : ""} onClick={() => setTab("audit")} role="tab" type="button"><Activity size={16} />审计 <b>{facts.risk.recent_audit.length}</b></button>
              <span>READ ONLY</span>
            </div>
            {tab === "manual" ? <ManualTasks facts={facts} /> : null}
            {tab === "failures" ? <FailedJobs facts={facts} /> : null}
            {tab === "controls" ? <Controls facts={facts} /> : null}
            {tab === "aftersales" ? <AfterSales facts={facts} /> : null}
            {tab === "audit" ? <AuditEvents facts={facts} /> : null}
          </section>
        </>
      ) : null}
    </>
  );
}

function ManualTasks({ facts }: { facts: OperationsFacts }) {
  const items = facts.risk.open_manual_tasks;
  return items.length ? <div className="table-wrap"><table><thead><tr><th>优先级</th><th>事项</th><th>原因</th><th>对象</th><th>创建时间</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><span className={`tag ${item.priority >= 80 ? "orange" : "blue"}`}>P{100 - item.priority}</span></td><td><b>{item.title}</b><br /><small className="muted">{item.type}</small></td><td>{item.reason}</td><td>{item.entity_type ?? "—"} {item.entity_id ? `#${item.entity_id}` : ""}</td><td>{new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}</td></tr>)}</tbody></table></div> : <SafeEmpty title="当前没有待人工事项" detail="人工队列为空；后续触发风控、失败阈值或数据不一致时会自动进入这里。" />;
}

function FailedJobs({ facts }: { facts: OperationsFacts }) {
  const items = facts.risk.failed_jobs;
  return items.length ? <div className="table-wrap"><table><thead><tr><th>任务</th><th>类型</th><th>状态</th><th>尝试</th><th>错误</th><th>时间</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><b>#{item.id}</b></td><td>{item.type}</td><td><span className="tag orange">{item.status}</span></td><td>{item.attempts}</td><td>{item.error_code ?? item.error_message ?? "未记录"}</td><td>{new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}</td></tr>)}</tbody></table></div> : <SafeEmpty title="最近没有失败任务" detail="这里只显示真实失败或转人工任务，不生成演示异常。" />;
}

function Controls({ facts }: { facts: OperationsFacts }) {
  return <div className="table-wrap"><table><thead><tr><th>范围</th><th>启用</th><th>模式</th><th>连续失败</th><th>熔断 / 停止原因</th></tr></thead><tbody>{facts.risk.controls.map((item) => <tr key={item.scope}><td><b>{item.scope}</b></td><td><span className={`tag ${item.enabled ? "green" : "gray"}`}>{item.enabled ? "已启用" : "已停止"}</span></td><td>{item.mode}</td><td>{item.consecutive_failures}</td><td>{item.cooldown_until ? `熔断至 ${new Date(item.cooldown_until).toLocaleString("zh-CN", { hour12: false })}` : item.stopped_reason ?? "—"}</td></tr>)}</tbody></table></div>;
}

function AfterSales({ facts }: { facts: OperationsFacts }) {
  const items = facts.risk.after_sales;
  return items.length ? <div className="table-wrap"><table><thead><tr><th>售后</th><th>订单</th><th>类型</th><th>状态</th><th>金额</th><th>原因</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><b>#{item.id}</b></td><td>#{item.order_id}</td><td>{item.type}</td><td><span className="tag orange">{item.status}</span></td><td>{item.amount ? `¥${item.amount}` : "—"}</td><td>{item.reason ?? "—"}</td></tr>)}</tbody></table></div> : <SafeEmpty title="当前没有售后事项" detail="退款、争议和平台介入出现后会在这里保留处理事实。" />;
}

function AuditEvents({ facts }: { facts: OperationsFacts }) {
  const items = facts.risk.recent_audit;
  return items.length ? <div className="table-wrap"><table><thead><tr><th>事件</th><th>对象</th><th>结果</th><th>执行者</th><th>时间</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><b>{item.action}</b><br /><small className="muted">#{item.id}</small></td><td>{item.entity_type} {item.entity_id ? `#${item.entity_id}` : ""}</td><td><span className={`tag ${item.result === "SUCCESS" ? "green" : "orange"}`}>{item.result}</span></td><td>{item.actor_type}</td><td>{new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}</td></tr>)}</tbody></table></div> : <SafeEmpty title="暂无审计事件" detail="登录、开关、任务和平台动作会自动留下审计记录。" />;
}

function SafeEmpty({ title, detail }: { title: string; detail: string }) {
  return <div className="empty-state"><ShieldCheck size={27} /><b>{title}</b><span>{detail}</span></div>;
}
