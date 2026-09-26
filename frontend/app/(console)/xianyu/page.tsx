"use client";

import {
  BarChart3,
  Boxes,
  CircleDollarSign,
  ClipboardPaste,
  ExternalLink,
  Link2Off,
  MessageCircleMore,
  RefreshCw,
  ShieldCheck,
  ShoppingBag,
  Upload,
  X,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import {
  isBridgeVersionCompatible,
  isCustomerServiceSubmissionReady,
  MINIMUM_BROWSER_BRIDGE_VERSION,
  supportsBridgeOperation,
} from "@/lib/browser-bridge";
import { fetchIntegrationStatus } from "@/lib/integrations";
import type { BrowserBridgeOverview, BrowserBridgeTask, IntegrationStatus, XianyuOverview, XianyuTrafficOverview } from "@/lib/types";

type Tab = "products" | "traffic" | "conversations" | "orders";

type ManualProductInput = {
  external_product_id: string;
  title: string;
  price: string | number;
  status?: "ACTIVE" | "SOLD" | "PAUSED" | "REMOVED";
  source_url: string;
};

type ReplySuggestion = {
  id: number;
  intent: string;
  reply: string | null;
  auto_eligible: boolean;
  requires_human: boolean;
  blockers: string[];
  reason: string;
  confidence: string;
  provider: string;
  model: string;
  generation_mode: "MODEL_GUARDED" | "POLICY_FALLBACK";
};

type ConversationDetail = {
  id: number;
  status: string;
  manual_mode: boolean;
  manual_reason: string | null;
  messages: Array<{ id: number; direction: string; role: string; content: string; sent_by_ai: boolean; created_at: string }>;
  suggestions: ReplySuggestion[];
};

const EMPTY: XianyuOverview = { accounts: [], products: [], conversations: [], orders: [] };
const EMPTY_TRAFFIC: XianyuTrafficOverview = {
  latest_date: null,
  period_start: null,
  period_end: null,
  data_days: 0,
  summary: { products: 0, exposures: 0, views: 0, ctr: "0", inquiries: 0, paid_orders: 0, paid_amount: "0.00" },
  daily: [],
  products: [],
  strategy: { observation_days: 3, minimum_exposures: 60, price_change_days: 7, note: "暂无官方经营数据，请导入经营罗盘近1天报表。" },
};

const xianyuStatusLabels: Record<string, string> = {
  ACTIVE: "在售",
  SOLD: "已售出",
  PAUSED: "已暂停",
  REMOVED: "已下架",
  UNPUBLISHED: "未发布",
};

function yesterdayLocal() {
  const value = new Date();
  value.setDate(value.getDate() - 1);
  return value.toLocaleDateString("en-CA");
}

function browserTaskKey(operation: string) {
  const entropy = new Uint32Array(4);
  window.crypto.getRandomValues(entropy);
  const suffix = Array.from(entropy, (value) => value.toString(16).padStart(8, "0")).join("");
  return `browser:${operation.toLowerCase()}:${Date.now().toString(36)}-${suffix}`;
}

export default function XianyuPage() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [browserBridge, setBrowserBridge] = useState<BrowserBridgeOverview | null>(null);
  const [data, setData] = useState<XianyuOverview>(EMPTY);
  const [traffic, setTraffic] = useState<XianyuTrafficOverview>(EMPTY_TRAFFIC);
  const [trafficDays, setTrafficDays] = useState(7);
  const [trafficLoading, setTrafficLoading] = useState(true);
  const [metricsFile, setMetricsFile] = useState<File | null>(null);
  const [metricsDate, setMetricsDate] = useState(yesterdayLocal);
  const [metricsImporting, setMetricsImporting] = useState(false);
  const [metricsCapturing, setMetricsCapturing] = useState(false);
  const [tab, setTab] = useState<Tab>("products");
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [importing, setImporting] = useState(false);
  const [manualNickname, setManualNickname] = useState("");
  const [manualActiveCount, setManualActiveCount] = useState("");
  const [manualSoldCount, setManualSoldCount] = useState("");
  const [manualComplete, setManualComplete] = useState(false);
  const [manualProducts, setManualProducts] = useState("[]");
  const [notice, setNotice] = useState("");
  const [conversationDetail, setConversationDetail] = useState<ConversationDetail | null>(null);
  const [conversationBusy, setConversationBusy] = useState(false);
  const [browserConversationSyncing, setBrowserConversationSyncing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [health, overview, bridge] = await Promise.all([
        fetchIntegrationStatus(),
        apiFetch<XianyuOverview>("/api/v1/xianyu"),
        apiFetch<BrowserBridgeOverview>("/api/v1/xianyu/browser-bridge"),
      ]);
      setStatus(health);
      setData(overview);
      setBrowserBridge(bridge);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "闲鱼只读数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const loadTraffic = useCallback(async (days: number) => {
    setTrafficLoading(true);
    try {
      setTraffic(await apiFetch<XianyuTrafficOverview>(`/api/v1/xianyu/metrics/overview?days=${days}`));
    } catch (cause) {
      setTraffic(EMPTY_TRAFFIC);
      setNotice(cause instanceof Error ? cause.message : "经营数据加载失败");
    } finally {
      setTrafficLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadTraffic(trafficDays);
  }, [loadTraffic, trafficDays]);

  useEffect(() => {
    function readLocation() {
      const queryValue = new URLSearchParams(window.location.search).get("tab");
      const value = window.location.hash.slice(1) || queryValue;
      if (value === "products" || value === "traffic" || value === "conversations" || value === "orders") {
        setTab(value);
      }
    }
    readLocation();
    window.addEventListener("hashchange", readLocation);
    window.addEventListener("popstate", readLocation);
    return () => {
      window.removeEventListener("hashchange", readLocation);
      window.removeEventListener("popstate", readLocation);
    };
  }, []);

  const ready = Boolean(status?.global_enabled && status.xianyu.authenticated);
  const writable = Boolean(ready && status?.write_enabled && status.xianyu.write_enabled);
  const browserAgent = browserBridge?.agents.find((agent) => agent.status === "ONLINE") ?? browserBridge?.agents[0];
  const browserBridgeCompatible = Boolean(
    browserAgent?.status === "ONLINE" && isBridgeVersionCompatible(browserAgent.version),
  );
  const browserConversationReady = supportsBridgeOperation(browserAgent, "CAPTURE_CONVERSATIONS");
  const browserMetricsReady = supportsBridgeOperation(browserAgent, "CAPTURE_PRODUCT_METRICS");
  const browserReplyReady = isCustomerServiceSubmissionReady(browserAgent, browserBridge?.safety);
  const lastConversationCapture = browserBridge?.tasks.find((item) => (
    item.operation === "CAPTURE_CONVERSATIONS"
    && item.status === "SUCCEEDED"
    && item.execution_result.conversation_import
  ))?.execution_result.conversation_import;
  const canReply = writable || browserReplyReady;
  const orderSupported = status?.xianyu.capabilities.orders !== false;

  async function syncAll() {
    if (!ready) return;
    setSyncing(true);
    try {
      const result = await apiFetch<{ products: number; conversations: number; orders: number; orders_supported: boolean }>("/api/v1/xianyu/sync", { method: "POST" });
      setNotice(`同步完成：商品 ${result.products}、会话 ${result.conversations}、订单 ${result.orders}${result.orders_supported ? "" : "（当前 Adapter 不支持订单）"}`);
      await load();
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "同步失败");
    } finally {
      setSyncing(false);
    }
  }

  async function syncBrowserConversations() {
    if (!browserConversationReady || browserConversationSyncing) return;
    setBrowserConversationSyncing(true);
    try {
      const task = await apiFetch<BrowserBridgeTask>("/api/v1/xianyu/browser-bridge/tasks", {
        method: "POST",
        body: JSON.stringify({
          operation: "CAPTURE_CONVERSATIONS",
          idempotency_key: browserTaskKey("CAPTURE_CONVERSATIONS"),
          confirm: false,
        }),
      });
      setNotice(`客服会话采集任务 #${task.id} 已排队，正在读取已登录闲鱼消息页`);
      for (let attempt = 0; attempt < 70; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1500));
        const overview = await apiFetch<BrowserBridgeOverview>("/api/v1/xianyu/browser-bridge");
        setBrowserBridge(overview);
        const current = overview.tasks.find((item) => item.id === task.id);
        if (!current || ["QUEUED", "RUNNING"].includes(current.status)) continue;
        if (current.status === "SUCCEEDED") {
          const imported = current.execution_result.conversation_import;
          setNotice(imported
            ? `会话同步完成：读取 ${imported.conversations_seen} 个会话，核验 ${imported.conversations_verified} 个本账号在售商品咨询，排除 ${imported.conversations_unverified} 个非客服会话，新增 ${imported.messages_created} 条消息`
            : "会话同步完成");
          await load();
          return;
        }
        throw new Error(current.error_message || current.execution_result.manual_reason || `会话同步${current.status}`);
      }
      setNotice(`任务 #${task.id} 仍在等待浏览器处理，可稍后刷新查看结果`);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "浏览器会话同步失败");
    } finally {
      setBrowserConversationSyncing(false);
    }
  }

  async function syncConversation(id: number) {
    if (browserConversationReady) {
      await syncBrowserConversations();
      return;
    }
    try {
      const result = await apiFetch<{ seen: number; created: number }>(`/api/v1/xianyu/conversations/${id}/sync`, { method: "POST" });
      setNotice(`消息同步完成：读取 ${result.seen} 条，新增 ${result.created} 条`);
      await load();
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "会话同步失败");
    }
  }

  async function openConversation(id: number) {
    setConversationBusy(true);
    try {
      setConversationDetail(await apiFetch<ConversationDetail>(`/api/v1/xianyu/conversations/${id}`));
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "会话详情加载失败");
    } finally {
      setConversationBusy(false);
    }
  }

  async function generateSuggestion(id: number) {
    setConversationBusy(true);
    try {
      const suggestion = await apiFetch<ReplySuggestion>(`/api/v1/xianyu/conversations/${id}/suggestion`, {
        method: "POST",
        timeoutMs: 130_000,
      });
      setNotice(suggestion.requires_human ? `已转人工：${suggestion.reason}` : "已生成基于商品事实的回复建议");
      await openConversation(id);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "回复建议生成失败");
      setConversationBusy(false);
    }
  }

  async function sendSuggestion(conversationId: number, suggestion: ReplySuggestion) {
    if (!suggestion.reply || suggestion.requires_human || !canReply) return;
    if (!window.confirm(`确认发送这条回复？\n\n${suggestion.reply}`)) return;
    setConversationBusy(true);
    try {
      const action = await apiFetch<{ id: number; status: string; error_message?: string }>(`/api/v1/xianyu/conversations/${conversationId}/send-suggestion`, { method: "POST", body: JSON.stringify({ suggestion_id: suggestion.id, confirm: true }) });
      if (browserReplyReady && ["QUEUED", "RUNNING"].includes(action.status)) {
        setNotice(`浏览器回复任务 #${action.id} 已排队，发送前会重新核验买家最新消息`);
        let completed = false;
        for (let attempt = 0; attempt < 30; attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 1500));
          const overview = await apiFetch<BrowserBridgeOverview>("/api/v1/xianyu/browser-bridge");
          setBrowserBridge(overview);
          const current = overview.tasks.find((item) => item.id === action.id);
          if (!current || ["QUEUED", "RUNNING"].includes(current.status)) continue;
          if (current.status !== "SUCCEEDED") {
            throw new Error(current.error_message || current.execution_result.manual_reason || `回复任务${current.status}`);
          }
          setNotice(`回复任务 #${action.id} 已发送并取得闲鱼消息气泡证据`);
          completed = true;
          break;
        }
        if (!completed) setNotice(`回复任务 #${action.id} 仍在执行，请在浏览器任务页查看最终结果；系统不会自动重复发送`);
      } else {
        setNotice(`回复动作 #${action.id}：${action.status}${action.error_message ? ` · ${action.error_message}` : ""}`);
      }
      await openConversation(conversationId);
      await load();
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "回复发送失败");
      setConversationBusy(false);
    }
  }

  async function importManualSnapshot() {
    setImporting(true);
    try {
      let products: ManualProductInput[];
      try {
        products = JSON.parse(manualProducts) as ManualProductInput[];
      } catch {
        throw new Error("商品 JSON 格式无效，请检查括号、引号和逗号");
      }
      if (!Array.isArray(products) || products.length === 0) {
        throw new Error("商品 JSON 必须是至少包含一项的数组");
      }
      const result = await apiFetch<{
        products_seen: number;
        products_created: number;
        products_updated: number;
        products_removed: number;
        snapshots_written: number;
      }>("/api/v1/xianyu/manual-snapshot", {
        method: "POST",
        body: JSON.stringify({
          nickname: manualNickname,
          source_url: "https://www.goofish.com/personal",
          captured_at: new Date().toISOString(),
          declared_active_count: manualActiveCount === "" ? null : Number(manualActiveCount),
          declared_sold_count: manualSoldCount === "" ? null : Number(manualSoldCount),
          snapshot_complete: manualComplete,
          products,
        }),
      });
      setNotice(`人工快照导入完成：读取 ${result.products_seen} 条，新建 ${result.products_created} 条，更新 ${result.products_updated} 条${result.products_removed ? `（含移除 ${result.products_removed} 条）` : ""}，新增事实快照 ${result.snapshots_written} 条`);
      setManualOpen(false);
      await load();
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "人工网页快照导入失败");
    } finally {
      setImporting(false);
    }
  }

  async function importMetricsReport() {
    if (!metricsFile) return;
    setMetricsImporting(true);
    try {
      const result = await apiFetch<{ rows: number; created: number; updated: number; managed_products: number }>(
        `/api/v1/xianyu/metrics/import?metric_date=${encodeURIComponent(metricsDate)}`,
        {
          method: "POST",
          headers: { "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" },
          body: await metricsFile.arrayBuffer(),
        },
      );
      setNotice(`经营日报导入完成：${result.rows} 款商品，新建 ${result.created} 条、更新 ${result.updated} 条；AutoFish 已管理 ${result.managed_products} 款。`);
      setMetricsFile(null);
      await loadTraffic(trafficDays);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "经营日报导入失败");
    } finally {
      setMetricsImporting(false);
    }
  }

  async function captureMetricsReport() {
    if (!browserMetricsReady) {
      setNotice("浏览器桥未就绪或缺少经营数据采集能力，任务未创建");
      return;
    }
    setMetricsCapturing(true);
    try {
      const task = await apiFetch<{ id: number; status: string }>("/api/v1/xianyu/browser-bridge/tasks", {
        method: "POST",
        body: JSON.stringify({
          operation: "CAPTURE_PRODUCT_METRICS",
          idempotency_key: `ui:metrics:${metricsDate}:${Date.now()}`,
          confirm: false,
        }),
      });
      setNotice(`近1天经营数据采集任务 #${task.id} 已排队；浏览器桥接在线时会自动读取并写入日报。`);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "自动采集任务创建失败");
    } finally {
      setMetricsCapturing(false);
    }
  }

  function selectTab(next: Tab) {
    setTab(next);
    window.history.replaceState(null, "", `${window.location.pathname}#${next}`);
  }

  return (
    <>
      <div className="page-heading">
        <div><div className="eyebrow">XIANYU · CONTROLLED OPERATIONS</div><h2>闲鱼运营中心</h2><p>{writable ? "账号事实与 Adapter 受控写入通道已连接；每个动作仍受限额、幂等、熔断与审计保护。" : browserReplyReady ? "企业 API 不可用时，已登录浏览器可同步真实会话、生成受事实约束的 AI 建议，并在确认后发送。" : "先验证账号、在售商品、会话与订单事实；写入通道未通过时只开放只读同步。"}</p></div>
        <div className="heading-actions">
          <button className="secondary-button" onClick={() => setManualOpen(true)} type="button"><ClipboardPaste size={16} />导入网页快照</button>
          <button className="primary-button" disabled={!ready || syncing} onClick={syncAll} type="button"><RefreshCw className={syncing ? "spin" : ""} size={16} />{syncing ? "同步中…" : "同步 Adapter 数据"}</button>
        </div>
      </div>
      {notice ? <button className="toast" onClick={() => setNotice("")}>{notice}</button> : null}

      {!ready ? <div className="integration-notice"><Link2Off size={18} /><div><b>闲鱼企业 Adapter 尚未授权</b><span>{browserConversationReady ? "浏览器 AI 客服通道已就绪；商品、订单等能力仍按各自通道状态开放。" : browserAgent?.status === "ONLINE" && !browserBridgeCompatible ? `浏览器扩展 ${browserAgent.version} 不满足最低兼容版本 ${MINIMUM_BROWSER_BRIDGE_VERSION}，相关采集与发送入口已禁用。` : status?.xianyu.message ?? "可先导入人工网页快照建立只读事实，或配置经授权的 JSON Gateway。"}</span></div><span className={`tag ${browserConversationReady ? "green" : "orange"}`}>{browserConversationReady ? "浏览器客服可用" : `等待扩展 ${MINIMUM_BROWSER_BRIDGE_VERSION}+`}</span></div> : null}

      <section className="summary-grid xianyu-summary">
        <article className="card summary-card"><span>账号</span><strong>{data.accounts.length}</strong><small>{data.accounts[0] ? `${data.accounts[0].nickname} · ${data.accounts[0].source === "manual-browser-snapshot" ? "人工网页快照" : data.accounts[0].source === "browser-bridge-chat" ? "浏览器消息" : "Adapter"}` : "未连接"}</small></article>
        <article className="card summary-card"><span>在售商品快照</span><strong>{data.products.length}</strong><small>{writable ? "可受控发布 / 改价 / 下架" : "只读同步"}</small></article>
        <article className="card summary-card"><span>会话</span><strong>{data.conversations.length}</strong><small>{canReply ? browserReplyReady ? "浏览器受控回复" : "Adapter 受控回复" : "消息发送关闭"}</small></article>
        <article className="card summary-card"><span>订单</span><strong>{data.orders.length}</strong><small>{orderSupported ? "只读能力可用" : "当前 Adapter 不支持"}</small></article>
      </section>

      <section className="card xianyu-workspace">
        <div className="workspace-tabs" role="tablist">
          <button aria-selected={tab === "products"} className={tab === "products" ? "active" : ""} onClick={() => selectTab("products")} role="tab" type="button"><ShoppingBag size={16} />在售商品 <b>{data.products.length}</b></button>
          <button aria-selected={tab === "traffic"} className={tab === "traffic" ? "active" : ""} onClick={() => selectTab("traffic")} role="tab" type="button"><BarChart3 size={16} />流量诊断 <b>{traffic.summary.views}</b></button>
          <button aria-selected={tab === "conversations"} className={tab === "conversations" ? "active" : ""} onClick={() => selectTab("conversations")} role="tab" type="button"><MessageCircleMore size={16} />会话 <b>{data.conversations.length}</b></button>
          <button aria-selected={tab === "orders"} className={tab === "orders" ? "active" : ""} onClick={() => selectTab("orders")} role="tab" type="button"><CircleDollarSign size={16} />订单 <b>{data.orders.length}</b></button>
          <span><ShieldCheck size={15} />{canReply ? "CONTROLLED REPLY" : "READ ONLY"}</span>
        </div>

        {loading ? <div className="empty-state"><RefreshCw className="spin" size={24} /><span>正在读取本地事实库</span></div> : null}
        {!loading && tab === "products" ? <ProductTable rows={data.products} /> : null}
        {tab === "traffic" ? <TrafficPanel captureReady={browserMetricsReady} capturing={metricsCapturing} days={trafficDays} file={metricsFile} loading={trafficLoading} metricsDate={metricsDate} onCapture={() => void captureMetricsReport()} onDateChange={setMetricsDate} onDaysChange={setTrafficDays} onFileChange={setMetricsFile} onImport={() => void importMetricsReport()} report={traffic} uploading={metricsImporting} /> : null}
        {!loading && tab === "conversations" ? <ConversationTable browserReady={browserConversationReady} browserSyncing={browserConversationSyncing} lastCapture={lastConversationCapture} onBrowserSync={syncBrowserConversations} onOpen={openConversation} onSync={syncConversation} ready={ready || browserConversationReady} rows={data.conversations} /> : null}
        {!loading && tab === "orders" ? <OrderTable rows={data.orders} supported={orderSupported} /> : null}
      </section>

      {manualOpen ? (
        <div className="dialog-overlay" role="presentation">
          <section aria-labelledby="manual-snapshot-title" aria-modal="true" className="dialog-card" role="dialog">
            <div className="dialog-header"><div><h3 id="manual-snapshot-title">导入闲鱼网页快照</h3><small>只接收规范化商品事实，不接收 Cookie、Token、消息或买家隐私。</small></div><button aria-label="关闭导入窗口" className="icon-button" onClick={() => setManualOpen(false)} type="button"><X size={18} /></button></div>
            <div className="dialog-body manual-import-grid">
              <label className="field"><span>闲鱼昵称</span><input maxLength={160} onChange={(event) => setManualNickname(event.target.value)} placeholder="当前登录账号昵称" value={manualNickname} /></label>
              <label className="field"><span>页面显示在售数</span><input min="0" onChange={(event) => setManualActiveCount(event.target.value)} type="number" value={manualActiveCount} /></label>
              <label className="field"><span>页面显示已售出数</span><input min="0" onChange={(event) => setManualSoldCount(event.target.value)} type="number" value={manualSoldCount} /></label>
              <label className="snapshot-check"><input checked={manualComplete} onChange={(event) => setManualComplete(event.target.checked)} type="checkbox" /><span>这是完整的在售列表（勾选后数量必须完全一致）</span></label>
              <label className="field full"><span>商品 JSON 数组</span><textarea className="snapshot-textarea" onChange={(event) => setManualProducts(event.target.value)} spellCheck={false} value={manualProducts} /><small>每项必须包含 external_product_id、title、price、source_url；source_url 必须是 ID 一致的闲鱼商品页。</small></label>
              <div className="manual-source-note"><ShieldCheck size={17} /><span>来源固定为 <code>https://www.goofish.com/personal</code>，超过 24 小时、重复 ID、伪造域名或链接 ID 不一致的快照会被拒绝。</span></div>
            </div>
            <div className="dialog-actions"><a className="secondary-button" href="https://www.goofish.com/personal" rel="noreferrer" target="_blank"><ExternalLink size={15} />打开闲鱼个人页</a><button className="secondary-button" onClick={() => setManualOpen(false)} type="button">取消</button><button className="primary-button" disabled={importing || !manualNickname.trim()} onClick={() => void importManualSnapshot()} type="button">{importing ? "导入中…" : "确认只读导入"}</button></div>
          </section>
        </div>
      ) : null}

      {conversationDetail ? (
        <div className="dialog-overlay" role="presentation">
          <section aria-labelledby="conversation-title" aria-modal="true" className="dialog-card conversation-dialog" role="dialog">
            <div className="dialog-header"><div><h3 id="conversation-title">会话 #{conversationDetail.id}</h3><small>{conversationDetail.manual_mode ? `人工接管：${conversationDetail.manual_reason ?? "高风险意图"}` : "低风险回复可进入策略预检，高风险意图不会自动发送。"}</small></div><button aria-label="关闭会话" className="icon-button" onClick={() => setConversationDetail(null)} type="button"><X size={18} /></button></div>
            <div className="dialog-body conversation-body">
              <div className="message-thread">{conversationDetail.messages.map((message) => <article className={`message-bubble ${message.direction === "OUTBOUND" ? "outbound" : "inbound"}`} key={message.id}><small>{message.role}{message.sent_by_ai ? " · 自动策略" : ""}</small><p>{message.content}</p><time>{new Date(message.created_at).toLocaleString("zh-CN", { hour12: false })}</time></article>)}{conversationDetail.messages.length === 0 ? <div className="empty-state">暂无消息</div> : null}</div>
              <aside className="suggestion-panel"><div className="panel-heading"><h3>回复建议</h3><span className={`tag ${conversationDetail.manual_mode ? "orange" : "green"}`}>{conversationDetail.manual_mode ? "人工接管" : "策略保护"}</span></div>{conversationDetail.suggestions[0] ? <div className="suggestion-card"><b>{conversationDetail.suggestions[0].intent}</b><p>{conversationDetail.suggestions[0].reply ?? conversationDetail.suggestions[0].reason}</p><small>置信度 {conversationDetail.suggestions[0].confidence}{conversationDetail.suggestions[0].auto_eligible ? " · 可自动执行" : " · 需确认"}</small><small>{conversationDetail.suggestions[0].generation_mode === "MODEL_GUARDED" ? `${conversationDetail.suggestions[0].provider} / ${conversationDetail.suggestions[0].model} · 大模型事实约束` : "本地安全模板兜底"}</small>{conversationDetail.suggestions[0].reply && !conversationDetail.suggestions[0].requires_human ? <button className="primary-button" disabled={conversationBusy || !canReply} onClick={() => void sendSuggestion(conversationDetail.id, conversationDetail.suggestions[0])} type="button">确认并发送</button> : null}</div> : <div className="empty-state"><MessageCircleMore size={24} /><span>尚未生成回复建议</span></div>}<button className="secondary-button" disabled={conversationBusy || conversationDetail.manual_mode} onClick={() => void generateSuggestion(conversationDetail.id)} type="button">{conversationBusy ? "处理中…" : "生成 AI 事实型建议"}</button></aside>
            </div>
          </section>
        </div>
      ) : null}
    </>
  );
}

