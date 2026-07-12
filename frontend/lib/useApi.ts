"use client";
import { useCallback, useState } from "react";

interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

/**
 * Minimal fetch-state hook shared by every dashboard panel: tracks
 * loading/error/data for a promise-returning call and exposes `run` to
 * (re)trigger it — on mount via useEffect, or on demand (e.g. form submit).
 */
export function useApi<T>(initialLoading = true) {
  const [state, setState] = useState<ApiState<T>>({
    data: null,
    loading: initialLoading,
    error: null,
  });

  const run = useCallback((promise: Promise<T>) => {
    setState({ data: null, loading: true, error: null });
    promise.then(
      (data) => setState({ data, loading: false, error: null }),
      (err: unknown) =>
        setState({
          data: null,
          loading: false,
          error: err instanceof Error ? err.message : String(err),
        })
    );
  }, []);

  return { ...state, run };
}
