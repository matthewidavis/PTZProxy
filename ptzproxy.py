import threading
import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
from tkinter.scrolledtext import ScrolledText
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import Response
import requests
import socket
import uvicorn
from pyngrok import ngrok

# -----------------------------------------------------------------------------
# FastAPI Proxy Server Setup
# -----------------------------------------------------------------------------
app = FastAPI()

# Default configurations (modifiable via the GUI tables)
WHITELISTED_ORIGINS = {"https://your-cloud-site.com"}
ALLOWED_LAN_DEVICES = {
    "camera1": "192.168.1.100",
    "camera2": "192.168.1.101",
    "controller": "192.168.1.200"
}

# For our editable tables we maintain lists:
sites_list = list(WHITELISTED_ORIGINS)
devices_list = [{"name": k, "ip": v} for k, v in ALLOWED_LAN_DEVICES.items()]

USE_NGROK = False  # Global flag for Ngrok usage
NGROK_URL = None

# -----------------------------------------------------------------------------
# Logging Function (writes to the log area in the Proxy Control tab)
# -----------------------------------------------------------------------------
def log_message(message):
    log_box.insert(tk.END, message + "\n")
    log_box.yview(tk.END)

# -----------------------------------------------------------------------------
# FastAPI Middleware & Endpoints
# -----------------------------------------------------------------------------
@app.middleware("http")
async def check_origin(request: Request, call_next):
    origin = request.headers.get("origin")
    if origin not in WHITELISTED_ORIGINS:
        log_message(f"🚫 [BLOCKED] Unauthorized origin: {origin}")
        raise HTTPException(status_code=403, detail="Unauthorized origin")
    return await call_next(request)

@app.get("/proxy/http")
async def proxy_http_get(device: str, url: str):
    if device not in ALLOWED_LAN_DEVICES:
        log_message(f"🚫 [BLOCKED] Unauthorized device: {device}")
        raise HTTPException(status_code=403, detail="Unauthorized device")
    target_url = f"http://{ALLOWED_LAN_DEVICES[device]}{url}"
    try:
        response = requests.get(target_url)
        log_message(f"✅ [HTTP] GET {device} -> {url} ({response.status_code})")
        return response.json()
    except requests.exceptions.RequestException as e:
        log_message(f"⚠️ [ERROR] HTTP GET {device} -> {url} failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/proxy/http")
async def proxy_http_post(device: str, url: str, data: dict):
    if device not in ALLOWED_LAN_DEVICES:
        log_message(f"🚫 [BLOCKED] Unauthorized device: {device}")
        raise HTTPException(status_code=403, detail="Unauthorized device")
    target_url = f"http://{ALLOWED_LAN_DEVICES[device]}{url}"
    try:
        response = requests.post(target_url, json=data)
        log_message(f"✅ [HTTP] POST {device} -> {url} ({response.status_code})")
        return response.json()
    except requests.exceptions.RequestException as e:
        log_message(f"⚠️ [ERROR] HTTP POST {device} -> {url} failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/proxy/snapshot")
async def proxy_snapshot(device: str):
    if device not in ALLOWED_LAN_DEVICES:
        log_message(f"🚫 [BLOCKED] Unauthorized device: {device}")
        raise HTTPException(status_code=403, detail="Unauthorized device")
    target_url = f"http://{ALLOWED_LAN_DEVICES[device]}/snapshot.jpg"
    try:
        response = requests.get(target_url, stream=True)
        log_message(f"📸 [SNAPSHOT] Fetched image from {device}")
        return Response(content=response.content, media_type="image/jpeg")
    except requests.exceptions.RequestException as e:
        log_message(f"⚠️ [ERROR] Snapshot from {device} failed: {str(e)}")
        return Response(content=str(e), status_code=500)

