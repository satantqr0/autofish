"use client";

import { useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";

type Rank = { category: string; matched: number; sample_count: number; change_pct: string; previous_price: string; current_price: string; examples: { id: string; title: string; url: string; price: string }[] };
type Report = { status: string; schedule: string; next_collection_at: string; latest_slot: string | null; collected_at: string | null; baseline_slot: string | null; message: string; stale: boolean; scope: string; method: string; eligible_categories: number; categories: string[]; rising: Rank[]; falling: Rank[]; latest_samples: {category: string; count: number; raw_count: number; source: string; sampled_at: string; examples: Rank["examples"]}[]; history: {slot: string; status: string; message: string}[]; browser_required: boolean; market_bridge_online: boolean };
const statusNames: Record<string, string> = { WAITING: "等待采集", WAITING_BROWSER: "等待浏览器采集", BROWSER_RUNNING: "浏览器采集中", MISSED: "浏览器离线，错过采集", RUNNING: "采集中", BLOCKED: "采集通道待接通", COMPLETED: "采集完成", PARTIAL: "部分完成", FAILED: "采集失败", INTERRUPTED: "采集中断" };

function Ranking({ title, rows }: {title: string; rows: Rank[]}) {
  return <section className="card" style={{marginBottom:16}}><div className="panel-heading"><h3>{title}</h3><span>24 小时 · 最多 10 类</span></div>
    {rows.length ? <div className="table-wrap"><table><thead><tr><th>排名 / 品类</th><th>涨跌幅</th><th>前期 / 当前中位价</th><th>匹配 / 采样</th><th>样本核验</th></tr></thead><tbody>{rows.map((row, i) => <tr key={row.category}><td>{i+1}. {row.category}</td><td>{Number(row.change_pct)>0 ? "+" : ""}{row.change_pct}%</td><td>¥{Number(row.previous_price).toFixed(2)} → ¥{Number(row.current_price).toFixed(2)}</td><td>{row.matched} / {row.sample_count}</td><td><details><summary>查看商品</summary>{row.examples.map(x => <p key={x.id}><a href={x.url} target="_blank" rel="noopener noreferrer">{x.title} · ¥{x.price}</a></p>)}</details></td></tr>)}</tbody></table></div> : <div className="empty-state"><b>暂无满足条件的品类</b><span>需要相隔 24 小时的两轮真实样本，每类至少匹配 10 件商品。无涨跌或样本不足时不凑满十名。</span></div>}
  </section>;
}

export default function MarketPricesPage() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [notice, setNotice] = useState("");
  async function startCollection() {
    setSubmitting(true); setError(""); setNotice("");
    try {
      const result = await apiFetch<{message:string}>("/api/v1/market-prices/collect",{method:"POST",body:"{}"});
      setNotice(result.message); setRefresh(x=>x+1);
    } catch(e) {setError(e instanceof Error ? e.message : "采集请求失败");}
    finally {setSubmitting(false);}
  }
  const collecting = report?.status === "WAITING_BROWSER" || report?.status === "BROWSER_RUNNING" || report?.status === "RUNNING";
  useEffect(()=>{
    if (!collecting) return;
    const timer = setInterval(()=>{if (!document.hidden) setRefresh(x=>x+1);},15000);
    return ()=>clearInterval(timer);
  },[collecting]);
  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    apiFetch<Report>("/api/v1/market-prices").then(data => { if (active) setReport(data); }).catch(e => { if (active) setError(e instanceof Error ? e.message : "行情读取失败"); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [refresh]);
  return <>
    <div className="page-heading"><div><div className="eyebrow">XIANYU · MARKET PRICES</div><h2>闲鱼品类价格涨跌榜</h2><p>涨价最快与降价最快前十品类，每天两次采样。</p></div><div style={{display:"flex",gap:8,flexWrap:"wrap"}}><button className="secondary-button" type="button" disabled={submitting || collecting || report?.status === "COMPLETED" || report?.status === "PARTIAL"} onClick={()=>void startCollection()}>{submitting ? "正在排队…" : "立即采集"}</button><button className="secondary-button" type="button" disabled={loading} onClick={() => setRefresh(x=>x+1)}>{loading ? "读取中…" : "刷新榜单"}</button></div></div>
    {error ? <div role="alert" className="card empty-state">{error}</div> : null}
    {notice ? <p role="status">{notice}</p> : null}
    {report ? <>
      {report.browser_required ? <section className="card" style={{marginBottom:16}}><h3>真实行情通道：普通 Chrome 桥接</h3><p>{report.market_bridge_online ? "支持行情采集的浏览器已在线。" : "尚未检测到支持行情采集的在线浏览器。"} 请保持 Chrome 打开、闲鱼登录有效，AutoFish 扩展需为 0.6.0 或更新版本。</p><p className="muted">NAS 每天 09:00、21:00 下发任务；扩展读取搜索页并回传挂牌价。电脑休眠、浏览器关闭或登录失效会中断采集；遇到验证立即停止，不绕过验证。</p><a href="/xianyu-workbench">查看浏览器连接与安装说明</a></section> : null}
      <section className="summary-grid" style={{marginBottom:16}}>
        <article className="card summary-card"><span>采集状态</span><strong style={{fontSize:22}}>{statusNames[report.status] || report.status}</strong><small>{report.message}</small></article>
        <article className="card summary-card"><span>采集安排</span><strong style={{fontSize:22}}>09:00 / 21:00</strong><small>北京时间 · 下次 {new Date(report.next_collection_at).toLocaleString("zh-CN", {timeZone:"Asia/Shanghai"})}</small></article>
        <article className="card summary-card"><span>可比品类 / 监测品类</span><strong>{report.eligible_categories} / {report.categories.length}</strong><small>当前 {report.latest_slot || "尚无样本"} · 基期 {report.baseline_slot || "等待 24 小时基线"}</small></article>
      </section>
      {report.stale ? <p role="alert">数据已超过 13 小时未更新，请检查采集记录。</p> : null}
      <p className="muted">{report.scope} {report.method} 最近实际采集：{report.collected_at ? new Date(report.collected_at).toLocaleString("zh-CN", {timeZone:"Asia/Shanghai"}) : "尚未开始"}。</p>
      <Ranking title="涨价最快前十品类" rows={report.rising}/><Ranking title="降价最快前十品类" rows={report.falling}/>
      <section className="card" style={{marginBottom:16}}><div className="panel-heading"><h3>真实样本与来源核验</h3><span>{report.latest_samples.reduce((n,x)=>n+x.count,0)} 件有效样本</span></div><p>{report.categories.join("、")}</p>{report.latest_samples.length ? <div className="table-wrap"><table><thead><tr><th>品类</th><th>有效 / 原始</th><th>实采时间与方式</th><th>商品原页</th></tr></thead><tbody>{report.latest_samples.map(x=><tr key={x.category}><td>{x.category}</td><td>{x.count} / {x.raw_count}</td><td>{x.sampled_at ? new Date(x.sampled_at).toLocaleString("zh-CN",{timeZone:"Asia/Shanghai"}) : "未知"}<br/>{x.source === "goofish-chrome-bridge" ? "AutoFish 扩展自动采集" : x.source === "codex-supervised-browser" ? "Codex 辅助实采（非自动）" : x.source}</td><td><details><summary>核验 {x.examples?.length || 0} 件</summary>{x.examples?.map(s=><p key={s.id}><a href={s.url} target="_blank" rel="noopener noreferrer">{s.title.slice(0,80)} · ¥{s.price}</a></p>)}</details></td></tr>)}</tbody></table></div> : <p>本轮尚未获得真实商品样本</p>}</section>
      <section className="card"><div className="panel-heading"><h3>最近采集记录</h3></div><div className="table-wrap"><table><thead><tr><th>采集时段</th><th>状态</th><th>说明</th></tr></thead><tbody>{report.history.map(row=><tr key={row.slot}><td>{row.slot}</td><td>{statusNames[row.status] || row.status}</td><td>{row.message}</td></tr>)}</tbody></table></div>{!report.history.length ? <p className="empty-state">等待首轮采集记录</p> : null}</section>
    </> : loading ? <p role="status">正在读取行情…</p> : null}
  </>;
}
