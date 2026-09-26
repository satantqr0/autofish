"use client";

import {
  Bot,
  CheckCircle2,
  CircleAlert,
  FileCheck2,
  ImageIcon,
  LoaderCircle,
  RefreshCw,
  Rocket,
  ShieldCheck,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { AISettingsResponse, AutonomousLaunch, RuntimeReadiness } from "@/lib/types";

type LaunchList = { items: AutonomousLaunch[]; total: number };

const ACTIVE_STATUSES = new Set(["PENDING", "RUNNING", "RETRY"]);

function statusLabel(status: string) {
  return {
    PENDING: "排队中",
    RUNNING: "自主处理中",
    RETRY: "等待重试",
    PAUSED: "已暂停",
    MANUAL_REQUIRED: "需要人工处理",
    READY_FOR_BROWSER_HANDOFF: "草稿已就绪",
    PUBLISHED: "闲鱼已发布",
    SUPERSEDED: "已被新版本取代",
  }[status] ?? status;
}

function statusTone(status: string) {
  if (["READY_FOR_BROWSER_HANDOFF", "PUBLISHED"].includes(status)) return "green";
  if (status === "MANUAL_REQUIRED") return "orange";
  if (ACTIVE_STATUSES.has(status)) return "blue";
  return "gray";
}

export default function AutonomousPage() {
  const [launches, setLaunches] = useState<AutonomousLaunch[]>([]);
  const [ai, setAi] = useState<AISettingsResponse | null>(null);
  const [readiness, setReadiness] = useState<RuntimeReadiness | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [candidateId, setCandidateId] = useState("");
  const [query, setQuery] = useState("");
  const [imageCount, setImageCount] = useState(2);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState("");

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [launchData, aiData, runtimeData] = await Promise.all([
        apiFetch<LaunchList>("/api/v1/autonomous-launches"),
        apiFetch<AISettingsResponse>("/api/v1/ai-settings"),
        apiFetch<RuntimeReadiness>("/api/v1/dashboard/readiness"),
      ]);
      setLaunches(launchData.items);
      setAi(aiData);
      setReadiness(runtimeData);
      setSelectedId((current) => current ?? launchData.items[0]?.id ?? null);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "自主上架状态加载失败");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const hasActiveLaunch = launches.some((item) => ACTIVE_STATUSES.has(item.status));
  useEffect(() => {
    if (!hasActiveLaunch) return;
    const timer = window.setInterval(() => void load(true), 3000);
    return () => window.clearInterval(timer);
  }, [hasActiveLaunch, load]);

  const selected = useMemo(
    () => launches.find((item) => item.id === selectedId) ?? launches[0] ?? null,
    [launches, selectedId],
  );
  const qwen = ai?.providers.find((item) => item.provider === "qwen");
  const contentStage = readiness?.stages.find((item) => item.key === "content");
  const publishStage = readiness?.stages.find((item) => item.key === "publish");
  const aiReady = Boolean(contentStage?.ready);

  async function start(event: FormEvent) {
    event.preventDefault();
    setRunning(true);
    setNotice("");
    try {
      const item = await apiFetch<AutonomousLaunch>("/api/v1/autonomous-launches", {
        method: "POST",
        body: JSON.stringify({
          candidate_id: candidateId ? Number(candidateId) : null,
          query: query.trim() || null,
          candidate_limit: 50,
          image_count: imageCount,
          platform_fee: "2.00",
          after_sales_reserve: "3.00",
          minimum_profit: "8.00",
          target_profit: "14.00",
          negotiation_margin: "2.00",
          idempotency_key: `autonomous-ui-${Date.now()}`,
        }),
      });
      setSelectedId(item.id);
      setNotice("任务已交给 AutoFish，页面会自动刷新阶段状态");
      await load(true);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "任务启动失败");
    } finally {
      setRunning(false);
    }
  }

  async function retry() {
    if (!selected) return;
    setRunning(true);
    try {
      await apiFetch(`/api/v1/autonomous-launches/${selected.id}/retry`, { method: "POST" });
      setNotice("已从持久化检查点重试");
      await load(true);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "重试失败");
    } finally {
      setRunning(false);
    }
  }

  if (loading) {
    return (
      <div className="card empty-state">
        <LoaderCircle className="spin" size={26} />
        <b>正在读取自主上架引擎</b>
      </div>
    );
  }

  return (
    <>
      <div className="page-heading autonomous-heading">
        <div>
          <div className="eyebrow">AUTOFISH AUTONOMOUS LAUNCH</div>
          <h2>自主选品与上架草稿</h2>
          <p>AutoFish 负责选择、文案、精品图、视觉质检、排队和结果回写；本机浏览器桥接执行已登录网页操作。</p>
        </div>
        <div className={`adapter-health ${aiReady ? "ready" : "warning"}`}>
          {aiReady ? <ShieldCheck size={18} /> : <CircleAlert size={18} />}
          <div>
            <strong>{aiReady ? "自主内容引擎已就绪" : "模型运行尚未就绪"}</strong>
            <small>{contentStage?.detail ?? (qwen ? `${qwen.text_model} · ${qwen.vision_model} · ${qwen.image_model}` : "请先配置模型")}</small>
          </div>
        </div>
      </div>

      {notice ? (
        <button className="toast" onClick={() => setNotice("")} type="button">{notice}</button>
      ) : null}

      <section className="autonomous-layout">
        <div className="autonomous-left">
          <form className="card autonomous-form" onSubmit={start}>
            <div className="section-head">
              <div>
                <span className="eyebrow">NEW RUN</span>
                <h3>启动自主流程</h3>
              </div>
              <Rocket size={22} />
            </div>
            <label className="field">
              <span>标题关键词（可选）</span>
              <input maxLength={200} onChange={(event) => setQuery(event.target.value)} placeholder="例如：桌面收纳" value={query} />
            </label>
            <label className="field">
              <span>通过质检的精品图数量</span>
              <select onChange={(event) => setImageCount(Number(event.target.value))} value={imageCount}>
                <option value={1}>1 张</option>
                <option value={2}>2 张</option>
                <option value={3}>3 张</option>
                <option value={4}>4 张</option>
              </select>
            </label>
            <details className="autonomous-advanced">
              <summary>高级选项</summary>
              <label className="field">
                <span>限定候选 ID</span>
                <input min={1} onChange={(event) => setCandidateId(event.target.value)} placeholder="默认由系统自主选择" type="number" value={candidateId} />
              </label>
            </details>
            <button className="primary-button" disabled={!aiReady || running} type="submit">
              {running ? <LoaderCircle className="spin" size={16} /> : <Bot size={16} />}
              {running ? "正在提交…" : "由 AutoFish 自主生成"}
            </button>
            <p className="form-guidance">
              {publishStage?.ready
                ? "内容通过质检后会自动进入本机浏览器发布队列。"
                : `内容可以先生成；发布暂不可用：${publishStage?.detail ?? "正在检查浏览器执行器"}`}
            </p>
          </form>

          <section className="card autonomous-history">
            <div className="section-head">
              <div><span className="eyebrow">RUN HISTORY</span><h3>任务记录</h3></div>
              <button className="icon-button" onClick={() => void load()} title="刷新" type="button"><RefreshCw size={17} /></button>
            </div>
            <div className="launch-list">
              {launches.map((item) => (
                <button className={`launch-row ${selected?.id === item.id ? "active" : ""}`} key={item.id} onClick={() => setSelectedId(item.id)} type="button">
                  <span><b>#{item.id}</b><small>{item.ai_copy.title || `候选 ${item.sourcing_candidate_id ?? "待选择"}`}</small></span>
                  <span className={`tag ${statusTone(item.status)}`}>{statusLabel(item.status)}</span>
                </button>
              ))}
              {!launches.length ? <div className="empty-copy">尚无自主上架任务</div> : null}
            </div>
          </section>
        </div>

        <LaunchDetail launch={selected} retry={retry} running={running} />
      </section>
    </>
  );
}

