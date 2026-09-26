"use client";

import {
  ArrowUpRight,
  Boxes,
  ClipboardCheck,
  ExternalLink,
  FileInput,
  MonitorCheck,
  RefreshCw,
  Rocket,
  ShieldCheck,
  ShoppingBag,
  TriangleAlert,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import {
  isBridgeVersionCompatible,
  isCustomerServiceSubmissionReady,
  isManualPublishReady,
  MINIMUM_BROWSER_BRIDGE_VERSION,
  supportsBridgeOperation,
  type BrowserBridgeOperation,
} from "@/lib/browser-bridge";
import type {
  BrowserBridgeOverview,
  BrowserBridgeTask,
  ClawHubList,
  XianyuDraftItem,
} from "@/lib/types";

const MODULES = [
  "消息",
  "数据总览",
  "商品数据",
  "商品发布",
  "商品管理",
  "订单管理",
  "退款管理",
  "评价管理",
  "退货地址",
] as const;

const STATUS_LABELS: Record<string, string> = {
  QUEUED: "等待代理",
  RUNNING: "执行中",
  SUCCEEDED: "已完成",
  MANUAL_REQUIRED: "等待人工确认",
  FAILED: "失败",
  CANCELLED: "已取消",
  BLOCKED: "已阻断",
};

const OPERATION_LABELS: Record<BrowserBridgeTask["operation"], string> = {
  OPEN_MODULE: "打开模块",
  CAPTURE_OVERVIEW: "检测工作台",
  CAPTURE_PRODUCT_METRICS: "采集商品日报",
  CAPTURE_CONVERSATIONS: "同步客服会话",
  PREFILL_PRODUCT: "预填商品草稿",
  PUBLISH_PRODUCT: "发布商品",
  SEND_REPLY: "发送客服回复",
};

function idempotencyKey(operation: string) {
  const entropy = new Uint32Array(4);
  window.crypto.getRandomValues(entropy);
  const suffix = Array.from(entropy, (value) => value.toString(16).padStart(8, "0")).join("");
  return `browser:${operation.toLowerCase()}:${Date.now().toString(36)}-${suffix}`;
}

export default function XianyuWorkbenchPage() {
  const [overview, setOverview] = useState<BrowserBridgeOverview | null>(null);
  const [drafts, setDrafts] = useState<XianyuDraftItem[]>([]);
  const [draftId, setDraftId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState("");

  const loadStatus = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const data = await apiFetch<BrowserBridgeOverview>("/api/v1/xianyu/browser-bridge");
      setOverview(data);
    } catch (error) {
      if (!quiet) setNotice(error instanceof Error ? error.message : "工作台状态加载失败");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      const [bridge, draftResponse] = await Promise.all([
        apiFetch<BrowserBridgeOverview>("/api/v1/xianyu/browser-bridge"),
        apiFetch<ClawHubList<XianyuDraftItem>>("/api/v1/clawhub/drafts"),
      ]);
      if (cancelled) return;
      const availableDrafts = draftResponse.items.filter(
        (item) => item.validation.passed && item.status === "REVIEW_READY",
      );
      setOverview(bridge);
      setDrafts(availableDrafts);
      setDraftId(availableDrafts[0] ? String(availableDrafts[0].id) : "");
      setLoading(false);
    }
    void load().catch((error) => {
      if (!cancelled) {
        setNotice(error instanceof Error ? error.message : "工作台加载失败");
        setLoading(false);
      }
    });
    const timer = window.setInterval(() => void loadStatus(true), 10_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [loadStatus]);

  const agent = overview?.agents.find((item) => item.status === "ONLINE") ?? overview?.agents[0];
  const online = agent?.status === "ONLINE";
  const bridgeVersionCompatible = Boolean(online && isBridgeVersionCompatible(agent?.version));
  const canOpenModule = supportsBridgeOperation(agent, "OPEN_MODULE");
  const canCaptureOverview = supportsBridgeOperation(agent, "CAPTURE_OVERVIEW");
  const canCaptureMetrics = supportsBridgeOperation(agent, "CAPTURE_PRODUCT_METRICS");
  const canPrefill = supportsBridgeOperation(agent, "PREFILL_PRODUCT");
  const publishCapabilityReady = supportsBridgeOperation(agent, "PUBLISH_PRODUCT");
  const manualPublishReady = isManualPublishReady(agent, overview?.safety);
  const automaticPublishReady = Boolean(
    manualPublishReady && overview?.safety.automatic_publish_ready,
  );
  const missingHighRiskCapabilities = bridgeVersionCompatible
    ? (["PREFILL_PRODUCT", "PUBLISH_PRODUCT", "SEND_REPLY"] as BrowserBridgeOperation[])
      .filter((operation) => !agent?.capabilities.includes(operation))
    : [];

  async function queueTask(
    operation: BrowserBridgeOperation,
    extra: Record<string, unknown> = {},
  ) {
    if (!supportsBridgeOperation(agent, operation)) {
      setNotice(`浏览器桥未就绪或不支持「${OPERATION_LABELS[operation]}」，任务未创建`);
      return;
    }
    if (operation === "PUBLISH_PRODUCT" && !isManualPublishReady(agent, overview?.safety)) {
      setNotice("人工确认发布尚未放行，任务未创建；客服发送权限不会放行商品发布");
      return;
    }
    if (operation === "SEND_REPLY" && !isCustomerServiceSubmissionReady(agent, overview?.safety)) {
      setNotice("客服发送尚未放行，任务未创建");
      return;
    }
    setBusy(`${operation}:${String(extra.module ?? extra.draft_id ?? "")}`);
    try {
      const task = await apiFetch<BrowserBridgeTask>("/api/v1/xianyu/browser-bridge/tasks", {
        method: "POST",
        body: JSON.stringify({
          operation,
          idempotency_key: idempotencyKey(operation),
          ...extra,
        }),
      });
      setNotice(`任务 #${task.id} 已加入浏览器队列`);
      await loadStatus(true);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "任务创建失败");
    } finally {
      setBusy("");
    }
  }

  function prefillDraft() {
    const draft = drafts.find((item) => item.id === Number(draftId));
    if (!draft) return;
    const confirmed = window.confirm(
      `确认把草稿 #${draft.id} 发送到本机浏览器预填？\n\n${draft.title}\n¥${draft.price}\n\n代理不会点击最终发布按钮。`,
    );
    if (!confirmed) return;
    void queueTask("PREFILL_PRODUCT", { draft_id: draft.id, confirm: true });
  }

  function publishDraft() {
    const draft = drafts.find((item) => item.id === Number(draftId));
    if (!draft || !manualPublishReady) return;
    const confirmed = window.confirm(
      `确认由 AutoFish 在本机浏览器完成这次真实发布？\n\n草稿 #${draft.id}\n${draft.title}\n¥${draft.price}\n\n系统将上传已质检精品图并点击最终发布；遇到验证码、风控或结果不明确会立即停止。`,
    );
    if (!confirmed) return;
    void queueTask("PUBLISH_PRODUCT", { draft_id: draft.id, confirm: true });
  }

  async function cancelTask(taskId: number) {
    setBusy(`cancel:${taskId}`);
    try {
      await apiFetch(`/api/v1/xianyu/browser-bridge/tasks/${taskId}/cancel`, {
        method: "POST",
      });
      setNotice(`任务 #${taskId} 已取消`);
      await loadStatus(true);
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "任务取消失败");
    } finally {
      setBusy("");
    }
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">XIANYU · LOCAL BROWSER BRIDGE</div>
          <h2>闲鱼商家工作台</h2>
          <p>AutoFish 自主生成、质检、排队并回写结果；本机 Chrome 仅作为已登录的受控执行器。</p>
        </div>
        <div className="heading-actions">
          <button
            className="secondary-button"
            disabled={busy === "CAPTURE_OVERVIEW:" || !canCaptureOverview}
            onClick={() => void queueTask("CAPTURE_OVERVIEW")}
            type="button"
          >
            <MonitorCheck size={16} />检测工作台
          </button>
          <button
            className="secondary-button"
            disabled={busy === "CAPTURE_PRODUCT_METRICS:" || !canCaptureMetrics}
            onClick={() => void queueTask("CAPTURE_PRODUCT_METRICS")}
            type="button"
          >
            <RefreshCw className={busy === "CAPTURE_PRODUCT_METRICS:" ? "spin" : ""} size={16} />采集商品日报
          </button>
          <a
            className="primary-button"
            href="https://seller.goofish.com/"
            rel="noreferrer"
            target="_blank"
          >
            <ExternalLink size={16} />打开闲鱼工作台
          </a>
        </div>
      </div>

      {notice ? <button className="toast" onClick={() => setNotice("")}>{notice}</button> : null}

      {!overview?.configured ? (
        <div className="integration-notice">
          <TriangleAlert size={18} />
          <div><b>浏览器桥接尚未配置</b><span>请先在 NAS 创建独立桥接令牌，再安装项目中的 Chrome 扩展。</span></div>
          <span className="tag orange">不可领取任务</span>
        </div>
      ) : null}

      {online && !bridgeVersionCompatible ? (
        <div className="integration-notice">
          <TriangleAlert size={18} />
          <div><b>浏览器扩展需要更新</b><span>当前 {agent?.version} 不满足最低兼容版本 {MINIMUM_BROWSER_BRIDGE_VERSION}；请在 Chrome 扩展页重新加载项目 browser-bridge 目录。更新前已暂停全部桥接任务。</span></div>
          <span className="tag orange">需重新加载</span>
        </div>
      ) : null}

      {missingHighRiskCapabilities.length > 0 ? (
        <div className="integration-notice">
          <TriangleAlert size={18} />
          <div><b>浏览器扩展能力不完整</b><span>缺少 {missingHighRiskCapabilities.map((operation) => OPERATION_LABELS[operation]).join("、")}；对应入口已禁用，请重新加载完整扩展。</span></div>
          <span className="tag orange">能力缺失</span>
        </div>
      ) : null}

      <section className="summary-grid workbench-summary">
        <article className="card summary-card">
          <span>本地代理</span><strong>{online ? bridgeVersionCompatible ? "在线" : "需更新" : "离线"}</strong>
          <small>{agent ? `${agent.name} · ${agent.version}${bridgeVersionCompatible ? "" : `（最低 ${MINIMUM_BROWSER_BRIDGE_VERSION}）`}` : "尚未收到心跳"}</small>
        </article>
        <article className="card summary-card">
          <span>等待任务</span><strong>{overview?.tasks.filter((item) => item.status === "QUEUED").length ?? 0}</strong>
          <small>每 30 秒自动领取</small>
        </article>
        <article className="card summary-card">
          <span>等待人工</span><strong>{overview?.tasks.filter((item) => item.status === "MANUAL_REQUIRED").length ?? 0}</strong>
          <small>登录失效、验证码或结果不明确时接管</small>
        </article>
        <article className="card summary-card">
          <span>发布执行</span><strong>{!publishCapabilityReady ? "不可用" : automaticPublishReady ? "自动发布就绪" : manualPublishReady ? "人工确认" : "仅预填"}</strong>
          <small>{manualPublishReady ? automaticPublishReady ? "人工与自动发布门禁均已通过" : "人工发布已放行 · 自动发布未开启" : "客服权限不会放行商品发布"}</small>
        </article>
      </section>

      <div className="workbench-grid">
        <section className="card workbench-control-card">
          <div className="panel-heading">
            <div><h3>工作台模块</h3><p>通过可见中文菜单定位；找不到或出现风控时自动停止。</p></div>
            <ShieldCheck size={20} />
          </div>
          <div className="workbench-modules">
            {MODULES.map((module) => (
              <button
                className="module-button"
                disabled={!canOpenModule || busy === `OPEN_MODULE:${module}`}
                key={module}
                onClick={() => void queueTask("OPEN_MODULE", { module })}
                type="button"
              >
                <span>{module}</span><ArrowUpRight size={15} />
              </button>
            ))}
          </div>
        </section>

        <section className="card workbench-control-card">
          <div className="panel-heading">
            <div><h3>商品发布执行</h3><p>可先预填验表；放行后由扩展上传精品图、复核字段并提交。</p></div>
            <FileInput size={20} />
          </div>
          {drafts.length > 0 ? (
            <div className="prefill-form">
              <label className="field">
                <span>选择已校验草稿</span>
                <select onChange={(event) => setDraftId(event.target.value)} value={draftId}>
                  {drafts.map((draft) => (
                    <option key={draft.id} value={draft.id}>#{draft.id} · {draft.title} · ¥{draft.price}</option>
                  ))}
                </select>
              </label>
              <button
                className="primary-button"
                disabled={!canPrefill || !draftId || busy === `PREFILL_PRODUCT:${draftId}`}
                onClick={prefillDraft}
                type="button"
              >
                <ClipboardCheck size={16} />发送到浏览器预填
              </button>
              <button
                className="primary-button"
                disabled={!manualPublishReady || !draftId || busy === `PUBLISH_PRODUCT:${draftId}`}
                onClick={publishDraft}
                type="button"
              >
                <Rocket size={16} />上传精品图并发布
              </button>
              <div className="workbench-safety-note">
                <ShieldCheck size={16} /><span>{!publishCapabilityReady
                  ? "浏览器桥版本、在线状态或发布能力未就绪，最终发布入口已禁用。"
                  : !overview?.safety.publish_submission_enabled
                  ? `人工确认发布未放行；当前只能预填。${overview?.safety.automatic_publish_blockers.join("；") || "自动发布未开启"}`
                  : automaticPublishReady
                  ? "人工确认发布已放行；自动发布门禁已通过。字段回读、图片哈希、风控检测任一失败都会停止。"
                  : "人工确认发布已放行；自动发布仍受控、未开启，每次发布仍需明确确认。"}</span>
              </div>
            </div>
          ) : (
            <div className="empty-state"><Boxes size={25} /><b>暂无通过校验的商品草稿</b><span>先在 1688 能力台生成闲鱼草稿。</span></div>
          )}
        </section>
      </div>

      <section className="card workbench-task-card">
        <div className="panel-heading">
          <div>
            <h3>浏览器任务审计</h3>
            <p>准备阶段租约超时可重试；进入提交阶段后结果不明确会立即转人工，禁止盲目重发。</p>
          </div>
          <button className="icon-button" disabled={loading} onClick={() => void loadStatus()} type="button" aria-label="刷新任务">
            <RefreshCw className={loading ? "spin" : ""} size={17} />
          </button>
        </div>
        <TaskTable busy={busy} onCancel={cancelTask} rows={overview?.tasks ?? []} />
      </section>
    </>
  );
}

