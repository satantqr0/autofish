import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "AutoFish 运营控制台",
  description: "闲鱼无人值守运营系统的安全控制台",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