function LaunchDetail({ launch, retry, running }: { launch: AutonomousLaunch | null; retry: () => void; running: boolean }) {
  if (!launch) return <section className="card autonomous-detail empty-state"><FileCheck2 size={28} /><b>启动任务后查看全链路证据</b></section>;
  const assets = launch.asset_manifest.filter((item) => item.kind === "generated" && item.qa?.passed);
  return (
    <section className="card autonomous-detail">
      <div className="section-head">
        <div><span className="eyebrow">RUN #{launch.id}</span><h3>{launch.ai_copy.title || "正在生成商品内容"}</h3></div>
        <span className={`tag ${statusTone(launch.status)}`}>{statusLabel(launch.status)}</span>
      </div>

      <div className="autonomous-metrics">
        <Metric label="候选评分" value={launch.selection.total_score ?? "—"} />
        <Metric label="候选 / 商品" value={`${launch.sourcing_candidate_id ?? "—"} / ${launch.product_id ?? "—"}`} />
        <Metric label="草稿" value={launch.draft_id ? `#${launch.draft_id}` : "—"} />
        <Metric label="图片质检" value={`${launch.vision_qa.passed_count ?? 0}/${launch.vision_qa.required_count ?? "—"}`} />
      </div>

      <div className="pipeline-stages">
        {launch.stages.map((stage, index) => (
          <div className="pipeline-stage" key={`${stage.name}-${index}`}>
            {stage.status === "OK" || stage.status === "READY" ? <CheckCircle2 size={17} /> : ACTIVE_STATUSES.has(launch.status) && stage.name === launch.current_stage ? <LoaderCircle className="spin" size={17} /> : <CircleAlert size={17} />}
            <span><b>{stage.name}</b><small>{stage.status}</small></span>
          </div>
        ))}
      </div>

      {launch.ai_copy.description ? <div className="autonomous-copy"><span>发布文案</span><p>{launch.ai_copy.description}</p></div> : null}

      {assets.length ? (
        <div className="autonomous-assets">
          {assets.map((asset) => (
            <figure key={asset.filename}>
              {/* eslint-disable-next-line @next/next/no-img-element -- Authenticated same-origin assets require session cookies that the image optimizer cannot reliably forward. */}
              <img alt={`${launch.ai_copy.title} 精品图`} src={`/api/v1/autonomous-launches/${launch.id}/assets/${encodeURIComponent(asset.filename)}`} />
              <figcaption><ImageIcon size={14} />{asset.model} · 质检 {asset.qa?.confidence}</figcaption>
            </figure>
          ))}
        </div>
      ) : null}

      {launch.blockers.length ? <div className="autonomous-blockers"><b>停止原因</b>{launch.blockers.map((item) => <p key={item}>{item}</p>)}</div> : null}

      <div className="handoff-note">
        <ShieldCheck size={18} />
        <div><b>外部提交：{launch.handoff.external_submission_performed ? "已完成" : "等待浏览器队列"}</b><small>正常链路由 AutoFish 与本机桥接闭环；只有登录、验证码、风控或结果不明确时需要人工。</small></div>
      </div>

      {launch.status === "MANUAL_REQUIRED" ? <button className="secondary-button" disabled={running} onClick={retry} type="button"><RefreshCw size={15} />从检查点重试</button> : null}
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}
