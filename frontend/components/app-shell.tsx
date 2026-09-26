"use client";

import {
  AlertOctagon,
  BarChart3,
  Bot,
  BookOpenCheck,
  Boxes,
  BrainCircuit,
  BriefcaseBusiness,
  ChevronDown,
  ClipboardList,
  Fish,
  Gauge,
  ListChecks,
  LogOut,
  Menu,
  MessageCircleMore,
  MonitorSmartphone,
  Network,
  PackageCheck,
  Rocket,
  Search,
  Settings,
  ShieldAlert,
  ShoppingBag,
  Store,
  Truck,
  X,
} from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { apiFetch } from "@/lib/api";
import type { RuntimeReadiness } from "@/lib/types";

const navigation = [
  { href: "/dashboard", label: "总览", title: "经营总览", icon: Gauge },
  { href: "/autonomous", label: "自主上架", title: "自主上架", icon: Rocket },
  {
    label: "商品与货源",
    icon: Boxes,
    items: [
      { href: "/products", label: "商品中心", icon: Boxes },
      { href: "/sourcing", label: "选品中心", icon: Search },
      { href: "/clawhub", label: "1688 能力台", icon: Network },
      { href: "/suppliers", label: "供应商", icon: Store },
    ],
  },
  {
    label: "交易",
    icon: ClipboardList,
    items: [
      { href: "/xianyu", label: "闲鱼商品", icon: ShoppingBag },
      { href: "/xianyu-workbench", label: "闲鱼工作台", icon: MonitorSmartphone },
      { href: "/conversations", label: "AI 客服", icon: MessageCircleMore },
      { href: "/orders", label: "订单", icon: ClipboardList },
      { href: "/purchases", label: "采购任务", icon: PackageCheck },
      { href: "/logistics", label: "物流", icon: Truck },
    ],
  },
  {
    label: "系统",
    icon: Settings,
    items: [
      { href: "/settings", label: "系统设置", icon: Settings },
      { href: "/model-settings", label: "大模型设置", icon: BrainCircuit },
      { href: "/operations", label: "运营方法", icon: BookOpenCheck },
      { href: "/commercialization", label: "商业化", icon: BriefcaseBusiness },
      { href: "/agents", label: "AI Agent", icon: Bot },
      { href: "/tasks", label: "任务中心", icon: ListChecks },
      { href: "/risk", label: "风险与人工", icon: ShieldAlert },
      { href: "/analytics", label: "经营分析", icon: BarChart3 },
      { href: "/market-prices", label: "品类涨跌榜", icon: BarChart3 },
    ],
  },
] as const;

