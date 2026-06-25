// Compatibility re-export. Canonical hook is `./useSessionState`.
// New imports should go through there directly; this file exists so
// existing components don't all need rewriting in one pass.
export { useSessionState as useUiState } from "./useSessionState";
