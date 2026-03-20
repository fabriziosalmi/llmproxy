import React from 'react';
import { useHUDStore } from '../store';
import { Search, Zap, Activity, DollarSign } from 'lucide-react';

export const TopBar: React.FC = () => {
  const { stats } = useHUDStore();
  
  return (
    <header className="h-12 border-b border-white/10 bg-zinc-950/80 backdrop-blur-md flex items-center justify-between px-6 sticky top-0 z-50">
      <div className="flex items-center gap-6">
        <div className="flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${stats.status === 'ONLINE' ? 'bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)] animate-pulse' : 'bg-red-500'}`} />
          <span className="text-[11px] font-bold tracking-widest text-zinc-400">SYS_HUD_{stats.status}</span>
        </div>
        
        <div className="h-4 w-px bg-white/10 mx-2" />
        
        <div className="flex items-center gap-6 text-xs font-medium text-zinc-400">
          <div className="flex items-center gap-1.5">
            <Activity size={14} className="text-accent" />
            <span>{stats.rps} REQ/S</span>
          </div>
          <div className="flex items-center gap-1.5">
            <Zap size={14} className="text-yellow-500" />
            <span>{stats.ttft}MS TTFT</span>
          </div>
          <div className="flex items-center gap-1.5">
            <DollarSign size={14} className="text-green-500" />
            <span>${stats.cost.toFixed(2)} / DAY</span>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <div className="relative group">
          <Search size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-500" />
          <input 
            type="text" 
            placeholder="CMD + K TO DISPATCH..." 
            className="bg-zinc-900/50 border border-white/10 rounded-md py-1.5 pl-9 pr-4 text-[11px] w-64 focus:outline-none focus:border-accent/40 transition-all placeholder:text-zinc-600"
          />
        </div>
        
        <div className="flex items-center gap-2 px-3 py-1 rounded bg-accent/10 border border-accent/20 text-accent text-[10px] font-bold tracking-tighter">
          OLYMPUS_v1.0
        </div>
      </div>
    </header>
  );
};
