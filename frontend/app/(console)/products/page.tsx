"use client";

import {
  Box,
  ChevronLeft,
  ChevronRight,
  CirclePlus,
  FileJson,
  PackagePlus,
  Search,
  Send,
  ShieldCheck,
  Upload,
  X,
} from "lucide-react";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { ProductItem, ProductResponse } from "@/lib/types";

const lifecycleLabels: Record<string, string> = {
  CANDIDATE: "候选期",
  TESTING: "测试期",
  ACTIVE: "成长期",
  WINNER: "Winner",
  DECLINING: "衰退期",
  PAUSED: "已暂停",
  REMOVED: "已移除",
};

const lifecycleColors: Record<string, string> = {
  CANDIDATE: "gray",
  TESTING: "orange",
  ACTIVE: "blue",
  WINNER: "green",
  DECLINING: "orange",
  PAUSED: "gray",
  REMOVED: "gray",
};

const xianyuStatusLabels: Record<string, string> = {
  ACTIVE: "在售",
  SOLD: "已售出",
  PAUSED: "已暂停",
  REMOVED: "已下架",
  UNPUBLISHED: "未发布",
};

const scoreLabels: Record<string, string> = {
  profit_space: "利润空间",
  after_sales_safety: "退货风险",
  fault_safety: "故障风险",
  compatibility_safety: "兼容风险",
  transport_safety: "运输风险",
  stock_stability: "库存稳定",
  price_stability: "价格稳定",
  verticality: "账号垂直",
};

const importExample = JSON.stringify(
  {
    items: [
      {
        supplier_code: "MANUAL-001",
        supplier_name: "人工导入供应商",
        external_product_id: "P-20260815-001",
        external_sku_id: "S-20260815-001",
        internal_code: "AF-P011",
        sku_code: "AF-S011",
        title: "Mini PC 被动散热支架",
        category: "NAS / Mini PC 被动配件",
        supplier_price: 39,
        shipping_cost: 6,
        platform_fee: 2,
        after_sales_reserve: 3,
        minimum_profit: 20,
        target_profit: 26,
        negotiation_margin: 5,
        stock: 100,
      },
    ],
  },
  null,
  2,
);

