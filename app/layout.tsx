import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "iBalance",
  description: "iBalance frontend",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko">
      <body
        style={{
          fontFamily:
            "system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
          margin: 0,
          background: "#0b1020",
          color: "#e6e8f0",
        }}
      >
        {children}
      </body>
    </html>
  );
}
