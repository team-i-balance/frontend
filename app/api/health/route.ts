import { NextResponse } from "next/server";

// Backend base URL. Set this in Vercel as an environment variable:
//   BACKEND_URL=http://13.193.207.69:8501
const BACKEND_URL = process.env.BACKEND_URL ?? "http://localhost:8000";

// This runs on the server (Vercel), so it can call an http backend without
// browser mixed-content issues.
export async function GET() {
  const target = `${BACKEND_URL.replace(/\/$/, "")}/health`;

  try {
    const res = await fetch(target, {
      cache: "no-store",
      // keep the hop short so a dead backend fails fast
      signal: AbortSignal.timeout(5000),
    });

    const ok = res.ok;
    let backend: unknown = null;
    try {
      backend = await res.json();
    } catch {
      backend = await res.text();
    }

    return NextResponse.json(
      {
        frontend: "ok",
        backendReachable: ok,
        backendUrl: target,
        backend,
      },
      { status: ok ? 200 : 502 },
    );
  } catch (err) {
    return NextResponse.json(
      {
        frontend: "ok",
        backendReachable: false,
        backendUrl: target,
        error: err instanceof Error ? err.message : String(err),
      },
      { status: 502 },
    );
  }
}
