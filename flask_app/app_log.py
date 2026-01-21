import time
from flask import Flask, Response, render_template_string, stream_with_context

app = Flask(__name__)

# Map screen names to their log files
LOG_FILES = {
    "hv_control": "/local/home/banco/dylan/Cosmic_Bench_DAQ_Control/logs/hv_control.log",
    "dream_daq": "/local/home/banco/dylan/Cosmic_Bench_DAQ_Control/logs/dream_daq.log",
    "decoder": "/local/home/banco/dylan/Cosmic_Bench_DAQ_Control/logs/decoder.log",
    "daq_control": "/local/home/banco/dylan/Cosmic_Bench_DAQ_Control/logs/daq_control.log",
    # add more here...
}

# Number of lines to preload from each log
PRELOAD_LINES = 200

@app.route("/")
def index():
    # Build grid with one <pre> per log
    template = """
    <html>
    <head>
      <style>
        body { background: #222; color: #eee; font-family: monospace; }
        .grid { display: grid; grid-template-columns: 1fr 1fr; gap: 1em; }
        .log-window { background: #111; padding: 0.5em; border: 1px solid #555;
                      border-radius: 6px; height: 40vh; overflow-y: scroll; }
        h2 { margin-top: 0; font-size: 1.2em; }
      </style>
    </head>
    <body>
      <h1>Live Logs</h1>
      <div class="grid">
        {% for name, path in logs.items() %}
        <div>
          <h2>{{name}}</h2>
          <pre id="log-{{name}}" class="log-window">{{preloads[name]}}</pre>
        </div>
        {% endfor %}
      </div>
      <script>
        {% for name, path in logs.items() %}
        (function() {
            var logElem = document.getElementById("log-{{name}}");
            var evtSource = new EventSource("/stream/{{name}}");
            evtSource.onmessage = function(e) {
                logElem.textContent += e.data + "\\n";
                logElem.scrollTop = logElem.scrollHeight; // auto-scroll
            }
        })();
        {% endfor %}
      </script>
    </body>
    </html>
    """

    # Preload last N lines of each log
    preloads = {}
    for name, path in LOG_FILES.items():
        try:
            with open(path, "r") as f:
                lines = f.readlines()
                preloads[name] = "".join(lines[-PRELOAD_LINES:])
        except Exception as e:
            preloads[name] = f"(Error reading {path}: {e})"

    return render_template_string(template, logs=LOG_FILES, preloads=preloads)


@app.route("/stream/<name>")
def stream_log(name):
    path = LOG_FILES.get(name)
    if not path:
        return "No such log", 404

    def generate():
        with open(path, "r") as f:
            # Start at end for *new* lines only
            f.seek(0, 2)
            while True:
                line = f.readline()
                if line:
                    yield f"data: {line.rstrip()}\n\n"
                else:
                    time.sleep(0.5)

    return Response(stream_with_context(generate()), mimetype="text/event-stream")
