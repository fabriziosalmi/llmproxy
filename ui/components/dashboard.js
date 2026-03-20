/**
 * Dashboard Component
 */
import { store } from '../services/store.js';

let chartInstance = null;

export function renderDashboard() {
    const ctx = document.getElementById('mainChart');
    if (!ctx || chartInstance) return; // Only init once for now
    
    chartInstance = new Chart(ctx.getContext('2d'), {
        type: 'line',
        data: {
            labels: Array.from({length: 24}, (_, i) => `${i}h`),
            datasets: [{
                label: 'Latency (ms)',
                data: [412, 420, 395, 410, 415, 450, 480, 520, 510, 490, 450, 420, 415, 410, 405, 420, 430, 460, 450, 440, 420, 415, 410, 420],
                borderColor: '#f43f5e',
                borderWidth: 2,
                stepped: false, // Cleaner look
                fill: true,
                backgroundColor: 'rgba(244, 63, 94, 0.05)',
                pointRadius: 0,
                tension: 0.4 // Smooth curve
            }]
        },
        options: { 
            responsive: true, 
            maintainAspectRatio: false, 
            plugins: { legend: { display: false } },
            scales: { 
                x: { 
                    border: { display: true, color: 'rgba(255,255,255,0.1)' }, // X-axis Baseline
                    grid: { display: false }, 
                    ticks: { color: '#64748b', font: { size: 10, family: 'monospace' }, padding: 10 } 
                },
                y: { 
                    grid: { color: 'rgba(255,255,255,0.05)', borderDash: [2, 4] }, 
                    ticks: { color: '#64748b', font: { size: 10, family: 'monospace' }, padding: 15 } 
                } 
            }
        }
    });

    initSparklines();
    updateLatencyColor();
}

function updateLatencyColor() {
    // 6. DECLINE in latency is GOOD (Green)
    const latencyIndicator = document.querySelector('div.glass:nth-child(3) span.text-rose-400');
    if (latencyIndicator && latencyIndicator.innerText.includes('DECLINE')) {
        latencyIndicator.classList.remove('text-rose-400');
        latencyIndicator.classList.add('text-emerald-400');
    }
}

function initSparklines() {
    // 5. Unique Sparklines logic
    document.querySelectorAll('polyline').forEach((el, idx) => {
        const h = 20;
        const w = 100;
        const seed = idx * 10;
        const pts = [];
        for(let i=0; i<=5; i++){
            const y = (Math.sin((i + seed) * 0.5) + 1) * (h * 0.4) + (h * 0.1);
            pts.push(`${i*(w/5)},${y}`);
        }
        el.setAttribute('points', pts.join(' '));
    });

    setInterval(() => {
        document.querySelectorAll('polyline').forEach((el, idx) => {
            const h = 20;
            const w = 100;
            const seed = Date.now() / 1000 + idx;
            let pts = [];
            for(let i=0; i<=5; i++){
                const y = (Math.sin((i * 1.5) + seed) + 1) * (h * 0.4) + (h * 0.1);
                pts.push(`${i*(w/5)},${y}`);
            }
            el.style.transition = 'all 1s ease-in-out';
            el.setAttribute('points', pts.join(' '));
        });
    }, 2000);
}
