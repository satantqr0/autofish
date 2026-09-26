"use client";

import { BrainCircuit, Gauge, KeyRound, LockKeyhole, PlugZap, Save, Settings, ShieldCheck } from "lucide-react";
import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";

import { apiFetch } from "@/lib/api";
import { fetchIntegrationStatus } from "@/lib/integrations";
import type { AutomationControl, BrowserBridgeOverview, IntegrationHealth, IntegrationStatus } from "@/lib/types";

const labels: Record<string, string> = { GLOBAL: "全局自动化", PUBLISH: "商品发布", CUSTOMER_SERVICE: "消息与客服", PURCHASE: "自动采购", REPRICING: "价格与库存", LOGISTICS: "订单与物流" };

export default function SettingsPage() {
  const [controls, setControls] = useState<AutomationControl[]>([]);
  const [integrations, setIntegrations] = useState<IntegrationStatus | null>(null);
  const [browserBridge, setBrowserBridge] = useState<BrowserBridgeOverview | null>(null);
  const [notice, setNotice] = useState("");
  const [changingPassword, setChangingPassword] = useState(false);

  const load = useCallback(async () => {
    try {
      const [nextControls, nextIntegrations, nextBrowserBridge] = await Promise.all([
        apiFetch<AutomationControl[]>("/api/v1/automation/controls"),
        fetchIntegrationStatus(),
        apiFetch<BrowserBridgeOverview>("/api/v1/xianyu/browser-bridge"),
      ]);
      setControls(nextControls);
      setIntegrations(nextIntegrations);
      setBrowserBridge(nextBrowserBridge);
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "设置加载失败");
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  function updateLocal(scope: string, values: Partial<AutomationControl>) {
    setControls((current) => current.map((item) => item.scope === scope ? { ...item, ...values } : item));
  }

  async function savePolicy(control: AutomationControl, enabled = control.enabled) {
    try {
      await apiFetch(`/api/v1/automation/controls/${control.scope}`, { method: "PATCH", body: JSON.stringify({ enabled, reason: enabled ? "操作员确认启用受控自动化" : "操作员从控制台暂停", mode: control.mode, daily_limit: control.daily_limit, min_interval_seconds: control.min_interval_seconds, failure_threshold: control.failure_threshold }) });
      setNotice(`${labels[control.scope] ?? control.scope}策略已保存`);
      await load();
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "更新失败");
    }
  }

  async function toggle(control: AutomationControl) {
    if (!control.enabled && !window.confirm(`启用“${labels[control.scope]}”？动作仍需同时通过 Adapter 总开关、限额、价格底线与熔断检查。`)) return;
    await savePolicy(control, !control.enabled);
  }

  async function changeMode(control: AutomationControl, mode: AutomationControl["mode"]) {
    if (mode === "AUTOMATIC" && !window.confirm(`把“${labels[control.scope]}”切换为自动执行？建议先完成 REVIEW 模式下的真实小流量验证。`)) return;
    const updated = { ...control, mode };
    updateLocal(control.scope, { mode });
    await savePolicy(updated);
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const formElement = event.currentTarget;
    const formData = new FormData(formElement);
    const current = String(formData.get("current_password") ?? "");
    const next = String(formData.get("new_password") ?? "");
    const confirmation = String(formData.get("confirmation") ?? "");
    if (next !== confirmation) {
      setNotice("两次输入的新密码不一致");
      return;
    }
    setChangingPassword(true);
    try {
      const response = await fetch("/api/session/password", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ current_password: current, new_password: next }) });
      const body = await response.json().catch(() => ({ detail: "改密失败" })) as { detail?: string };
      if (!response.ok) throw new Error(body.detail ?? "改密失败");
      formElement.reset();
      setNotice("密码已更新，其他旧会话已失效");
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "改密失败");
    } finally {
      setChangingPassword(false);
    }
  }

  return (
    <>
      <div className="page-heading"><div><div className="eyebrow">CONTROL PLANE</div><h2>系统设置</h2><p>连接状态、自动化权限和管理员凭据在同一控制面管理，所有变更进入审计日志。</p></div></div>
      {notice ? <button className="toast" onClick={() => setNotice("")}>{notice}</button> : null}

      <section className="settings-grid settings-product-grid">
        <article className="card settings-card model-settings-entry"><BrainCircuit size={25} /><div><h3>大模型设置</h3><p>配置 OpenAI、DeepSeek、阿里云百炼的 API Key、模型、任务路由和月度预算。</p></div><Link className="primary-button" href="/model-settings">进入模型控制面</Link></article>
        <article className="card settings-card automation-policy-card"><h3><Settings size={17} />自动化控制</h3><p>REVIEW 只生成可执行预览；AUTOMATIC 才会在限额内自动写入。失败达到阈值后自动熔断 15 分钟。</p><div className="settings-section-body">{controls.map((control) => <div className="policy-row" key={control.scope}><div className="policy-title"><div><b>{labels[control.scope] ?? control.scope}</b><small>{control.cooldown_until ? `熔断至 ${new Date(control.cooldown_until).toLocaleString("zh-CN", { hour12: false })}` : control.enabled ? `${control.mode === "AUTOMATIC" ? "自动执行" : "人工确认"} · 连续失败 ${control.consecutive_failures}` : control.stopped_reason ?? "已暂停"}</small></div><button aria-checked={control.enabled} aria-label={`切换${labels[control.scope]}`} className={`switch ${control.enabled ? "on" : ""}`} onClick={() => void toggle(control)} role="switch" type="button" /></div><div className="policy-editor"><label><span>模式</span><select disabled={control.scope === "GLOBAL"} onChange={(event) => void changeMode(control, event.target.value as AutomationControl["mode"])} value={control.mode}><option value="REVIEW">REVIEW</option><option value="AUTOMATIC">AUTOMATIC</option></select></label><label><span>每日上限</span><input min="1" onChange={(event) => updateLocal(control.scope, { daily_limit: Number(event.target.value) })} type="number" value={control.daily_limit} /></label><label><span>最小间隔(秒)</span><input min="1" onChange={(event) => updateLocal(control.scope, { min_interval_seconds: Number(event.target.value) })} type="number" value={control.min_interval_seconds} /></label><label><span>熔断阈值</span><input min="1" onChange={(event) => updateLocal(control.scope, { failure_threshold: Number(event.target.value) })} type="number" value={control.failure_threshold} /></label><button aria-label={`保存${labels[control.scope]}策略`} className="table-action policy-save" onClick={() => void savePolicy(control)} type="button"><Save size={14} />保存</button></div></div>)}</div></article>

        <article className="card settings-card">
          <h3><PlugZap size={17} />平台连接</h3>
          <p>授权健康只代表通道可用；真实写入还必须通过对应通道、总开关和上方业务策略。</p>
          <div className="settings-section-body integration-list">
            <IntegrationRow health={integrations?.supplier} label="1688 / 供应商" />
            <IntegrationRow health={integrations?.xianyu} label="闲鱼 API / Adapter" />
            <div className="control-row">
              <div><b>API / Adapter 写入总开关</b><small>控制 API 发布、回复、下架与发货；支付和退款始终不自动执行</small></div>
              <span className={`tag ${integrations?.write_enabled ? "green" : "gray"}`}>{integrations?.write_enabled ? "受控开启" : "已关闭"}</span>
            </div>
            <div className="control-row">
              <div><b>闲鱼浏览器通道</b><small>{browserBridge?.agents.some((agent) => agent.status === "ONLINE") ? "浏览器代理在线；登录态只保留在用户浏览器" : browserBridge?.configured ? "已配置，等待浏览器代理在线" : "尚未配置浏览器桥接"}</small></div>
              <div className="table-actions">
                <span className={`tag ${browserBridge?.safety.publish_submission_enabled ? "blue" : "gray"}`}>人工发布：{browserBridge?.safety.publish_submission_enabled ? "已放行" : "未放行"}</span>
                <span className={`tag ${browserBridge?.safety.automatic_publish_ready ? "green" : "gray"}`}>自动发布：{browserBridge?.safety.automatic_publish_ready ? "已就绪" : "未开启"}</span>
                <span className={`tag ${browserBridge?.safety.customer_service_submission_enabled ? "blue" : "gray"}`}>客服发送：{browserBridge?.safety.customer_service_submission_enabled ? "已放行" : "未放行"}</span>
              </div>
            </div>
            <div className="control-row"><div><b>数据模式</b><small>服务端当前运行状态</small></div><span className="tag blue">{integrations?.data_mode ?? "读取中"}</span></div>
          </div>
        </article>

        <article className="card settings-card"><h3><Gauge size={17} />上线门禁</h3><p>建议按顺序完成：通道健康 → REVIEW 模式单笔验证 → 核对平台结果 → 再开启 AUTOMATIC 与调度器。</p><div className="settings-section-body"><div className="control-row"><div><b>闲鱼 API / Adapter</b><small>{integrations?.xianyu.message ?? integrations?.xianyu.source ?? "未配置"}</small></div><span className={`tag ${integrations?.xianyu.write_enabled ? "green" : "orange"}`}>{integrations?.xianyu.write_enabled ? "可受控写入" : "仅只读或未授权"}</span></div><div className="control-row"><div><b>浏览器自动发布门禁</b><small>{browserBridge?.safety.automatic_publish_blockers.join("；") || "自动发布所需门禁已通过"}</small></div><Link className="table-action" href="/xianyu-workbench">查看工作台</Link></div><div className="control-row"><div><b>异常策略</b><small>验证码、授权失效、限流、响应不确定</small></div><span className="tag orange">停手并转人工</span></div></div></article>

        <article className="card settings-card password-card"><h3><KeyRound size={17} />管理员密码</h3><p>修改后会递增会话版本，其他设备上的旧 JWT 立即失效。</p><form className="password-form" onSubmit={changePassword}><label className="field"><span>当前密码</span><input autoComplete="current-password" minLength={8} name="current_password" required type="password" /></label><label className="field"><span>新密码</span><input autoComplete="new-password" minLength={12} name="new_password" required type="password" /></label><label className="field"><span>确认新密码</span><input autoComplete="new-password" minLength={12} name="confirmation" required type="password" /></label><small>至少 12 位，并包含大小写字母、数字、符号中的三类。</small><button className="primary-button" disabled={changingPassword} type="submit"><LockKeyhole size={15} />{changingPassword ? "正在更新…" : "更新密码并撤销旧会话"}</button></form></article>

        <article className="card settings-card compliance-card"><ShieldCheck size={24} color="var(--success)" /><h3>合规与数据保护</h3><p>外部快照在落库前自动遮蔽 Cookie、Token、手机号、地址和密码字段；验证码、人脸、二次确认和平台风险提示全部转人工。</p><div className="status-line compliance-ok"><span className="status-dot" />保护策略已启用</div></article>
      </section>
    </>
  );
}

function IntegrationRow({ label, health }: { label: string; health?: IntegrationHealth }) {
  const ready = Boolean(health?.authenticated);
  const writable = Boolean(health?.write_enabled);
  return <div className="control-row"><div><b>{label}</b><small>{health?.message ?? `${health?.configured_provider ?? "disabled"} · ${health?.source ?? "未配置"}`}</small></div><span className={`tag ${writable || ready ? "green" : "orange"}`}>{writable ? "受控写入" : ready ? "只读可用" : "待授权"}</span></div>;
}