@app.post("/proxy/visca")
async def proxy_visca(device: str, command: str):
    if device not in ALLOWED_LAN_DEVICES:
        log_message(f"🚫 [BLOCKED] Unauthorized device: {device}")
        raise HTTPException(status_code=403, detail="Unauthorized device")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.connect((ALLOWED_LAN_DEVICES[device], 52381))
            s.sendall(bytes.fromhex(command))
            response = s.recv(1024)
        log_message(f"🎥 [VISCA] Sent command to {device}, Response: {response.hex()}")
        return {"response": response.hex()}
    except Exception as e:
        log_message(f"⚠️ [ERROR] VISCA command to {device} failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# -----------------------------------------------------------------------------
# Functions to Start/Stop the Proxy Server
# -----------------------------------------------------------------------------
def start_proxy():
    global server_thread, NGROK_URL, USE_NGROK
    log_message("🚀 Starting Proxy Server...")
    server_thread = threading.Thread(target=lambda: uvicorn.run(app, host="0.0.0.0", port=5000), daemon=True)
    server_thread.start()
    if USE_NGROK:
        public_url = ngrok.connect(5000).public_url
        NGROK_URL = public_url
        log_message(f"🌍 Ngrok URL: {NGROK_URL}")
    start_button.config(state=tk.DISABLED)
    stop_button.config(state=tk.NORMAL)
    status_label.config(text="🟢 Proxy Running", foreground="green")

def stop_proxy():
    log_message("🛑 Stopping Proxy Server...")
    start_button.config(state=tk.NORMAL)
    stop_button.config(state=tk.DISABLED)
    status_label.config(text="🔴 Proxy Stopped", foreground="red")
    # Note: cleanly stopping uvicorn requires additional handling.

def toggle_ngrok():
    global USE_NGROK
    USE_NGROK = ngrok_var.get()
    log_message(f"Ngrok Enabled: {USE_NGROK}")

# -----------------------------------------------------------------------------
# GUI Functions for Editable Tables (Site & Device Management)
# -----------------------------------------------------------------------------
# ---- Site Management Table ----
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

# ---- Device Management Table ----
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
        btn_save = ttk.Button(device_table_frame, text="Save", 
                             command=lambda idx=i, n=name_entry, ip=ip_entry: save_device(idx, n, ip))
        btn_save.grid(row=i+1, column=2, padx=2)
        btn_cancel = ttk.Button(device_table_frame, text="Cancel", 
                               command=lambda idx=i, n=name_entry, ip=ip_entry: cancel_device(idx, n, ip))
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

# -----------------------------------------------------------------------------
# GUI Setup: Tabbed Interface with Modern Styling
# -----------------------------------------------------------------------------
root = tk.Tk()
root.title("LAN Proxy Server (Cloud-Enabled)")
root.geometry("900x700")

# Use ttk style for a modern look
style = ttk.Style(root)
style.theme_use("clam")
style.configure("Header.TLabel", font=("Arial", 10, "bold"))

notebook = ttk.Notebook(root)
notebook.pack(expand=True, fill="both", padx=10, pady=10)

# ----- Tab 1: Proxy Control -----
proxy_frame = ttk.Frame(notebook, padding=10)
notebook.add(proxy_frame, text="Proxy Control")

# Header frame for controls
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

# Log box with a border frame
log_frame = ttk.LabelFrame(proxy_frame, text="Logs", padding=10)
log_frame.pack(fill="both", expand=True, pady=10)
log_box = ScrolledText(log_frame, height=20, width=100, font=("Arial", 10))
log_box.pack(fill="both", expand=True)

# ----- Tab 2: Site Management (Whitelist) -----
site_mgmt_frame = ttk.Frame(notebook, padding=10)
notebook.add(site_mgmt_frame, text="Site Management")

site_table_frame = ttk.Frame(site_mgmt_frame)
site_table_frame.pack(fill="both", expand=True, pady=10)
refresh_sites_table()

add_site_button = ttk.Button(site_mgmt_frame, text="Add Site", command=add_site)
add_site_button.pack(pady=5)

# ----- Tab 3: Device Management -----
device_mgmt_frame = ttk.Frame(notebook, padding=10)
notebook.add(device_mgmt_frame, text="Device Management")

device_table_frame = ttk.Frame(device_mgmt_frame)
device_table_frame.pack(fill="both", expand=True, pady=10)
refresh_devices_table()

add_device_button = ttk.Button(device_mgmt_frame, text="Add Device", command=add_device)
add_device_button.pack(pady=5)

root.mainloop()
