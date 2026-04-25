import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { backendSink, consoleSink, createLogger, installGlobalErrorHandlers, type LogRecord } from './logger';

describe('createLogger', () => {
    it('forwards records to every registered sink', () => {
        const captured: LogRecord[] = [];
        const sink = { emit: (r: LogRecord) => captured.push(r) };
        const logger = createLogger({ sinks: [sink], minLevel: 'debug' });
        logger.info('hello', { user: 'fab' });
        logger.warn('careful');
        logger.error('boom', { code: 500 });
        expect(captured).toHaveLength(3);
        expect(captured[0]).toMatchObject({ level: 'info', message: 'hello', context: { user: 'fab' } });
        expect(captured[2]?.context).toEqual({ code: 500 });
    });

    it('drops records below minLevel', () => {
        const captured: LogRecord[] = [];
        const logger = createLogger({ sinks: [{ emit: (r) => captured.push(r) }], minLevel: 'warn' });
        logger.debug('skip');
        logger.info('skip');
        logger.warn('keep');
        logger.error('keep');
        expect(captured.map((r) => r.level)).toEqual(['warn', 'error']);
    });

    it('a throwing sink does not silence the others', () => {
        const captured: LogRecord[] = [];
        const logger = createLogger({
            sinks: [
                {
                    emit: () => {
                        throw new Error('bad sink');
                    },
                },
                { emit: (r) => captured.push(r) },
            ],
        });
        logger.info('still works');
        expect(captured).toHaveLength(1);
    });

    it('addSink lets you plug in a sink at runtime', () => {
        const captured: LogRecord[] = [];
        const logger = createLogger();
        logger.info('before-sink'); // dropped, no sinks
        logger.addSink({ emit: (r) => captured.push(r) });
        logger.info('after-sink');
        expect(captured.map((r) => r.message)).toEqual(['after-sink']);
    });

    it('flush() awaits each sink flush', async () => {
        const order: string[] = [];
        const logger = createLogger({
            sinks: [
                {
                    emit: () => {},
                    flush: async () => {
                        await Promise.resolve();
                        order.push('a');
                    },
                },
                {
                    emit: () => {},
                    flush: () => {
                        order.push('b');
                    },
                },
            ],
        });
        await logger.flush();
        expect(order.sort()).toEqual(['a', 'b']);
    });
});

describe('consoleSink', () => {
    it('routes by level to the matching console.* method', () => {
        const info = vi.spyOn(console, 'info').mockImplementation(() => {});
        const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
        const error = vi.spyOn(console, 'error').mockImplementation(() => {});

        consoleSink.emit({ level: 'info', message: 'a', context: {}, ts: 0 });
        consoleSink.emit({ level: 'warn', message: 'b', context: {}, ts: 0 });
        consoleSink.emit({ level: 'error', message: 'c', context: { x: 1 }, ts: 0 });

        expect(info).toHaveBeenCalled();
        expect(warn).toHaveBeenCalled();
        expect(error).toHaveBeenCalled();
        // The error call must include the context payload.
        expect(error.mock.calls.at(-1)?.[1]).toEqual({ x: 1 });

        info.mockRestore();
        warn.mockRestore();
        error.mockRestore();
    });
});

describe('backendSink', () => {
    beforeEach(() => {
        vi.useFakeTimers();
    });
    afterEach(() => {
        vi.useRealTimers();
    });

    it('flushes when batchSize is reached', async () => {
        const fetchImpl = vi.fn().mockResolvedValue({ ok: true } as Response);
        const sink = backendSink({ endpoint: '/api/v1/logs/client', batchSize: 3, fetchImpl });
        sink.emit({ level: 'info', message: 'a', context: {}, ts: 1 });
        sink.emit({ level: 'info', message: 'b', context: {}, ts: 2 });
        expect(fetchImpl).not.toHaveBeenCalled();
        sink.emit({ level: 'info', message: 'c', context: {}, ts: 3 });
        // doFlush is async — let microtasks run.
        await Promise.resolve();
        await Promise.resolve();
        expect(fetchImpl).toHaveBeenCalledTimes(1);
        const body = fetchImpl.mock.calls[0]![1]?.body as string;
        const parsed = JSON.parse(body);
        expect(parsed.records).toHaveLength(3);
    });

    it('flushes on the interval', async () => {
        const fetchImpl = vi.fn().mockResolvedValue({ ok: true } as Response);
        const sink = backendSink({ endpoint: '/log', batchSize: 100, flushIntervalMs: 1_000, fetchImpl });
        sink.emit({ level: 'info', message: 'a', context: {}, ts: 1 });
        vi.advanceTimersByTime(1_000);
        await Promise.resolve();
        expect(fetchImpl).toHaveBeenCalledTimes(1);
    });

    it('attaches the bearer token from getToken()', async () => {
        const fetchImpl = vi.fn().mockResolvedValue({ ok: true } as Response);
        const sink = backendSink({ endpoint: '/log', batchSize: 1, fetchImpl, getToken: () => 'TK-1' });
        sink.emit({ level: 'info', message: 'x', context: {}, ts: 1 });
        await Promise.resolve();
        await Promise.resolve();
        const headers = fetchImpl.mock.calls[0]![1]?.headers as Record<string, string>;
        expect(headers.Authorization).toBe('Bearer TK-1');
    });

    it('a fetch failure does not throw out of emit()', async () => {
        const fetchImpl = vi.fn().mockRejectedValue(new Error('offline'));
        const sink = backendSink({ endpoint: '/log', batchSize: 1, fetchImpl });
        expect(() => sink.emit({ level: 'error', message: 'x', context: {}, ts: 1 })).not.toThrow();
        await Promise.resolve();
        await Promise.resolve();
        expect(fetchImpl).toHaveBeenCalled();
    });
});

describe('installGlobalErrorHandlers', () => {
    it('routes window error events through logger.error', () => {
        const captured: LogRecord[] = [];
        const logger = createLogger({ sinks: [{ emit: (r) => captured.push(r) }] });
        const detach = installGlobalErrorHandlers(logger);
        const ev = new ErrorEvent('error', { message: 'window-bad', filename: 'a.js', lineno: 12, colno: 4 });
        window.dispatchEvent(ev);
        expect(captured.at(-1)?.level).toBe('error');
        expect(captured.at(-1)?.message).toContain('window-bad');
        detach();
    });

    it('detach() stops further events from reaching the logger', () => {
        const captured: LogRecord[] = [];
        const logger = createLogger({ sinks: [{ emit: (r) => captured.push(r) }] });
        const detach = installGlobalErrorHandlers(logger);
        detach();
        window.dispatchEvent(new ErrorEvent('error', { message: 'should-not-fire' }));
        expect(captured).toHaveLength(0);
    });
});
