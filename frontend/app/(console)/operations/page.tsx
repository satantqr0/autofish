"use client";

import { BookOpenCheck, LoaderCircle, ShieldCheck, Workflow } from "lucide-react";
import { useEffect, useState } from "react";

import ModelEvaluation from "@/components/operations/model-evaluation";
import WorkflowConsole from "@/components/operations/workflow-console";
import { apiFetch } from "@/lib/api";
import type { OperationsGuide } from "@/lib/types";

export default function OperationsPage() {
  const [guide, setGuide] = useState<OperationsGuide | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void apiFetch<OperationsGuide>("/api/v1/operations/guide")
      .then((data) => {
        if (!cancelled) setGuide(data);
      })
      .catch((cause) => {
        if (!cancelled) setError(cause instanceof Error ? cause.message : "运营方法加载失败");
      });
    return () => { cancelled = true; };
  }, []);

  if (!guide) {
    return (
      <div className="card empty-state">
        <LoaderCircle className="spin" size={26} />
        <b>{error || "正在加载运营方法与模型价格快照"}</b>
      </div>
    );
  }

  return (
    <>
      <div className="page-heading operations-heading">
        <div>
          <div className="eyebrow">OPERATIONS SYSTEM · {guide.version}</div>
          <h2>运营方法中心</h2>
          <p>把选品、素材、发布、交易、履约、售后、对抗测试和 NAS 运维收口为同一套可执行标准。</p>
        </div>
        <div className="operations-snapshot">
          <ShieldCheck size={19} />
          <div><strong>价格与能力已核验</strong><small>官方快照 {guide.price_checked_at}</small></div>
        </div>
      </div>

      <section className="operations-overview" aria-label="运营方法概览">
        <Summary icon={Workflow} label="标准流程" value={`${guide.workflow_count} 项`} detail="端到端覆盖" />
        <Summary icon={BookOpenCheck} label="每项均包含" value="5 类门禁" detail="触发 · 步骤 · 证据 · 停手 · 验收" />
        <Summary icon={ShieldCheck} label="模型边界" value="规则优先" detail="高风险始终人工接管" />
        <Summary icon={LoaderCircle} label="首选方案" value="GPT-5.6 Luna" detail="精品图搭配 GPT Image 2" />
      </section>

      <WorkflowConsole workflows={guide.workflows} />
      <ModelEvaluation providers={guide.model_providers} scenario={guide.default_scenario} strategy={guide.model_strategy} />

      <section className="operations-guardrails card">
        <header><ShieldCheck size={20} /><div><h3>不可绕过的系统边界</h3><p>这页是方法与预算控制面，不会自动启用任何外部模型或平台写入。</p></div></header>
        <div>{guide.guardrails.map((item) => <p key={item}>{item}</p>)}</div>
      </section>
    </>
  );
}

function Summary({ icon: Icon, label, value, detail }: { icon: typeof Workflow; label: string; value: string; detail: string }) {
  return (
    <article className="card operations-summary-card">
      <div><Icon size={18} /><span>{label}</span></div>
      <strong>{value}</strong>
      <small>{detail}</small>
    </article>
  );
}
