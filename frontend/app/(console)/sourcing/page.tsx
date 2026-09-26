"use client";

import {
  Box,
  ClipboardCheck,
  DatabaseZap,
  Download,
  FileJson,
  Plus,
  RefreshCw,
  Search,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";
import { fetchIntegrationStatus } from "@/lib/integrations";
import type { IntegrationStatus, SourcingCandidate } from "@/lib/types";

type CandidateResponse = { items: SourcingCandidate[]; total: number };

type VerificationSkuForm = {
  rowId: number;
  externalSkuId: string;
  specJson: string;
  price: string;
  shipping: string;
  stock: string;
};

const FEED_EXAMPLE = `{
  "adapter_name": "authorized-json-feed",
  "adapter_version": "1.0",
  "items": [{
    "external_product_id": "1688-商品ID",
    "title": "商品标题",
    "category": "被动配件",
    "external_supplier_id": "供应商ID",
    "supplier_name": "供应商名称",
    "score_inputs": {
      "profit_space": 80,
      "after_sales_safety": 90,
      "fault_safety": 90,
      "compatibility_safety": 85,
      "transport_safety": 90,
      "stock_stability": 80,
      "price_stability": 75,
      "verticality": 95
    },
    "skus": [{
      "external_sku_id": "SKU-ID",
      "spec": {"颜色": "银色"},
      "price": "29.90",
      "shipping": "6.00",
      "stock": 100
    }]
  }]
}`;

function formatMoney(value: string | null) {
  return value === null ? "—" : `¥${Number(value).toFixed(2)}`;
}

function missingEvidence(candidate: SourcingCandidate) {
  const missing: string[] = [];
  if (!candidate.url) missing.push("1688 详情链接");
  if (!candidate.category) missing.push("类目");
  if (!candidate.normalized_data.external_supplier_id) missing.push("供应商 ID");
  if (!candidate.supplier_name) missing.push("供应商名称");
  if (!candidate.image_url) missing.push("商品主图证据");
  if (!candidate.normalized_data.skus?.length) missing.push("结构化 SKU、成本、运费和库存");
  return missing;
}

function moneyInputValue(value: string | { amount: string } | undefined) {
  if (typeof value === "string") return value;
  return value?.amount ?? "";
}

function initialVerificationSkus(candidate: SourcingCandidate): VerificationSkuForm[] {
  const existing = candidate.normalized_data.skus ?? [];
  if (existing.length === 0) {
    return [{
      rowId: 0,
      externalSkuId: "",
      specJson: "{}",
      price: candidate.minimum_price ?? "",
      shipping: "",
      stock: "",
    }];
  }
  return existing.map((sku, index) => ({
    rowId: index,
    externalSkuId: sku.external_sku_id,
    specJson: JSON.stringify(sku.spec ?? {}, null, 2),
    price: moneyInputValue(sku.price),
    shipping: moneyInputValue(sku.shipping),
    stock: sku.stock === undefined ? "" : String(sku.stock),
  }));
}

function HealthPanel({ status }: { status: IntegrationStatus | null }) {
  const health = status?.supplier;
  const ready = Boolean(status?.global_enabled && health?.authenticated);
  return (
    <div className={`adapter-health ${ready ? "ready" : "waiting"}`}>
      <div>
        <span className={`status-dot ${ready ? "" : "amber"}`} />
        <strong>{ready ? "只读连接可用" : "等待授权"}</strong>
      </div>
      <small>{health?.configured_provider ?? "disabled"} · 写操作永久关闭</small>
    </div>
  );
}

export default function SourcingPage() {
  const [status, setStatus] = useState<IntegrationStatus | null>(null);
  const [items, setItems] = useState<SourcingCandidate[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);
  const [feedOpen, setFeedOpen] = useState(false);
  const [feedText, setFeedText] = useState("");
  const [importOpen, setImportOpen] = useState(false);
  const [verificationOpen, setVerificationOpen] = useState(false);

  const load = useCallback(async (forceHealth = false) => {
    setLoading(true);
    try {
      const [health, candidates] = await Promise.all([
        fetchIntegrationStatus(forceHealth),
        apiFetch<CandidateResponse>("/api/v1/sourcing"),
      ]);
      setStatus(health);
      setItems(candidates.items);
      setSelectedId((current) => current ?? candidates.items[0]?.id ?? null);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "选品数据加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const selected = useMemo(
    () => items.find((item) => item.id === selectedId) ?? null,
    [items, selectedId],
  );
  const searchReady = Boolean(status?.global_enabled && status.supplier.authenticated);

  async function searchProducts(event: FormEvent) {
    event.preventDefault();
    if (!query.trim() || !searchReady) return;
    setSearching(true);
    try {
      const response = await apiFetch<{ items: SourcingCandidate[] }>("/api/v1/sourcing/search", {
        method: "POST",
        body: JSON.stringify({ query: query.trim(), limit: 20, filters: {} }),
      });
      setItems(response.items);
      setSelectedId(response.items[0]?.id ?? null);
      setNotice(`已获取 ${response.items.length} 条只读候选`);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "搜索失败");
    } finally {
      setSearching(false);
    }
  }

  async function ingestFeed(event: FormEvent) {
    event.preventDefault();
    try {
      const payload = JSON.parse(feedText) as unknown;
      const response = await apiFetch<{ items: SourcingCandidate[] }>("/api/v1/sourcing/ingest", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      setFeedOpen(false);
      setFeedText("");
      setNotice(`已校验并写入 ${response.items.length} 条标准供应数据`);
      await load();
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "JSON 格式或字段不符合契约");
    }
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <div className="eyebrow">1688 SOURCING · READ ONLY</div>
          <h2>1688 选品</h2>
          <p>搜索授权数据源，核对成本、库存与供应稳定性后再导入商品中心。</p>
        </div>
        <HealthPanel status={status} />
      </div>

      {notice ? <button className="toast" onClick={() => setNotice("")}>{notice}</button> : null}

      {!searchReady ? (
        <div className="integration-notice">
          <ShieldCheck size={18} />
          <div><b>搜索接口尚未授权</b><span>{status?.supplier.message ?? "请配置官方 JSON Gateway 或经授权的 1688 AI 版 AK。"}</span></div>
          <button className="secondary-button" onClick={() => setFeedOpen(true)} type="button"><FileJson size={15} />导入标准数据</button>
        </div>
      ) : null}

      <form className="sourcing-search" onSubmit={searchProducts}>
        <div className="search-field sourcing-query"><Search size={17} /><input aria-label="1688 商品关键词" onChange={(event) => setQuery(event.target.value)} placeholder="输入商品关键词，例如：桌面收纳" value={query} /></div>
        <button className="primary-button" disabled={!searchReady || searching || query.trim().length < 2} type="submit"><Search size={16} />{searching ? "搜索中…" : "搜索货源"}</button>
        <button className="secondary-button" onClick={() => setFeedOpen(true)} type="button"><FileJson size={16} />标准 JSON</button>
        <button className="ghost-button" onClick={() => void load(true)} type="button"><RefreshCw size={15} />刷新</button>
      </form>

      <div className="sourcing-layout sourcing-simple">
        <section className="card sourcing-table-card">
          {loading ? <div className="empty-state"><RefreshCw className="spin" size={24} /><b>正在读取候选池</b></div> : items.length === 0 ? (
            <div className="empty-state"><DatabaseZap size={30} /><b>候选池为空</b><span>连接授权数据源搜索，或导入符合 Canonical Feed 契约的 JSON。</span></div>
          ) : (
            <div className="table-wrap"><table><thead><tr><th>商品</th><th>供应商</th><th>采购价</th><th>SKU</th><th>库存</th><th>评分</th><th>状态</th></tr></thead><tbody>
              {items.map((item) => <tr aria-selected={selectedId === item.id} className={`product-row ${selectedId === item.id ? "selected" : ""}`} key={item.id} onClick={() => setSelectedId(item.id)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelectedId(item.id); } }} tabIndex={0}><td><div className="product-cell"><span className="product-thumb"><Box size={19} /></span><span className="product-name"><b>{item.title}</b><small>{item.category ?? "类目待补全"} · {item.external_product_id}</small></span></div></td><td>{item.supplier_name ?? "待详情补全"}</td><td>{formatMoney(item.minimum_price)}</td><td>{item.sku_count || "—"}</td><td>{item.stock ?? "—"}</td><td><span className="score-value">{item.score ?? "—"}</span></td><td><span className={`tag ${item.status === "IMPORTED" || item.status === "EXCLUDED" ? "gray" : item.import_ready ? "green" : "orange"}`}>{item.status === "IMPORTED" ? "已导入" : item.status === "EXCLUDED" ? "已排除" : item.import_ready ? "可导入" : "待补字段"}</span></td></tr>)}
            </tbody></table></div>
          )}
        </section>

        <aside className="card sourcing-inspector">
          {selected ? (
            <>
              <div className="inspector-hero"><span className="product-thumb"><Box size={24} /></span><div><h3>{selected.title}</h3><p>{selected.category ?? "类目待补全"}</p><span className={`tag ${selected.import_ready ? "green" : "orange"}`}>{selected.import_ready ? "字段完整" : "不可安全导入"}</span></div></div>
              <div className="inspector-section"><h4>来源事实</h4><div className="detail-list"><div className="detail-row"><span>Adapter</span><strong>{selected.adapter_name}</strong></div><div className="detail-row"><span>外部商品</span><strong>{selected.external_product_id}</strong></div><div className="detail-row"><span>供应商</span><strong>{selected.supplier_name ?? "—"}</strong></div><div className="detail-row"><span>最近获取</span><strong>{new Date(selected.last_fetched_at).toLocaleString("zh-CN", { hour12: false })}</strong></div></div></div>
              <div className="inspector-section"><h4>SKU 与成本</h4><div className="detail-list"><div className="detail-row"><span>SKU 数</span><strong>{selected.sku_count}</strong></div><div className="detail-row"><span>库存合计</span><strong>{selected.stock ?? "—"}</strong></div><div className="detail-row total"><span>采购价格带</span><strong>{formatMoney(selected.minimum_price)}{selected.maximum_price !== selected.minimum_price ? ` – ${formatMoney(selected.maximum_price)}` : ""}</strong></div></div></div>
              {!selected.import_ready && !["IMPORTED", "EXCLUDED"].includes(selected.status) ? (
                <div className="verification-missing">
                  <b>待核验字段</b>
                  <span>{missingEvidence(selected).join("、") || "供应事实尚未通过完整性门禁"}</span>
                </div>
              ) : null}
              <div className="inspector-actions">
                {!selected.import_ready && !["IMPORTED", "EXCLUDED"].includes(selected.status) ? (
                  <button className="secondary-button" onClick={() => setVerificationOpen(true)} type="button"><ClipboardCheck size={16} />人工核验补全</button>
                ) : null}
                <button className="primary-button" disabled={!selected.import_ready || selected.status === "IMPORTED"} onClick={() => setImportOpen(true)} type="button"><Download size={16} />{selected.status === "IMPORTED" ? "已导入商品中心" : "导入商品中心"}</button>
                {selected.url ? <a className="secondary-button" href={selected.url} rel="noreferrer" target="_blank">查看授权来源</a> : null}
              </div>
            </>
          ) : <div className="empty-state"><Box size={27} /><span>选择一条候选查看事实快照</span></div>}
        </aside>
      </div>

      {feedOpen ? <FeedDialog feedText={feedText} onChange={setFeedText} onClose={() => setFeedOpen(false)} onSubmit={ingestFeed} /> : null}
      {verificationOpen && selected ? <VerificationDialog candidate={selected} onClose={() => setVerificationOpen(false)} onDone={async () => { setVerificationOpen(false); await load(); }} onNotice={setNotice} /> : null}
      {importOpen && selected ? <ImportDialog candidate={selected} onClose={() => setImportOpen(false)} onDone={async () => { setImportOpen(false); await load(); }} onNotice={setNotice} /> : null}
    </>
  );
}

