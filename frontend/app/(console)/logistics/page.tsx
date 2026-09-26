"use client";

import { CircleAlert, RefreshCw, Route, ShieldCheck, Truck } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { OperationsFacts, RuntimeReadiness } from "@/lib/types";

export default function LogisticsPage() {
  const [facts, setFacts] = useState<OperationsFacts | null>(null);
  const [readiness, setReadiness] = useState<RuntimeReadiness | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [nextFacts, nextReadiness] = await Promise.all([
        apiFetch<OperationsFacts>("/api/v1/operations/facts"),
        apiFetch<RuntimeReadiness>("/api/v1/dashboard/readiness"),
      ]);
      setFacts(nextFacts);
      setReadiness(nextReadiness);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "物流状态加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  const fulfillment = readiness?.stages.find((stage) => stage.key === "fulfillment");
  const items = facts?.logistics.items ?? [];

  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">SHIPMENT FACTS · READ ONLY</div><h2>物流</h2><p>追踪供应商发货和闲鱼履约状态，只显示脱敏事实，不生成或伪造物流单号。</p></div><button aria-label="刷新物流状态" className="icon-button" onClick={() => void load()} type="button"><RefreshCw className={loading ? "spin" : ""} size={17} /></button></div>
      {fulfillment ? <div className="phase-banner"><ShieldCheck size={18} /><b>{fulfillment.ready ? "履约同步可用" : "履约同步待授权"}</b><span>{fulfillment.detail}</span></div> : null}
      {error ? <div className="card empty-state"><CircleAlert size={27} /><b>物流事实不可用</b><span>{error}</span><button className="secondary-button" onClick={() => void load()} type="button">重试</button></div> : null}
      {!error && loading ? <div className="card empty-state"><RefreshCw className="spin" size={25} /><span>正在读取脱敏物流事实</span></div> : null}
      {!error && !loading && facts ? (
        <>
          <section className="summary-grid" style={{ marginBottom: 12 }}><article className="card summary-card"><span>物流记录</span><strong>{items.length}</strong><small>与供应商采购单关联</small></article><article className="card summary-card"><span>已记录运单</span><strong>{items.filter((item) => item.tracking_recorded).length}</strong><small>页面不返回完整运单号</small></article><article className="card summary-card"><span>已签收</span><strong>{items.filter((item) => item.delivered_at || item.status === "DELIVERED").length}</strong><small>异常会进入人工队列</small></article></section>
          <section className="card">
            <div className="panel-heading"><h3>物流轨迹</h3><Route size={18} /></div>
            {items.length ? <div className="table-wrap"><table><thead><tr><th>物流记录</th><th>采购单</th><th>承运商</th><th>运单</th><th>状态</th><th>最近事件</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><b>#{item.id}</b></td><td>#{item.supplier_order_id}</td><td>{item.carrier ?? "待回传"}</td><td>{item.tracking_recorded ? "已加密记录" : "未记录"}</td><td><span className={`tag ${item.delivered_at || item.status === "DELIVERED" ? "green" : "orange"}`}>{item.status}</span></td><td>{item.last_event_at ? new Date(item.last_event_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</td></tr>)}</tbody></table></div> : <div className="empty-state"><Truck size={28} /><b>当前没有物流记录</b><span>只有真实采购单产生运单后才会显示；系统不会创建虚假物流。</span><Link className="secondary-button" href="/purchases">查看采购任务</Link></div>}
          </section>
        </>
      ) : null}
    </>
  );
}
