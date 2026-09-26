"use client";

import { CircleAlert, PackageCheck, RefreshCw, ShieldCheck, ShoppingCart } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { OperationsFacts, RuntimeReadiness } from "@/lib/types";

export default function PurchasesPage() {
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
      setError(cause instanceof Error ? cause.message : "采购任务加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  const fulfillment = readiness?.stages.find((stage) => stage.key === "fulfillment");
  const items = facts?.purchases.items ?? [];

  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">PURCHASE FACTS · GUARDED</div><h2>采购任务</h2><p>只展示由真实闲鱼订单产生的供应商采购事实；没有订单时不创建占位任务。</p></div><button aria-label="刷新采购任务" className="icon-button" onClick={() => void load()} type="button"><RefreshCw className={loading ? "spin" : ""} size={17} /></button></div>
      {fulfillment ? <div className="phase-banner"><ShieldCheck size={18} /><b>{fulfillment.ready ? "订单链路已就绪" : "采购链路受保护"}</b><span>{fulfillment.detail}</span></div> : null}
      {error ? <div className="card empty-state"><CircleAlert size={27} /><b>采购事实不可用</b><span>{error}</span><button className="secondary-button" onClick={() => void load()} type="button">重试</button></div> : null}
      {!error && loading ? <div className="card empty-state"><RefreshCw className="spin" size={25} /><span>正在核对订单与供应商任务</span></div> : null}
      {!error && !loading && facts ? (
        <>
          <section className="summary-grid" style={{ marginBottom: 12 }}><article className="card summary-card"><span>采购任务</span><strong>{items.length}</strong><small>全部来自真实订单</small></article><article className="card summary-card"><span>已记录供应商单号</span><strong>{items.filter((item) => item.external_order_recorded).length}</strong><small>不展示或托管支付凭证</small></article><article className="card summary-card"><span>待处理</span><strong>{items.filter((item) => !["COMPLETED", "CANCELLED"].includes(item.status)).length}</strong><small>采购与付款保持人工门禁</small></article></section>
          <section className="card">
            <div className="panel-heading"><h3>供应商采购单</h3><PackageCheck size={18} /></div>
            {items.length ? <div className="table-wrap"><table><thead><tr><th>采购单</th><th>闲鱼订单</th><th>供应商</th><th>货款</th><th>运费</th><th>外部单号</th><th>状态</th></tr></thead><tbody>{items.map((item) => <tr key={item.id}><td><b>#{item.id}</b></td><td>#{item.order_id}</td><td>{item.supplier}</td><td>¥{item.cost}</td><td>¥{item.shipping_cost}</td><td>{item.external_order_recorded ? "已安全记录" : "未创建"}</td><td><span className={`tag ${item.status === "COMPLETED" ? "green" : "orange"}`}>{item.status}</span></td></tr>)}</tbody></table></div> : <div className="empty-state"><ShoppingCart size={28} /><b>当前没有采购任务</b><span>原因是尚无真实闲鱼订单进入采购阶段；这不是系统故障，也不会用模拟订单填充页面。</span><Link className="secondary-button" href="/orders">查看闲鱼订单</Link></div>}
          </section>
        </>
      ) : null}
    </>
  );
}
