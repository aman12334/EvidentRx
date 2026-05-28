import { create } from "zustand";
import type { CaseStatus, InvestigationCase } from "@/lib/types/investigation";

interface InvestigationStore {
  selectedCaseId:   string | null;
  queueStatusFilter: CaseStatus | null;
  queuePage:        number;
  setSelectedCase:  (id: string | null) => void;
  setStatusFilter:  (status: CaseStatus | null) => void;
  setQueuePage:     (page: number) => void;
  reset:            () => void;
}

export const useInvestigationStore = create<InvestigationStore>((set) => ({
  selectedCaseId:    null,
  queueStatusFilter: null,
  queuePage:         1,

  setSelectedCase:  (id) => set({ selectedCaseId: id }),
  setStatusFilter:  (status) => set({ queueStatusFilter: status, queuePage: 1 }),
  setQueuePage:     (page) => set({ queuePage: page }),
  reset:            () => set({ selectedCaseId: null, queueStatusFilter: null, queuePage: 1 }),
}));
