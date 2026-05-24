import { useCoAgent } from "@copilotkit/react-core";

import { AGENT_NAME, EMPTY_UI_STATE, type UiState } from "../state/UiState";

// Single seam between CopilotKit's shared-state primitive and the rest of the
// app. Every map / card / chat component reads the agent's authoritative
// UiState through this hook; write-back (e.g. card-click → setState({active_id}))
// also goes through here. If we ever swap CopilotKit for assistant-ui this is
// the only file that changes.
export function useUiState() {
  return useCoAgent<UiState>({
    name: AGENT_NAME,
    initialState: EMPTY_UI_STATE,
  });
}
