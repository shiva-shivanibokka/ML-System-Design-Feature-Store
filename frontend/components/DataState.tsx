"use client";

/**
 * Shared loading / error / empty presentation for every panel.
 *
 * The error copy used to assert a cold start: "it may be cold-starting — the
 * Cloud Run backend scales to zero when idle and can take up to a minute to
 * wake". That was a guess about the cause, and once the backend was switched
 * off for good it was the wrong guess, repeated in every panel, next to a
 * retry button that could not succeed.
 *
 * A panel cannot tell a cold start from a dead service, so it no longer claims
 * to. It reports what it knows -- the request failed -- and defers the reason
 * to `BackendNotice`, which is driven by the health check and does know.
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
          <strong>Couldn&rsquo;t reach the feature server.</strong> If the
          backend is cold-starting this will succeed on a retry; if it is
          offline, the notice at the top of the page says so.
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
