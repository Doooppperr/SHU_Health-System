const rawCommit = String(import.meta.env.VITE_RELEASE_COMMIT || "").trim();

export const BUILD_INFO = Object.freeze({
  releaseCommit: rawCommit,
  shortCommit: rawCommit ? rawCommit.slice(0, 8) : "dev",
});

export function buildLabel() {
  return `版本 ${BUILD_INFO.shortCommit}`;
}
