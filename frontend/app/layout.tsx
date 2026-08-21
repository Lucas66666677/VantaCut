import type { ReactNode } from "react";
import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "VantaCut — AI 輔助影片剪輯",
  description: "從素材到多平台成片，保留人類決策權的 AI 影片工作室。",
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="zh-Hant" data-theme="dark"><body>{children}</body></html>;
}
