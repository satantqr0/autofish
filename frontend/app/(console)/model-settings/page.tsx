"use client";

import { BrainCircuit, KeyRound, LoaderCircle, ShieldCheck, WalletCards } from "lucide-react";
import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

import ProviderCard from "@/components/ai-settings/provider-card";
import RuntimePanel from "@/components/ai-settings/runtime-panel";
import { apiFetch } from "@/lib/api";
import type { AISettingsResponse } from "@/lib/types";

export default function ModelSettingsPage() {
  const [settings, setSettings] = useState<AISettingsResponse | null>(null);
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      setSettings(await apiFetch<AISettingsResponse>("/api/v1/ai-settings"));
    } catch (cause) {
      setNotice(cause instanceof Error ? cause.message : "大模型设置加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const stats = useMemo(() => {
    const configured = settings?.providers.filter((provider) => provider.api_key_configured).length ?? 0;
    const tested = settings?.providers.filter((provider) => provider.last_test_status === "SUCCESS").length ?? 0;
    return { configured, tested };
  }, [settings]);

  if (loading || !settings) {
    return <div className="card empty-state"><LoaderCircle className="spin" size={26} /><b>{notice || "正在读取加密模型配置"}</b></div>;
  }

  const primary = settings.providers.find((provider) => provider.provider === settings.runtime.primary_provider);
  const preferred = settings.providers.find((provider) => provider.provider === "qwen") ?? primary ?? settings.providers[0];
  const advancedProviders = settings.providers.filter((provider) => provider.provider !== preferred?.provider);

  return (
    <>
      <div className="page-heading model-settings-heading">
        <div><h2>大模型设置</h2><p>日常只需确认推荐服务、运行开关和连接状态；路由与备用服务放在高级选项。</p></div>
        <Link className="secondary-button" href="/operations">查看模型评估与价格</Link>
      </div>

      {notice ? <button className="toast" onClick={() => setNotice("")} type="button">{notice}</button> : null}

      <section className="model-settings-summary" aria-label="模型配置状态">
        <Summary icon={BrainCircuit} label="调用状态" value={settings.runtime.enabled ? "已启用" : "保护关闭"} detail="独立于平台自动化" />
        <Summary icon={KeyRound} label="已保存密钥" value={`${stats.configured} / ${settings.providers.length}`} detail="只回显脱敏提示" />
        <Summary icon={ShieldCheck} label="连接测试通过" value={`${stats.tested} 个`} detail={stats.tested ? "可进入任务路由" : "启用前必须测试"} />
        <Summary icon={WalletCards} label="预算提醒值" value={`¥${Number(settings.runtime.monthly_budget_cny).toFixed(0)}`} detail={`主路由：${primary?.display_name ?? "未选择"}`} />
      </section>

      <div className="model-settings-layout">
        <RuntimePanel onChange={load} onNotice={setNotice} providers={settings.providers} runtime={settings.runtime} />
        <aside className="card model-security-card">
          <ShieldCheck size={25} />
          <h3>密钥与启用门禁</h3>
          <p>API Key 使用服务端加密后写入数据库；浏览器和读取接口不会返回密钥明文。</p>
          <ul>
            <li>更换模型或地址后自动取消“已测试”状态</li>
            <li>主服务商未通过连接测试时不能启用</li>
            <li>测试只读取模型列表，不执行付费生成</li>
            <li>所有保存、清除、测试和启停都有审计记录</li>
          </ul>
        </aside>
      </div>

      <section className="provider-section">
        <div><h3>推荐服务</h3><p>Qwen 同时覆盖中文文案、视觉质检和参考图编辑，适合作为 AutoFish 的单一日常入口。</p></div>
        <div className="provider-card-grid">
          {preferred ? <ProviderCard key={preferred.provider} onChange={load} onNotice={setNotice} provider={preferred} /> : null}
        </div>
        {advancedProviders.length ? (
          <details className="advanced-provider-settings">
            <summary>其他服务商与故障回退</summary>
            <p>仅在需要切换文字模型或配置备用线路时展开。API 地址只允许对应服务商官方 HTTPS 域名。</p>
            <div className="provider-card-grid">
              {advancedProviders.map((provider) => <ProviderCard key={provider.provider} onChange={load} onNotice={setNotice} provider={provider} />)}
            </div>
          </details>
        ) : null}
      </section>
    </>
  );
}

function Summary({ detail, icon: Icon, label, value }: { detail: string; icon: typeof BrainCircuit; label: string; value: string }) {
  return <article className="card model-setting-stat"><div><Icon size={17} /><span>{label}</span></div><strong>{value}</strong><small>{detail}</small></article>;
}