const titles: Record<string, string> = {};
for (const group of navigation) {
  if ("href" in group) {
    titles[group.href] = group.title;
  } else {
    for (const item of group.items) titles[item.href] = item.label;
  }
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [notice, setNotice] = useState("");
  const [readiness, setReadiness] = useState<RuntimeReadiness | null>(null);

  const activeGroup = useMemo(
    () =>
      navigation.find((group) =>
        "href" in group
          ? pathname === group.href || pathname.startsWith(`${group.href}/`)
          : group.items.some(
              (item) => pathname === item.href || pathname.startsWith(`${item.href}/`),
            ),
      ),
    [pathname],
  );

  useEffect(() => {
    let cancelled = false;
    async function loadReadiness() {
      try {
        const result = await apiFetch<RuntimeReadiness>("/api/v1/dashboard/readiness");
        if (!cancelled) setReadiness(result);
      } catch {
        if (!cancelled) setReadiness(null);
      }
    }
    void loadReadiness();
    const timer = window.setInterval(() => void loadReadiness(), 30_000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [pathname]);

  async function stopAll() {
    if (!window.confirm("确认停止所有自动化？正在执行的安全任务也会在检查点退出。")) return;
    setStopping(true);
    try {
      await apiFetch("/api/v1/automation/stop-all", {
        method: "POST",
        body: JSON.stringify({ enabled: false, reason: "操作员从控制台执行紧急停止" }),
      });
      setNotice("所有自动化已停止");
      setReadiness((current) =>
        current
          ? { ...current, status: "BLOCKED", listing_ready: false, summary: "自动化已停止" }
          : current,
      );
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "停止失败");
    } finally {
      setStopping(false);
    }
  }

  async function logout() {
    await fetch("/api/session/logout", { method: "POST" });
    router.replace("/login");
  }

  const sidebar = (
    <>
      <div className="brand">
        <span className="brand-mark"><Fish size={24} /></span>
        <span>AutoFish</span>
      </div>
      <nav className="side-nav simple-nav" aria-label="主导航">
        {navigation.map((group) => {
          const groupActive = activeGroup?.label === group.label;
          if ("href" in group) {
            const Icon = group.icon;
            return (
              <Link
                className={`nav-item nav-primary ${groupActive ? "active" : ""}`}
                href={group.href}
                key={group.href}
                onClick={() => setMobileOpen(false)}
              >
                <Icon size={18} strokeWidth={1.8} />
                <span>{group.label}</span>
              </Link>
            );
          }
          const Icon = group.icon;
          const open = expanded === group.label || groupActive;
          return (
            <div className={`nav-group ${open ? "open" : ""}`} key={group.label}>
              <button
                aria-expanded={open}
                className={`nav-item nav-primary ${groupActive ? "active" : ""}`}
                onClick={() => setExpanded((value) => (value === group.label ? null : group.label))}
                type="button"
              >
                <Icon size={18} strokeWidth={1.8} />
                <span>{group.label}</span>
                <ChevronDown className="nav-chevron" size={15} />
              </button>
              {open ? (
                <div className="nav-submenu">
                  {group.items.map(({ href, label, icon: SubIcon }) => {
                    const active = pathname === href || pathname.startsWith(`${href}/`);
                    return (
                      <Link
                        className={`nav-subitem ${active ? "active" : ""}`}
                        href={href}
                        key={href}
                        onClick={() => setMobileOpen(false)}
                      >
                        <SubIcon size={15} />
                        <span>{label}</span>
                      </Link>
                    );
                  })}
                </div>
              ) : null}
            </div>
          );
        })}
      </nav>
      <div className="sidebar-footer">
        <button className="emergency-button" disabled={stopping} onClick={stopAll} type="button">
          <AlertOctagon size={18} />
          {stopping ? "正在停止…" : "紧急停止"}
        </button>
      </div>
    </>
  );

  const statusLabel = readiness?.listing_ready
    ? "自主上架可运行"
    : readiness?.summary ?? "正在检查";

  return (
    <div className="app-shell">
      <aside className="sidebar">{sidebar}</aside>
      {mobileOpen ? (
        <div className="mobile-drawer">
          <button className="drawer-scrim" onClick={() => setMobileOpen(false)} aria-label="关闭菜单" />
          <aside className="sidebar mobile-sidebar">
            <button className="drawer-close" onClick={() => setMobileOpen(false)} aria-label="关闭菜单">
              <X size={20} />
            </button>
            {sidebar}
          </aside>
        </div>
      ) : null}
      <div className="app-main">
        <header className="topbar">
          <div className="topbar-title">
            <button className="mobile-menu" onClick={() => setMobileOpen(true)} aria-label="打开菜单">
              <Menu size={21} />
            </button>
            <h1>{titles[pathname] ?? "AutoFish"}</h1>
          </div>
          <div className="topbar-actions">
            <Link
              className={`runtime-status ${readiness?.listing_ready ? "ready" : "blocked"}`}
              href={readiness?.listing_ready ? "/autonomous" : "/dashboard"}
            >
              <span />{statusLabel}
            </Link>
            <div className="operator-menu">
              <span className="avatar">A</span>
              <span className="operator-copy"><b>管理员</b><small>本地账户</small></span>
            </div>
            <button className="icon-button" onClick={logout} title="退出登录" aria-label="退出登录">
              <LogOut size={18} />
            </button>
          </div>
        </header>
        {notice ? (
          <button className="toast" onClick={() => setNotice("")} type="button">{notice}</button>
        ) : null}
        <main className="page-content">{children}</main>
      </div>
    </div>
  );
}
