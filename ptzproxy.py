import threading
import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

import asyncio
import urllib.parse
import httpx
from pyngrok import ngrok
import sys
import os
import logging
from queue import Queue

import uvicorn
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import Response
from fastapi.middleware.cors import CORSMiddleware

##############################################################################
# Logging Setup (Queue-based for Tkinter)
##############################################################################

log_queue = Queue()

class TkinterLogHandler(logging.Handler):
    """A logging handler that sends logs to a Queue, which the Tkinter GUI will poll."""
    def emit(self, record):
        msg = self.format(record)
        log_queue.put(msg)

logger = logging.getLogger("ptzproxy")
logger.setLevel(logging.INFO)  # or DEBUG, etc.

handler = TkinterLogHandler()
formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
handler.setFormatter(formatter)
logger.addHandler(handler)

##############################################################################
# Lifespan function
##############################################################################
async def lifespan(app: FastAPI):
    """Single async function returning a context manager for startup/shutdown."""
    logger.info("Starting up: creating shared AsyncClient.")
    app.state.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        follow_redirects=True
    )
    # ---- Start-up complete; yield to run the server
    yield
    # ---- Shutdown logic
    logger.info("Shutting down: closing shared AsyncClient.")
    await app.state.http_client.aclose()

##############################################################################
# Build the FastAPI app
##############################################################################
app = FastAPI(lifespan=lifespan)

# Setup CORS
WHITELISTED_ORIGINS = {
    "https://matthewidavis.github.io",
    "https://matthewidavis.github.io/PTZProxy"
}
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(WHITELISTED_ORIGINS),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

##############################################################################
# Global state (devices, sites)
##############################################################################
ALLOWED_LAN_DEVICES = {
    "camera1": "192.168.101.88",
    "camera2": "192.168.101.231",
    "controller": "192.168.1.200"
}
sites_list = list(WHITELISTED_ORIGINS)
devices_list = [{"name": k, "ip": v} for k, v in ALLOWED_LAN_DEVICES.items()]
USE_NGROK = False
NGROK_URL = None

##############################################################################
# Device checker
##############################################################################
def get_device_ip(device: str):
    if device not in ALLOWED_LAN_DEVICES:
        logger.warning(f"[BLOCKED] Unauthorized device: {device}")
        raise HTTPException(status_code=403, detail="Unauthorized device")
    return ALLOWED_LAN_DEVICES[device]

