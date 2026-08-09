import { CalendarCheck2, Clock3, ShieldCheck } from "lucide-react";
import type { DashboardStatus } from "../types";

function formatNext(value: string | null, now = new Date()) {
  if (!value) return "未安装";
  const date = new Date(value);
  if (Number.isNaN(date.valueOf())) return value;
  const calendarDay = (item: Date) => Date.UTC(
    item.getFullYear(),
    item.getMonth(),
    item.getDate(),
  );
  const dayOffset = Math.round(
    (calendarDay(date) - calendarDay(now)) / 86_400_000,
  );
  const time = `${String(date.getHours()).padStart(2, "0")}:${String(date.getMinutes()).padStart(2, "0")}`;
  return dayOffset === 1
    ? `明日 ${time}`
    : `${date.getMonth() + 1}月${date.getDate()}日 ${time}`;
}

export function StatusRail({ status }: { status: DashboardStatus }) {
  const loginLabel = status.login.status === "failed"
    ? "需要登录"
    : status.login.status === "success"
      ? "正常"
      : "未检查";
  return (
    <section className="status-rail" aria-label="核心状态">
      <div className="status-item blue">
        <span className="status-icon"><CalendarCheck2 /></span>
        <div><small>今日任务</small><strong>{status.today.complete ? "已完成" : "进行中"} <b>{status.today.succeeded}/{status.today.total}</b></strong></div>
      </div>
      <div className="status-item green">
        <span className="status-icon"><ShieldCheck /></span>
        <div><small>登录状态</small><strong>{loginLabel}</strong></div>
      </div>
      <div className="status-item amber">
        <span className="status-icon"><Clock3 /></span>
        <div><small>下次运行</small><strong>{formatNext(status.next_run)}</strong></div>
      </div>
    </section>
  );
}
