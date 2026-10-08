"use client";
import { useBackendHealth } from "./useBackendHealth";

/**
 * Reports what the health check actually found.
 *
 * This used to collapse every failure into "waking up · Cloud Run cold start"
 * and poll forever. The check itself was right -- it correctly saw the backend
 * was unreachable -- but the label was a cold-start message, so a service that
 * was switched off read as a service that was about to arrive. The polling in
 * `useBackendHealth` now gives up after a cold start's worth of attempts and
 * this says "backend offline" instead of promising something that is not
 * coming.
 */
export default function StatusPill() {
  const health = useBackendHealth();

  const label =
    health === "ok"
      ? "live · DuckDB + Valkey · Cloud Run"
      : health === "waking"
        ? "waking up · Cloud Run cold start"
        : health === "offline"
          ? "backend offline"
          : "checking · Cloud Run";

  return (
    <span className={`pill ${health}`}>
      <span className="dot" aria-hidden="true" />
      {label}
    </span>
  );
}
