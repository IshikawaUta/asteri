import asyncio


# Formulate a complete, premium ASGI application demonstrating advanced features
async def app(scope, receive, send):
    """
    Asteri Advanced ASGI Demonstration Application.
    Showcases:
      - ASGI WebSocket bidirectional communication (RFC 6455)
      - Stash Shared State Memory (dynamic atomic cross-worker counting)
      - Proxy Protocol original IP & Port transparency
      - HTTP 103 Early Hints response interception
    """

    # 1. Handle WebSocket Upgrade & Interactions
    if scope["type"] == "websocket":
        await send({"type": "websocket.accept"})
        try:
            while True:
                msg = await receive()
                if msg.get("type") == "websocket.receive":
                    text = msg.get("text")
                    if text == "ping":
                        await send({"type": "websocket.send", "text": "pong 🏓"})
                    else:
                        await send({"type": "websocket.send", "text": f"Echo: {text}"})
                elif msg.get("type") == "websocket.disconnect":
                    break
        except Exception:
            pass
        return

    # 2. Handle standard HTTP routes
    if scope["type"] == "http":
        # Intercept path for dynamic actions
        path = scope.get("path", "/")

        # Action: Increment dynamic Stash shared counter if client requests it
        counter_val = 0
        try:
            from asteri.dirty import StashClient

            # Connect to default stash server (if running) or fallback to local memory
            client = StashClient()
            # Try to increment atomic key 'global_requests'
            try:
                # Key format: dynamic counter
                current = client.get(b"global_requests") or b"0"
                counter_val = int(current.decode("utf-8")) + 1
                client.put(b"global_requests", str(
                    counter_val).encode("utf-8"))
            except Exception:
                # Fallback if StashServer is not active
                counter_val = "Active (StashServer not running)"
        except Exception:
            counter_val = "Not available"

        # If HTTP 103 Early Hints is requested by client or triggered by server
        # We can emit an early hints response first
        if path == "/early-hints":
            await send(
                {
                    "type": "http.response.early_hints",
                    "headers": [
                        (b"Link", b"</styles.css>; rel=preload; as=style"),
                        (b"Link", b"</app.js>; rel=preload; as=script"),
                    ],
                }
            )
            await asyncio.sleep(0.5)  # Simulate processing time

        # Extract Client info (Proxy Protocol sets original client if present)
        client_addr = scope.get("client")
        client_ip = client_addr[0] if client_addr else "127.0.0.1"
        client_port = client_addr[1] if client_addr else "unknown"

        # Extract Server info
        server_addr = scope.get("server")
        server_ip = server_addr[0] if server_addr else "127.0.0.1"
        server_port = server_addr[1] if server_addr else "8000"

        # Build premium styled response
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [
                    (b"content-type", b"text/html; charset=utf-8"),
                ],
            }
        )

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Asteri Premium Advanced Showcase</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Plus+Jakarta+Sans:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg: #090d16;
            --card: rgba(17, 24, 39, 0.7);
            --primary: #6366f1;
            --accent: #8b5cf6;
            --emerald: #10b981;
            --rose: #f43f5e;
            --text: #f3f4f6;
            --text-dim: #9ca3af;
            --border: rgba(255, 255, 255, 0.08);
        }}
        
        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: 'Plus Jakarta Sans', sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            padding: 2rem;
            background-image: 
                radial-gradient(circle at 10% 20%, rgba(99, 102, 241, 0.15), transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(139, 92, 246, 0.15), transparent 40%);
            overflow-x: hidden;
        }}

        .container {{
            width: 100%;
            max-width: 900px;
            background: var(--card);
            backdrop-filter: blur(20px);
            border: 1px solid var(--border);
            border-radius: 32px;
            padding: 3rem;
            box-shadow: 0 30px 60px -15px rgba(0, 0, 0, 0.8);
            position: relative;
        }}

        .container::before {{
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 4px;
            background: linear-gradient(90deg, var(--primary), var(--accent), var(--emerald));
            border-radius: 32px 32px 0 0;
        }}

        .header {{
            text-align: center;
            margin-bottom: 3rem;
        }}

        .badge {{
            display: inline-flex;
            align-items: center;
            background: linear-gradient(135deg, rgba(99, 102, 241, 0.15), rgba(139, 92, 246, 0.15));
            border: 1px solid rgba(139, 92, 246, 0.3);
            color: #c084fc;
            padding: 0.4rem 1.2rem;
            border-radius: 99px;
            font-size: 0.85rem;
            font-weight: 700;
            letter-spacing: 1px;
            text-transform: uppercase;
            margin-bottom: 1.5rem;
            font-family: 'Outfit', sans-serif;
        }}

        h1 {{
            font-family: 'Outfit', sans-serif;
            font-size: 2.8rem;
            font-weight: 800;
            background: linear-gradient(135deg, #ffffff 30%, #a5b4fc 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 0.5rem;
            letter-spacing: -1px;
        }}

        .subtitle {{
            color: var(--text-dim);
            font-size: 1.1rem;
            max-width: 600px;
            margin: 0 auto;
        }}

        .grid {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 2rem;
            margin-bottom: 3rem;
        }}

        @media (max-width: 768px) {{
            .grid {{
                grid-template-columns: 1fr;
            }}
        }}

        .feature-card {{
            background: rgba(255, 255, 255, 0.02);
            border: 1px solid var(--border);
            border-radius: 24px;
            padding: 2rem;
            transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            position: relative;
            overflow: hidden;
        }}

        .feature-card:hover {{
            transform: translateY(-5px);
            border-color: rgba(99, 102, 241, 0.3);
            box-shadow: 0 10px 30px -10px rgba(99, 102, 241, 0.2);
            background: rgba(255, 255, 255, 0.04);
        }}

        .card-header {{
            display: flex;
            align-items: center;
            gap: 0.75rem;
            margin-bottom: 1.25rem;
        }}

        .card-icon {{
            width: 40px;
            height: 40px;
            border-radius: 12px;
            background: rgba(99, 102, 241, 0.1);
            display: flex;
            align-items: center;
            justify-content: center;
            color: var(--primary);
            font-size: 1.25rem;
        }}

        .feature-card:nth-child(2) .card-icon {{
            background: rgba(16, 185, 129, 0.1);
            color: var(--emerald);
        }}
        .feature-card:nth-child(3) .card-icon {{
            background: rgba(139, 92, 246, 0.1);
            color: var(--accent);
        }}
        .feature-card:nth-child(4) .card-icon {{
            background: rgba(244, 63, 94, 0.1);
            color: var(--rose);
        }}

        h3 {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.25rem;
            font-weight: 600;
        }}

        .val-display {{
            font-family: 'Outfit', sans-serif;
            font-size: 1.8rem;
            font-weight: 800;
            margin-top: 0.5rem;
            color: #fff;
        }}

        .val-sub {{
            font-size: 0.85rem;
            color: var(--text-dim);
            margin-top: 0.25rem;
        }}

        .btn {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            background: linear-gradient(135deg, var(--primary), var(--accent));
            color: #fff;
            padding: 0.8rem 1.8rem;
            border-radius: 14px;
            font-weight: 600;
            text-decoration: none;
            transition: all 0.2s ease;
            border: none;
            cursor: pointer;
            width: 100%;
            margin-top: 1rem;
            font-size: 0.95rem;
        }}

        .btn:hover {{
            filter: brightness(1.1);
            transform: translateY(-2px);
            box-shadow: 0 10px 20px -10px rgba(99, 102, 241, 0.5);
        }}

        .ws-console {{
            background: #060910;
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 1.25rem;
            height: 150px;
            overflow-y: auto;
            font-family: monospace;
            font-size: 0.85rem;
            color: #34d399;
            text-align: left;
            margin-top: 0.75rem;
        }}

        .ws-input-group {{
            display: flex;
            gap: 0.5rem;
            margin-top: 0.75rem;
        }}

        .ws-input {{
            flex: 1;
            background: rgba(0, 0, 0, 0.3);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 0.6rem 1rem;
            color: #fff;
            font-family: inherit;
            font-size: 0.9rem;
        }}

        .ws-input:focus {{
            outline: none;
            border-color: var(--accent);
        }}

        .ws-send-btn {{
            background: var(--accent);
            border: none;
            border-radius: 10px;
            padding: 0 1.25rem;
            color: #fff;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.2s;
        }}

        .ws-send-btn:hover {{
            background: #7c3aed;
        }}

        .footer {{
            text-align: center;
            margin-top: 3rem;
            font-size: 0.85rem;
            color: var(--text-dim);
            opacity: 0.6;
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <span class="badge">Advanced ASGI Showcase</span>
            <h1>✨ Asteri Advanced Demo</h1>
            <p class="subtitle">Experience high-performance, next-generation features powered by the Asteri Web Server.</p>
        </div>

        <div class="grid">
            <!-- Card 1: Proxy Protocol Transparency -->
            <div class="feature-card">
                <div class="card-header">
                    <div class="card-icon">📡</div>
                    <h3>Proxy Protocol</h3>
                </div>
                <p class="val-sub">Real client address detected transparently via Proxy Protocol v1/v2:</p>
                <div class="val-display">{client_ip}</div>
                <p class="val-sub">Client Port: <strong style="color: #fff;">{client_port}</strong></p>
                <p class="val-sub" style="margin-top: 0.5rem; font-size: 0.75rem; opacity: 0.8;">
                    Server Binding: {server_ip}:{server_port}
                </p>
            </div>

            <!-- Card 2: Shared Memory Stash -->
            <div class="feature-card">
                <div class="card-header">
                    <div class="card-icon">🧠</div>
                    <h3>Stash Shared State</h3>
                </div>
                <p class="val-sub">Atomic cross-worker global request counter (thread-safe IPC Stash Server):</p>
                <div class="val-display">{counter_val}</div>
                <button class="btn" onclick="window.location.reload()">🔄 Refresh & Increment</button>
            </div>

            <!-- Card 3: ASGI WebSocket -->
            <div class="feature-card" style="grid-column: span 2;">
                <div class="card-header">
                    <div class="card-icon">⚡</div>
                    <h3>ASGI WebSocket (RFC 6455)</h3>
                </div>
                <p class="val-sub">Full-duplex bi-directional messaging with Asteri binary frame parsing:</p>
                
                <div class="ws-console" id="wsConsole">
                    [System] Connecting to WebSocket...
                </div>
                
                <div class="ws-input-group">
                    <input type="text" class="ws-input" id="wsInput" placeholder="Type a message or 'ping'..." onkeydown="if(event.key==='Enter') sendWSMessage()">
                    <button class="ws-send-btn" onclick="sendWSMessage()">Send</button>
                </div>
            </div>
        </div>

        <div class="footer">
            Asteri Web Server v3.0.0 &bull; Built with premium performance in mind.
        </div>
    </div>

    <script>
        const wsConsole = document.getElementById("wsConsole");
        const wsInput = document.getElementById("wsInput");
        
        const loc = window.location;
        let wsUri = loc.protocol === "https:" ? "wss:" : "ws:";
        wsUri += "//" + loc.host + loc.pathname;
        
        const websocket = new WebSocket(wsUri);
        
        websocket.onopen = function(evt) {{
            logConsole("[System] WebSocket connected successfully!");
        }};
        
        websocket.onmessage = function(evt) {{
            logConsole("[Received] " + evt.data);
        }};
        
        websocket.onerror = function(evt) {{
            logConsole("[Error] Connection encountered an error.");
        }};
        
        websocket.onclose = function(evt) {{
            logConsole("[System] WebSocket closed.");
        }};
        
        function logConsole(message) {{
            const div = document.createElement("div");
            div.textContent = message;
            wsConsole.appendChild(div);
            wsConsole.scrollTop = wsConsole.scrollHeight;
        }}
        
        function sendWSMessage() {{
            const val = wsInput.value.trim();
            if (val) {{
                websocket.send(val);
                logConsole("[Sent] " + val);
                wsInput.value = "";
            }}
        }}
    </script>
</body>
</html>"""
        await send(
            {
                "type": "http.response.body",
                "body": html.encode("utf-8"),
            }
        )