function FeedDialog({ feedText, onChange, onClose, onSubmit }: { feedText: string; onChange: (value: string) => void; onClose: () => void; onSubmit: (event: FormEvent) => void }) {
  return <div className="dialog-backdrop"><form aria-labelledby="sourcing-feed-title" aria-modal="true" className="dialog-card" onSubmit={onSubmit} role="dialog"><div className="dialog-header"><div><h3 id="sourcing-feed-title">导入标准供应数据</h3><p className="muted">只接受 Canonical Feed；敏感字段会在落库前脱敏。</p></div><button aria-label="关闭标准供应数据导入" className="icon-button" onClick={onClose} type="button"><X size={19} /></button></div><div className="dialog-body"><textarea aria-label="标准供应 JSON" onChange={(event) => onChange(event.target.value)} placeholder={FEED_EXAMPLE} rows={18} value={feedText} style={{ width: "100%", fontFamily: "ui-monospace, monospace", fontSize: 12 }} /></div><div className="dialog-actions"><button className="ghost-button" onClick={onClose} type="button">取消</button><button className="primary-button" disabled={!feedText.trim()} type="submit"><DatabaseZap size={15} />校验并写入</button></div></form></div>;
}

function VerificationDialog({ candidate, onClose, onDone, onNotice }: { candidate: SourcingCandidate; onClose: () => void; onDone: () => Promise<void>; onNotice: (value: string) => void }) {
  const [saving, setSaving] = useState(false);
  const [skus, setSkus] = useState<VerificationSkuForm[]>(() => initialVerificationSkus(candidate));
  const nextRowId = useRef(skus.length);

  function updateSku(rowId: number, patch: Partial<VerificationSkuForm>) {
    setSkus((current) => current.map((sku) => sku.rowId === rowId ? { ...sku, ...patch } : sku));
  }

  function addSku() {
    const rowId = nextRowId.current;
    nextRowId.current += 1;
    setSkus((current) => [...current, { rowId, externalSkuId: "", specJson: "{}", price: "", shipping: "", stock: "" }]);
  }

  function removeSku(rowId: number) {
    setSkus((current) => current.length === 1 ? current : current.filter((sku) => sku.rowId !== rowId));
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const read = (name: string) => String(form.get(name) ?? "").trim();
    setSaving(true);
    try {
      const verifiedSkus = skus.map((sku, index) => {
        let parsedSpec: unknown;
        try {
          parsedSpec = JSON.parse(sku.specJson || "{}") as unknown;
        } catch {
          throw new Error(`第 ${index + 1} 个 SKU 的规格 JSON 格式错误`);
        }
        if (!parsedSpec || Array.isArray(parsedSpec) || typeof parsedSpec !== "object") {
          throw new Error(`第 ${index + 1} 个 SKU 的规格必须是 JSON 对象`);
        }
        const stock = Number(sku.stock);
        if (!Number.isSafeInteger(stock) || stock < 0) {
          throw new Error(`第 ${index + 1} 个 SKU 的库存必须是非负整数`);
        }
        return {
          external_sku_id: sku.externalSkuId.trim(),
          spec: parsedSpec,
          price: sku.price,
          shipping: sku.shipping,
          stock,
        };
      });
      const response = await apiFetch<{ candidate: SourcingCandidate }>(`/api/v1/sourcing/${candidate.id}/manual-verification`, {
        method: "POST",
        body: JSON.stringify({
          source_url: read("source_url"),
          captured_at: new Date().toISOString(),
          category: read("category"),
          external_supplier_id: read("external_supplier_id"),
          supplier_name: read("supplier_name"),
          image_url: read("image_url"),
          skus: verifiedSkus,
          evidence_note: read("evidence_note"),
        }),
      });
      onNotice(response.candidate.import_ready ? "供应证据已保存，候选现已通过导入门禁" : "供应证据已保存，但候选仍有阻断项");
      await onDone();
    } catch (cause) {
      onNotice(cause instanceof Error ? cause.message : "供应证据核验失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop">
      <form aria-labelledby="supplier-verification-title" aria-modal="true" className="dialog-card verification-dialog" onSubmit={submit} role="dialog">
        <div className="dialog-header">
          <div><h3 id="supplier-verification-title">人工核验供应证据</h3><p className="muted">请从刚打开的 1688 详情页逐项抄录；证据超过 24 小时会被拒绝。</p></div>
          <button aria-label="关闭供应证据核验" className="icon-button" onClick={onClose} type="button"><X size={19} /></button>
        </div>
        <div className="dialog-body verification-body">
          <div className="form-grid">
            <label className="field full"><span>1688 商品详情链接</span><input defaultValue={candidate.url ?? `https://detail.1688.com/offer/${candidate.external_product_id}.html`} name="source_url" required type="url" /></label>
            <label className="field"><span>类目</span><input defaultValue={candidate.category ?? ""} name="category" required /></label>
            <label className="field"><span>供应商 ID</span><input defaultValue={candidate.normalized_data.external_supplier_id ?? ""} name="external_supplier_id" required /></label>
            <label className="field full"><span>供应商名称</span><input defaultValue={candidate.supplier_name ?? ""} name="supplier_name" required /></label>
            <label className="field full"><span>1688 主图 HTTPS 地址</span><input defaultValue={candidate.image_url ?? ""} name="image_url" placeholder="https://cbu01.alicdn.com/…" required type="url" /></label>
          </div>

          <div className="verification-sku-heading">
            <div><h4>SKU 成本证据</h4><span>每个 SKU 必须有真实 ID、规格、采购价、运费和可售库存。</span></div>
            <button className="secondary-button" onClick={addSku} type="button"><Plus size={15} />添加 SKU</button>
          </div>
          <div className="verification-sku-list">
            {skus.map((sku, index) => (
              <section className="verification-sku" key={sku.rowId}>
                <div className="verification-sku-title"><b>SKU {index + 1}</b><button aria-label={`删除 SKU ${index + 1}`} className="icon-button" disabled={skus.length === 1} onClick={() => removeSku(sku.rowId)} type="button"><Trash2 size={15} /></button></div>
                <div className="form-grid verification-sku-grid">
                  <label className="field full"><span>外部 SKU ID</span><input onChange={(event) => updateSku(sku.rowId, { externalSkuId: event.target.value })} required value={sku.externalSkuId} /></label>
                  <label className="field full"><span>规格 JSON</span><textarea onChange={(event) => updateSku(sku.rowId, { specJson: event.target.value })} required rows={3} value={sku.specJson} /></label>
                  <label className="field"><span>采购价</span><input min="0" onChange={(event) => updateSku(sku.rowId, { price: event.target.value })} required step="0.01" type="number" value={sku.price} /></label>
                  <label className="field"><span>单件运费</span><input min="0" onChange={(event) => updateSku(sku.rowId, { shipping: event.target.value })} required step="0.01" type="number" value={sku.shipping} /></label>
                  <label className="field full"><span>可售库存</span><input min="0" onChange={(event) => updateSku(sku.rowId, { stock: event.target.value })} required step="1" type="number" value={sku.stock} /></label>
                </div>
              </section>
            ))}
          </div>
          <label className="field"><span>核验备注</span><textarea defaultValue="人工核对 1688 详情页、SKU 面板和供应商信息" name="evidence_note" required rows={3} /></label>
        </div>
        <div className="dialog-actions"><button className="ghost-button" onClick={onClose} type="button">取消</button><button className="primary-button" disabled={saving} type="submit"><ClipboardCheck size={15} />{saving ? "校验中…" : "校验并保存"}</button></div>
      </form>
    </div>
  );
}

function ImportDialog({ candidate, onClose, onDone, onNotice }: { candidate: SourcingCandidate; onClose: () => void; onDone: () => Promise<void>; onNotice: (value: string) => void }) {
  const [saving, setSaving] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setSaving(true);
    try {
      await apiFetch(`/api/v1/sourcing/${candidate.id}/import`, { method: "POST", body: JSON.stringify({ external_sku_id: form.get("external_sku_id") || null, internal_code: form.get("internal_code"), sku_code: form.get("sku_code"), minimum_profit: form.get("minimum_profit"), target_profit: form.get("target_profit"), negotiation_margin: form.get("negotiation_margin"), platform_fee: form.get("platform_fee"), after_sales_reserve: form.get("after_sales_reserve") }) });
      onNotice("候选已进入商品中心，并完成定价与 ProductScore 计算");
      await onDone();
    } catch (cause) {
      onNotice(cause instanceof Error ? cause.message : "导入失败");
    } finally {
      setSaving(false);
    }
  }
  const code = candidate.external_product_id.replace(/[^a-zA-Z0-9]/g, "").slice(-18) || String(candidate.id);
  return <div className="dialog-backdrop"><form aria-labelledby="import-sourcing-title" aria-modal="true" className="dialog-card" onSubmit={submit} role="dialog"><div className="dialog-header"><h3 id="import-sourcing-title">导入商品中心</h3><button aria-label="关闭候选商品导入" className="icon-button" onClick={onClose} type="button"><X size={19} /></button></div><div className="dialog-body"><div className="form-grid"><label className="field"><span>内部商品编码</span><input defaultValue={`AF-${code}`} name="internal_code" required /></label><label className="field"><span>内部 SKU 编码</span><input defaultValue={`AF-${code}-01`} name="sku_code" required /></label><label className="field full"><span>供应 SKU</span><select name="external_sku_id">{candidate.normalized_data.skus?.map((sku) => <option key={sku.external_sku_id} value={sku.external_sku_id}>{sku.external_sku_id} · ¥{moneyInputValue(sku.price)} · 库存 {sku.stock ?? 0}</option>)}</select></label><label className="field"><span>最低利润</span><input defaultValue="20" min="0.01" name="minimum_profit" step="0.01" type="number" /></label><label className="field"><span>目标利润</span><input defaultValue="30" min="0.01" name="target_profit" step="0.01" type="number" /></label><label className="field"><span>议价余量</span><input defaultValue="5" min="0" name="negotiation_margin" step="0.01" type="number" /></label><label className="field"><span>平台费用</span><input defaultValue="0" min="0" name="platform_fee" step="0.01" type="number" /></label><label className="field"><span>售后准备金</span><input defaultValue="3" min="0" name="after_sales_reserve" step="0.01" type="number" /></label></div></div><div className="dialog-actions"><button className="ghost-button" onClick={onClose} type="button">取消</button><button className="primary-button" disabled={saving} type="submit"><Download size={15} />{saving ? "导入中…" : "确认导入"}</button></div></form></div>;
}
