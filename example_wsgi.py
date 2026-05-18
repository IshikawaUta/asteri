def app(environ, start_response):
    """Simple WSGI Application with premium Asteri styling."""
    status = "200 OK"
    headers = [("Content-Type", "text/html; charset=utf-8")]
    start_response(status, headers)

    html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Asteri WSGI</title>
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;800&family=Plus+Jakarta+Sans:wght@300;400;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #090d16;
            --card: rgba(17, 24, 39, 0.7);
            --primary: #6366f1;
            --accent: #8b5cf6;
            --text: #f3f4f6;
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
                radial-gradient(circle at 10% 20%, rgba(99, 102, 241, 0.15), transparent 40%),
                radial-gradient(circle at 90% 80%, rgba(139, 92, 246, 0.15), transparent 40%);
            overflow-x: hidden;
        }

        .container {
            width: 100%;
            max-width: 600px;
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
            background: rgba(99, 102, 241, 0.1);
            border: 1px solid rgba(99, 102, 241, 0.3);
            color: #a5b4fc;
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
            background: linear-gradient(135deg, #ffffff 40%, #c7d2fe 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            margin-bottom: 1.5rem;
            letter-spacing: -0.5px;
        }

        p {
            color: var(--text-dim);
            font-size: 1.05rem;
            line-height: 1.6;
            margin-bottom: 1.5rem;
        }

        .footer {
            font-size: 0.85rem;
            color: var(--text-dim);
            opacity: 0.6;
            margin-top: 2rem;
        }
    </style>
</head>
<body>
    <div class="container">
        <span class="badge">WSGI Application</span>
        <h1>🌟 Hello from Asteri</h1>
        <p>This high-performance WSGI application is powered by the fast and light Asteri web server.</p>
        <div class="footer">
            Asteri Web Server v2.2.2 &bull; Powered by Asteri WSGI Engine
        </div>
    </div>
</body>
</html>"""
    return [html.encode("utf-8")]
