export class ApiError extends Error {
  status: number;
  code: string;
  extra: Record<string, unknown>;
  constructor(status: number, code: string, message: string, extra: Record<string, unknown> = {}) {
    super(message);
    this.status = status;
    this.code = code;
    this.extra = extra;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers, credentials: "same-origin" });
  const text = await response.text();
  let data: unknown = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = text;
  }
  if (!response.ok) {
    const err = (data as { error?: { code?: string; message?: string } } | null)?.error;
    throw new ApiError(
      response.status,
      err?.code ?? "HTTP_" + response.status,
      err?.message ?? "请求失败（" + response.status + "）",
      (err ?? {}) as Record<string, unknown>,
    );
  }
  return data as T;
}

export const json = (body: unknown, method = "POST"): RequestInit => ({
  method,
  body: JSON.stringify(body),
});

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof Error) return "网络异常：" + error.message;
  return "未知错误";
}

export async function openHtmlReport(id: string) {
  const url = `/api/audits/${id}/report?format=html`;
  window.open(url, "_blank", "noopener");
}

export async function downloadJsonReport(id: string) {
  const data = await api<unknown>(`/api/audits/${id}/report`);
  const href = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const link = document.createElement("a");
  link.href = href;
  link.download = `预审报告-${id.slice(0, 8)}.json`;
  link.click();
  setTimeout(() => URL.revokeObjectURL(href), 1000);
}