function percent(value: string) {
  return `${(Number(value) * 100).toFixed(2)}%`;
}

function TrafficPanel({
  captureReady,
  capturing,
  days,
  file,
  loading,
  metricsDate,
  onDateChange,
  onCapture,
  onDaysChange,
  onFileChange,
  onImport,
  report,
  uploading,
}: {
  captureReady: boolean;
  capturing: boolean;
  days: number;
  file: File | null;
  loading: boolean;
  metricsDate: string;
  onCapture: () => void;
  onDateChange: (value: string) => void;
  onDaysChange: (value: number) => void;
  onFileChange: (value: File | null) => void;
  onImport: () => void;
  report: XianyuTrafficOverview;
  uploading: boolean;
}) {
  const severityClass = (severity: XianyuTrafficOverview["products"][number]["diagnosis"]["severity"]) => {
    if (severity === "success") return "green";
    if (severity === "critical" || severity === "high") return "orange";
    if (severity === "medium") return "blue";
    return "gray";
  };

  return (
    <div className="traffic-panel">
      <div className="traffic-toolbar">
        <div>
          <b>官方经营罗盘日报</b>
          <small>{report.latest_date ? `最新数据 ${report.latest_date} · 已采集 ${report.data_days} 天` : "数据 T+1 更新，请下载“近1天”商品明细"}</small>
        </div>
        <div className="traffic-import-controls">
          <input aria-label="经营数据日期" max={yesterdayLocal()} onChange={(event) => onDateChange(event.target.value)} type="date" value={metricsDate} />
          <button className="secondary-button" disabled={!captureReady || capturing || metricsDate !== yesterdayLocal()} onClick={onCapture} title={!captureReady ? "浏览器桥未就绪或缺少经营数据采集能力" : metricsDate !== yesterdayLocal() ? "自动采集仅支持昨天的近1天数据" : undefined} type="button"><RefreshCw className={capturing ? "spin" : ""} size={15} />{capturing ? "排队中…" : "自动采集近1天"}</button>
          <label className="secondary-button traffic-file-button">
            <Upload size={15} />{file ? file.name : "选择 XLSX"}
            <input accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onChange={(event) => onFileChange(event.target.files?.[0] ?? null)} type="file" />
          </label>
          <button className="primary-button" disabled={!file || !metricsDate || uploading} onClick={onImport} type="button">{uploading ? "导入中…" : "导入日报"}</button>
        </div>
      </div>

      <div className="traffic-periods" role="group">
        {[1, 7, 30].map((value) => <button aria-pressed={days === value} className={days === value ? "active" : ""} key={value} onClick={() => onDaysChange(value)} type="button">{value} 天</button>)}
        <span>{report.period_start && report.period_end ? `${report.period_start} 至 ${report.period_end}` : "暂无周期"}</span>
      </div>

      {loading ? <div className="empty-state"><RefreshCw className="spin" size={24} /><span>正在计算商品流量分层</span></div> : null}
      {!loading ? (
        <>
          <div className="traffic-kpis">
            <article><span>AutoFish 曝光</span><strong>{report.summary.managed_exposures ?? report.summary.exposures}</strong><small>店铺总曝光 {report.summary.exposures}</small></article>
            <article><span>AutoFish 浏览</span><strong>{report.summary.managed_views ?? report.summary.views}</strong><small>点击率 {percent(report.summary.managed_ctr ?? report.summary.ctr)} · {report.summary.awaiting_t1 ?? 0} 款待回传</small></article>
            <article><span>询单人数</span><strong>{report.summary.inquiries}</strong><small>先修点击，再判断详情转化</small></article>
            <article><span>支付订单</span><strong>{report.summary.paid_orders}</strong><small>支付金额 ¥{report.summary.paid_amount}</small></article>
          </div>
          <div className="traffic-strategy-note"><ShieldCheck size={17} /><div><b>自动调整门槛</b><span>{report.strategy.note}</span></div></div>
          {report.products.length ? (
            <div className="table-wrap traffic-table"><table><thead><tr><th>商品</th><th>曝光 / 浏览</th><th>点击率</th><th>询单 / 支付</th><th>诊断</th><th>下一动作</th></tr></thead><tbody>{report.products.map((item) => <tr key={item.external_product_id}><td><b>{item.title}</b><small>{item.external_product_id} · 上架 {item.listing_days} 天 · {item.data_days} 天数据</small>{item.managed ? <span className="tag green">AutoFish</span> : <span className="tag gray">店铺对照</span>}</td><td>{item.exposures} / {item.views}</td><td><b>{percent(item.ctr)}</b></td><td>{item.inquiries} / {item.paid_orders}</td><td><span className={`tag ${severityClass(item.diagnosis.severity)}`}>{item.diagnosis.label}</span></td><td><ol>{item.diagnosis.actions.map((action) => <li key={action}>{action}</li>)}</ol></td></tr>)}</tbody></table></div>
          ) : <Empty icon={BarChart3} text="尚未导入经营罗盘商品日报" />}
        </>
      ) : null}
    </div>
  );
}

