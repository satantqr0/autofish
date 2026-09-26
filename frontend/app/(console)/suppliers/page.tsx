"use client";

import { Boxes, PlugZap, Store } from "lucide-react";
import { useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";

type Supplier = { id: number; code: string; name: string; source_type: string; status: string; product_count: number };

export default function SuppliersPage() {
  const [items, setItems] = useState<Supplier[]>([]);
  const [error, setError] = useState("");
  useEffect(() => { apiFetch<Supplier[]>("/api/v1/suppliers").then(setItems).catch((cause) => setError(cause instanceof Error ? cause.message : "加载失败")); }, []);

  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">SUPPLIER FACTS</div><h2>供应商</h2><p>供应商身份与外部商品快照分离保存，凭证只保留引用。</p></div></div>
      <div className="phase-banner"><PlugZap size={18} />1688 Shopkeeper Adapter 尚未启用；当前仅展示人工导入来源。</div>
      {error ? <div className="card empty-state"><Store size={27} /><b>{error}</b></div> : <section className="summary-grid">{items.map((item) => <article className="card summary-card" key={item.id}><span className="tag gray">{item.source_type}</span><h3>{item.name}</h3><p className="muted">{item.code}</p><div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginTop: 22 }}><span><Boxes size={16} style={{ verticalAlign: "middle", marginRight: 7 }} />商品快照</span><strong style={{ fontSize: 22, margin: 0 }}>{item.product_count}</strong></div><div className="status-line" style={{ color: "var(--success)", marginTop: 16 }}><span className="status-dot" />{item.status}</div></article>)}</section>}
      {!error && items.length === 0 && <div className="card empty-state"><Store size={28} /><b>暂无供应商</b><span>先在商品中心人工导入一个测试商品。</span></div>}
    </>
  );
}
