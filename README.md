# PTZOptics Cloud-Enabled Proxy

A FastAPI-based proxy server with a desktop GUI (Tkinter) and a web interface to remotely control PTZOptics cameras over LAN. This project routes API requests from cloud clients to LAN devices, handling PTZ control commands and camera snapshot requests.

## Overview

- **Proxy Server**: Uses FastAPI (with CORS enabled) to route requests from remote clients to LAN-based cameras.
- **Desktop GUI**: A Tkinter-based application that provides three main tabs:
  - **Proxy Control**: Start/stop the server, view logs, and enable ngrok tunneling.
  - **Site Management**: Manage whitelisted origins.
  - **Device Management**: Manage LAN device configurations.
- **Web Interface**: A browser-based control panel for live camera preview and PTZ control.

## API Adjustments

### Snapshot Requests
- The snapshot endpoint adds the header `ngrok-skip-browser-warning: true` to bypass ngrok’s browser warning.
- The snapshot is served using a `stream=True` request, and the response includes the header `"Cache-Control": "no-store"`.

Example code snippet:
```python
headers = {"Cache-Control": "no-store", "ngrok-skip-browser-warning": "true"}
return Response(
    content=response.content,
    media_type="image/jpeg",
    headers=headers
)
```

### Control Commands
- PTZ commands (e.g., pan, tilt, zoom, preset) are formatted to match the camera’s standard API.
- The URL construction uses URL encoding to embed the PTZ command parameters properly.

Example command URL:
```
/proxy/http?device=camera1&url=%2Fcgi-bin%2Fptzctrl.cgi%3Fptzcmd%26home
```

### CORS & Security
- CORS middleware is added so that remote web clients (from approved origins) can interact with the proxy server.

## Usage

- **Desktop Application**: Run the Python script (`ptzproxy.py`) to start the proxy server. Use the GUI to manage proxy settings, whitelists, and LAN devices.
- **Web Interface**: Open the `simplectl_cloud.html` file in your browser, set the proxy URL (ngrok or otherwise), and use the controls to interact with the camera.

## Screenshots

**Proxy Control Tab**  
![Proxy Control Tab](https://github.com/user-attachments/assets/b8ef0c0c-980b-416f-9f72-2ae41d986e25)

**Site Management Tab**  
![Site Management Tab](https://github.com/user-attachments/assets/6087470c-8a69-41d1-a88f-0a5713f24094)

**Device Management Tab**  
![Device Management Tab](https://github.com/user-attachments/assets/e3f3362c-ade7-4c6f-978f-551ea8f9bafc)

**Web Interface**  
![Web Interface](https://github.com/user-attachments/assets/02ebdf02-af98-4928-8047-72c810e25bde)

