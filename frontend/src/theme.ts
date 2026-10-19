import type { ThemeConfig } from "antd";

// 沿用原型的墨绿体系；文字色比原型加深，保证白底对比度。
export const colors = {
  ink: "#18342e",
  inkMuted: "#4f6359",
  bg: "#f4f6f1",
  top: "#10252a",
  primary: "#1e674e",
  border: "#e0e9df",
  link: "#126c9d",
};

export const theme: ThemeConfig = {
  token: {
    colorPrimary: colors.primary,
    colorLink: colors.link,
    colorText: colors.ink,
    colorTextSecondary: colors.inkMuted,
    colorBgLayout: colors.bg,
    colorBorderSecondary: colors.border,
    borderRadius: 9,
    fontSize: 14,
    fontFamily:
      'Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
  },
};
