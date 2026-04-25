/**
 * Threat chart — 24-hour bar chart of blocked vs passed events. Uses the
 * Chart.js global loaded via vendor/chart.min.js (declared as ambient).
 *
 * Listens to the `llmproxy:threat-event` CustomEvent dispatched by the
 * EventFeed so the histogram updates as security events stream in.
 */
import type { SecurityEvent } from '../types';

declare const Chart: new (ctx: CanvasRenderingContext2D, config: ChartConfig) => ChartInstance;

interface ChartConfig {
    type: 'bar';
    data: {
        labels: string[];
        datasets: Array<{
            label: string;
            data: number[];
            backgroundColor: string;
            borderColor: string;
            borderWidth: number;
            borderRadius: number;
        }>;
    };
    options: Record<string, unknown>;
}

interface ChartInstance {
    data: {
        labels: string[];
        datasets: Array<{ data: number[] }>;
    };
    update: (mode?: string) => void;
    destroy: () => void;
}

export interface ThreatChartHandle {
    /** Manually push an event into the chart (used by tests). */
    push(entry: SecurityEvent): void;
    destroy(): void;
}

function isBlocked(entry: SecurityEvent): boolean {
    const level = (entry.level || '').toUpperCase();
    if (level === 'SECURITY') return true;
    return (entry.message || '').toUpperCase().includes('BLOCK');
}

export function mountThreatChart(canvas: HTMLCanvasElement): ThreatChartHandle | null {
    if (typeof Chart === 'undefined') return null;
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;

    const labels = Array.from({ length: 24 }, (_, i) => `${i}:00`);
    const blocked = new Array<number>(24).fill(0);
    const passed = new Array<number>(24).fill(0);

    const chart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                {
                    label: 'Blocked',
                    data: blocked,
                    backgroundColor: 'rgba(244, 63, 94, 0.4)',
                    borderColor: 'rgba(244, 63, 94, 0.8)',
                    borderWidth: 1,
                    borderRadius: 4,
                },
                {
                    label: 'Passed',
                    data: passed,
                    backgroundColor: 'rgba(52, 211, 153, 0.2)',
                    borderColor: 'rgba(52, 211, 153, 0.5)',
                    borderWidth: 1,
                    borderRadius: 4,
                },
            ],
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    labels: { color: '#64748b', font: { size: 10, family: 'JetBrains Mono' } },
                },
            },
            scales: {
                x: {
                    stacked: true,
                    ticks: { color: '#334155', font: { size: 9 } },
                    grid: { color: 'rgba(255,255,255,0.03)' },
                },
                y: {
                    stacked: true,
                    ticks: { color: '#334155', font: { size: 9 } },
                    grid: { color: 'rgba(255,255,255,0.03)' },
                },
            },
        },
    });

    function push(entry: SecurityEvent): void {
        const hour = new Date().getHours();
        if (isBlocked(entry)) chart.data.datasets[0]!.data[hour]! += 1;
        else chart.data.datasets[1]!.data[hour]! += 1;
        chart.update('none');
    }

    const onEvent = (ev: Event): void => {
        const detail = (ev as CustomEvent<SecurityEvent>).detail;
        if (!detail) return;
        push(detail);
    };
    if (typeof window !== 'undefined') window.addEventListener('llmproxy:threat-event', onEvent);

    return {
        push,
        destroy(): void {
            if (typeof window !== 'undefined') window.removeEventListener('llmproxy:threat-event', onEvent);
            chart.destroy();
        },
    };
}
