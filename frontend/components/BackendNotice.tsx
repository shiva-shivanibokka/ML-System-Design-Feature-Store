"use client";
import { useBackendHealth } from "./useBackendHealth";

const RESULTS_URL =
  "https://github.com/shiva-shivanibokka/ML-System-Design-Feature-Store/blob/main/RESULTS.md";

/**
 * Says so, once, at the top of the page, when the backend is not there.
 *
 * Every panel below this fetches from the feature server. When that server is
 * gone they each show a retry button, which invites a visitor to keep pressing
 * it. This states the situation before that happens and points at the part of
 * the project that does not need a backend: the written evaluation, whose
 * numbers were measured when the service was running and are still true.
 *
 * Rendered from the live health check, not hardcoded, so it disappears by
 * itself if the backend comes back.
 */
export default function BackendNotice() {
  const health = useBackendHealth();
  if (health !== "offline") return null;

  return (
    <p className="notice" role="status">
      <b>The backend for this demo is switched off.</b> It ran on Google Cloud Run under a
      billing account that has since been closed, so the panels below cannot load and
      retrying will not help. The measurements do not depend on it —{" "}
      <a href={RESULTS_URL} target="_blank" rel="noopener noreferrer">
        read the recorded evaluation
      </a>
      , which documents six defects found in this repository, including the two serving
      paths returning different values for the same entity and an advertised ROC-AUC that
      could not be reproduced because the training script did not run.
    </p>
  );
}
