def app(environ, start_response):
    """Simple WSGI Application with Asteri styling."""
    status = '200 OK'
    headers = [('Content-type', 'text/html')]
    start_response(status, headers)
    
    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Asteri WSGI</title>
    <style>
        :root {
            --bg: #0f172a;
            --card: rgba(30, 41, 59, 0.7);
            --primary: #6366f1;
            --accent: #a855f7;
            --text: #f8fafc;
            --text-dim: #94a3b8;
        }
        body {
            font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            margin: 0;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            background-image: radial-gradient(circle at top right, #1e1b4b, transparent),
                              radial-gradient(circle at bottom left, #1e1b4b, transparent);
        }
        .container {
            width: 90%;
            max-width: 600px;
            background: var(--card);
            backdrop-filter: blur(12px);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 24px;
            padding: 2.5rem;
            box-shadow: 0 25px 50px -12px rgba(0,0,0,0.5);
            text-align: center;
        }
        h1 { margin: 0; font-size: 2rem; color: var(--primary); margin-bottom: 1rem; }
        p { color: var(--text-dim); font-size: 1.1rem; line-height: 1.6; }
        .badge {
            display: inline-block;
            background: linear-gradient(135deg, var(--primary), var(--accent));
            padding: 0.3rem 1rem;
            border-radius: 99px;
            font-size: 0.9rem;
            font-weight: bold;
            margin-bottom: 1.5rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <span class="badge">WSGI APPLICATION</span>
        <h1>🌟 Hello from Asteri</h1>
        <p>This WSGI application is running on the high-performance Asteri web server.</p>
        <p style="margin-top: 2rem; font-size: 0.8rem; opacity: 0.5;">
            v1.2.1 &bull; Powered by Asteri
        </p>
    </div>
</body>
</html>"""
    return [html.encode('utf-8')]
