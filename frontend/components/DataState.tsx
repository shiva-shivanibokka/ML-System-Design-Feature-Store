"use client";
import styles from "./shared.module.css";

/**
 * Shared loading / error / empty presentation for every panel. The error
 * copy assumes the likeliest real-world cause: the HF Spaces backend is
 * free-tier and sleeps when idle, so the first request can take up to a
 * minute to wake it — this is a routine state for this app, not a crash.
 */
export function DataState({
  loading,
  error,
  empty,
  emptyMessage = "Nothing here yet.",
  onRetry,
  children,
}: {
  loading: boolean;
  error: string | null;
  empty?: boolean;
  emptyMessage?: string;
  onRetry?: () => void;
  children: React.ReactNode;
}) {
  if (loading) {
    return (
      <div className={styles.state}>
        <span className={styles.spinner} aria-hidden="true" />
        <span>Fetching from the feature server…</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className={styles.stateError} role="alert">
        <p>
          <strong>Couldn&rsquo;t reach the feature server.</strong> It may be
          asleep — Hugging Face Spaces free tier sleeps after idle time and
          can take up to a minute to wake on the next request.
        </p>
        <p className={styles.stateErrorDetail}>{error}</p>
        {onRetry && (
          <button type="button" className={styles.retryButton} onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    );
  }
  if (empty) {
    return <div className={styles.state}>{emptyMessage}</div>;
  }
  return <>{children}</>;
}
