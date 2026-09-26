"use client";

import {
  ArrowUpRight,
  CheckCircle2,
  CircleStop,
  ClipboardCheck,
  FileSearch,
  ListChecks,
  PlayCircle,
  Search,
  ShieldCheck,
} from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import type { OperationsWorkflow } from "@/lib/types";
import { resolveFilteredActiveId } from "@/lib/workflow-selection";

const stageOrder = ["全部", "选品", "商品", "素材", "发布", "交易", "履约", "售后", "质量", "运维"];

function statusTone(status: string) {
  if (status === "已接入") return "green";
  if (status === "受控执行") return "blue";
  return "orange";
}

export default function WorkflowConsole({ workflows }: { workflows: OperationsWorkflow[] }) {
  const [requestedActiveId, setRequestedActiveId] = useState(workflows[0]?.id ?? "");
  const [stage, setStage] = useState("全部");
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();
    return workflows.filter((workflow) => {
      const matchesStage = stage === "全部" || workflow.stage === stage;
      const haystack = [
        workflow.name,
        workflow.goal,
        workflow.trigger,
        ...workflow.steps,
        ...workflow.stop_conditions,
      ].join(" ").toLowerCase();
      return matchesStage && (!keyword || haystack.includes(keyword));
    });
  }, [query, stage, workflows]);

  const activeId = resolveFilteredActiveId(filtered, requestedActiveId);
  const selected = filtered.find((workflow) => workflow.id === activeId);

  return (
    <section className="operations-section" aria-labelledby="workflow-title">
      <div className="operations-section-heading">
        <div>
          <span className="eyebrow">END-TO-END RUNBOOK</span>
          <h3 id="workflow-title">全链路运营流程</h3>
          <p>每一步都明确触发条件、自动化边界、证据、停手条件和验收标准。</p>
        </div>
        <span className="operations-count">{workflows.length} 项标准流程</span>
      </div>

      <div className="operations-filters">
        <label className="operations-search">
          <Search size={15} />
          <input
            aria-label="搜索运营流程"
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索步骤、异常或证据"
            value={query}
          />
        </label>
        <div className="operations-stage-tabs" aria-label="流程阶段">
          {stageOrder.map((item) => (
            <button
              aria-pressed={stage === item}
              className={stage === item ? "active" : ""}
              key={item}
              onClick={() => setStage(item)}
              type="button"
            >
              {item}
            </button>
          ))}
        </div>
      </div>

      <div className="operations-workspace">
        <aside className="card operations-index" aria-label="运营流程列表">
          {filtered.length ? filtered.map((workflow) => (
            <button
              className={`operations-index-item ${selected?.id === workflow.id ? "active" : ""}`}
              key={workflow.id}
              onClick={() => setRequestedActiveId(workflow.id)}
              type="button"
            >
              <span className="operations-step-number">{String(workflow.order).padStart(2, "0")}</span>
              <span>
                <b>{workflow.name}</b>
                <small>{workflow.stage} · {workflow.automation}</small>
              </span>
              <span className={`tag ${statusTone(workflow.system_status)}`}>{workflow.system_status}</span>
            </button>
          )) : (
            <div className="operations-no-result">没有匹配流程，请调整搜索或阶段。</div>
          )}
        </aside>

        {selected ? (
          <article className="card operations-detail">
            <header className="operations-detail-head">
              <div>
                <div className="operations-detail-kicker">流程 {String(selected.order).padStart(2, "0")} · {selected.stage}</div>
                <h4>{selected.name}</h4>
                <p>{selected.goal}</p>
              </div>
              <Link className="secondary-button" href={selected.route}>
                {selected.route_label}<ArrowUpRight size={15} />
              </Link>
            </header>

            <div className="operations-trigger">
              <PlayCircle size={17} />
              <div><span>触发方式</span><b>{selected.trigger}</b></div>
              <span className={`tag ${statusTone(selected.system_status)}`}>{selected.automation}</span>
            </div>

            <div className="operations-detail-grid">
              <GuideBlock icon={ClipboardCheck} items={selected.prerequisites} title="开始前门禁" />
              <GuideBlock icon={ListChecks} items={selected.steps} ordered title="标准操作步骤" />
              <GuideBlock icon={FileSearch} items={selected.evidence} title="必须留存的证据" />
              <GuideBlock danger icon={CircleStop} items={selected.stop_conditions} title="立即停手条件" />
            </div>

            <div className="operations-acceptance">
              <ShieldCheck size={19} />
              <div><span>完成验收</span><strong>{selected.acceptance}</strong></div>
            </div>
          </article>
        ) : null}
      </div>
    </section>
  );
}

function GuideBlock({
  danger = false,
  icon: Icon,
  items,
  ordered = false,
  title,
}: {
  danger?: boolean;
  icon: typeof CheckCircle2;
  items: string[];
  ordered?: boolean;
  title: string;
}) {
  const List = ordered ? "ol" : "ul";
  return (
    <section className={`operations-guide-block ${danger ? "danger" : ""}`}>
      <h5><Icon size={16} />{title}</h5>
      <List>
        {items.map((item) => <li key={item}>{item}</li>)}
      </List>
    </section>
  );
}
