"use client";
import { useEffect, useState } from "react";

export type BackendHealth = "checking" | "ok" | "waking" | "offline";

const BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:7860";
const POLL_MS = 20_000;

/**
 * How many failed checks to attribute to a cold start before calling the
 * backend offline. Cloud Run scales to zero when idle and a cold start can
 * take up to a minute, so three checks twenty seconds apart is long enough to
 * cover a genuinely-waking instance.
 *
 * Past that, continuing to say "waking up" is not a cold-start message any
 * more, it is a wrong one -- and the previous version of this polled forever,
 * so a backend that was switched off reported "waking up · Cloud Run cold
 * start" indefinitely, once every twenty seconds, for as long as the tab
 * stayed open.
 */
const COLD_START_ATTEMPTS = 3;

/**
 * Polls `/health` and reports one of four states.
 *
 * The distinction that matters is `waking` versus `offline`. The first is a
 * claim that the backend is coming; the second is a claim that it is not. This
 * hook will only make the first one for as long as it could plausibly be true.
 *
 * Everything here is driven by the live check rather than hardcoded, so if the
 * backend is ever restored the UI corrects itself with no code change -- which
 * is the property the hardcoded alternative would have lost.
 */
export function useBackendHealth(): BackendHealth {
  const [health, setHealth] = useState<BackendHealth>("checking");

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let failures = 0;

    const check = () => {
      fetch(`${BASE}/health`, { cache: "no-store" })
        .then((r) => {
          if (cancelled) return;
          if (r.ok) {
            failures = 0;
            setHealth("ok");
            return;
          }
          onFailure();
        })
        .catch(() => {
          if (cancelled) return;
          onFailure();
        });
    };

    const onFailure = () => {
      failures += 1;
      if (failures < COLD_START_ATTEMPTS) {
        setHealth("waking");
        timer = setTimeout(check, POLL_MS);
        return;
      }
      // Settle, and stop polling. A dead service does not need to be asked
      // again every twenty seconds for the lifetime of the page.
      setHealth("offline");
    };

    check();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, []);

  return health;
}
