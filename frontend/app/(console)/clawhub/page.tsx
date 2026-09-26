"use client";

import {
  Activity,
  ArrowRight,
  BarChart3,
  Boxes,
  ClipboardCheck,
  Factory,
  FileOutput,
  LoaderCircle,
  MessageSquareText,
  Network,
  PlayCircle,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";
import {
  isPlatformActionPreviewCurrent,
  platformActionFingerprint,
} from "@/lib/platform-action-preview";
import type {
  ClawHubCapability,
  ClawHubList,
  ClawHubOverview,
  PlatformActionItem,
  ProductDiagnosisItem,
  ProductEvaluationItem,
  ProductItem,
  ProductResponse,
  SourcingCandidate,
  SupplierCandidateItem,
  SupplierInquiryItem,
  XianyuDraftItem,
} from "@/lib/types";

type CandidateResponse = { items: SourcingCandidate[]; total: number };

const capabilityCopy: Record<string, { description: string; icon: typeof Search }> = {
  shopkeeper: { description: "关键词找货、趋势与即时商机", icon: Search },
  product_find: { description: "文字、图片、链接找同款并对比", icon: Boxes },
  supplier_source: { description: "建立主供与备用供应商事实池", icon: Factory },
  action_gateway: { description: "所有写动作先检查、预览和审计", icon: ShieldCheck },
  item_select: { description: "上架前后评分与 S/A/B/C 分层", icon: ClipboardCheck },
  product_analysis: { description: "识别流量、转化、利润与供应异常", icon: Activity },
  draft_pipeline: { description: "把商品事实转换为闲鱼待审草稿", icon: FileOutput },
  inquiry: { description: "询问库存、交期、运费与代发条件", icon: MessageSquareText },
};

const ACTION_LABELS: Record<string, string> = {
  PUBLISH_XIANYU: "发布闲鱼商品",
  UPDATE_PRICE: "更新价格",
  REMOVE_LISTING: "下架商品",
};

function statusClass(status: string) {
  if (status === "ACTIVE") return "green";
  if (status === "SAFE_PREVIEW") return "blue";
  if (status === "MANUAL_REQUIRED") return "orange";
  return "gray";
}

function statusLabel(status: string) {
  return {
    ACTIVE: "已接入",
    PARTIAL: "部分可用",
    SAFE_PREVIEW: "安全预览",
    MANUAL_REQUIRED: "需人工",
  }[status] ?? status;
}

function parseObject(value: string): Record<string, number> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("指标必须是 JSON 对象");
  }
  return parsed as Record<string, number>;
}

