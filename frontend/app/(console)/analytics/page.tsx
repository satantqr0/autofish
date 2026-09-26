"use client";

import { BarChart3, CircleAlert, Eye, MessageCircleMore, MousePointerClick, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { XianyuTrafficOverview } from "@/lib/types";

export default function AnalyticsPage() {
  const [days, setDays] = useState(7);
  const [report, setReport] = useState<XianyuTrafficOverview | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      setReport(await apiFetch<XianyuTrafficOverview>(`/api/v1/xianyu/metrics/overview?days=${days}`));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "经营分析加载失败");
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => { void load(); }, [load]);

  const actionable = useMemo(() => report?.products.filter((item) => item.diagnosis.severity !== "info").slice(0, 12) ?? [], [report]);
  const ctr = Number(report?.summary.ctr ?? 0);

  return (
    <>
      <div className="page-heading">
        <div><div className="eyebrow">LIVE FACTS · XIANYU METRICS</div><h2>经营分析</h2><p>只使用闲鱼经营罗盘已导入日报，展示真实曝光、浏览、询单和商品诊断。</p></div>
        <div className="heading-actions"><Link className="secondary-button" href="/xianyu#traffic">管理日报</Link><button aria-label="刷新经营分析" className="icon-button" onClick={() => void load()} type="button"><RefreshCw className={loading ? "spin" : ""} size={17} /></button></div>
      </div>
      <div className="metric-period" style={{ marginBottom: 12 }}>{[1, 7, 30].map((value) => <button aria-pressed={days === value} className={days === value ? "active" : ""} key={value} onClick={() => setDays(value)} type="button">{value} 天</button>)}<span>{report?.period_start && report.period_end ? `${report.period_start} 至 ${report.period_end}` : "暂无可分析周期"}</span></div>
      {error ? <div className="card empty-state"><CircleAlert size={27} /><b>经营分析不可用</b><span>{error}</span><button className="secondary-button" onClick={() => void load()} type="button">重试</button></div> : null}
      {!error && loading ? <div className="card empty-state"><RefreshCw className="spin" size={25} /><span>正在汇总真实经营日报</span></div> : null}
      {!error && !loading && report ? (
        <>
          <section className="summary-grid" style={{ marginBottom: 12 }}>
            <article className="card summary-card"><span><Eye size={15} /> 曝光</span><strong>{report.summary.exposures}</strong><small>{report.data_days} 个有效数据日</small></article>
            <article className="card summary-card"><span><MousePointerClick size={15} /> 浏览</span><strong>{report.summary.views}</strong><small>点击率 {(ctr * 100).toFixed(2)}%</small></article>
            <article className="card summary-card"><span><MessageCircleMore size={15} /> 询单</span><strong>{report.summary.inquiries}</strong><small>支付订单 {report.summary.paid_orders}</small></article>
            <article className="card summary-card"><span>AutoFish 商品</span><strong>{report.summary.managed_products ?? 0}</strong><small>待 T+1：{report.summary.awaiting_t1 ?? 0}</small></article>
          </section>
          <section className="card" style={{ marginBottom: 12 }}>
            <div className="panel-heading"><h3>逐日趋势</h3><BarChart3 size={18} /></div>
            {report.daily.length ? <div className="table-wrap"><table><thead><tr><th>日期</th><th>商品</th><th>曝光</th><th>浏览</th><th>点击率</th><th>询单</th><th>支付</th></tr></thead><tbody>{report.daily.map((item) => <tr key={item.date}><td><b>{item.date}</b></td><td>{item.products}</td><td>{item.exposures}</td><td>{item.views}</td><td>{(Number(item.ctr) * 100).toFixed(2)}%</td><td>{item.inquiries}</td><td>{item.paid_orders}</td></tr>)}</tbody></table></div> : <div className="empty-state"><BarChart3 size={27} /><b>尚无真实日报</b><span>先在闲鱼商品页自动采集昨天数据或导入官方 XLSX，系统不会生成演示曲线。</span><Link className="secondary-button" href="/xianyu#traffic">前往流量诊断</Link></div>}
          </section>
          <section className="card">
            <div className="panel-heading"><h3>需要行动的商品</h3><span className="muted">达到系统门槛后才列入</span></div>
            {actionable.length ? <div className="table-wrap"><table><thead><tr><th>商品</th><th>曝光 / 浏览</th><th>点击率</th><th>诊断</th><th>建议</th></tr></thead><tbody>{actionable.map((item) => <tr key={item.external_product_id}><td><b>{item.title}</b><br /><small className="muted">{item.external_product_id}</small></td><td>{item.exposures} / {item.views}</td><td>{(Number(item.ctr) * 100).toFixed(2)}%</td><td><span className={`tag ${item.diagnosis.severity === "success" ? "green" : "orange"}`}>{item.diagnosis.label}</span></td><td>{item.diagnosis.actions[0]}</td></tr>)}</tbody></table></div> : <div className="empty-state"><BarChart3 size={27} /><b>暂时没有达到调整门槛的商品</b><span>{report.strategy.note}</span></div>}
          </section>
        </>
      ) : null}
    </>
  );
}
