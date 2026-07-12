"use client";
import { useCallback, useRef, useState } from "react";

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

  // Bumped on every run() call; a settling promise only updates state if it's
  // still the most recent request — guards against a slow earlier response
  // overwriting fresher state (e.g. a Cloud Run cold start racing a retry).
  const gen = useRef(0);

  const run = useCallback((promise: Promise<T>) => {
    const mine = ++gen.current;
    setState({ data: null, loading: true, error: null });
    promise.then(
      (data) => {
        if (mine === gen.current) setState({ data, loading: false, error: null });
      },
      (err: unknown) => {
        if (mine === gen.current)
          setState({
            data: null,
            loading: false,
            error: err instanceof Error ? err.message : String(err),
          });
      }
    );
  }, []);

  return { ...state, run };
}
