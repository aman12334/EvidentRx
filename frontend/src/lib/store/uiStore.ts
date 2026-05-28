import { create } from "zustand";
import { persist } from "zustand/middleware";

interface UIStore {
  sidebarCollapsed: boolean;
  darkMode:         boolean;
  activeWorkspace:  string;
  toggleSidebar:    () => void;
  toggleDarkMode:   () => void;
  setWorkspace:     (workspace: string) => void;
}

export const useUIStore = create<UIStore>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      darkMode:         false,
      activeWorkspace:  "dashboard",

      toggleSidebar:  () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      toggleDarkMode: () => set((s) => ({ darkMode: !s.darkMode })),
      setWorkspace:   (workspace) => set({ activeWorkspace: workspace }),
    }),
    { name: "evidentrx-ui" }
  )
);