##############################################################################
# Async routes
##############################################################################
@app.api_route("/proxy/http", methods=["GET", "POST"])
async def proxy_http(
    device_ip: str = Depends(get_device_ip),
    url: str = "",
    request: Request = None
):
    """Handles GET/POST over HTTP using the shared AsyncClient."""
    decoded_url = urllib.parse.unquote(url)
    target_url = f"http://{device_ip}{decoded_url}"
    logger.info(f"[Proxy HTTP] {request.method} -> {target_url}")

    client: httpx.AsyncClient = request.app.state.http_client
    try:
        if request.method == "GET":
            if decoded_url.startswith("/cgi-bin/ptzctrl.cgi"):
                response = await client.get(
                    target_url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=False
                )
            else:
                response = await client.get(
                    target_url, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True
                )
            logger.info(f"[HTTP GET] -> {decoded_url} ({response.status_code})")
        else:  # POST
            data = await request.json()
            response = await client.post(
                target_url, json=data, headers={"User-Agent": "Mozilla/5.0"}
            )
            logger.info(f"[HTTP POST] -> {decoded_url} ({response.status_code})")

        # If redirect:
        if 300 <= response.status_code < 400:
            location = response.headers.get("Location", "")
            logger.info(f"Redirect detected: {location}")
            return Response(content=f"Redirect to: {location}", media_type="text/plain")

        # Try returning JSON; fall back to text
        try:
            return response.json()
        except Exception:
            return Response(content=response.text, media_type="text/plain")

    except httpx.RequestError as exc:
        logger.error(f"[ERROR] {request.method} -> {decoded_url} failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

@app.get("/proxy/snapshot")
async def proxy_snapshot(device_ip: str = Depends(get_device_ip)):
    """Fetches a JPEG snapshot from the device asynchronously."""
    target_url = f"http://{device_ip}/snapshot.jpg"
    logger.info(f"[SNAPSHOT GET] -> {target_url}")

    client: httpx.AsyncClient = app.state.http_client
    try:
        response = await client.get(target_url)
        logger.info(f"[SNAPSHOT] Fetched image from {device_ip}")
        headers = {"Cache-Control": "no-store", "ngrok-skip-browser-warning": "true"}
        return Response(content=response.content, media_type="image/jpeg", headers=headers)
    except httpx.RequestError as exc:
        logger.error(f"[ERROR] Snapshot failed: {exc}")
        return Response(content=str(exc), status_code=500)

@app.post("/proxy/visca")
async def proxy_visca(device_ip: str = Depends(get_device_ip), command: str = ""):
    """Sends a VISCA command using asyncio for non-blocking I/O."""
    logger.info(f"[VISCA] Sending command to {device_ip}: {command}")
    try:
        reader, writer = await asyncio.open_connection(device_ip, 52381)
        writer.write(bytes.fromhex(command))
        await writer.drain()

        response_data = await reader.read(1024)
        await writer.drain()

        writer.close()
        await writer.wait_closed()

        resp_hex = response_data.hex()
        logger.info(f"[VISCA] Response: {resp_hex}")
        return {"response": resp_hex}
    except Exception as exc:
        logger.error(f"[ERROR] VISCA command failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))

##############################################################################
# Uvicorn Startup/Shutdown in a Thread
##############################################################################
server = None
server_thread = None

def start_proxy():
    global server, server_thread, NGROK_URL, USE_NGROK
    logger.info("Starting Proxy Server...")
    config = uvicorn.Config(app, host="0.0.0.0", port=5001)
    server = uvicorn.Server(config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    if USE_NGROK:
        public_url = ngrok.connect(5001).public_url
        NGROK_URL = public_url
        logger.info(f"Ngrok URL: {NGROK_URL}")
        ngrok_url_label.config(text=NGROK_URL)

    start_button.config(state=tk.DISABLED)
    stop_button.config(state=tk.NORMAL)
    status_label.config(text="🟢 Proxy Running", foreground="green")

def stop_proxy():
    global server
    logger.info("Stopping Proxy Server...")
    if server is not None:
        server.should_exit = True

    start_button.config(state=tk.NORMAL)
    stop_button.config(state=tk.DISABLED)
    status_label.config(text="🔴 Proxy Stopped", foreground="red")

def toggle_ngrok():
    global USE_NGROK
    USE_NGROK = ngrok_var.get()
    logger.info(f"Ngrok Enabled: {USE_NGROK}")

def copy_ngrok_url(event):
    url_text = ngrok_url_label.cget("text")
    root.clipboard_clear()
    root.clipboard_append(url_text)
    logger.info(f"Copied Ngrok URL to clipboard: {url_text}")

##############################################################################
# Tkinter GUI
##############################################################################
root = tk.Tk()
root.title("LAN Proxy Server (Cloud-Enabled)")
root.geometry("620x500")

style = ttk.Style(root)
style.theme_use("clam")
style.configure("Header.TLabel", font=("Arial", 10, "bold"))

notebook = ttk.Notebook(root)
notebook.pack(expand=True, fill="both", padx=10, pady=10)

# ---- Tab 1: Proxy Control ----
proxy_frame = ttk.Frame(notebook, padding=10)
notebook.add(proxy_frame, text="Proxy Control")

controls_frame = ttk.Frame(proxy_frame)
controls_frame.pack(fill="x", pady=5)

status_label = ttk.Label(controls_frame, text="🔴 Proxy Stopped", foreground="red", font=("Arial", 14))
status_label.pack(side="left", padx=5)

start_button = ttk.Button(controls_frame, text="Start Proxy", command=start_proxy)
start_button.pack(side="left", padx=5)

stop_button = ttk.Button(controls_frame, text="Stop Proxy", command=stop_proxy, state="disabled")
stop_button.pack(side="left", padx=5)

ngrok_var = tk.BooleanVar(value=USE_NGROK)
ngrok_checkbox = ttk.Checkbutton(controls_frame, text="Enable Ngrok", variable=ngrok_var, command=toggle_ngrok)
ngrok_checkbox.pack(side="left", padx=5)

logs_header_frame = ttk.Frame(proxy_frame)
logs_header_frame.pack(fill="x", pady=(10,0))
logs_label = ttk.Label(logs_header_frame, text="Logs", font=("Arial", 10, "bold"))
logs_label.pack(side="left", padx=5)

ngrok_url_label = ttk.Label(logs_header_frame, text="", foreground="blue", cursor="hand2")
ngrok_url_label.pack(side="right", padx=5)
ngrok_url_label.bind("<Button-1>", copy_ngrok_url)

log_frame = ttk.Frame(proxy_frame)
log_frame.pack(fill="both", expand=True, pady=10)

log_box = ScrolledText(log_frame, height=20, width=100, font=("Arial", 10))
log_box.pack(fill="both", expand=True)

# ---- Tab 2: Site Management ----
site_mgmt_frame = ttk.Frame(notebook, padding=10)
notebook.add(site_mgmt_frame, text="Site Management")

site_table_frame = ttk.Frame(site_mgmt_frame)
site_table_frame.pack(fill="both", expand=True, pady=10)

def refresh_sites_table():
    for widget in site_table_frame.winfo_children():
        widget.destroy()
    ttk.Label(site_table_frame, text="Site URL", style="Header.TLabel").grid(row=0, column=0, padx=5, pady=5)
    ttk.Label(site_table_frame, text="Actions", style="Header.TLabel").grid(row=0, column=1, padx=5, pady=5)

    for i, site in enumerate(sites_list):
        entry = ttk.Entry(site_table_frame, width=50)
        entry.insert(0, site)
        entry.grid(row=i+1, column=0, padx=5, pady=2)

        btn_save = ttk.Button(site_table_frame, text="Save", command=lambda idx=i, e=entry: save_site(idx, e))
        btn_save.grid(row=i+1, column=1, padx=2)

        btn_cancel = ttk.Button(site_table_frame, text="Cancel", command=lambda idx=i, e=entry: cancel_site(idx, e))
        btn_cancel.grid(row=i+1, column=2, padx=2)

        btn_delete = ttk.Button(site_table_frame, text="Delete", command=lambda idx=i: delete_site(idx))
        btn_delete.grid(row=i+1, column=3, padx=2)

    update_whitelist_from_table()

def save_site(idx, entry):
    new_value = entry.get().strip()
    if new_value:
        sites_list[idx] = new_value
        refresh_sites_table()
    else:
        messagebox.showerror("Error", "Site URL cannot be empty.")

def cancel_site(idx, entry):
    entry.delete(0, tk.END)
    entry.insert(0, sites_list[idx])

def delete_site(idx):
    del sites_list[idx]
    refresh_sites_table()

def update_whitelist_from_table():
    global WHITELISTED_ORIGINS
    WHITELISTED_ORIGINS = set(sites_list)

def add_site():
    sites_list.append("")
    refresh_sites_table()

refresh_sites_table()
add_site_button = ttk.Button(site_mgmt_frame, text="Add Site", command=add_site)
add_site_button.pack(pady=5)

# ---- Tab 3: Device Management ----
device_mgmt_frame = ttk.Frame(notebook, padding=10)
notebook.add(device_mgmt_frame, text="Device Management")

device_table_frame = ttk.Frame(device_mgmt_frame)
device_table_frame.pack(fill="both", expand=True, pady=10)

def refresh_devices_table():
    for widget in device_table_frame.winfo_children():
        widget.destroy()
    ttk.Label(device_table_frame, text="Name", style="Header.TLabel").grid(row=0, column=0, padx=5, pady=5)
    ttk.Label(device_table_frame, text="IP", style="Header.TLabel").grid(row=0, column=1, padx=5, pady=5)
    ttk.Label(device_table_frame, text="Actions", style="Header.TLabel").grid(row=0, column=2, padx=5, pady=5)

    for i, device in enumerate(devices_list):
        name_entry = ttk.Entry(device_table_frame, width=20)
        name_entry.insert(0, device["name"])
        name_entry.grid(row=i+1, column=0, padx=5, pady=2)

        ip_entry = ttk.Entry(device_table_frame, width=20)
        ip_entry.insert(0, device["ip"])
        ip_entry.grid(row=i+1, column=1, padx=5, pady=2)

        btn_save = ttk.Button(
            device_table_frame,
            text="Save",
            command=lambda idx=i, n=name_entry, ip=ip_entry: save_device(idx, n, ip)
        )
        btn_save.grid(row=i+1, column=2, padx=2)

        btn_cancel = ttk.Button(
            device_table_frame,
            text="Cancel",
            command=lambda idx=i, n=name_entry, ip=ip_entry: cancel_device(idx, n, ip)
        )
        btn_cancel.grid(row=i+1, column=3, padx=2)

        btn_delete = ttk.Button(device_table_frame, text="Delete", command=lambda idx=i: delete_device(idx))
        btn_delete.grid(row=i+1, column=4, padx=2)

    update_devices_from_table()

def save_device(idx, name_entry, ip_entry):
    name = name_entry.get().strip()
    ip = ip_entry.get().strip()
    if name and ip:
        devices_list[idx] = {"name": name, "ip": ip}
        refresh_devices_table()
    else:
        messagebox.showerror("Error", "Both Name and IP must be provided.")

def cancel_device(idx, name_entry, ip_entry):
    name_entry.delete(0, tk.END)
    name_entry.insert(0, devices_list[idx]["name"])
    ip_entry.delete(0, tk.END)
    ip_entry.insert(0, devices_list[idx]["ip"])

def delete_device(idx):
    del devices_list[idx]
    refresh_devices_table()

def update_devices_from_table():
    global ALLOWED_LAN_DEVICES
    ALLOWED_LAN_DEVICES = {d["name"]: d["ip"] for d in devices_list}

def add_device():
    devices_list.append({"name": "", "ip": ""})
    refresh_devices_table()

refresh_devices_table()
add_device_button = ttk.Button(device_mgmt_frame, text="Add Device", command=add_device)
add_device_button.pack(pady=5)

##############################################################################
# Poll the log queue in the Tkinter event loop
##############################################################################
def poll_log_queue():
    while not log_queue.empty():
        msg = log_queue.get_nowait()
        log_box.insert(tk.END, msg + "\n")
        log_box.yview(tk.END)
    root.after(100, poll_log_queue)

poll_log_queue()
root.mainloop()
