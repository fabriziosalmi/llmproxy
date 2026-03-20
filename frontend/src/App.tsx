import { TopBar } from './components/TopBar';
import { Sidebar } from './components/Sidebar';
import { Terminal } from './components/Terminal';
import { useTelemetryStream } from './lib/telemetry';

function App() {
  // Activate real-time telemetry
  useTelemetryStream();

  return (
    <div className="flex bg-background text-foreground h-screen overflow-hidden">
      <Sidebar />
      
      <div className="flex-1 flex flex-col relative overflow-hidden">
        <TopBar />
        
        <main className="flex-1 p-8 overflow-y-auto pb-64">
          <div className="max-w-7xl mx-auto">
            {/* Stage content would go here based on activeTab */}
            <div className="flex flex-col gap-8">
              <div className="flex flex-col gap-2">
                <h1 className="text-2xl font-black italic tracking-tighter uppercase text-white">System Executive Dashboard</h1>
                <p className="text-zinc-500 text-sm">Operational HUD for the OLYMPUS LLMPROXY network.</p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                {[1, 2, 3].map((i) => (
                  <div key={i} className="glass-panel p-6 rounded-lg h-32 flex flex-col justify-between group hover:border-accent/40 transition-all cursor-crosshair">
                    <div className="text-[10px] font-bold text-zinc-500 tracking-widest uppercase">Node_Delta_0{i}</div>
                    <div className="flex items-end justify-between">
                      <div className="text-2xl font-black text-white italic">99.9%</div>
                      <div className="text-[10px] text-zinc-600 font-bold uppercase">Uptime_v1.0</div>
                    </div>
                  </div>
                ))}
              </div>

              <div className="glass-panel rounded-lg overflow-hidden">
                <div className="h-10 border-b border-white/5 flex items-center px-6 bg-zinc-900/40">
                  <span className="text-[10px] font-bold text-zinc-500 tracking-widest uppercase">Active Model Registry Scan</span>
                </div>
                <div className="p-12 flex items-center justify-center text-zinc-700 italic text-sm">
                  Registry view is currently optimizing for Shadow-ops...
                </div>
              </div>
            </div>
          </div>
        </main>

        <Terminal />
      </div>
    </div>
  );
}

export default App;
