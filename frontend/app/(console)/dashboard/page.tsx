"use client";

import {
  Boxes,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  ClipboardList,
  FileText,
  ImageIcon,
  PackageSearch,
  RefreshCw,
  Rocket,
  ShieldAlert,
  ShoppingCart,
  UserRoundCheck,
} from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { DashboardData, RuntimeReadiness } from "@/lib/types";

const stageIcons = {
  selection: PackageSearch,
  content: ImageIcon,
  publish: Rocket,
  fulfillment: ShoppingCart,
};

const jobLabels: Record<string, string> = {
  GENERATE_PRODUCT: "生成商品与精品图",
  PUBLISH_PRODUCT: "发布闲鱼商品",
  SYNC_MESSAGES: "同步闲鱼消息",
  SYNC_ORDERS: "同步闲鱼订单",
  CREATE_PURCHASE: "创建采购任务",
  SYNC_LOGISTICS: "同步物流",
  ANALYZE_SKU: "分析商品",
};

const statusLabels: Record<string, string> = {
  PENDING: "排队中",
  RUNNING: "执行中",
  RETRY: "等待重试",
  SUCCEEDED: "已完成",
  FAILED: "失败",
  MANUAL_REQUIRED: "待人工",
  CANCELLED: "已取消",
};

function statusTone(status: string) {
  if (status === "SUCCEEDED") return "green";
  if (["PENDING", "RUNNING", "RETRY"].includes(status)) return "blue";
  if (status === "MANUAL_REQUIRED") return "orange";
  return "gray";
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);
  const [readiness, setReadiness] = useState<RuntimeReadiness | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [dashboard, runtime] = await Promise.all([
        apiFetch<DashboardData>("/api/v1/dashboard"),
        apiFetch<RuntimeReadiness>("/api/v1/dashboard/readiness"),
      ]);
      setData(dashboard);
      setReadiness(runtime);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "经营总览加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (error) {
    return (
      <div className="card empty-state">
        <ShieldAlert size={28} />
        <b>经营总览暂时不可用</b>
        <span>{error}</span>
        <button className="secondary-button" onClick={() => void load()} type="button">重试</button>
      </div>
    );
  }
  if (loading || !data || !readiness) {
    return <div className="simple-dashboard-loading"><div className="skeleton" /><div className="skeleton" /><div className="skeleton" /></div>;
  }

  const firstBlocked = readiness.stages.find((stage) => !stage.ready);
  const primaryHref = readiness.listing_ready ? "/autonomous" : firstBlocked?.route ?? "/settings";
  const metrics = [
    { label: "商品档案", value: data.operation_summary.products, icon: Boxes },
    { label: "待发布", value: data.operation_summary.pending_drafts, icon: FileText },
    { label: "今日订单", value: data.operation_summary.today_orders, icon: ClipboardList },
    { label: "待人工", value: data.operation_summary.manual_tasks, icon: UserRoundCheck },
  ];

  return (
    <div className="simple-dashboard">
      <section className="autonomy-overview">
        <div className="autonomy-heading">
          <div>
            <h2>自主运行</h2>
            <p>{readiness.summary}</p>
          </div>
          <button aria-label="刷新经营总览" className="icon-button" onClick={() => void load()} type="button">
            <RefreshCw size={17} />
          </button>
        </div>

        <div className="autonomy-flow">
          {readiness.stages.map((stage, index) => {
            const Icon = stageIcons[stage.key];
            return (
              <Link className={`autonomy-stage ${stage.ready ? "ready" : "blocked"}`} href={stage.route} key={stage.key}>
                <span className="stage-icon">
                  {stage.ready ? <CheckCircle2 size={21} /> : <Icon size={21} />}
                </span>
                <span><b>{stage.label}</b><small>{stage.detail}</small></span>
                {index < readiness.stages.length - 1 ? <ChevronRight className="stage-arrow" size={18} /> : null}
              </Link>
            );
          })}
        </div>

        <Link className="primary-button autonomy-primary" href={primaryHref}>
          {readiness.listing_ready ? <Rocket size={17} /> : <CircleAlert size={17} />}
          {readiness.listing_ready ? "开始自主上架" : "处理阻塞项"}
        </Link>

        <div className="simple-metrics">
          {metrics.map(({ label, value, icon: Icon }) => (
            <div key={label}>
              <span><Icon size={16} />{label}</span>
              <strong>{value.toLocaleString("zh-CN")}</strong>
            </div>
          ))}
        </div>
      </section>

      <section className="simple-panel" style={{ marginBottom: 12 }}>
        <header><h3>近 7 日真实流量</h3><Link href="/analytics">查看经营分析 <ChevronRight size={15} /></Link></header>
        <div className="simple-metrics">
          <div><span>曝光</span><strong>{data.traffic.exposures.toLocaleString("zh-CN")}</strong></div>
          <div><span>浏览</span><strong>{data.traffic.views.toLocaleString("zh-CN")}</strong></div>
          <div><span>询单</span><strong>{data.traffic.consultations.toLocaleString("zh-CN")}</strong></div>
          <div><span>支付订单</span><strong>{data.business.orders.toLocaleString("zh-CN")}</strong></div>
        </div>
      </section>

      <div className="dashboard-lists">
        <section className="simple-panel recent-task-panel">
          <header><h3>最近任务</h3><Link href="/tasks">查看全部任务 <ChevronRight size={15} /></Link></header>
          {data.recent_tasks.length ? (
            <div className="simple-task-table">
              <div className="simple-table-head"><span>任务</span><span>状态</span><span>更新时间</span></div>
              {data.recent_tasks.map((task) => (
                <Link className="simple-task-row" href="/tasks" key={task.id}>
                  <span><b>{jobLabels[task.type] ?? task.type}</b><small>#{task.id}</small></span>
                  <span><i className={`tag ${statusTone(task.status)}`}>{statusLabels[task.status] ?? task.status}</i></span>
                  <time>{new Date(task.updated_at).toLocaleString("zh-CN", { hour12: false })}</time>
                </Link>
              ))}
            </div>
          ) : <div className="simple-empty">尚无自动化任务</div>}
        </section>

        <section className="simple-panel attention-panel">
          <header><h3>需要处理</h3><Link href="/risk">查看全部 <ChevronRight size={15} /></Link></header>
          {data.manual_items.length ? (
            <div className="attention-list">
              {data.manual_items.map((item) => (
                <Link href="/risk" key={item.id}>
                  <CircleAlert size={18} />
                  <span><b>{item.title}</b><small>{item.reason}</small></span>
                  <ChevronRight size={16} />
                </Link>
              ))}
            </div>
          ) : (
            <div className="simple-empty success"><CheckCircle2 size={20} />当前没有待人工事项</div>
          )}
        </section>
      </div>
    </div>
  );
}
