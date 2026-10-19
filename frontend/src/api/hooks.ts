import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type { AuditTask, StandardOption } from "./types";
import { SETTLED } from "../lib/labels";

// 任务进入稳定状态后停止轮询；resume 后状态变为 CHECKING，轮询自动恢复。
export function useTask(id: string) {
  return useQuery({
    queryKey: ["task", id],
    queryFn: () => api<AuditTask>(`/api/audits/${id}`),
    refetchInterval: (query) => {
      const task = query.state.data;
      if (query.state.error) return false;
      return task && SETTLED.includes(task.status) ? false : 2000;
    },
  });
}

export function useStandards() {
  return useQuery({
    queryKey: ["standards"],
    queryFn: () => api<StandardOption[]>("/api/standards"),
    staleTime: Infinity,
  });
}

// 后端暂把视觉降级等提示写在 error 里；未失败的任务把它当作警告展示。
export function taskWarnings(task: AuditTask): { code: string; message: string }[] {
  const list = [...(task.warnings ?? [])];
  if (task.status !== "FAILED" && task.error) list.push({ code: task.error.code, message: task.error.message });
  return list;
}
