try:
    from flask import Flask, render_template_string
except ImportError:
    # Fallback if flask is not installed
    class Flask:
        def __init__(self, name): pass
        def route(self, path): return lambda x: x
    render_template_string = lambda x, **y: x

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>Asteri + Flask</title>
    <style>
        body { font-family: sans-serif; background: #0f172a; color: white; display: flex; justify-content: center; align-items: center; min-height: 100vh; margin: 0; }
        .card { background: rgba(255,255,255,0.05); padding: 2rem; border-radius: 20px; border: 1px solid rgba(255,255,255,0.1); text-align: center; }
        h1 { color: #6366f1; }
        .badge { background: #6366f1; padding: 4px 12px; border-radius: 99px; font-size: 0.8rem; }
    </style>
</head>
<body>
    <div class="card">
        <span class="badge">FLASK + WSGI</span>
        <h1>🌟 Asteri is Powering Flask</h1>
        <p>This is a standard Flask application running on Asteri.</p>
        <p>Try running with: <code>asteri example_flask:app -k gthread -w 4</code></p>
    </div>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

if __name__ == '__main__':
    app.run()
