import React from 'react';
import { useHUDStore } from '../store';
import { Terminal as TerminalIcon, X } from 'lucide-react';

export const Terminal: React.FC = () => {
  const { events } = useHUDStore();
  const [isOpen, setIsOpen] = React.useState(true);
  
  if (!isOpen) return (
    <button 
      onClick={() => setIsOpen(true)}
      className="fixed bottom-6 right-6 p-3 bg-zinc-900 border border-white/10 rounded-full shadow-lg text-zinc-400 hover:text-white transition-all"
    >
      <TerminalIcon size={20} />
    </button>
  );

  return (
    <div className="fixed bottom-0 left-56 right-0 h-48 bg-black/90 backdrop-blur-xl border-t border-white/10 flex flex-col z-40">
      <div className="h-8 border-b border-white/5 flex items-center justify-between px-6 bg-zinc-900/50">
        <div className="flex items-center gap-2">
          <TerminalIcon size={12} className="text-zinc-500" />
          <span className="text-[10px] font-bold text-zinc-500 tracking-widest uppercase">Live Trace Stream</span>
        </div>
        <button onClick={() => setIsOpen(false)} className="text-zinc-600 hover:text-white">
          <X size={14} />
        </button>
      </div>
      
      <div className="flex-1 overflow-y-auto p-4 font-mono text-[11px] leading-relaxed">
        {events.length === 0 ? (
          <div className="text-zinc-800 italic">Waiting for telemetry heartbeat...</div>
        ) : (
          events.map((event, i) => (
            <div key={i} className="flex gap-4 mb-1">
              <span className="text-zinc-700">[{new Date(event.timestamp).toLocaleTimeString()}]</span>
              <span className={`font-bold ${
                event.type.startsWith('proxy.request') ? 'text-blue-500' :
                event.type.includes('security') ? 'text-red-500' : 
                'text-zinc-400'
              }`}>{event.type.toUpperCase()}</span>
              <span className="text-zinc-300">{JSON.stringify(event.data)}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
};
