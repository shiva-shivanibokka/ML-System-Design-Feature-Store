"use client";
import { useEffect, useState } from "react";
import styles from "./StatusPill.module.css";

type Status = "checking" | "ok" | "down";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:7860";

/** Pings /health once on mount. HF Spaces free tier sleeps when idle, so
 * "down" here just as often means "asleep" as it means broken — the label
 * reflects that rather than reading as an outage. */
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
    status === "ok" ? "API online" : status === "down" ? "API asleep / unreachable" : "Checking API…";

  return (
    <span className={`${styles.pill} ${styles[status]}`}>
      <span className={styles.dot} aria-hidden="true" />
      {label}
    </span>
  );
}
