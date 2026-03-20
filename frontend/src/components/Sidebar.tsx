import React from 'react';
import { useHUDStore } from '../store';
import { 
  BarChart3, Logs, Network, Settings2, 
  ShieldAlert, Database, KeyRound, Menu 
} from 'lucide-react';

const SidebarItem: React.FC<{ 
  icon: React.ReactNode; 
  label: string; 
  id: string; 
  active?: boolean;
}> = ({ icon, label, id, active }) => {
  const setTab = useHUDStore((state) => state.setTab);
  
  return (
    <button 
      onClick={() => setTab(id)}
      className={`w-full flex items-center gap-3 px-4 py-2.5 text-[11px] font-semibold tracking-wide transition-all group ${
        active 
          ? 'text-white bg-white/5 border-r-2 border-accent' 
          : 'text-zinc-500 hover:text-zinc-300 hover:bg-white/5'
      }`}
    >
      <div className={`${active ? 'text-accent' : 'text-zinc-500 group-hover:text-zinc-300'}`}>
        {icon}
      </div>
      <span className="uppercase">{label}</span>
    </button>
  );
};

export const Sidebar: React.FC = () => {
  const { activeTab, toggleSidebar } = useHUDStore();
  
  return (
    <aside className="w-56 h-screen border-r border-white/10 bg-zinc-950 flex flex-col pt-4">
      <div className="px-6 mb-8 flex items-center justify-between">
        <span className="text-sm font-black tracking-tighter text-white">LLMPROXY</span>
        <button onClick={toggleSidebar} className="text-zinc-600 hover:text-white transition-colors">
          <Menu size={16} />
        </button>
      </div>

      <nav className="flex-1 space-y-8">
        <div>
          <h3 className="px-6 text-[9px] font-bold text-zinc-600 mb-2 tracking-widest uppercase">Oversight</h3>
          <SidebarItem id="dashboard" label="Dashboard" icon={<BarChart3 size={16} />} active={activeTab === 'dashboard'} />
          <SidebarItem id="logs" label="Trace Logs" icon={<Logs size={16} />} active={activeTab === 'logs'} />
        </div>

        <div>
          <h3 className="px-6 text-[9px] font-bold text-zinc-600 mb-2 tracking-widest uppercase">Routing & Models</h3>
          <SidebarItem id="registry" label="Registry" icon={<Network size={16} />} active={activeTab === 'registry'} />
          <SidebarItem id="endpoints" label="Endpoints" icon={<Settings2 size={16} />} active={activeTab === 'endpoints'} />
        </div>

        <div>
          <h3 className="px-6 text-[9px] font-bold text-zinc-600 mb-2 tracking-widest uppercase">Security & Rules</h3>
          <SidebarItem id="guardrails" label="Guardrails" icon={<ShieldAlert size={16} />} active={activeTab === 'guardrails'} />
          <SidebarItem id="cache" label="Semantic Cache" icon={<Database size={16} />} active={activeTab === 'cache'} />
        </div>

        <div>
          <h3 className="px-6 text-[9px] font-bold text-zinc-600 mb-2 tracking-widest uppercase">Access Control</h3>
          <SidebarItem id="keys" label="Virtual Keys" icon={<KeyRound size={16} />} active={activeTab === 'keys'} />
        </div>
      </nav>

      <div className="p-4 mt-auto border-t border-white/5 bg-zinc-900/40">
        <div className="flex items-center gap-3 px-2">
          <div className="w-8 h-8 rounded bg-zinc-800 border border-white/10 flex items-center justify-center text-[10px] font-bold text-zinc-500">
            ADMIN
          </div>
          <div className="flex flex-col">
            <span className="text-[10px] font-bold text-zinc-100 uppercase">Fabrizio</span>
            <span className="text-[9px] font-medium text-zinc-600 lowercase">fab@llmproxy.io</span>
          </div>
        </div>
      </div>
    </aside>
  );
};
