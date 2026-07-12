"use client";

/**
 * Shared loading / error / empty presentation for every panel. The error
 * copy assumes the likeliest real-world cause: the Cloud Run backend
 * scales to zero when idle, so the first request can take up to a
 * minute to cold-start — this is a routine state for this app, not a crash.
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
      <div className="state">
        <span className="spinner" aria-hidden="true" />
        <span>Fetching from the feature server…</span>
      </div>
    );
  }
  if (error) {
    return (
      <div className="state-error" role="alert">
        <p>
          <strong>Couldn&rsquo;t reach the feature server.</strong> It may be
          cold-starting — the Cloud Run backend scales to zero when idle
          and can take up to a minute to wake on the next request.
        </p>
        <p className="state-error-detail">{error}</p>
        {onRetry && (
          <button type="button" className="retry-btn" onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    );
  }
  if (empty) {
    return <div className="state">{emptyMessage}</div>;
  }
  return <>{children}</>;
}