export default function ClawHubPage() {
  const [overview, setOverview] = useState<ClawHubOverview | null>(null);
  const [candidates, setCandidates] = useState<SourcingCandidate[]>([]);
  const [products, setProducts] = useState<ProductItem[]>([]);
  const [suppliers, setSuppliers] = useState<SupplierCandidateItem[]>([]);
  const [evaluations, setEvaluations] = useState<ProductEvaluationItem[]>([]);
  const [diagnoses, setDiagnoses] = useState<ProductDiagnosisItem[]>([]);
  const [drafts, setDrafts] = useState<XianyuDraftItem[]>([]);
  const [inquiries, setInquiries] = useState<SupplierInquiryItem[]>([]);
  const [actions, setActions] = useState<PlatformActionItem[]>([]);
  const [activeId, setActiveId] = useState(1);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState("");
  const [result, setResult] = useState<unknown>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    try {
      const [
        overviewData,
        candidateData,
        productData,
        supplierData,
        evaluationData,
        diagnosisData,
        draftData,
        inquiryData,
        actionData,
      ] = await Promise.all([
        apiFetch<ClawHubOverview>("/api/v1/clawhub/overview"),
        apiFetch<CandidateResponse>("/api/v1/sourcing"),
        apiFetch<ProductResponse>("/api/v1/products?page_size=100"),
        apiFetch<ClawHubList<SupplierCandidateItem>>("/api/v1/clawhub/suppliers"),
        apiFetch<ClawHubList<ProductEvaluationItem>>("/api/v1/clawhub/evaluations"),
        apiFetch<ClawHubList<ProductDiagnosisItem>>("/api/v1/clawhub/diagnoses"),
        apiFetch<ClawHubList<XianyuDraftItem>>("/api/v1/clawhub/drafts"),
        apiFetch<ClawHubList<SupplierInquiryItem>>("/api/v1/clawhub/inquiries"),
        apiFetch<ClawHubList<PlatformActionItem>>("/api/v1/clawhub/actions"),
      ]);
      setOverview(overviewData);
      setCandidates(candidateData.items);
      setProducts(productData.items);
      setSuppliers(supplierData.items);
      setEvaluations(evaluationData.items);
      setDiagnoses(diagnosisData.items);
      setDrafts(draftData.items);
      setInquiries(inquiryData.items);
      setActions(actionData.items);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "能力台数据加载失败");
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function run(label: string, operation: () => Promise<unknown>) {
    setRunning(true);
    setNotice("");
    try {
      const response = await operation();
      setResult(response);
      setNotice(`${label}已完成`);
      await load(true);
    } catch (cause) {
      setResult(null);
      setNotice(cause instanceof Error ? cause.message : `${label}失败`);
    } finally {
      setRunning(false);
    }
  }

  const capabilities = overview?.capabilities ?? [];
  const active = capabilities.find((item) => item.id === activeId) ?? null;

  return (
    <>
      <div className="page-heading clawhub-heading">
        <div>
          <div className="eyebrow">CLAW HUB · 8 CAPABILITIES</div>
          <h2>1688 能力台</h2>
          <p>从货源发现到平台动作的统一工作台。事实可追溯，写入必须通过策略、限额、价格底线与审计。</p>
        </div>
        <div className="adapter-health ready">
          <ShieldCheck size={18} />
          <div>
            <strong>{overview?.safety.external_writes_enabled ? "受控写入与审计已启用" : "只读与审计保护已启用"}</strong>
            <small>{overview?.safety.mode ?? "正在读取安全状态"}</small>
          </div>
        </div>
      </div>

      {notice ? (
        <button className="toast" onClick={() => setNotice("")} type="button">
          {notice}
        </button>
      ) : null}

      {loading ? (
        <div className="card empty-state">
          <LoaderCircle className="spin" size={26} />
          <b>正在读取八大能力状态</b>
        </div>
      ) : (
        <>
          <section className="clawhub-capabilities" aria-label="八大能力">
            {capabilities.map((capability) => {
              const copy = capabilityCopy[capability.key];
              const Icon = copy?.icon ?? Sparkles;
              return (
                <button
                  aria-pressed={activeId === capability.id}
                  className={`capability-card ${activeId === capability.id ? "active" : ""}`}
                  key={capability.key}
                  onClick={() => {
                    setActiveId(capability.id);
                    setResult(null);
                  }}
                  type="button"
                >
                  <span className="capability-number">0{capability.id}</span>
                  <Icon size={20} />
                  <strong>{capability.name}</strong>
                  <small>{copy?.description}</small>
                  <span className={`tag ${statusClass(capability.status)}`}>
                    {statusLabel(capability.status)}
                  </span>
                </button>
              );
            })}
          </section>

          <section className="clawhub-summary">
            <Summary label="选品候选" value={overview?.counts.sourcing_candidates ?? 0} />
            <Summary label="供应商池" value={suppliers.length} />
            <Summary label="评估 / 诊断" value={evaluations.length + diagnoses.length} />
            <Summary label="草稿 / 询价" value={drafts.length + inquiries.length} />
            <button className="ghost-button" onClick={() => void load()} type="button">
              <RefreshCw size={15} />刷新事实
            </button>
          </section>

          <div className="clawhub-workspace">
            <section className="card capability-console">
              <div className="capability-console-head">
                <div>
                  <span>能力 {active?.id}</span>
                  <h3>{active?.name}</h3>
                  <p>{active ? capabilityCopy[active.key]?.description : ""}</p>
                </div>
                {active ? (
                  <span className={`tag ${statusClass(active.status)}`}>
                    {statusLabel(active.status)}
                  </span>
                ) : null}
              </div>
              <CapabilityForm
                activeId={activeId}
                actions={actions}
                candidates={candidates}
                drafts={drafts}
                products={products}
                running={running}
                suppliers={suppliers}
                run={run}
              />
            </section>
            <ResultPanel active={active} result={result} />
          </div>
        </>
      )}
    </>
  );
}

