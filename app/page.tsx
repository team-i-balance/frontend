"use client";

import { useCallback, useEffect, useState } from "react";

type HealthResult = {
  frontend: string;
  backendReachable: boolean;
  backendUrl: string;
  backend?: unknown;
  error?: string;
};

export default function Home() {
  const [data, setData] = useState<HealthResult | null>(null);
  const [loading, setLoading] = useState(true);

  const check = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/health", { cache: "no-store" });
      setData(await res.json());
    } catch (err) {
      setData({
        frontend: "ok",
        backendReachable: false,
        backendUrl: "(unknown)",
        error: err instanceof Error ? err.message : String(err),
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    check();
  }, [check]);

  const backendOk = data?.backendReachable === true;

  return (
    <main
      style={{
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 24,
        padding: 24,
      }}
    >
      <h1 style={{ margin: 0 }}>iBalance</h1>

      <section
        style={{
          width: "100%",
          maxWidth: 420,
          background: "#141a30",
          border: "1px solid #26304f",
          borderRadius: 12,
          padding: 20,
        }}
      >
        <h2 style={{ marginTop: 0, fontSize: 16 }}>연동 상태</h2>

        <StatusRow label="Frontend" ok={true} text="ok" />
        <StatusRow
          label="Backend"
          ok={backendOk}
          text={
            loading
              ? "확인 중..."
              : backendOk
                ? "연결됨"
                : "연결 안 됨"
          }
        />

        {data?.error && (
          <p style={{ color: "#ff9a9a", fontSize: 13, marginBottom: 0 }}>
            {data.error}
          </p>
        )}

        <pre
          style={{
            marginTop: 16,
            marginBottom: 16,
            background: "#0b1020",
            border: "1px solid #26304f",
            borderRadius: 8,
            padding: 12,
            fontSize: 12,
            overflowX: "auto",
          }}
        >
          {data ? JSON.stringify(data, null, 2) : "..."}
        </pre>

        <button
          onClick={check}
          disabled={loading}
          style={{
            width: "100%",
            padding: "10px 14px",
            borderRadius: 8,
            border: "none",
            background: "#4c6ef5",
            color: "white",
            fontSize: 14,
            cursor: loading ? "default" : "pointer",
            opacity: loading ? 0.6 : 1,
          }}
        >
          다시 확인
        </button>
      </section>
    </main>
  );
}

function StatusRow({
  label,
  ok,
  text,
}: {
  label: string;
  ok: boolean;
  text: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "8px 0",
      }}
    >
      <span>{label}</span>
      <span style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span
          style={{
            width: 10,
            height: 10,
            borderRadius: "50%",
            background: ok ? "#51cf66" : "#ff6b6b",
            display: "inline-block",
          }}
        />
        {text}
      </span>
    </div>
  );
}
