# LLMPROXY - Features List & Architecture Map

Questo documento mappa tutte le features introdotte nel sistema LLMPROXY, aggregate per layer tecnologico, per consolidare e favorire una transizione verso architetture SOA "FAANG 10x level".

## Frontend & UI (User Interface)

- **Single Page Application (SPA) Nativa:** React 18 + Vite (Vanilla JS Modules) senza reload per navigazione istantanea.
- **Design System "Glassmorphic 2030":** Interfaccia premium dark-mode sfumata con sub-pixel borders ed effetti di trasparenza spinta (backdrop-blur-3xl).
- **Data Density & Sparklines Arrays:** Micro-grafici inline in SVG, logit heatmaps ad altissima densità per minimizzare i whitespaces.
- **Topology Map:** Visualizzazione interattiva della catena di rete Proxy -> Guardrail -> Endpoint per consapevolezza del flusso.
- **Command Palette Globale (Cmd+K):** Un overlay onnipresente e command-driven per scorciatoie veloci e azioni di emergenza.
- **Interactive Terminal Logger:** Streaming JSON dal vivo, filtrabile tramite input in stile Unix `grep`, scroll dinamico e hover-freeze.
- **Neural Chat Interface:** Conversazione chat arricchita da metadata micro-telemetrici (TTFT, costi) esteticamente "Palantir-like".
- **Dynamic Branching UI:** Placeholder nativi per flussi di split-testing prompt-driven.
- **Cinema Mode (KeyF Focus):** Concentrazione pura sul canvas centrale e offuscamento di sidebar e header system.
- **Fluid Layout & CSS Keyframes:** Spinner rimossi a favore di skeleton loaders dinamici (pulse) e micro-interazioni da 60fps.

## UX (User Experience)

- **Optimistic UI Interactivity:** I clic dei bottoni non attendono chiamate API visibilmente, le interfacce restituiscono frame responsivi in 0.2s scalando attivamente (transform scale).
- **Sonic Physics & Haptic Feedback:** HTML5 Audio context sintetizzato on-the-fly (`oscillator`) per un timido e rassicurante click-bloop ad ogni interazione chiave.
- **Event Ticker Marquee:** Un ticker log sempre in moto, per monitoraggio passivo di eventi di sicurezza senza dover aprire il terminale.
- **Slide-over Diagnostics Drawer:** Interfaccia a scomparsa laterale destra che espone l'utilizzo CPU / DB senza invadere lo spazio principale.
- **Redaction Animations:** Se il guardrail interviene, invece di messaggi d'errore brutali, interviene una vistosa animazione CSS che brucia e re-datta la stringa offendente visivamente.
- **Persistent State Controls:** Salvataggio delle preferenze layout e session state master via config proxy.

## Backend & API

- **FastAPI Core Asincrono:** L'intero webserver gira in asincrono su processore UVloop, per tollerare traffico simultaneo intenso.
- **Store Architetturale SQLite Async:** Rimpiazzato file JSON basici in locale con un database AIO-SQLite persistente (`data/store.db`) con gestione in-memory object cache per i setting ultra-fast.
- **Event-Driven Telemetry Node (SSE):** Push realtime dei log dal server al frontend attraverso canali *Server-Sent Events*. Nessun long-polling client.
- **Rotator Engine (Round Robin / Priority):** Un motore di dispacciamento dinamico delle chiamate per non incagliare i modelli rate-limited, failover configurabili.

## Security & Guardrails

- **Sovereign Encryption Layer:** Placeholder implementato per la crittografia dei prompt prima di uscire in reti pubbliche (tunneling PII).
- **Cognitive Threat Detection:** Filtro logico implementabile che intercetta anomalie strutturali, CWE rule e prompt injection prima del dispatching LLM.
- **Transparent Logging:** Qualsiasi richiesta subisce la biforcazione "Log/Emit/Relay", fornendo la "scatola nera" totale ai team di sec-ops.

## Performance & Usability

- **Semantic Cache System:** Pre-cache hit basate non solo sulla sintassi pura ma sul limite semantico o ID similarity vettoriale (threshold config >=0.95).
- **Zero-Refresh Pipeline:** Nessuna navigazione provoca reload di script e stylesheet. 
- **VLLM Engine Offline:** Disaccoppiamento del load model locale al bisogno. 

## DX (Developer Experience) & Sviluppo

- **Plugin Middleware Pattern:** Tutti i wrapper (logging, caching, security routing) sono moduli separati (core/semantic_cache.py, core/load_predictor.py ecc.) estendibili.
- **CLI Arg Parsing & Daemon Readiness:** Prontuario multi-stage via `python main.py` ma estendibile e containerizzabile out of the box su pm2/systemd.
- **OpenTelemetry Instrumentation (OTEL):** Registrazione automatica asincrona di path call latency / spanning trace, pronte all'export per Jaeger/Datadog.

---
**Nota finale sulla modularità (FAANG 10x):**

Tutte le viste frontend e i task logici Backend sono stati disaccoppiati in componenti separati (sia i mod di `/ui/components/*.js` che `/core/*` su Python). Creare nuovi adapters per nuovi Endpoint LLM o nuovi tool richiede l'iniezione locale in `/core/mcp_hub.py` tramite standard interface definition senza modificare routine preesistenti (Open/Closed Principle SOLID).