function TaskTable({
  busy,
  onCancel,
  rows,
}: {
  busy: string;
  onCancel: (taskId: number) => Promise<void>;
  rows: BrowserBridgeTask[];
}) {
  if (rows.length === 0) {
    return <div className="empty-state"><ShoppingBag size={25} /><b>尚无浏览器任务</b><span>先检测工作台或打开一个模块。</span></div>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead><tr><th>任务</th><th>状态</th><th>结果</th><th>尝试</th><th>创建时间</th><th>操作</th></tr></thead>
        <tbody>
          {rows.map((task) => {
            const rawDetails = task.execution_result.manual_reason
              || task.error_message
              || task.execution_result.module
              || task.execution_result.modules?.join("、")
              || (task.status === "SUCCEEDED" ? "任务已完成，历史记录未保存结果摘要" : "—");
            const details = rawDetails === "Could not establish connection. Receiving end does not exist."
              ? "浏览器扩展未连接到工作台页面，请重新加载扩展后重试"
              : rawDetails;
            const effectiveStatus = task.execution_result.manual_reason && task.status === "SUCCEEDED"
              ? "MANUAL_REQUIRED"
              : task.status;
            return (
              <tr key={task.id}>
                <td><b>#{task.id}</b><small className="table-subline">{OPERATION_LABELS[task.operation]}</small></td>
                <td><span className={`tag ${effectiveStatus === "SUCCEEDED" ? "green" : effectiveStatus === "FAILED" ? "red" : effectiveStatus === "MANUAL_REQUIRED" ? "orange" : "blue"}`}>{STATUS_LABELS[effectiveStatus] ?? effectiveStatus}</span></td>
                <td className="workbench-result-cell">{details}</td>
                <td>{task.attempts} / 3</td>
                <td>{new Date(task.created_at).toLocaleString("zh-CN", { hour12: false })}</td>
                <td>{task.status === "QUEUED" ? <button className="table-action" disabled={busy === `cancel:${task.id}`} onClick={() => void onCancel(task.id)} type="button"><XCircle size={14} />取消</button> : "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
