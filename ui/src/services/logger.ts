/**
 * Frontend logger — single funnel for runtime messages with pluggable
 * sinks. Default config installs the console sink + global error handlers
 * so uncaught errors and unhandled-rejection traces hit the same surface
 * as explicit `logger.error()` calls.
 *
 * Design choices:
 *  - No third-party dep. Sinks are tiny, hand-rolled.
 *  - The backend sink batches (size + interval) to avoid one POST per log
 *    line; falls back to `navigator.sendBeacon` on `pagehide` so the last
 *    batch survives tab close.
 *  - All sinks ignore exceptions raised by other sinks — a misbehaving
 *    backend never breaks the console output.
 */

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export interface LogContext {
    [key: string]: unknown;
}

export interface LogRecord {
    level: LogLevel;
    message: string;
    context: LogContext;
    /** Epoch ms — assigned by the logger right before emit. */
    ts: number;
}

export interface LogSink {
    emit(record: LogRecord): void;
    /** Optional: called when the logger requests a final flush (page hide). */
    flush?(): Promise<void> | void;
}

export interface Logger {
    debug(message: string, context?: LogContext): void;
    info(message: string, context?: LogContext): void;
    warn(message: string, context?: LogContext): void;
    error(message: string, context?: LogContext): void;
    /** Add a sink at runtime (e.g. plug in a backend later). */
    addSink(sink: LogSink): void;
    /** Force every sink to flush. Resolves when the slowest sink finishes. */
    flush(): Promise<void>;
}

const LEVEL_RANK: Record<LogLevel, number> = { debug: 10, info: 20, warn: 30, error: 40 };

export interface CreateLoggerOptions {
    sinks?: LogSink[];
    /** Records below this level are dropped. Defaults to 'info'. */
    minLevel?: LogLevel;
}

export function createLogger(opts: CreateLoggerOptions = {}): Logger {
    const sinks: LogSink[] = [...(opts.sinks ?? [])];
    const min = LEVEL_RANK[opts.minLevel ?? 'info'];

    function emit(level: LogLevel, message: string, context: LogContext = {}): void {
        if (LEVEL_RANK[level] < min) return;
        const record: LogRecord = { level, message, context, ts: Date.now() };
        for (const sink of sinks) {
            try {
                sink.emit(record);
            } catch {
                /* a misbehaving sink never silences others */
            }
        }
    }

    return {
        debug: (m, c) => emit('debug', m, c),
        info: (m, c) => emit('info', m, c),
        warn: (m, c) => emit('warn', m, c),
        error: (m, c) => emit('error', m, c),
        addSink: (s) => sinks.push(s),
        async flush(): Promise<void> {
            await Promise.allSettled(sinks.map((s) => Promise.resolve(s.flush?.())));
        },
    };
}

/** Forward records to `console.<level>`. The default sink for dev. */
export const consoleSink: LogSink = {
    emit(record: LogRecord): void {
        const fn =
            record.level === 'error'
                ? console.error
                : record.level === 'warn'
                  ? console.warn
                  : record.level === 'debug'
                    ? console.debug
                    : console.info;
        if (Object.keys(record.context).length > 0) {
            fn.call(console, `[${record.level}] ${record.message}`, record.context);
        } else {
            fn.call(console, `[${record.level}] ${record.message}`);
        }
    },
};

export interface BackendSinkOptions {
    /** Endpoint to POST batched records to. */
    endpoint: string;
    /** Records per batch before forcing a POST. Defaults to 25. */
    batchSize?: number;
    /** Flush every `flushIntervalMs` regardless of fill. Defaults to 5s. */
    flushIntervalMs?: number;
    /** Bearer token, retrieved at flush time so token rotations apply. */
    getToken?: () => string;
    /** Custom fetch — useful for tests. */
    fetchImpl?: typeof fetch;
    /** Use navigator.sendBeacon when available on pagehide. Defaults to true. */
    useBeacon?: boolean;
}

/**
 * Batched POST sink. Queues up to `batchSize` records or flushes every
 * `flushIntervalMs`. On `pagehide` it issues a single sendBeacon so the
 * tail of the queue survives navigation.
 */
export function backendSink(opts: BackendSinkOptions): LogSink {
    const batchSize = opts.batchSize ?? 25;
    const interval = opts.flushIntervalMs ?? 5_000;
    const fetchImpl = opts.fetchImpl ?? (typeof fetch !== 'undefined' ? fetch : undefined);
    const queue: LogRecord[] = [];
    let timer: ReturnType<typeof setInterval> | null = null;

    function startTimer(): void {
        if (timer || typeof setInterval === 'undefined') return;
        timer = setInterval(() => void doFlush(false), interval);
    }
    function stopTimer(): void {
        if (timer) {
            clearInterval(timer);
            timer = null;
        }
    }

    async function doFlush(useBeacon: boolean): Promise<void> {
        if (queue.length === 0) return;
        const batch = queue.splice(0, queue.length);
        const body = JSON.stringify({ records: batch });
        const headers: Record<string, string> = { 'Content-Type': 'application/json' };
        const token = opts.getToken?.() ?? '';
        if (token) headers['Authorization'] = `Bearer ${token}`;

        if (useBeacon && opts.useBeacon !== false && typeof navigator !== 'undefined' && navigator.sendBeacon) {
            try {
                const blob = new Blob([body], { type: 'application/json' });
                navigator.sendBeacon(opts.endpoint, blob);
                return;
            } catch {
                /* fall through to fetch */
            }
        }
        if (!fetchImpl) return;
        try {
            await fetchImpl(opts.endpoint, { method: 'POST', headers, body, keepalive: true });
        } catch {
            // Drop on the floor — we already removed from queue. Backend
            // logs are best-effort; the console sink is the source of truth.
        }
    }

    if (typeof window !== 'undefined') {
        window.addEventListener('pagehide', () => {
            stopTimer();
            void doFlush(true);
        });
    }

    return {
        emit(record: LogRecord): void {
            queue.push(record);
            startTimer();
            if (queue.length >= batchSize) void doFlush(false);
        },
        flush(): Promise<void> {
            return doFlush(false);
        },
    };
}

export interface InstallGlobalHandlersOptions {
    /** Already-installed flag returned so callers can detach. Idempotent. */
    onError?: (event: ErrorEvent) => void;
    onUnhandledRejection?: (event: PromiseRejectionEvent) => void;
}

/**
 * Pipe `window.onerror` + `unhandledrejection` into the logger as `error`
 * records. Returns a detach function so tests / hot-reload can clean up.
 */
export function installGlobalErrorHandlers(logger: Logger, opts: InstallGlobalHandlersOptions = {}): () => void {
    if (typeof window === 'undefined') return () => {};

    const onError = (ev: ErrorEvent): void => {
        logger.error(ev.message || 'window.onerror', {
            source: ev.filename,
            line: ev.lineno,
            col: ev.colno,
            stack: ev.error?.stack,
        });
        opts.onError?.(ev);
    };
    const onRejection = (ev: PromiseRejectionEvent): void => {
        const reason = ev.reason;
        const message =
            (reason instanceof Error ? reason.message : typeof reason === 'string' ? reason : 'unhandledrejection') ??
            'unhandledrejection';
        logger.error(message, {
            stack: reason instanceof Error ? reason.stack : undefined,
            reason,
        });
        opts.onUnhandledRejection?.(ev);
    };

    window.addEventListener('error', onError);
    window.addEventListener('unhandledrejection', onRejection);

    return () => {
        window.removeEventListener('error', onError);
        window.removeEventListener('unhandledrejection', onRejection);
    };
}
