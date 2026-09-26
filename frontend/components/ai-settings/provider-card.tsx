"use client";

import {
  CheckCircle2,
  ImageIcon,
  KeyRound,
  LoaderCircle,
  Save,
  ScanEye,
  TestTube2,
  Trash2,
  Wrench,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { AIProviderSettings } from "@/lib/types";

type ProviderDraft = Pick<
  AIProviderSettings,
  "base_url" | "text_model" | "vision_model" | "image_model"
>;

function providerDraft(provider: AIProviderSettings): ProviderDraft {
  return {
    base_url: provider.base_url,
    text_model: provider.text_model,
    vision_model: provider.vision_model,
    image_model: provider.image_model,
  };
}

function sameDraft(left: ProviderDraft, right: ProviderDraft) {
  return left.base_url === right.base_url
    && left.text_model === right.text_model
    && left.vision_model === right.vision_model
    && left.image_model === right.image_model;
}

function statusMeta(status: AIProviderSettings["last_test_status"]) {
  if (status === "SUCCESS") return { label: "连接正常", tone: "green" };
  if (status === "FAILED") return { label: "测试失败", tone: "orange" };
  return { label: "待测试", tone: "gray" };
}

export default function ProviderCard({
  onChange,
  onNotice,
  provider,
}: {
  onChange: () => Promise<void>;
  onNotice: (message: string) => void;
  provider: AIProviderSettings;
}) {
  const [draft, setDraft] = useState<ProviderDraft>(() => providerDraft(provider));
  const [apiKey, setApiKey] = useState("");
  const [dirty, setDirty] = useState(false);
  const [working, setWorking] = useState<"save" | "test" | "clear" | "">("");
  const pendingSavedDraft = useRef<ProviderDraft | null>(null);

  useEffect(() => {
    if (dirty) return;
    const nextDraft = providerDraft(provider);
    if (pendingSavedDraft.current && !sameDraft(pendingSavedDraft.current, nextDraft)) return;
    pendingSavedDraft.current = null;
    setDraft(nextDraft);
  }, [dirty, provider]);

  function changeDraft(patch: Partial<ProviderDraft>) {
    const nextDraft = { ...draft, ...patch };
    setDraft(nextDraft);
    setDirty(Boolean(apiKey) || !sameDraft(nextDraft, providerDraft(provider)));
  }

  function changeApiKey(nextApiKey: string) {
    setApiKey(nextApiKey);
    setDirty(Boolean(nextApiKey) || !sameDraft(draft, providerDraft(provider)));
  }

  async function save() {
    setWorking("save");
    try {
      const savedProvider = await apiFetch<AIProviderSettings>(`/api/v1/ai-settings/providers/${provider.provider}`, {
        method: "PUT",
        body: JSON.stringify({ ...draft, api_key: apiKey || null }),
      });
      const savedDraft = providerDraft(savedProvider);
      pendingSavedDraft.current = savedDraft;
      setDraft(savedDraft);
      setApiKey("");
      setDirty(false);
      onNotice(`${provider.display_name} 配置已加密保存，请执行连接测试`);
      await onChange();
    } catch (cause) {
      onNotice(cause instanceof Error ? cause.message : "模型配置保存失败");
    } finally {
      setWorking("");
    }
  }

  async function testConnection() {
    setWorking("test");
    try {
      const result = await apiFetch<AIProviderSettings>(
        `/api/v1/ai-settings/providers/${provider.provider}/test`,
        { method: "POST", timeoutMs: 35_000 },
      );
      onNotice(`${provider.display_name}：${result.last_test_message ?? "测试完成"}`);
      await onChange();
    } catch (cause) {
      onNotice(cause instanceof Error ? cause.message : "连接测试失败");
    } finally {
      setWorking("");
    }
  }

  async function clearKey() {
    if (!window.confirm(`确认清除 ${provider.display_name} 的 API Key？`)) return;
    setWorking("clear");
    try {
      await apiFetch(`/api/v1/ai-settings/providers/${provider.provider}/api-key`, {
        method: "DELETE",
      });
      onNotice(`${provider.display_name} API Key 已清除`);
      await onChange();
    } catch (cause) {
      onNotice(cause instanceof Error ? cause.message : "API Key 清除失败");
    } finally {
      setWorking("");
    }
  }

  const status = statusMeta(provider.last_test_status);
  const dirtyKey = Boolean(apiKey);

  return (
    <article className="card ai-provider-card">
      <header>
        <div className={`provider-mark ${provider.provider}`}>{provider.display_name.slice(0, 1)}</div>
        <div className="provider-title">
          <h3>{provider.display_name}</h3>
          <span>{provider.provider}</span>
        </div>
        <span className={`tag ${status.tone}`}>{status.label}</span>
      </header>

      <div className="provider-capabilities" aria-label={`${provider.display_name}能力`}>
        <span><Wrench size={13} />文本 / 工具</span>
        <span className={!provider.capabilities.vision ? "disabled" : ""}><ScanEye size={13} />视觉</span>
        <span className={!provider.capabilities.image_generation ? "disabled" : ""}><ImageIcon size={13} />生图</span>
      </div>

      <div className="provider-form-grid">
        <label className="field full">
          <span>兼容 API 地址</span>
          <input
            onChange={(event) => changeDraft({ base_url: event.target.value })}
            spellCheck={false}
            value={draft.base_url}
          />
        </label>
        <label className="field">
          <span>文本模型</span>
          <input onChange={(event) => changeDraft({ text_model: event.target.value })} value={draft.text_model} />
        </label>
        <label className="field">
          <span>视觉模型</span>
          <input onChange={(event) => changeDraft({ vision_model: event.target.value || null })} placeholder="不支持可留空" value={draft.vision_model ?? ""} />
        </label>
        <label className="field">
          <span>图像生成模型</span>
          <input onChange={(event) => changeDraft({ image_model: event.target.value || null })} placeholder="不支持可留空" value={draft.image_model ?? ""} />
        </label>
        <label className="field api-key-field">
          <span>API Key</span>
          <div>
            <KeyRound size={15} />
            <input
              autoComplete="new-password"
              onChange={(event) => changeApiKey(event.target.value)}
              placeholder={provider.api_key_configured ? `已保存 ${provider.api_key_hint ?? "加密密钥"}` : "输入后加密保存"}
              type="password"
              value={apiKey}
            />
          </div>
        </label>
      </div>

      <div aria-live="polite" className="provider-test-status">
        {dirty ? <Save size={16} /> : provider.last_test_status === "SUCCESS" ? <CheckCircle2 size={16} /> : <TestTube2 size={16} />}
        <div>
          <b>{dirty ? "有未保存修改" : provider.last_test_message ?? "保存密钥后测试模型列表接口"}</b>
          <small>{dirty ? "其他服务商的保存、测试或刷新不会覆盖这些输入；点击保存后生效。" : provider.last_tested_at ? new Date(provider.last_tested_at).toLocaleString("zh-CN", { hour12: false }) : "尚未测试"}</small>
        </div>
      </div>

      <footer>
        <button className="primary-button" disabled={Boolean(working)} onClick={() => void save()} type="button">
          {working === "save" ? <LoaderCircle className="spin" size={15} /> : <Save size={15} />}
          保存配置
        </button>
        <button className="secondary-button" disabled={Boolean(working) || !provider.api_key_configured || dirtyKey} onClick={() => void testConnection()} type="button">
          {working === "test" ? <LoaderCircle className="spin" size={15} /> : <TestTube2 size={15} />}
          测试已保存配置
        </button>
        {provider.api_key_configured ? (
          <button aria-label={`清除${provider.display_name} API Key`} className="provider-clear" disabled={Boolean(working)} onClick={() => void clearKey()} type="button">
            <Trash2 size={15} />
          </button>
        ) : null}
      </footer>
    </article>
  );
}
