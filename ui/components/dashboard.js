/**
 * Dashboard Component
 */
import { store } from '../services/store.js';

let chartInstance = null;

// 2.4: Threshold line plugin
const thresholdPlugin = {
    id: 'thresholdLine',
    afterDraw(chart) {
        const { ctx, chartArea, scales } = chart;
        const yVal = scales.y.getPixelForValue(500);
        if (yVal < chartArea.top || yVal > chartArea.bottom) return;

        ctx.save();
        ctx.setLineDash([6, 4]);
        ctx.strokeStyle = 'rgba(244, 63, 94, 0.4)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(chartArea.left, yVal);
        ctx.lineTo(chartArea.right, yVal);
        ctx.stroke();

        ctx.setLineDash([]);
        ctx.font = '9px JetBrains Mono, monospace';
        ctx.fillStyle = 'rgba(244, 63, 94, 0.5)';
        ctx.textAlign = 'right';
        ctx.fillText('P99 SLA 500ms', chartArea.right - 4, yVal - 5);
        ctx.restore();
    }
};

export function renderDashboard() {
    const ctx = document.getElementById('mainChart');
    if (!ctx || chartInstance) return;
    // Defer until canvas is actually visible in DOM
    if (!ctx.offsetWidth) {
        requestAnimationFrame(renderDashboard);
        return;
    }

    // 2.3: Gradient fill
    const context2d = ctx.getContext('2d');
    const gradient = context2d.createLinearGradient(0, 0, 0, 400);
    gradient.addColorStop(0, 'rgba(244, 63, 94, 0.15)');
    gradient.addColorStop(0.6, 'rgba(244, 63, 94, 0.03)');
    gradient.addColorStop(1, 'rgba(244, 63, 94, 0)');

    chartInstance = new Chart(ctx, {
        type: 'line',
        plugins: [thresholdPlugin],
        data: {
            labels: Array.from({length: 24}, (_, i) => `${i}h`),
            datasets: [{
                label: 'Latency (ms)',
                data: [412, 420, 395, 410, 415, 450, 480, 520, 510, 490, 450, 420, 415, 410, 405, 420, 430, 460, 450, 440, 420, 415, 410, 420],
                borderColor: '#f43f5e',
                borderWidth: 2,
                fill: true,
                backgroundColor: gradient,
                pointRadius: 0,
                pointHoverRadius: 4,
                pointHoverBackgroundColor: '#f43f5e',
                pointHoverBorderColor: '#fff',
                pointHoverBorderWidth: 2,
                tension: 0.4
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: {
                intersect: false,
                mode: 'index'
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    backgroundColor: 'rgba(0,0,0,0.85)',
                    borderColor: 'rgba(255,255,255,0.1)',
                    borderWidth: 1,
                    titleFont: { family: 'JetBrains Mono, monospace', size: 10 },
                    bodyFont: { family: 'JetBrains Mono, monospace', size: 11 },
                    padding: 10,
                    cornerRadius: 8,
                    displayColors: false,
                    callbacks: {
                        label: (item) => `${item.parsed.y}ms`
                    }
                }
            },
            scales: {
                x: {
                    grid: { display: false },
                    border: { display: true, color: 'rgba(255,255,255,0.08)' },
                    ticks: {
                        color: '#475569',
                        font: { size: 9, family: 'JetBrains Mono, monospace' },
                        maxRotation: 0
                    }
                },
                y: {
                    grid: { color: 'rgba(255,255,255,0.04)', drawBorder: false },
                    border: { display: false },
                    ticks: {
                        color: '#475569',
                        font: { size: 9, family: 'JetBrains Mono, monospace' },
                        padding: 12
                    },
                    suggestedMin: 350,
                    suggestedMax: 600
                }
            }
        }
    });

    initSparklines();
    initTopologyFlow();
}

function initTopologyFlow() {
    const connectors = document.querySelectorAll('.flex-1.flex.items-center.justify-center.relative div.w-full');
    connectors.forEach((conn, idx) => {
        const particle = document.createElement('div');
        particle.className = 'absolute w-1 h-1 rounded-full bg-white shadow-[0_0_8px_#fff] opacity-80';
        particle.style.left = '0%';
        conn.parentElement.appendChild(particle);

        const duration = 2000 + (idx * 500);
        particle.animate([
            { left: '0%', opacity: 0 },
            { left: '20%', opacity: 1 },
            { left: '80%', opacity: 1 },
            { left: '100%', opacity: 0 }
        ], { duration, iterations: Infinity, easing: 'linear' });
    });
}

// 2.1: Unique sparklines per card (different seed per index)
function initSparklines() {
    const patterns = [
        [0.2, 0.5, 0.3, 0.8, 0.6, 1.0],   // Inferences — uptrend
        [0.7, 0.7, 0.3, 0.3, 0.5, 0.5],    // Active Adapters — plateau
        [0.8, 0.6, 0.5, 0.4, 0.3, 0.35],   // FinOps — descending (spend)
    ];

    document.querySelectorAll('polyline').forEach((el, idx) => {
        const h = 20;
        const w = 100;
        const base = patterns[idx % patterns.length];
        const pts = base.map((v, i) => `${i * (w / (base.length - 1))},${h - v * h * 0.8}`);
        el.setAttribute('points', pts.join(' '));
    });

    setInterval(() => {
        document.querySelectorAll('polyline').forEach((el, idx) => {
            const h = 20;
            const w = 100;
            const t = Date.now() / 1000;
            const base = patterns[idx % patterns.length];
            const pts = base.map((v, i) => {
                const jitter = Math.sin(t * 1.2 + i * 2 + idx * 7) * 0.12;
                const y = h - (v + jitter) * h * 0.8;
                return `${i * (w / (base.length - 1))},${Math.max(1, Math.min(h - 1, y))}`;
            });
            el.setAttribute('points', pts.join(' '));
        });
    }, 2000);
}
