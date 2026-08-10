import { create } from "zustand";

import type { AgentToolCall } from "@/features/editor/agentic-editing";
import type { SandboxSnapshot } from "@/features/editor/non-destructive-history";

export interface AgentProposal {
  instruction: string;
  toolCalls: AgentToolCall[];
  snapshot: SandboxSnapshot;
  explanation?: string | null;
  createdAt: number;
}

interface AgentProposalState {
  proposal: AgentProposal | null;
  setProposal: (proposal: AgentProposal) => void;
  clearProposal: () => void;
}

export const useAgentProposalStore = create<AgentProposalState>((set) => ({
  proposal: null,
  setProposal: (proposal) => set({ proposal }),
  clearProposal: () => set({ proposal: null }),
}));
