import { NextResponse } from "next/server";

const backend = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:18181";

export async function POST(request: Request) {
  const cookie = request.headers.get("cookie") ?? "";
  const payload = await request.json();
  const response = await fetch(`${backend}/api/v1/auth/change-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json", cookie },
    body: JSON.stringify(payload),
    cache: "no-store",
    signal: AbortSignal.timeout(8_000),
  });
  const body = await response.json().catch(() => ({ detail: "改密服务不可用" }));
  if (!response.ok) {
    return NextResponse.json(body, { status: response.status });
  }
  const result = NextResponse.json({ ok: true });
  result.cookies.set("autofish_token", body.access_token, {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.COOKIE_SECURE === "true",
    path: "/",
    maxAge: body.expires_in,
  });
  return result;
}
