import { create } from 'zustand';

interface TelemetryEvent {
  type: string;
  timestamp: string;
  data: any;
}

interface HUDState {
  events: TelemetryEvent[];
  stats: {
    rps: number;
    ttft: number;
    cost: number;
    status: 'ONLINE' | 'OFFLINE';
  };
  activeTab: string;
  isSidebarOpen: boolean;
  
  addEvent: (event: TelemetryEvent) => void;
  updateStats: (updates: Partial<HUDState['stats']>) => void;
  setTab: (tab: string) => void;
  toggleSidebar: () => void;
}

export const useHUDStore = create<HUDState>((set) => ({
  events: [],
  stats: {
    rps: 0,
    ttft: 0,
    cost: 0,
    status: 'ONLINE',
  },
  activeTab: 'dashboard',
  isSidebarOpen: true,

  addEvent: (event) => set((state) => ({ 
    events: [event, ...state.events].slice(0, 100) 
  })),
  
  updateStats: (updates) => set((state) => ({ 
    stats: { ...state.stats, ...updates } 
  })),
  
  setTab: (tab) => set({ activeTab: tab }),
  toggleSidebar: () => set((state) => ({ isSidebarOpen: !state.isSidebarOpen })),
}));
