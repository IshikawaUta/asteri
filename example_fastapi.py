try:
    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
except ImportError:
    # Fallback if fastapi is not installed
    class FastAPI: pass
    HTMLResponse = lambda content: content

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def read_items():
    return """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <title>Asteri + FastAPI</title>
        <style>
            body { font-family: sans-serif; background: #0f172a; color: white; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
            .card { background: rgba(255,255,255,0.05); padding: 2rem; border-radius: 20px; border: 1px solid rgba(255,255,255,0.1); text-align: center; }
            h1 { color: #a855f7; }
            .badge { background: #a855f7; padding: 4px 12px; border-radius: 99px; font-size: 0.8rem; }
        </style>
    </head>
    <body>
        <div class="card">
            <span class="badge">FASTAPI + ASGI</span>
            <h1>🚀 Asteri is Powering FastAPI</h1>
            <p>This is a modern async FastAPI application running on Asteri.</p>
            <p>Try running with: <code>asteri example_fastapi:app -k asgi -w 4</code></p>
        </div>
    </body>
    </html>
    """
