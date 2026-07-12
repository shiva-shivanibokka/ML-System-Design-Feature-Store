"use client";
import { useEffect, useState } from "react";

type Status = "checking" | "ok" | "down";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:7860";
const POLL_MS = 20_000;

/** Pings /health on mount and then every POLL_MS until it reports ok. Cloud
 * Run scales to zero when idle, so a cold start can take longer than a
 * single check — polling means the pill self-corrects to "live" once the
 * instance is up instead of being stuck on "waking up" forever. */
export default function StatusPill() {
  const [status, setStatus] = useState<Status>("checking");

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const check = () => {
      fetch(`${BASE}/health`, { cache: "no-store" })
        .then((r) => {
          if (cancelled) return;
          const ok = r.ok;
          setStatus(ok ? "ok" : "down");
          if (!ok) timer = setTimeout(check, POLL_MS);
        })
        .catch(() => {
          if (cancelled) return;
          setStatus("down");
          timer = setTimeout(check, POLL_MS);
        });
    };
    check();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
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
