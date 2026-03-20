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
            labels: Array.from({length: 12}, (_, i) => `${i*2}h`),
            datasets: [{
                data: [0.4, 0.45, 0.38, 0.42, 0.5, 0.41, 0.39, 0.35, 0.42, 0.48, 0.45, 0.42],
                borderColor: '#007aff',
                borderWidth: 2,
                tension: 0.4,
                fill: true,
                backgroundColor: 'rgba(0, 122, 255, 0.05)',
                pointRadius: 0
            }]
        },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } },
            scales: { x: { grid: { display: false }, ticks: { color: '#48484a', font: { size: 9 } } },
                      y: { grid: { color: 'rgba(255,255,255,0.02)' }, ticks: { color: '#48484a', font: { size: 9 } } } }
        }
    });
}
