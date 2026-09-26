"use client";

import { CirclePlus, ListChecks, ShieldAlert } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";

type TaskData = {
  jobs: Array<{ id: number; type: string; status: string; idempotency_key: string; attempts: number; max_attempts: number; error_code: string | null; created_at: string }>;
  manual_tasks: Array<{ id: number; type: string; title: string; reason: string; priority: number; entity_type: string; entity_id: string; created_at: string }>;
};

export default function TasksPage() {
  const [data, setData] = useState<TaskData>({ jobs: [], manual_tasks: [] });
  const [notice, setNotice] = useState("");
  const load = useCallback(() => apiFetch<TaskData>("/api/v1/tasks").then(setData).catch((cause) => setNotice(cause instanceof Error ? cause.message : "加载失败")), []);
  useEffect(() => { void load(); }, [load]);

  async function createLocalTask() {
    try {
      await apiFetch("/api/v1/tasks", { method: "POST", body: JSON.stringify({ type: "ANALYZE_SKU", idempotency_key: `console-analyze-${Date.now()}`, payload: { mode: "LOCAL_RULE_ENGINE" }, timeout_seconds: 60 }) });
      setNotice("本地分析任务已创建；保护模式下会进入人工队列");
      await load();
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "任务创建失败");
    }
  }

  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">JOBS · AUDIT TRAIL</div><h2>任务中心</h2><p>幂等键、重试、超时、人工接管与审计共用一条任务链路。</p></div><button className="primary-button" onClick={createLocalTask}><CirclePlus size={16} />创建本地分析任务</button></div>
      {notice && <button className="toast" onClick={() => setNotice("")}>{notice}</button>}
      <section className="summary-grid" style={{ marginBottom: 12 }}><article className="card summary-card"><span>自动化任务</span><strong>{data.jobs.length}</strong></article><article className="card summary-card"><span>待人工处理</span><strong>{data.manual_tasks.length}</strong></article><article className="card summary-card"><span>安全策略</span><strong style={{ fontSize: 18, color: "var(--warning)" }}>默认停止</strong></article></section>
      <section className="card" style={{ marginBottom: 12 }}><div className="panel-heading" style={{ margin: 0, padding: "14px 16px 11px" }}><h3>人工队列</h3><ShieldAlert size={18} color="var(--warning)" /></div><div className="table-wrap"><table><thead><tr><th>优先级</th><th>事项</th><th>原因</th><th>对象</th><th>创建时间</th></tr></thead><tbody>{data.manual_tasks.map((item) => <tr key={item.id}><td><span className={`tag ${item.priority >= 80 ? "orange" : "blue"}`}>P{100 - item.priority}</span></td><td><b>{item.title}</b><br /><small className="muted">{item.type}</small></td><td>{item.reason}</td><td>{item.entity_type} #{item.entity_id}</td><td>{new Date(item.created_at).toLocaleString("zh-CN", { hour12: false })}</td></tr>)}</tbody></table></div>{data.manual_tasks.length === 0 && <div className="empty-state"><ListChecks size={25} /><span>当前没有待人工事项</span></div>}</section>
      <section className="card"><div className="panel-heading" style={{ margin: 0, padding: "14px 16px 11px" }}><h3>自动化任务</h3><span className="muted">最近 100 条</span></div><div className="table-wrap"><table><thead><tr><th>ID</th><th>类型</th><th>状态</th><th>尝试次数</th><th>幂等键</th><th>错误码</th></tr></thead><tbody>{data.jobs.map((job) => <tr key={job.id}><td>#{job.id}</td><td>{job.type}</td><td><span className={`tag ${job.status === "SUCCEEDED" ? "green" : job.status === "MANUAL_REQUIRED" ? "orange" : "gray"}`}>{job.status}</span></td><td>{job.attempts} / {job.max_attempts}</td><td>{job.idempotency_key}</td><td>{job.error_code ?? "—"}</td></tr>)}</tbody></table></div>{data.jobs.length === 0 && <div className="empty-state"><ListChecks size={25} /><span>尚未创建自动化任务</span></div>}</section>
    </>
  );
}
