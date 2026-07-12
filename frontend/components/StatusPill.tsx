"use client";
import { useEffect, useState } from "react";

type Status = "checking" | "ok" | "down";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:7860";

/** Pings /health once on mount. Cloud Run scales to zero when idle, so
 * "down" here just as often means "cold-starting" as it means broken —
 * the label reflects that rather than reading as an outage. */
export default function StatusPill() {
  const [status, setStatus] = useState<Status>("checking");

  useEffect(() => {
    let cancelled = false;
    fetch(`${BASE}/health`, { cache: "no-store" })
      .then((r) => {
        if (!cancelled) setStatus(r.ok ? "ok" : "down");
      })
      .catch(() => {
        if (!cancelled) setStatus("down");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const label =
    status === "ok"
      ? "live · MotherDuck + Valkey · Cloud Run"
      : status === "down"
        ? "waking up · Cloud Run cold start"
        : "checking · Cloud Run";

  return (
    <span className={`pill ${status}`}>
      <span className="dot" aria-hidden="true" />
      {label}
    </span>
  );
}
