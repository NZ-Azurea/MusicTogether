import os
import sys
import threading
import time
import webview

html_loader = """
<!DOCTYPE html>
<html>
<head>
<title>Music Together Launcher</title>
<style>
body { 
    font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
    background: #121212; 
    color: white; 
    display:flex; 
    flex-direction:column; 
    align-items:center; 
    justify-content:center; 
    height:100vh; 
    margin:0; 
}
h1 { font-size: 42px; margin-bottom: 10px; }
.subtitle { color: #aaaaaa; margin-bottom: 40px; }
.btn { 
    background: #bb86fc; 
    color: #000; 
    padding: 15px 30px; 
    border-radius: 8px; 
    border: none; 
    font-size: 18px; 
    font-weight: bold; 
    cursor: pointer; 
    margin: 10px; 
    width: 300px; 
    transition: 0.2s;
}
.btn:hover { background: #9c6aea; transform: scale(1.02); }
input { 
    padding: 15px; 
    font-size: 16px; 
    width: 270px; 
    margin-bottom: 15px; 
    border-radius: 8px; 
    border: 1px solid #333; 
    background: #1e1e1e; 
    color: white; 
    outline: none;
}
input:focus { border-color: #bb86fc; }
.loading { display: none; margin-top: 20px; color: #bb86fc; font-weight: bold; }
</style>
</head>
<body>
    <h1>🎵 Music Together</h1>
    <p class="subtitle">Host your own lobby or join a friend's offline session!</p>
    
    <div style="display:flex; flex-direction:column; align-items:center;">
        <button class="btn" onclick="hostSession()">👑 Host a Session</button>
        <div id="loading" class="loading">Booting Local Server...</div>
        
        <p style="margin: 30px 0 15px; color:#aaa; font-size:14px;">OR CONNECT TO SESSION:</p>
        
        <input type="text" id="ip" placeholder="http://192.168.1.X:54321" />
        <button class="btn" onclick="connectSession()" style="background:#03dac6;">🔗 Connect</button>
    </div>

    <script>
        function hostSession() {
            document.getElementById('loading').style.display = 'block';
            pywebview.api.host_server().then(() => {
                window.location.href = "http://localhost:54321/web/index.html";
            });
        }
        function connectSession() {
            let ip = document.getElementById('ip').value.trim();
            if(!ip) {
                alert("Please enter an IP address!");
                return;
            }
            if(!ip.startsWith('http')) ip = 'http://' + ip;
            window.location.href = ip + "/web/index.html";
        }
    </script>
</body>
</html>
"""

def run_server():
    api_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'Python_API')
    sys.path.append(api_dir)
    import uvicorn
    from Music_Together_API import app, print_network_interfaces
    port = 54321
    print_network_interfaces(port)
    uvicorn.run(app, host="::", port=port, log_level="info")

class Api:
    def __init__(self):
        self.server_started = False
        
    def host_server(self):
        if not self.server_started:
            self.server_started = True
            t = threading.Thread(target=run_server, daemon=True)
            t.start()
            # Give Uvicorn a few seconds to fully bind to the port
            time.sleep(2)
        return True

if __name__ == '__main__':
    api = Api()
    webview.create_window(
        'Music Together Launcher', 
        html=html_loader, 
        js_api=api, 
        width=1280, 
        height=800,
        min_size=(1000, 600),
        background_color='#121212'
    )
    webview.start()