function ProductDialog({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    setError("");
    const values: Record<string, unknown> = Object.fromEntries(new FormData(event.currentTarget));
    const numberFields = ["supplier_price", "shipping_cost", "platform_fee", "after_sales_reserve", "minimum_profit", "target_profit", "negotiation_margin", "stock"];
    for (const key of numberFields) values[key] = Number(values[key]);
    try {
      await apiFetch("/api/v1/products", { method: "POST", body: JSON.stringify(values) });
      onCreated();
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "创建失败");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form aria-labelledby="new-product-title" aria-modal="true" className="dialog-card" onSubmit={submit} role="dialog">
        <div className="dialog-header"><div><div className="eyebrow">MANUAL CATALOG</div><h3 id="new-product-title">新建测试商品</h3></div><button aria-label="关闭新建商品" className="icon-button" type="button" onClick={onClose}><X size={19} /></button></div>
        <div className="dialog-body">
          <div className="phase-banner"><ShieldCheck size={18} />该入口只写入内部商品库，不会连接或发布到真实平台。</div>
          <div className="form-grid">
            <div className="field"><label>商品名称</label><input name="title" defaultValue="Mini PC 被动配件" required /></div>
            <div className="field"><label>类目</label><input name="category" defaultValue="NAS / Mini PC 被动配件" required /></div>
            <div className="field"><label>内部商品编码</label><input name="internal_code" placeholder="AF-P011" required /></div>
            <div className="field"><label>内部 SKU</label><input name="sku_code" placeholder="AF-S011" required /></div>
            <div className="field"><label>供应商编码</label><input name="supplier_code" defaultValue="MANUAL-001" required /></div>
            <div className="field"><label>供应商名称</label><input name="supplier_name" defaultValue="人工导入供应商" required /></div>
            <div className="field"><label>供应商商品 ID</label><input name="external_product_id" placeholder="P-001" required /></div>
            <div className="field"><label>供应商 SKU ID</label><input name="external_sku_id" placeholder="S-001" required /></div>
            <div className="field"><label>供货价（元）</label><input name="supplier_price" type="number" min="0" step="0.01" defaultValue="42" required /></div>
            <div className="field"><label>运费（元）</label><input name="shipping_cost" type="number" min="0" step="0.01" defaultValue="6" required /></div>
            <div className="field"><label>平台费预估（元）</label><input name="platform_fee" type="number" min="0" step="0.01" defaultValue="2" required /></div>
            <div className="field"><label>售后准备金（元）</label><input name="after_sales_reserve" type="number" min="0" step="0.01" defaultValue="4" required /></div>
            <div className="field"><label>最低利润（元）</label><input name="minimum_profit" type="number" min="0.01" step="0.01" defaultValue="20" required /></div>
            <div className="field"><label>目标利润（元）</label><input name="target_profit" type="number" min="0.01" step="0.01" defaultValue="28" required /></div>
            <div className="field"><label>议价余量（元）</label><input name="negotiation_margin" type="number" min="0" step="0.01" defaultValue="5" required /></div>
            <div className="field"><label>库存</label><input name="stock" type="number" min="0" step="1" defaultValue="100" required /></div>
            <div className="field full"><label>生命周期</label><select name="lifecycle" defaultValue="CANDIDATE"><option value="CANDIDATE">候选期</option><option value="TESTING">测试期</option><option value="ACTIVE">成长期</option></select></div>
          </div>
          {error && <div className="form-error" style={{ marginTop: 14 }}>{error}</div>}
        </div>
        <div className="dialog-actions"><button className="secondary-button" type="button" onClick={onClose}>取消</button><button className="primary-button" disabled={saving}><PackagePlus size={16} />{saving ? "正在创建…" : "创建内部商品"}</button></div>
      </form>
    </div>
  );
}

function ImportDialog({ onClose, onImported }: { onClose: () => void; onImported: (message: string) => void }) {
  const [value, setValue] = useState(importExample);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  async function submit() {
    setSaving(true);
    setError("");
    try {
      const payload = JSON.parse(value);
      const result = await apiFetch<{ created: unknown[]; skipped: unknown[] }>("/api/v1/products/import", { method: "POST", body: JSON.stringify(payload) });
      onImported(`已导入 ${result.created.length} 个，跳过 ${result.skipped.length} 个`);
      onClose();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "JSON 格式不正确");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div aria-labelledby="import-products-title" aria-modal="true" className="dialog-card" role="dialog">
        <div className="dialog-header"><div><div className="eyebrow">JSON IMPORT</div><h3 id="import-products-title">批量导入测试商品</h3></div><button aria-label="关闭批量导入" className="icon-button" onClick={onClose} type="button"><X size={19} /></button></div>
        <div className="dialog-body"><div className="phase-banner"><FileJson size={18} />最多一次导入 100 个商品；重复编码会安全跳过。</div><textarea aria-label="商品 JSON" rows={21} style={{ width: "100%", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 }} value={value} onChange={(event) => setValue(event.target.value)} />{error && <div className="form-error" style={{ marginTop: 12 }}>{error}</div>}</div>
        <div className="dialog-actions"><button className="secondary-button" onClick={onClose}>取消</button><button className="primary-button" onClick={submit} disabled={saving}><Upload size={16} />{saving ? "正在导入…" : "校验并导入"}</button></div>
      </div>
    </div>
  );
}

