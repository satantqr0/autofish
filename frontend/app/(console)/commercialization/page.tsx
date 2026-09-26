"use client";

import {
  BadgeCheck,
  BriefcaseBusiness,
  CircleAlert,
  LoaderCircle,
  Save,
  ShieldCheck,
  TrendingUp,
} from "lucide-react";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { CommercializationOverview } from "@/lib/types";

const acceptanceLabels = [
  ["account_owned_by_customer", "闲鱼账号归客户本人所有并由客户本人登录"],
  ["data_stays_customer_controlled", "业务数据保存在客户自有部署环境"],
  ["no_credential_custody", "服务方不托管密码、Cookie、验证码或支付凭证"],
  ["no_revenue_guarantee_acknowledged", "不承诺曝光、订单、收入或利润结果"],
  ["prohibited_automation_acknowledged", "不使用群控、刷量、虚假交易或风控绕过"],
  ["regulatory_obligations_acknowledged", "经营者自行承担登记、税务、售后及平台责任"],
] as const;

function currentMonth() {
  const now = new Date();
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-01`;
}

type EvidenceDraft = {
  period_start: string;
  qualified_leads: string;
  product_demos: string;
  paid_new_customers: string;
  active_customers: string;
  retained_30d_customers: string;
  refunded_customers: string;
  revenue_cny: string;
  delivery_hours: string;
  support_hours: string;
  notes: string;
};

type EvidenceNumericField = Exclude<keyof EvidenceDraft, "period_start" | "notes">;

function evidenceDraftFor(
  periodStart: string,
  periods: CommercializationOverview["periods"],
): EvidenceDraft {
  const normalizedPeriod = `${periodStart.slice(0, 7)}-01`;
  const existing = periods.find((item) => item.period_start.slice(0, 10) === normalizedPeriod);
  return {
    period_start: normalizedPeriod,
    qualified_leads: String(existing?.qualified_leads ?? 0),
    product_demos: String(existing?.product_demos ?? 0),
    paid_new_customers: String(existing?.paid_new_customers ?? 0),
    active_customers: String(existing?.active_customers ?? 0),
    retained_30d_customers: String(existing?.retained_30d_customers ?? 0),
    refunded_customers: String(existing?.refunded_customers ?? 0),
    revenue_cny: String(existing?.revenue_cny ?? 0),
    delivery_hours: String(existing?.delivery_hours ?? 0),
    support_hours: String(existing?.support_hours ?? 0),
    notes: existing?.notes ?? "",
  };
}

export default function CommercializationPage() {
  const [data, setData] = useState<CommercializationOverview | null>(null);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [accepted, setAccepted] = useState<Record<string, boolean>>({});
  const [evidenceDraft, setEvidenceDraft] = useState<EvidenceDraft>(() => evidenceDraftFor(currentMonth(), []));
  const [evidenceDirty, setEvidenceDirty] = useState(false);
  const evidenceInitialized = useRef(false);

  async function load() {
    setLoading(true);
    try {
      const response = await apiFetch<CommercializationOverview>("/api/v1/commercialization/overview");
      setData(response);
      if (!evidenceInitialized.current) {
        setEvidenceDraft(evidenceDraftFor(currentMonth(), response.periods));
        evidenceInitialized.current = true;
      }
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "商业化状态加载失败");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void load(); }, []);

  const allAccepted = useMemo(
    () => acceptanceLabels.every(([key]) => accepted[key]),
    [accepted],
  );

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!data) return;
    setSaving(true);
    const form = new FormData(event.currentTarget);
    try {
      setData(await apiFetch<CommercializationOverview>("/api/v1/commercialization/profile", {
        method: "PATCH",
        body: JSON.stringify({
          installation_name: form.get("installation_name"),
          customer_name: form.get("customer_name") || null,
          customer_type: form.get("customer_type"),
          plan: form.get("plan"),
        }),
      }));
      setNotice("商业交付档案已保存");
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "档案保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function acceptBoundaries() {
    if (!allAccepted) return;
    setSaving(true);
    try {
      const body = Object.fromEntries(acceptanceLabels.map(([key]) => [key, true]));
      setData(await apiFetch<CommercializationOverview>("/api/v1/commercialization/acceptance", {
        method: "POST",
        body: JSON.stringify(body),
      }));
      setNotice("客户自有化与商业交付边界已留档");
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "验收失败");
    } finally {
      setSaving(false);
    }
  }

  async function saveEvidence(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSaving(true);
    const numeric = (name: EvidenceNumericField) => Number(evidenceDraft[name] || 0);
    try {
      const response = await apiFetch<CommercializationOverview>("/api/v1/commercialization/evidence", {
        method: "PUT",
        body: JSON.stringify({
          period_start: evidenceDraft.period_start,
          qualified_leads: numeric("qualified_leads"),
          product_demos: numeric("product_demos"),
          paid_new_customers: numeric("paid_new_customers"),
          active_customers: numeric("active_customers"),
          retained_30d_customers: numeric("retained_30d_customers"),
          refunded_customers: numeric("refunded_customers"),
          revenue_cny: numeric("revenue_cny"),
          delivery_hours: numeric("delivery_hours"),
          support_hours: numeric("support_hours"),
          notes: evidenceDraft.notes || null,
        }),
      });
      setData(response);
      setEvidenceDraft(evidenceDraftFor(evidenceDraft.period_start, response.periods));
      setEvidenceDirty(false);
      setNotice("本月商业验证数据已记录；同月重复提交会安全更新");
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "商业证据保存失败");
    } finally {
      setSaving(false);
    }
  }

  if (loading || !data) {
    return <div className="card empty-state"><LoaderCircle className="spin" size={26} /><b>{notice || "正在计算商业准备度"}</b></div>;
  }

  const summary = data.evidence_summary;
  const acceptanceCurrent = data.deployment.acceptance.terms_version === data.versions.terms
    && Boolean(data.deployment.acceptance.accepted_at);
  const evidencePeriods = data.periods;
  const editingExistingPeriod = evidencePeriods.some(
    (item) => item.period_start.slice(0, 10) === evidenceDraft.period_start,
  );

  function updateEvidenceField(field: keyof EvidenceDraft, value: string) {
    setEvidenceDraft((current) => ({ ...current, [field]: value }));
    setEvidenceDirty(true);
  }

  function selectEvidenceMonth(value: string) {
    setEvidenceDraft(evidenceDraftFor(`${value}-01`, evidencePeriods));
    setEvidenceDirty(false);
  }

  return (
    <>
      <div className="page-heading commercial-heading">
        <div>
          <div className="eyebrow">PRIVATE DELIVERY · SINGLE TENANT</div>
          <h2>商业化控制台</h2>
          <p>用付费、留存和交付成本证明产品成立；未达到证据门槛时只允许付费试点。</p>
        </div>
        <span className={`commercial-status ${data.release_ready ? "ready" : "pilot"}`}>
          {data.release_ready ? <BadgeCheck size={18} /> : <CircleAlert size={18} />}{data.status_label}
        </span>
      </div>

      {notice ? <div className="notice-banner">{notice}</div> : null}

      <section className="commercial-summary">
        <Summary label="商业准备度" value={`${data.readiness_score}%`} detail={data.release_ready ? "通过私有部署正式销售门槛" : "当前不得宣传成熟商业版"} />
        <Summary label="成长层级" value={`第 ${data.growth_level} 层`} detail={data.growth_task} />
        <Summary label="累计付费客户" value={`${summary.paid_customers}`} detail={`累计收入 ¥${Number(summary.revenue_cny).toFixed(2)}`} />
        <Summary label="最近 30 天留存" value={summary.latest_retention_rate == null ? "待验证" : `${(summary.latest_retention_rate * 100).toFixed(0)}%`} detail="正式销售门槛 ≥ 67%" />
      </section>

      <section className="commercial-layout">
        <article className="card commercial-readiness">
          <header><TrendingUp size={20} /><div><h3>商业证据门禁</h3><p>代码完成度不能替代付费和留存证据。</p></div></header>
          <div className="commercial-stage-list">
            {data.stages.map((stage) => (
              <div className={stage.ready ? "ready" : "blocked"} key={stage.key}>
                {stage.ready ? <BadgeCheck size={17} /> : <CircleAlert size={17} />}
                <span><b>{stage.label}</b><small>{stage.detail}</small></span>
              </div>
            ))}
          </div>
        </article>

        <article className="card commercial-boundary-card">
          <header><ShieldCheck size={20} /><div><h3>永久产品边界</h3><p>所有套餐和后续版本均不可绕过。</p></div></header>
          <ul>{data.boundaries.map((item) => <li key={item}>{item}</li>)}</ul>
        </article>
      </section>

      <section className="commercial-offers">
        {data.offers.map((offer) => (
          <article className={`card offer-card ${offer.code === "STANDARD" ? "recommended" : ""}`} key={offer.code}>
            <div className="offer-label">{offer.code === "STANDARD" ? "验证后主推" : offer.code}</div>
            <h3>{offer.name}</h3>
            <strong>¥{offer.price_cny.toLocaleString("zh-CN")}</strong>
            <small>{offer.billing}</small>
            <p>{offer.purpose}</p>
            <ul>{offer.includes.map((item) => <li key={item}>{item}</li>)}</ul>
            <div className="offer-excludes">不包含：{offer.not_included.join("、")}</div>
          </article>
        ))}
      </section>

      <section className="commercial-forms">
        <form className="card commercial-form" onSubmit={saveProfile}>
          <header><BriefcaseBusiness size={20} /><div><h3>交付档案</h3><p>只描述当前这一套客户自有部署。</p></div></header>
          <label className="field"><span>安装名称</span><input defaultValue={data.deployment.installation_name} name="installation_name" required /></label>
          <label className="field"><span>客户或经营主体</span><input defaultValue={data.deployment.customer_name ?? ""} name="customer_name" placeholder="内部验证可暂不填写" /></label>
          <label className="field"><span>主体类型</span><select defaultValue={data.deployment.customer_type} name="customer_type"><option value="INDIVIDUAL_OPERATOR">个人经营者</option><option value="SOLE_PROPRIETOR">个体工商户</option><option value="COMPANY">企业</option></select></label>
          <label className="field"><span>交付套餐</span><select defaultValue={data.deployment.plan} name="plan"><option value="PILOT">付费验证版</option><option value="STANDARD">私有部署标准版</option><option value="PRO">私有部署专业版</option></select></label>
          <button className="primary-button" disabled={saving} type="submit"><Save size={15} />保存交付档案</button>
        </form>

        <article className="card commercial-form acceptance-form">
          <header><ShieldCheck size={20} /><div><h3>客户自有化验收</h3><p>版本 {data.versions.terms}；更新条款后必须重新确认。</p></div></header>
          {acceptanceCurrent ? (
            <div className="acceptance-complete"><BadgeCheck size={24} /><b>当前版本已确认</b><small>{new Date(data.deployment.acceptance.accepted_at!).toLocaleString("zh-CN", { hour12: false })}</small></div>
          ) : (
            <>
              <div className="acceptance-checks">
                {acceptanceLabels.map(([key, label]) => <label key={key}><input checked={Boolean(accepted[key])} onChange={(event) => setAccepted((current) => ({ ...current, [key]: event.target.checked }))} type="checkbox" /><span>{label}</span></label>)}
              </div>
              <button className="primary-button" disabled={!allAccepted || saving} onClick={() => void acceptBoundaries()} type="button"><ShieldCheck size={15} />确认并写入审计</button>
            </>
          )}
        </article>

        <form className="card commercial-form evidence-form" onSubmit={saveEvidence}>
          <header><TrendingUp size={20} /><div><h3>月度商业证据</h3><p>只记录汇总指标，不保存潜在客户个人信息。</p></div></header>
          <div className="notice-banner">
            {editingExistingPeriod
              ? "已载入该月份的完整原始数据；只有修改后才能保存，避免未填写字段被清零。"
              : "这是新月份；填写至少一项指标或备注后才能保存。"}
          </div>
          <div className="evidence-grid">
            <label className="field"><span>月份</span><input max={currentMonth().slice(0, 7)} onChange={(event) => selectEvidenceMonth(event.target.value)} type="month" value={evidenceDraft.period_start.slice(0, 7)} required /></label>
            <NumberField label="有效线索" name="qualified_leads" onChange={updateEvidenceField} value={evidenceDraft.qualified_leads} />
            <NumberField label="产品演示" name="product_demos" onChange={updateEvidenceField} value={evidenceDraft.product_demos} />
            <NumberField label="新增付费客户" name="paid_new_customers" onChange={updateEvidenceField} value={evidenceDraft.paid_new_customers} />
            <NumberField label="活跃客户" name="active_customers" onChange={updateEvidenceField} value={evidenceDraft.active_customers} />
            <NumberField label="30 天留存客户" name="retained_30d_customers" onChange={updateEvidenceField} value={evidenceDraft.retained_30d_customers} />
            <NumberField label="退款客户" name="refunded_customers" onChange={updateEvidenceField} value={evidenceDraft.refunded_customers} />
            <NumberField label="实收金额（元）" name="revenue_cny" onChange={updateEvidenceField} step="0.01" value={evidenceDraft.revenue_cny} />
            <NumberField label="交付工时" name="delivery_hours" onChange={updateEvidenceField} step="0.01" value={evidenceDraft.delivery_hours} />
            <NumberField label="当月支持工时" name="support_hours" onChange={updateEvidenceField} step="0.01" value={evidenceDraft.support_hours} />
          </div>
          <label className="field"><span>备注</span><textarea onChange={(event) => updateEvidenceField("notes", event.target.value)} placeholder="仅记录渠道、套餐与改进事项，不填写姓名、手机号或账号凭证" value={evidenceDraft.notes} /></label>
          <button className="primary-button" disabled={saving || !evidenceDirty} type="submit"><Save size={15} />{editingExistingPeriod ? "更新该月完整数据" : "记录商业证据"}</button>
        </form>
      </section>
    </>
  );
}

function Summary({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <article className="card"><span>{label}</span><strong>{value}</strong><small>{detail}</small></article>;
}

function NumberField({
  label,
  name,
  onChange,
  step = "1",
  value,
}: {
  label: string;
  name: EvidenceNumericField;
  onChange: (field: keyof EvidenceDraft, value: string) => void;
  step?: string;
  value: string;
}) {
  return <label className="field"><span>{label}</span><input min="0" name={name} onChange={(event) => onChange(name, event.target.value)} step={step} type="number" value={value} required /></label>;
}
