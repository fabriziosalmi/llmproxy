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
                borderWidth: 1.5,
                stepped: true, // FAANG level 10x
                fill: true,
                backgroundColor: 'rgba(244, 63, 94, 0.05)',
                pointRadius: 0
            }]
        },
        options: { 
            responsive: true, 
            maintainAspectRatio: false, 
            plugins: { legend: { display: false } },
            scales: { 
                x: { 
                    grid: { display: false }, 
                    ticks: { color: '#64748b', font: { size: 10, family: 'monospace' } } 
                },
                y: { 
                    grid: { color: 'rgba(255,255,255,0.05)', borderDash: [2, 4] }, 
                    ticks: { color: '#64748b', font: { size: 10, family: 'monospace' }, padding: 10 } 
                } 
            }
        }
    });

    initSparklines();
}

function initSparklines() {
    setInterval(() => {
        document.querySelectorAll('polyline').forEach(el => {
            const h = 20;
            const w = 100;
            let pts = [];
            for(let i=0; i<=5; i++){
                pts.push(`${i*(w/5)},${Math.random()*(h*0.8) + (h*0.1)}`);
            }
            el.style.transition = 'all 0.5s ease-in-out';
            el.setAttribute('points', pts.join(' '));
        });
    }, 3000);
}