function ProductInspector({ product, onNotice }: { product: ProductItem | null; onNotice: (message: string) => void }) {
  const [queueing, setQueueing] = useState(false);
  const [factsOpen, setFactsOpen] = useState(false);
  if (!product) return <aside className="card product-inspector"><div className="empty-state"><Box size={27} /><span>选择一个商品查看详情</span></div></aside>;
  const currentProduct = product;
  const factsSummary = JSON.stringify({
    product_id: currentProduct.id,
    title: currentProduct.title,
    category: currentProduct.category,
    sku: currentProduct.sku.sku_code,
    supplier: currentProduct.supplier.code,
    final_cost: currentProduct.sku.final_cost,
    minimum_sale_price: currentProduct.sku.minimum_sale_price,
    recommended_price: currentProduct.sku.recommended_price,
    stock: currentProduct.sku.stock,
    score: currentProduct.sku.score,
    lifecycle: currentProduct.lifecycle,
    xianyu_status: currentProduct.xianyu_status,
  }, null, 2);

  async function queuePublication() {
    setQueueing(true);
    try {
      const result = await apiFetch<{ status: string; idempotent: boolean }>(`/api/v1/products/${currentProduct.id}/queue-publication`, { method: "POST", body: JSON.stringify({ note: "运营控制台人工提交复核" }) });
      onNotice(result.idempotent ? "该商品已在人工复核队列" : "已加入发布前人工复核队列");
    } catch (cause) {
      onNotice(cause instanceof Error ? cause.message : "加入队列失败");
    } finally {
      setQueueing(false);
    }
  }

  return (
    <>
      <aside className="card product-inspector">
        <div className="inspector-hero"><span className="product-thumb"><Box size={29} /></span><div><h3>{product.title}</h3><p>SKU: {product.sku.sku_code}</p><span className={`tag ${lifecycleColors[product.lifecycle]}`}>{lifecycleLabels[product.lifecycle]}</span></div></div>
        <section className="inspector-section"><h4>供应商映射</h4><div className="detail-list"><div className="detail-row"><span>供应商</span><strong>{product.supplier.code}</strong></div><div className="detail-row"><span>外部商品 ID</span><strong>{product.supplier.masked_external_id}</strong></div><div className="detail-row"><span>类目路径</span><strong>{product.category}</strong></div></div></section>
        <section className="inspector-section"><h4>成本明细</h4><div className="detail-list"><div className="detail-row"><span>供应商价</span><strong>¥{product.sku.supplier_cost}</strong></div><div className="detail-row"><span>运费</span><strong>¥{product.sku.supplier_shipping}</strong></div><div className="detail-row"><span>平台费用</span><strong>¥{product.sku.platform_fee}</strong></div><div className="detail-row"><span>售后准备金</span><strong>¥{product.sku.after_sales_reserve}</strong></div><div className="detail-row total"><span>合计成本</span><strong>¥{product.sku.final_cost}</strong></div></div></section>
        <section className="inspector-section"><div className="price-triplet"><div><span>最低售价</span><strong>¥{product.sku.minimum_sale_price}</strong></div><div><span>目标售价</span><strong>¥{product.sku.target_sale_price}</strong></div><div><span>建议挂牌</span><strong>¥{product.sku.recommended_price}</strong></div></div></section>
        <section className="inspector-section"><h4>综合评分</h4><div className="score-layout"><div><div className="score-big">{Number(product.sku.score).toFixed(0)}<small>/100</small></div><small className="muted">可复算</small></div><div className="score-bars">{Object.entries(product.score_breakdown).map(([key, value]) => <div className="score-bar-row" key={key}><span>{scoreLabels[key] ?? key}</span><i className="score-track"><i className="score-fill" style={{ width: `${Math.min(100, Number(value))}%` }} /></i><b>{Number(value).toFixed(0)}</b></div>)}</div></div></section>
        <section className="inspector-section"><h4>价格 / 库存检查</h4><div className="detail-list"><div className="detail-row"><span>库存状态</span><strong className={product.sku.stock < 50 ? "stock-low" : "positive"}>{product.sku.stock < 50 ? "低库存" : "正常"}</strong></div><div className="detail-row"><span>闲鱼状态</span><strong>{xianyuStatusLabels[product.xianyu_status] ?? product.xianyu_status}</strong></div><div className="detail-row"><span>最近检查</span><strong>{product.sku.last_checked_at ? new Date(product.sku.last_checked_at).toLocaleString("zh-CN", { hour12: false }) : "—"}</strong></div></div></section>
        <div className="inspector-actions"><button className="primary-button" disabled={queueing} onClick={queuePublication}><Send size={16} />{queueing ? "正在提交…" : "加入发布前人工复核"}</button><button className="secondary-button" onClick={() => setFactsOpen(true)}><FileJson size={16} />查看事实摘要</button></div>
      </aside>
      {factsOpen ? <div className="dialog-backdrop" role="presentation"><section aria-labelledby="product-facts-title" aria-modal="true" className="dialog-card" role="dialog"><div className="dialog-header"><div><div className="eyebrow">AUDITABLE PRODUCT FACTS</div><h3 id="product-facts-title">商品事实摘要</h3></div><button aria-label="关闭商品事实摘要" className="icon-button" onClick={() => setFactsOpen(false)} type="button"><X size={19} /></button></div><div className="dialog-body"><textarea aria-label="商品事实 JSON" readOnly rows={18} style={{ width: "100%", fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace", fontSize: 12 }} value={factsSummary} /></div><div className="dialog-actions"><button className="primary-button" onClick={() => setFactsOpen(false)} type="button">完成复核</button></div></section></div> : null}
    </>
  );
}

export default function ProductsPage() {
  const [data, setData] = useState<ProductResponse>({ items: [], total: 0, page: 1, page_size: 20 });
  const [page, setPage] = useState(1);
  const [selected, setSelected] = useState<ProductItem | null>(null);
  const [suppliers, setSuppliers] = useState<Array<{ code: string; name: string }>>([]);
  const [filters, setFilters] = useState({ q: "", lifecycle: "", supplier: "", stock_status: "", sort: "score_desc" });
  const [loading, setLoading] = useState(true);
  const [dialog, setDialog] = useState<"create" | "import" | null>(null);
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    const params = new URLSearchParams(Object.entries(filters).filter(([, value]) => value));
    params.set("page", String(page));
    params.set("page_size", "20");
    try {
      const response = await apiFetch<ProductResponse>(`/api/v1/products?${params}`);
      const responseTotalPages = Math.max(1, Math.ceil(response.total / response.page_size));
      if (page > responseTotalPages) {
        setPage((currentPage) => currentPage === page ? responseTotalPages : currentPage);
        return;
      }
      setData(response);
      setSelected((current) => response.items.find((item) => item.id === current?.id) ?? response.items[0] ?? null);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "商品加载失败");
    } finally {
      setLoading(false);
    }
  }, [filters, page]);

  useEffect(() => { const timer = window.setTimeout(load, 220); return () => window.clearTimeout(timer); }, [load]);
  useEffect(() => { apiFetch<Array<{ code: string; name: string }>>("/api/v1/products/meta/suppliers").then(setSuppliers).catch(() => undefined); }, []);

  function updateFilter(key: keyof typeof filters, value: string) {
    setPage(1);
    setFilters((current) => ({ ...current, [key]: value }));
  }

  const totalPages = Math.max(1, Math.ceil(data.total / data.page_size));

  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">CATALOG · TRACEABLE FACTS</div><h2>商品中心</h2><p>供应商事实、成本、价格和评分在同一条可追溯链路中。</p></div><div className="heading-actions"><button className="secondary-button" onClick={() => setDialog("import")}><Upload size={16} />导入商品</button><button className="primary-button" onClick={() => setDialog("create")}><CirclePlus size={16} />新建商品</button></div></div>
      {notice && <button className="toast" onClick={() => setNotice("")}>{notice}</button>}
      <section className="toolbar">
        <div className="filters">
          <div className="search-field"><Search size={16} /><input aria-label="搜索商品" placeholder="搜索商品、SKU 或供应商" value={filters.q} onChange={(event) => updateFilter("q", event.target.value)} /></div>
          <div className="filter-field"><label>生命周期</label><select value={filters.lifecycle} onChange={(event) => updateFilter("lifecycle", event.target.value)}><option value="">全部</option>{Object.entries(lifecycleLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></div>
          <div className="filter-field"><label>供应商</label><select value={filters.supplier} onChange={(event) => updateFilter("supplier", event.target.value)}><option value="">全部</option>{suppliers.map((item) => <option value={item.code} key={item.code}>{item.name}</option>)}</select></div>
          <div className="filter-field"><label>库存状态</label><select value={filters.stock_status} onChange={(event) => updateFilter("stock_status", event.target.value)}><option value="">全部</option><option value="in_stock">有库存</option><option value="low_stock">低库存</option><option value="out_of_stock">缺货</option></select></div>
        </div>
        <div className="filter-field"><label>排序</label><select value={filters.sort} onChange={(event) => updateFilter("sort", event.target.value)}><option value="score_desc">综合评分</option><option value="stock_asc">库存从低到高</option><option value="updated_desc">最近检查</option></select></div>
      </section>

      <section className="product-workspace">
        <article className="card product-table-card">
          <div className="table-wrap">
            <table><thead><tr><th>商品</th><th>供应商</th><th>成本</th><th>建议售价</th><th>最低售价</th><th>库存</th><th>综合评分</th><th>生命周期</th><th>闲鱼状态</th></tr></thead>
              <tbody>{loading ? <tr><td colSpan={9}><div className="skeleton" /></td></tr> : data.items.map((item) => <tr aria-selected={selected?.id === item.id} className={`product-row ${selected?.id === item.id ? "selected" : ""}`} key={item.id} onClick={() => setSelected(item)} onKeyDown={(event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); setSelected(item); } }} tabIndex={0}><td><div className="product-cell"><span className="product-thumb"><Box size={20} /></span><span className="product-name"><b>{item.title}</b><small>SKU: {item.sku.sku_code}</small></span></div></td><td>{item.supplier.masked_external_id}</td><td>¥{item.sku.final_cost}</td><td>¥{item.sku.recommended_price}</td><td className="money-floor">¥{item.sku.minimum_sale_price}</td><td className={item.sku.stock < 50 ? "stock-low" : ""}>{item.sku.stock}</td><td><span className="score-value">{Number(item.sku.score).toFixed(0)}</span></td><td><span className={`tag ${lifecycleColors[item.lifecycle]}`}>{lifecycleLabels[item.lifecycle]}</span></td><td><span className={`tag ${item.xianyu_status === "ACTIVE" ? "green" : item.xianyu_status === "UNPUBLISHED" ? "gray" : "orange"}`}>{xianyuStatusLabels[item.xianyu_status] ?? item.xianyu_status}</span></td></tr>)}</tbody>
            </table>
          </div>
          {!loading && data.items.length === 0 && <div className="empty-state"><Box size={28} /><b>没有匹配商品</b><span>调整筛选条件或导入一批测试商品。</span></div>}
          <div className="table-footer"><span>共 {data.total} 个商品 · 第 {data.page}/{totalPages} 页</span><div className="pagination"><button aria-label="上一页" disabled={loading || data.page <= 1} onClick={() => setPage((current) => Math.max(1, current - 1))} type="button"><ChevronLeft size={14} /></button><button aria-label={`当前第 ${data.page} 页，共 ${totalPages} 页`} className="active" type="button">{data.page}</button><button aria-label="下一页" disabled={loading || data.page >= totalPages} onClick={() => setPage((current) => Math.min(totalPages, current + 1))} type="button"><ChevronRight size={14} /></button></div></div>
        </article>
        <ProductInspector product={selected} onNotice={setNotice} />
      </section>

      {dialog === "create" && <ProductDialog onClose={() => setDialog(null)} onCreated={() => { setNotice("内部商品创建成功"); void load(); }} />}
      {dialog === "import" && <ImportDialog onClose={() => setDialog(null)} onImported={(message) => { setNotice(message); void load(); }} />}
    </>
  );
}