function ProductTable({ rows }: { rows: XianyuOverview["products"] }) {
  if (rows.length === 0) return <Empty icon={Boxes} text="暂无闲鱼商品快照" />;
  return <div className="table-wrap"><table><thead><tr><th>商品</th><th>外部 ID</th><th>价格</th><th>状态</th><th>来源</th><th>最后同步</th></tr></thead><tbody>{rows.map((item) => <tr key={item.id}><td>{item.source_url ? <a className="product-title-link" href={item.source_url} rel="noreferrer" target="_blank"><b>{item.title ?? "未命名商品"}</b><ExternalLink size={13} /></a> : <b>{item.title ?? "未命名商品"}</b>}</td><td>{item.external_product_id}</td><td>{item.price ? `¥${item.price}` : "—"}</td><td><span className={`tag ${item.status === "ACTIVE" ? "green" : "gray"}`}>{xianyuStatusLabels[item.status] ?? item.status}</span></td><td>{item.source === "manual-browser-snapshot" ? "人工网页" : "Adapter"}</td><td>{item.last_synced_at ? new Date(item.last_synced_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</td></tr>)}</tbody></table></div>;
}

function ConversationTable({ browserReady, browserSyncing, lastCapture, onBrowserSync, rows, ready, onSync, onOpen }: { browserReady: boolean; browserSyncing: boolean; lastCapture?: BrowserBridgeTask["execution_result"]["conversation_import"]; onBrowserSync: () => Promise<void>; rows: XianyuOverview["conversations"]; ready: boolean; onSync: (id: number) => Promise<void>; onOpen: (id: number) => Promise<void> }) {
  if (rows.length === 0) return <div className="empty-state"><MessageCircleMore size={27} /><b>{lastCapture ? "真实会话核验已完成" : "还没有同步真实会话"}</b><span>{lastCapture ? `最近读取 ${lastCapture.conversations_seen} 个会话，核验到 ${lastCapture.conversations_verified} 个本账号在售商品咨询，安全排除 ${lastCapture.conversations_unverified} 个采购、系统或非本账号商品会话；当前没有需要 AI 回复的真实买家咨询。` : browserReady ? "可从本机已登录的闲鱼消息页读取可见会话与文本消息。" : "请重新加载 0.5.1 浏览器桥接扩展后同步真实会话。"}</span><button className="primary-button" disabled={!browserReady || browserSyncing} onClick={() => void onBrowserSync()} type="button"><RefreshCw className={browserSyncing ? "spin" : ""} size={15} />{browserSyncing ? "正在同步…" : "从浏览器同步会话"}</button></div>;
  return <><div className="conversation-toolbar"><div><b>真实闲鱼会话</b><small>仅保存可见文本和经过哈希的会话身份，不读取 Cookie 或 Token。</small></div><button className="secondary-button" disabled={!browserReady || browserSyncing} onClick={() => void onBrowserSync()} type="button"><RefreshCw className={browserSyncing ? "spin" : ""} size={15} />{browserSyncing ? "正在同步…" : "同步浏览器会话"}</button></div><div className="table-wrap"><table><thead><tr><th>买家 / 会话</th><th>状态</th><th>消息数</th><th>最近消息</th><th>动作</th></tr></thead><tbody>{rows.map((item) => <tr key={item.id}><td><button className="link-button" onClick={() => void onOpen(item.id)} type="button"><b>{item.customer_name}</b><small>{item.external_conversation_id}</small></button></td><td><span className={`tag ${item.manual_mode ? "orange" : "green"}`}>{item.manual_mode ? "人工接管" : item.status}</span></td><td>{item.message_count}</td><td>{item.last_message_at ? new Date(item.last_message_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</td><td><div className="table-actions"><button className="table-action" onClick={() => void onOpen(item.id)} type="button">查看 / 回复</button><button className="table-action" disabled={!ready || browserSyncing} onClick={() => void onSync(item.id)} type="button">同步消息</button></div></td></tr>)}</tbody></table></div></>;
}

function OrderTable({ rows, supported }: { rows: XianyuOverview["orders"]; supported: boolean }) {
  if (!supported) return <Empty icon={Link2Off} text="当前 Adapter 没有订单读取能力；系统不会伪造订单状态。" />;
  if (rows.length === 0) return <Empty icon={CircleDollarSign} text="暂无订单快照" />;
  return <div className="table-wrap"><table><thead><tr><th>订单</th><th>状态</th><th>成交额</th><th>预计利润</th><th>实际利润</th><th>最后同步</th></tr></thead><tbody>{rows.map((item) => <tr key={item.id}><td>{item.external_order_id}</td><td><span className="tag blue">{item.status}</span></td><td>¥{item.revenue}</td><td>¥{item.expected_profit}</td><td>{item.actual_profit ? `¥${item.actual_profit}` : "待完结"}</td><td>{item.last_synced_at ? new Date(item.last_synced_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</td></tr>)}</tbody></table></div>;
}

function Empty({ icon: Icon, text }: { icon: typeof Boxes; text: string }) {
  return <div className="empty-state"><Icon size={29} /><b>{text}</b><span>可配置授权 Adapter，或导入已登录网页的人工只读快照。</span></div>;
}
