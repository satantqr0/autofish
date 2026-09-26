"use client";

import { CircleStop, LoaderCircle, Route, Save, ShieldCheck, ToggleLeft } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";
import {
  runtimeDraft,
  sameRuntimeDraft,
  type AIRuntimeDraft,
} from "@/lib/runtime-settings";
import type { AIProviderSettings, AIRuntimeSettings } from "@/lib/types";

const taskLabels: Record<string, string> = {
  customer_service: "客服回复",
  product_copy: "商品文案",
  vision_quality: "图片事实质检",
  image_generation: "精品图片生成",
  batch_text: "批量文本任务",
  risk_summary: "风险事实摘要",
};

export default function RuntimePanel({
  onChange,
  onNotice,
  providers,
  runtime,
}: {
  onChange: () => Promise<void>;
  onNotice: (message: string) => void;
  providers: AIProviderSettings[];
  runtime: AIRuntimeSettings;
}) {
  const [draft, setDraft] = useState<AIRuntimeDraft>(() => runtimeDraft(runtime));
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const pendingSavedDraft = useRef<AIRuntimeDraft | null>(null);

  useEffect(() => {
    if (dirty) return;
    const nextDraft = runtimeDraft(runtime);
    if (pendingSavedDraft.current && !sameRuntimeDraft(pendingSavedDraft.current, nextDraft)) return;
    pendingSavedDraft.current = null;
    setDraft(nextDraft);
  }, [dirty, runtime]);

  function changeDraft(patch: Partial<AIRuntimeDraft>) {
    const nextDraft = { ...draft, ...patch };
    setDraft(nextDraft);
    setDirty(!sameRuntimeDraft(nextDraft, runtimeDraft(runtime)));
  }

  function changeTaskRoute(task: string, provider: AIRuntimeDraft["task_routing"][string]) {
    changeDraft({
      task_routing: {
        ...draft.task_routing,
        [task]: provider,
      },
    });
  }

  async function save() {
    setSaving(true);
    try {
      const savedRuntime = await apiFetch<AIRuntimeSettings>("/api/v1/ai-settings/runtime", {
        method: "PATCH",
        body: JSON.stringify(draft),
      });
      const savedDraft = runtimeDraft(savedRuntime);
      pendingSavedDraft.current = savedDraft;
      setDraft(savedDraft);
      setDirty(false);
      onNotice(savedRuntime.enabled ? "模型调用设置已保存并启用" : "模型调用设置已保存，当前保持关闭");
      await onChange();
    } catch (cause) {
      onNotice(cause instanceof Error ? cause.message : "模型运行设置保存失败");
    } finally {
      setSaving(false);
    }
  }

  const connected = new Set(
    providers.filter((provider) => provider.last_test_status === "SUCCESS").map((provider) => provider.provider),
  );

  return (
    <article className="card ai-runtime-card">
      <header>
        <div><ToggleLeft size={19} /><div><h3>模型调用总开关</h3><p>只控制 AI Agent 调用，不会开启闲鱼发布、回复、采购或物流写入。</p></div></div>
        <button
          aria-checked={draft.enabled}
          aria-label="切换模型调用总开关"
          className={`switch ${draft.enabled ? "on" : ""}`}
          onClick={() => changeDraft({ enabled: !draft.enabled })}
          role="switch"
          type="button"
        />
      </header>

      <div className="runtime-form-grid">
        <label className="field"><span>主服务商</span><select onChange={(event) => changeDraft({ primary_provider: event.target.value as AIRuntimeSettings["primary_provider"] })} value={draft.primary_provider}>{providers.map((provider) => <option key={provider.provider} value={provider.provider}>{provider.display_name}{connected.has(provider.provider) ? " · 已测试" : " · 未就绪"}</option>)}</select></label>
        <label className="field"><span>故障降级</span><select onChange={(event) => changeDraft({ fallback_provider: event.target.value ? event.target.value as AIRuntimeSettings["fallback_provider"] : null })} value={draft.fallback_provider ?? ""}><option value="">不自动降级</option>{providers.map((provider) => <option key={provider.provider} value={provider.provider}>{provider.display_name}</option>)}</select></label>
        <label className="field"><span>月度预算提醒（元）</span><input min="1" onChange={(event) => changeDraft({ monthly_budget_cny: event.target.value })} step="1" type="number" value={draft.monthly_budget_cny} /></label>
        <label className="field"><span>Temperature</span><input max="2" min="0" onChange={(event) => changeDraft({ temperature: event.target.value })} step="0.1" type="number" value={draft.temperature} /></label>
        <label className="field"><span>最大输出 token</span><input max="32768" min="64" onChange={(event) => changeDraft({ max_output_tokens: Number(event.target.value) })} type="number" value={draft.max_output_tokens} /></label>
        <label className="field"><span>请求超时（秒）</span><input max="180" min="5" onChange={(event) => changeDraft({ request_timeout_seconds: Number(event.target.value) })} type="number" value={draft.request_timeout_seconds} /></label>
      </div>

      <section className="task-routing-editor">
        <h4><Route size={16} />任务路由</h4>
        <div>
          {Object.entries(taskLabels).map(([task, label]) => (
            <label key={task}>
              <span>{label}</span>
              <select onChange={(event) => changeTaskRoute(task, event.target.value as AIRuntimeSettings["task_routing"][string])} value={draft.task_routing[task] ?? draft.primary_provider}>
                {providers.map((provider) => <option key={provider.provider} value={provider.provider}>{provider.display_name}</option>)}
                {task === "risk_summary" ? <option value="human">仅人工</option> : null}
              </select>
            </label>
          ))}
        </div>
      </section>

      <div aria-live="polite" className={`runtime-gate ${!dirty && draft.enabled ? "enabled" : ""}`}>
        {dirty ? <Save size={17} /> : draft.enabled ? <ShieldCheck size={17} /> : <CircleStop size={17} />}
        <span>{dirty ? "有未保存的运行设置；服务商测试或页面刷新不会覆盖，点击保存后才会生效。" : draft.enabled ? "启用前会检查主服务商已保存密钥并通过连接测试。" : "当前关闭：配置可以保存，但 AI Agent 不会调用外部模型。"}</span>
      </div>

      <button className="primary-button runtime-save" disabled={saving || !dirty} onClick={() => void save()} type="button">
        {saving ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}
        {saving ? "保存中…" : "保存运行设置"}
      </button>
    </article>
  );
}
