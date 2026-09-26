"use client";

import {
  ArrowUpRight,
  Bot,
  Calculator,
  Check,
  CircleDollarSign,
  CloudCog,
  Gauge,
  Route,
  ShieldAlert,
  Sparkles,
  WalletCards,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";

import type { ModelPlan, ModelProviderSnapshot, OperationsGuide } from "@/lib/types";

type ModelStrategy = OperationsGuide["model_strategy"];
type Scenario = OperationsGuide["default_scenario"];

function formatContext(tokens: number) {
  return tokens >= 1_000_000 ? `${(tokens / 1_000_000).toFixed(tokens % 1_000_000 ? 2 : 0)}M` : `${tokens / 1_000}K`;
}

function toCnyCost(
  model: ModelProviderSnapshot,
  input: number,
  output: number,
  usdToCny: number,
  offpeak = false,
) {
  const inputRate = offpeak && model.offpeak_input_per_million != null
    ? model.offpeak_input_per_million
    : model.input_per_million;
  const outputRate = offpeak && model.offpeak_output_per_million != null
    ? model.offpeak_output_per_million
    : model.output_per_million;
  const native = input * inputRate + output * outputRate;
  return model.currency === "USD" ? native * usdToCny : native;
}

export default function ModelEvaluation({
  providers,
  scenario,
  strategy,
}: {
  providers: ModelProviderSnapshot[];
  scenario: Scenario;
  strategy: ModelStrategy;
}) {
  const [input, setInput] = useState(scenario.input_million_tokens);
  const [output, setOutput] = useState(scenario.output_million_tokens);
  const [usdToCny, setUsdToCny] = useState(scenario.usd_to_cny);

  const costs = useMemo(() => providers.map((provider) => ({
    ...provider,
    cost: toCnyCost(provider, input, output, usdToCny),
    offpeakCost: provider.offpeak_input_per_million != null
      ? toCnyCost(provider, input, output, usdToCny, true)
      : null,
  })).sort((a, b) => a.cost - b.cost), [input, output, providers, usdToCny]);

  return (
    <section className="operations-section model-evaluation" aria-labelledby="model-title">
      <div className="operations-section-heading">
        <div>
          <span className="eyebrow">MODEL ROUTING & COST</span>
          <h3 id="model-title">大模型适配与成本评估</h3>
          <p>没有一个文字模型能同时包办客服、视觉核验和精品图生成；生产上应按任务分层路由。</p>
        </div>
        <span className="model-decision"><Route size={16} />{strategy.decision}</span>
      </div>

      <div className="model-plan-grid">
        <PlanCard featured icon={Sparkles} label="推荐" plan={strategy.single_provider} />
        <PlanCard icon={WalletCards} label="最低成本" plan={strategy.lowest_cost} />
        <PlanCard icon={CloudCog} label="国内链路" plan={strategy.domestic} />
      </div>

      <div className="model-evaluation-grid">
        <article className="card cost-calculator">
          <header>
            <div><Calculator size={18} /><h4>月度文本成本测算</h4></div>
            <span>人民币预算口径</span>
          </header>
          <div className="cost-inputs">
            <label><span>输入量（百万 token）</span><input min="0" onChange={(event) => setInput(Number(event.target.value))} step="0.1" type="number" value={input} /></label>
            <label><span>输出量（百万 token）</span><input min="0" onChange={(event) => setOutput(Number(event.target.value))} step="0.1" type="number" value={output} /></label>
            <label><span>美元参考汇率</span><input min="1" onChange={(event) => setUsdToCny(Number(event.target.value))} step="0.01" type="number" value={usdToCny} /></label>
          </div>
          <div className="cost-results">
            {costs.map((model, index) => (
              <div className={`cost-result ${model.id === "openai-luna" ? "recommended" : ""}`} key={model.id}>
                <div>
                  <span>{model.provider}</span>
                  <b>{model.model}</b>
                  {model.offpeakCost != null ? <small>低谷约 ¥{model.offpeakCost.toFixed(2)}</small> : null}
                </div>
                <strong>¥{model.cost.toFixed(2)}</strong>
                {index === 0 ? <span className="cost-rank">标准价最低</span> : null}
              </div>
            ))}
          </div>
          <ul className="cost-assumptions">
            {scenario.assumptions.map((item) => <li key={item}>{item}</li>)}
          </ul>
        </article>

        <article className="card routing-card">
          <header><Route size={18} /><h4>建议生产路由</h4></header>
          <div className="routing-list">
            {strategy.routing.map((item) => (
              <div className="routing-row" key={item.task}>
                <span>{item.task}</span>
                <div><b>{item.route}</b><small>备选：{item.fallback}</small></div>
              </div>
            ))}
          </div>
          <div className="routing-warning">
            <ShieldAlert size={17} />
            <span>支付、退款、纠纷、价格越界和事实冲突不交给模型自动决定。</span>
          </div>
        </article>
      </div>

      <article className="card model-matrix-card">
        <header>
          <div><Gauge size={18} /><h4>官方价格快照与能力矩阵</h4></div>
          <span>文本标准价 / 每百万 token</span>
        </header>
        <div className="table-wrap">
          <table className="model-matrix">
            <thead><tr><th>厂商 / 模型</th><th>输入 / 输出</th><th>上下文</th><th>JSON / 工具</th><th>视觉输入</th><th>图像生成</th><th>适配分</th><th>结论与来源</th></tr></thead>
            <tbody>
              {providers.map((model) => (
                <tr key={model.id}>
                  <td><b>{model.provider}</b><span>{model.model}</span></td>
                  <td><b>{model.currency === "CNY" ? "¥" : "$"}{model.input_per_million.toFixed(2)} / {model.currency === "CNY" ? "¥" : "$"}{model.output_per_million.toFixed(2)}</b><span>{model.currency}</span></td>
                  <td>{formatContext(model.context_tokens)}</td>
                  <td><Capability ok={model.structured_output && model.tool_calling} /></td>
                  <td><Capability ok={model.vision_input} /></td>
                  <td><span className={model.image_generation === "不覆盖" ? "model-no" : "model-suite"}>{model.image_generation}</span></td>
                  <td><strong className="fit-score">{model.fit_score}</strong><span>/ 100</span></td>
                  <td className="model-conclusion"><b>{model.recommendation}</b><span>{model.notes}</span><a href={model.pricing_url} rel="noreferrer" target="_blank">官方价格<ArrowUpRight size={12} /></a></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </article>

      <div className="model-controls-grid">
        <Checklist icon={CircleDollarSign} items={strategy.cost_controls} title="成本控制规则" />
        <Checklist icon={Bot} items={strategy.production_gate} title="模型上线门禁" />
      </div>
    </section>
  );
}

function PlanCard({ featured = false, icon: Icon, label, plan }: { featured?: boolean; icon: typeof Sparkles; label: string; plan: ModelPlan }) {
  return (
    <article className={`card model-plan-card ${featured ? "featured" : ""}`}>
      <div className="model-plan-label"><Icon size={17} /><span>{label}</span></div>
      <h4>{plan.name}</h4>
      <div className="model-stack"><b>{plan.primary}</b>{plan.offpeak_text ? <span>+ {plan.offpeak_text}</span> : null}<span>+ {plan.image}</span></div>
      <p>{plan.why}</p>
    </article>
  );
}

function Capability({ ok }: { ok: boolean }) {
  return ok ? <span className="capability-yes"><Check size={14} />支持</span> : <span className="capability-no"><X size={14} />缺失</span>;
}

function Checklist({ icon: Icon, items, title }: { icon: typeof CircleDollarSign; items: string[]; title: string }) {
  return (
    <article className="card model-checklist">
      <header><Icon size={18} /><h4>{title}</h4></header>
      <ol>{items.map((item) => <li key={item}>{item}</li>)}</ol>
    </article>
  );
}
