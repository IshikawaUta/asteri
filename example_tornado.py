# example_tornado.py
# A Premium WSGI application designed to run under Asteri's Tornado workers

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Asteri + Tornado / GTornado</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Plus+Jakarta+Sans:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #030712;
            --card: rgba(17, 24, 39, 0.7);
            --primary: #f59e0b; /* Amber */
            --accent: #d97706;
            --text: #f9fafb;
            --text-dim: #9ca3af;
            --border: rgba(255, 255, 255, 0.08);
        }
        
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }

        body {
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
                radial-gradient(circle at 10% 20%, rgba(245, 158, 11, 0.12), transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(217, 119, 6, 0.12), transparent 40%);
            overflow-x: hidden;
        }

        .container {
            width: 100%;
            max-width: 650px;
            background: var(--card);
            backdrop-filter: blur(20px);
            border: 1px solid var(--border);
            border-radius: 32px;
            padding: 3rem;
            box-shadow: 0 30px 60px -15px rgba(0, 0, 0, 0.8);
            position: relative;
            text-align: center;
        }

        .container::before {
            content: '';
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            height: 4px;
            background: linear-gradient(90deg, var(--primary), var(--accent));
            border-radius: 32px 32px 0 0;
        }

        .badge {
            display: inline-flex;
            align-items: center;
            background: rgba(245, 158, 11, 0.1);
            border: 1px solid rgba(245, 158, 11, 0.3);
            color: #fbbf24;
            padding: 0.4rem 1.2rem;
            border-radius: 99px;
            font-size: 0.85rem;
            font-weight: 700;
            letter-spacing: 1px;
            text-transform: uppercase;
            margin-bottom: 1.5rem;
            font-family: 'Outfit', sans-serif;
        }

        h1 {
            font-family: 'Outfit', sans-serif;
            font-size: 2.5rem;
            font-weight: 800;
            background: linear-gradient(135deg, #ffffff 40%, #fde68a 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 1rem;
            letter-spacing: -0.5px;
        }

        p {
            color: var(--text-dim);
            font-size: 1.05rem;
            line-height: 1.6;
            margin-bottom: 2rem;
        }

        .info-box {
            background: rgba(0, 0, 0, 0.2);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 1.5rem;
            margin-bottom: 2rem;
            font-size: 0.95rem;
            text-align: left;
        }

        .info-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 0.75rem;
            border-bottom: 1px solid rgba(255, 255, 255, 0.03);
            padding-bottom: 0.50rem;
        }

        .info-row:last-child {
            margin-bottom: 0;
            border-bottom: none;
            padding-bottom: 0;
        }

        .info-label {
            color: var(--text-dim);
            font-weight: 600;
        }

        .info-value {
            color: #fff;
            font-family: monospace;
            font-weight: bold;
        }

        .footer {
            font-size: 0.85rem;
            color: var(--text-dim);
            opacity: 0.6;
        }
    </style>
</head>
<body>
    <div class="container">
        <span class="badge">Tornado WSGI worker</span>
        <h1>🌪️ Asteri + Tornado Event Loop</h1>
        <p>This high-performance WSGI application is powered by Asteri's non-blocking asynchronous Tornado web server worker.</p>
        
        <div class="info-box">
            <div class="info-row">
                <span class="info-label">Server Engine</span>
                <span class="info-value" style="color: #fbbf24;">Asteri Web Server</span>
            </div>
            <div class="info-row">
                <span class="info-label">Worker Class</span>
                <span class="info-value">Tornado / GTornado</span>
            </div>
            <div class="info-row">
                <span class="info-label">Event Loop Type</span>
                <span class="info-value">Asynchronous I/O Loop</span>
            </div>
            <div class="info-row">
                <span class="info-label">Python WSGI Container</span>
                <span class="info-value">tornado.wsgi.WSGIContainer</span>
            </div>
        </div>

        <div class="footer">
            Asteri Web Server v2.2.2 &bull; Powered by Tornado Async Engine
        </div>
    </div>
</body>
</html>
"""


def app(environ, start_response):
    """Standard WSGI entry point. Zero custom middleware needed!"""
    status = "200 OK"
    response_headers = [("Content-Type", "text/html; charset=utf-8")]
    start_response(status, response_headers)
    return [HTML_TEMPLATE.encode("utf-8")]
