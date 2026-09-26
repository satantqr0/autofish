import { NextResponse } from "next/server";

const backend = process.env.BACKEND_INTERNAL_URL ?? "http://127.0.0.1:18181";

export async function POST(request: Request) {
  const cookie = request.headers.get("cookie");
  if (cookie) {
    await fetch(`${backend}/api/v1/auth/logout`, {
      method: "POST",
      headers: { cookie },
      cache: "no-store",
      signal: AbortSignal.timeout(5_000),
    }).catch(() => undefined);
  }
  const response = NextResponse.json({ ok: true });
  response.cookies.set("autofish_token", "", {
    httpOnly: true,
    sameSite: "strict",
    secure: process.env.COOKIE_SECURE === "true",
    path: "/",
    maxAge: 0,
  });
  return response;
}