function Summary({ label, value }: { label: string; value: number }) {
  return (
    <div className="card clawhub-summary-item">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

type CapabilityFormProps = {
  activeId: number;
  candidates: SourcingCandidate[];
  drafts: XianyuDraftItem[];
  products: ProductItem[];
  suppliers: SupplierCandidateItem[];
  actions: PlatformActionItem[];
  running: boolean;
  run: (label: string, operation: () => Promise<unknown>) => Promise<void>;
};

function CapabilityForm(props: CapabilityFormProps) {
  if (props.activeId === 1) return <ShopkeeperForm {...props} />;
  if (props.activeId === 2) return <ProductFindForm {...props} />;
  if (props.activeId === 3) return <SupplierForm {...props} />;
  if (props.activeId === 4) return <ActionForm {...props} />;
  if (props.activeId === 5) return <EvaluationForm {...props} />;
  if (props.activeId === 6) return <DiagnosisForm {...props} />;
  if (props.activeId === 7) return <DraftForm {...props} />;
  return <InquiryForm {...props} />;
}

function ShopkeeperForm({ running, run }: CapabilityFormProps) {
  const [query, setQuery] = useState("");
  const [kind, setKind] = useState<"TREND" | "OPPORTUNITIES">("TREND");

  function search(event: FormEvent) {
    event.preventDefault();
    void run("关键词找货", () =>
      apiFetch("/api/v1/clawhub/discover", {
        method: "POST",
        body: JSON.stringify({ mode: "TEXT", query: query.trim(), limit: 20, filters: {} }),
      }),
    );
  }

  function insight() {
    void run("1688 洞察", () =>
      apiFetch("/api/v1/clawhub/insights", {
        method: "POST",
        body: JSON.stringify({
          kind,
          query: kind === "TREND" ? query.trim() : undefined,
          category: kind === "OPPORTUNITIES" ? query.trim() || undefined : undefined,
        }),
      }),
    );
  }

  return (
    <form className="capability-form" onSubmit={search}>
      <label className="field full">
        <span>关键词或类目</span>
        <input
          aria-label="1688 关键词或类目"
          minLength={2}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="例如：Mini PC VESA 支架"
          required
          value={query}
        />
      </label>
      <div className="capability-actions full">
        <button className="primary-button" disabled={running || query.trim().length < 2}>
          <Search size={16} />关键词找货
        </button>
        <select aria-label="洞察类型" onChange={(event) => setKind(event.target.value as typeof kind)} value={kind}>
          <option value="TREND">趋势</option>
          <option value="OPPORTUNITIES">即时商机</option>
        </select>
        <button className="secondary-button" disabled={running} onClick={insight} type="button">
          <BarChart3 size={16} />读取洞察
        </button>
      </div>
      <p className="form-guidance full">调用当前已授权的 Shopkeeper 只读 CLI，结果会保存为事实快照。</p>
    </form>
  );
}

function ProductFindForm({ candidates, running, run }: CapabilityFormProps) {
  const [mode, setMode] = useState<"TEXT" | "IMAGE" | "LINK">("IMAGE");
  const [value, setValue] = useState("");
  const [ids, setIds] = useState("");
  const suggestedIds = useMemo(() => candidates.slice(0, 2).map((item) => item.id).join(","), [candidates]);

  function discover(event: FormEvent) {
    event.preventDefault();
    void run("多入口找货", () =>
      apiFetch("/api/v1/clawhub/discover", {
        method: "POST",
        body: JSON.stringify({
          mode,
          query: mode === "TEXT" ? value.trim() : undefined,
          source_url: mode === "TEXT" ? undefined : value.trim(),
          limit: 20,
          filters: {},
        }),
      }),
    );
  }

  function compare() {
    const candidateIds = (ids || suggestedIds)
      .split(",")
      .map((item) => Number(item.trim()))
      .filter(Boolean);
    void run("候选比价", () =>
      apiFetch("/api/v1/clawhub/compare", {
        method: "POST",
        body: JSON.stringify({ candidate_ids: candidateIds }),
      }),
    );
  }

  return (
    <form className="capability-form" onSubmit={discover}>
      <label className="field">
        <span>发现入口</span>
        <select onChange={(event) => setMode(event.target.value as typeof mode)} value={mode}>
          <option value="TEXT">文字</option>
          <option value="IMAGE">图片 URL</option>
          <option value="LINK">商品链接</option>
        </select>
      </label>
      <label className="field">
        <span>{mode === "TEXT" ? "关键词" : "HTTPS 公网地址"}</span>
        <input onChange={(event) => setValue(event.target.value)} required value={value} />
      </label>
      <button className="primary-button form-submit" disabled={running}>
        <Network size={16} />查找同款
      </button>
      <label className="field full">
        <span>候选 ID（逗号分隔，至少两个）</span>
        <input
          onChange={(event) => setIds(event.target.value)}
          placeholder={suggestedIds || "例如：12,18"}
          value={ids}
        />
      </label>
      <div className="capability-actions full">
        <button className="secondary-button" disabled={running || (!ids && !suggestedIds)} onClick={compare} type="button">
          <BarChart3 size={16} />对比事实
        </button>
      </div>
      <p className="form-guidance full">图片和链接能力需要 Product Find CLI；未配置时会明确返回“不支持”。</p>
    </form>
  );
}

function SupplierForm({ candidates, running, run }: CapabilityFormProps) {
  const [candidateId, setCandidateId] = useState("");
  const [query, setQuery] = useState("");
  return (
    <form
      className="capability-form"
      onSubmit={(event) => {
        event.preventDefault();
        void run("供应商发现", () =>
          apiFetch("/api/v1/clawhub/suppliers/discover", {
            method: "POST",
            body: JSON.stringify({
              sourcing_candidate_id: candidateId ? Number(candidateId) : undefined,
              query: candidateId ? undefined : query.trim(),
              limit: 10,
            }),
          }),
        );
      }}
    >
      <label className="field">
        <span>从选品候选出发</span>
        <select onChange={(event) => setCandidateId(event.target.value)} value={candidateId}>
          <option value="">按关键词发现</option>
          {candidates.map((item) => <option key={item.id} value={item.id}>#{item.id} · {item.title}</option>)}
        </select>
      </label>
      <label className="field">
        <span>关键词</span>
        <input disabled={Boolean(candidateId)} onChange={(event) => setQuery(event.target.value)} value={query} />
      </label>
      <button className="primary-button form-submit" disabled={running || (!candidateId && query.trim().length < 2)}>
        <Factory size={16} />建立供应商池
      </button>
      <p className="form-guidance full">只使用已入库商品中的真实供应商标识，不补造响应速度、交付能力等未知事实。</p>
    </form>
  );
}

function ActionForm({ actions, drafts, products, running, run }: CapabilityFormProps) {
  const [targetId, setTargetId] = useState("");
  const [actionType, setActionType] = useState("PUBLISH_XIANYU");
  const [price, setPrice] = useState("");
  const [currentPreview, setCurrentPreview] = useState<{
    action: PlatformActionItem;
    actionLabel: string;
    fingerprint: string;
    price: string;
    targetLabel: string;
  } | null>(null);
  const previewRequestToken = useRef(0);
  const lastAction = actions[0];
  const publish = actionType === "PUBLISH_XIANYU";
  const options = publish ? drafts.filter((item) => item.status === "REVIEW_READY") : products;
  const formValue = { actionType, targetId, price };
  const executablePreview = isPlatformActionPreviewCurrent(currentPreview, formValue)
    ? currentPreview
    : null;

  function invalidatePreview() {
    previewRequestToken.current += 1;
    setCurrentPreview(null);
  }

  function changeActionType(nextActionType: string) {
    invalidatePreview();
    setActionType(nextActionType);
    setTargetId("");
    setPrice("");
  }

  function changeTargetId(nextTargetId: string) {
    invalidatePreview();
    setTargetId(nextTargetId);
  }

  function changePrice(nextPrice: string) {
    invalidatePreview();
    setPrice(nextPrice);
  }

  function preview(event: FormEvent) {
    event.preventDefault();
    const selectedTarget = options.find((item) => item.id === Number(targetId));
    if (!selectedTarget) return;

    const submittedFingerprint = platformActionFingerprint(formValue);
    const requestToken = previewRequestToken.current + 1;
    previewRequestToken.current = requestToken;
    setCurrentPreview(null);

    void run("动作预检", async () => {
      const action = await apiFetch<PlatformActionItem>("/api/v1/clawhub/actions/preview", {
        method: "POST",
        body: JSON.stringify({
          action_type: actionType,
          target_type: publish ? "XIANYU_DRAFT" : "PRODUCT",
          target_id: Number(targetId),
          payload: actionType === "UPDATE_PRICE" ? { price: Number(price) } : {},
        }),
      });
      if (previewRequestToken.current === requestToken) {
        setCurrentPreview({
          action,
          actionLabel: ACTION_LABELS[actionType] ?? actionType,
          fingerprint: submittedFingerprint,
          price,
          targetLabel: `#${selectedTarget.id} · ${selectedTarget.title}`,
        });
      }
      return action;
    });
  }

  function execute(preview: NonNullable<typeof executablePreview>) {
    if (!isPlatformActionPreviewCurrent(preview, formValue)) {
      invalidatePreview();
      return;
    }
    const priceChange = actionType === "UPDATE_PRICE" ? `\n新售价：¥${preview.price}` : "";
    if (!window.confirm(
      `确认执行「${preview.actionLabel}」？\n\n目标：${preview.targetLabel}${priceChange}\n\n动作会写入真实平台并留下审计记录。`,
    )) {
      return;
    }
    invalidatePreview();
    void run("平台动作", () =>
      apiFetch(`/api/v1/clawhub/actions/${preview.action.id}/execute`, {
        method: "POST",
        body: JSON.stringify({ confirm: preview.action.requires_confirmation }),
      }),
    );
  }

  return (
    <form className="capability-form" onSubmit={preview}>
      <label className="field">
        <span>动作</span>
        <select onChange={(event) => changeActionType(event.target.value)} value={actionType}>
          <option value="PUBLISH_XIANYU">发布闲鱼商品</option>
          <option value="UPDATE_PRICE">更新价格</option>
          <option value="REMOVE_LISTING">下架商品</option>
        </select>
      </label>
      <label className="field">
        <span>{publish ? "待发布草稿" : "商品目标"}</span>
        <select required onChange={(event) => changeTargetId(event.target.value)} value={targetId}>
          <option value="">请选择</option>
          {options.map((item) => <option key={item.id} value={item.id}>#{item.id} · {item.title}</option>)}
        </select>
      </label>
      {actionType === "UPDATE_PRICE" ? <label className="field"><span>新售价</span><input min="0.01" onChange={(event) => changePrice(event.target.value)} required step="0.01" type="number" value={price} /></label> : null}
      <button className="primary-button form-submit" disabled={running || !targetId}>
        <ShieldCheck size={16} />生成预览
      </button>
      {executablePreview ? <button className="danger-button form-submit" disabled={running} onClick={() => execute(executablePreview)} type="button"><PlayCircle size={16} />确认执行 #{executablePreview.action.id}</button> : null}
      {lastAction ? <p className="form-guidance full">最近动作 #{lastAction.id}：{lastAction.status} · {lastAction.provider ?? "未选择 Adapter"}{lastAction.error_message ? ` · ${lastAction.error_message}` : ""}</p> : null}
    </form>
  );
}

function EvaluationForm({ candidates, products, running, run }: CapabilityFormProps) {
  const [target, setTarget] = useState("candidate");
  const [targetId, setTargetId] = useState("");
  const [stage, setStage] = useState("PRE_LAUNCH");
  const [metrics, setMetrics] = useState('{"traffic":70,"interest":60,"conversion":55,"refund_safety":90,"profit":75,"supply_stability":80}');
  const options = target === "product" ? products : candidates;
  return (
    <form
      className="capability-form"
      onSubmit={(event) => {
        event.preventDefault();
        void run("商品评估", () =>
          apiFetch("/api/v1/clawhub/evaluations", {
            method: "POST",
            body: JSON.stringify({
              product_id: target === "product" ? Number(targetId) : undefined,
              sourcing_candidate_id: target === "candidate" ? Number(targetId) : undefined,
              stage,
              metrics: stage === "POST_LAUNCH" ? parseObject(metrics) : {},
            }),
          }),
        );
      }}
    >
      <label className="field"><span>对象</span><select onChange={(event) => { setTarget(event.target.value); setTargetId(""); }} value={target}><option value="candidate">选品候选</option><option value="product">商品</option></select></label>
      <label className="field"><span>目标</span><select required onChange={(event) => setTargetId(event.target.value)} value={targetId}><option value="">请选择</option>{options.map((item) => <option key={item.id} value={item.id}>#{item.id} · {item.title}</option>)}</select></label>
      <label className="field"><span>阶段</span><select onChange={(event) => setStage(event.target.value)} value={stage}><option value="PRE_LAUNCH">上架前</option><option value="POST_LAUNCH">上架后</option></select></label>
      {stage === "POST_LAUNCH" ? <label className="field full"><span>0–100 指标 JSON</span><textarea onChange={(event) => setMetrics(event.target.value)} rows={4} value={metrics} /></label> : null}
      <button className="primary-button form-submit" disabled={running || !targetId}><ClipboardCheck size={16} />计算等级</button>
      <p className="form-guidance full">S/A/B/C 只给出生命周期建议，不自动更改商品状态。</p>
    </form>
  );
}

function DiagnosisForm({ candidates, products, running, run }: CapabilityFormProps) {
  const [target, setTarget] = useState("candidate");
  const [targetId, setTargetId] = useState("");
  const [metrics, setMetrics] = useState('{"traffic":70,"conversion":10,"refund_risk":5,"profit":65,"supply_stability":80}');
  const options = target === "product" ? products : candidates;
  return (
    <form className="capability-form" onSubmit={(event) => { event.preventDefault(); void run("商品诊断", () => apiFetch("/api/v1/clawhub/diagnoses", { method: "POST", body: JSON.stringify({ product_id: target === "product" ? Number(targetId) : undefined, sourcing_candidate_id: target === "candidate" ? Number(targetId) : undefined, metrics: parseObject(metrics) }) })); }}>
      <label className="field"><span>对象</span><select onChange={(event) => { setTarget(event.target.value); setTargetId(""); }} value={target}><option value="candidate">选品候选</option><option value="product">商品</option></select></label>
      <label className="field"><span>目标</span><select required onChange={(event) => setTargetId(event.target.value)} value={targetId}><option value="">请选择</option>{options.map((item) => <option key={item.id} value={item.id}>#{item.id} · {item.title}</option>)}</select></label>
      <label className="field full"><span>0–100 诊断指标 JSON</span><textarea onChange={(event) => setMetrics(event.target.value)} rows={5} value={metrics} /></label>
      <button className="primary-button form-submit" disabled={running || !targetId}><Activity size={16} />运行诊断</button>
    </form>
  );
}

function DraftForm({ products, running, run }: CapabilityFormProps) {
  const [productId, setProductId] = useState("");
  const [price, setPrice] = useState("");
  return (
    <form className="capability-form" onSubmit={(event) => { event.preventDefault(); void run("闲鱼草稿", () => apiFetch("/api/v1/clawhub/drafts", { method: "POST", body: JSON.stringify({ product_id: Number(productId), title_mode: "RULE_ONLY", price: price ? Number(price) : undefined }) })); }}>
      <label className="field"><span>商品</span><select required onChange={(event) => setProductId(event.target.value)} value={productId}><option value="">请选择</option>{products.map((item) => <option key={item.id} value={item.id}>#{item.id} · {item.title}</option>)}</select></label>
      <label className="field"><span>覆盖售价（可选）</span><input min="0.01" onChange={(event) => setPrice(event.target.value)} step="0.01" type="number" value={price} /></label>
      <button className="primary-button form-submit" disabled={running || !productId}><FileOutput size={16} />生成待审草稿</button>
      <p className="form-guidance full">规则流水线会移除“自用闲置”等虚假来源表述，并校验最低安全售价。</p>
    </form>
  );
}

function InquiryForm({ candidates, suppliers, running, run }: CapabilityFormProps) {
  const [candidateId, setCandidateId] = useState("");
  const [supplierId, setSupplierId] = useState("");
  const [topic, setTopic] = useState("确认代发与履约条件");
  const [questions, setQuestions] = useState("当前可售库存是多少？\n正常发货时效是多少？\n是否支持一件代发？");
  return (
    <form className="capability-form" onSubmit={(event) => { event.preventDefault(); void run("询价任务", () => apiFetch("/api/v1/clawhub/inquiries", { method: "POST", body: JSON.stringify({ supplier_candidate_id: supplierId ? Number(supplierId) : undefined, sourcing_candidate_id: candidateId ? Number(candidateId) : undefined, topic, questions: questions.split("\n").map((item) => item.trim()).filter(Boolean) }) })); }}>
      <label className="field"><span>供应商候选（可选）</span><select onChange={(event) => setSupplierId(event.target.value)} value={supplierId}><option value="">未指定</option>{suppliers.map((item) => <option key={item.id} value={item.id}>#{item.id} · {item.name}</option>)}</select></label>
      <label className="field"><span>选品候选（可选）</span><select onChange={(event) => setCandidateId(event.target.value)} value={candidateId}><option value="">未指定</option>{candidates.map((item) => <option key={item.id} value={item.id}>#{item.id} · {item.title}</option>)}</select></label>
      <label className="field full"><span>主题</span><input onChange={(event) => setTopic(event.target.value)} value={topic} /></label>
      <label className="field full"><span>问题（每行一个）</span><textarea onChange={(event) => setQuestions(event.target.value)} rows={5} value={questions} /></label>
      <button className="primary-button form-submit" disabled={running || (!candidateId && !supplierId)}><MessageSquareText size={16} />创建询价</button>
      <p className="form-guidance full">上游写通道未启用时会创建“需人工”任务，不会宣称已经联系供应商。</p>
    </form>
  );
}

function ResultPanel({ active, result }: { active: ClawHubCapability | null; result: unknown }) {
  return (
    <aside className="card clawhub-result">
      <div className="panel-heading">
        <h3><PlayCircle size={16} />运行结果</h3>
        {active ? <span className="tag gray">能力 {active.id}</span> : null}
      </div>
      {result ? (
        <pre>{JSON.stringify(result, null, 2)}</pre>
      ) : (
        <div className="empty-state">
          <ArrowRight size={25} />
          <b>选择参数后运行</b>
          <span>这里展示真实返回、评分证据、阻断原因或人工任务状态。</span>
        </div>
      )}
    </aside>
  );
}
