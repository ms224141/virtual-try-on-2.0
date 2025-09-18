from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv
import os
import requests
import itertools
import aiohttp
import asyncio
import uuid
import threading

# --- Initial Setup ---
load_dotenv()
app = Flask(__name__)
STATIC_DIR = "static"
os.makedirs(STATIC_DIR, exist_ok=True)
TASKS = {} # In-memory store for task status

# --- API Configuration ---
API_URL = "https://api.piapi.ai/api/v1/task"
api_keys_env = os.environ.get("KLING_KEYS", "")
if not api_keys_env:
    raise ValueError("KLING_KEYS environment variable not set!")
API_KEYS = api_keys_env.split(",")
key_cycle = itertools.cycle(API_KEYS)

# --- [FIX #1: Absolute URL] ---
# Your Render app's public URL. This is critical for creating a working link.
# Make sure there is NO slash at the end.
BASE_URL = os.environ.get("BASE_URL", "https://virtual-try-on-2-0-1.onrender.com")

def get_next_api_key():
    return next(key_cycle)

def download_image(url, save_path):
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        with open(save_path, "wb") as f:
            f.write(r.content)
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error downloading image: {e}")
        return False

# This is the main background process function
async def process_try_on(job_id, model_img, dress_img):
    async with aiohttp.ClientSession() as session:
        try:
            api_key = get_next_api_key()
            headers = {"Content-Type": "application/json", "X-API-KEY": api_key}
            data = {"model": "kling", "task_type": "ai_try_on", "input": {"model_input": model_img, "dress_input": dress_img, "batch_size": 1}}

            TASKS[job_id]["status"] = "processing"
            # Get the external task_id from Kling API
            async with session.post(API_URL, json=data, headers=headers) as res:
                res.raise_for_status()
                res_json = await res.json()
                external_task_id = res_json['data']['task_id']
            
            check_url = f"{API_URL}/{external_task_id}"
            status = "pending"
            # Wait for the Kling API job to finish
            while status in ["pending", "running", "processing"]:
                await asyncio.sleep(5)
                async with session.get(check_url, headers=headers) as check_res:
                    check_res.raise_for_status()
                    check_json = await check_res.json()
                    status = check_json['data']['status']
            
            if status == "completed":
                img_url = check_json['data']['output']['works'][0]['image']['resource']
                local_filename_path = f"/static/{job_id}.png"
                full_local_path = os.path.join(STATIC_DIR, f"{job_id}.png")
                
                if download_image(img_url, full_local_path):
                    # Construct the full, absolute URL using the BASE_URL
                    full_image_url = f"{BASE_URL}{local_filename_path}"
                    
                    # --- [FIX #2: Complete JSON Response] ---
                    # Create the final JSON object with all four required fields.
                    final_result = {
                        "status": "completed",
                        "task_id": external_task_id,
                        "local_path": local_filename_path,
                        "image_url": full_image_url
                    }
                    TASKS[job_id].update(final_result)
                else:
                    raise Exception("Failed to download the final image.")
            else:
                 raise Exception(f"Task failed with status from Kling API: {status}")

        except Exception as e:
            print(f"Error in job {job_id}: {e}")
            TASKS[job_id].update({"status": "failed", "error": str(e)})

# --- Flask Routes ---
@app.route('/', methods=['GET'])
def home():
    return "Flask Virtual Try-On Server (Async) is Running!"

@app.route('/start-tryon', methods=['POST'])
def start_tryon_api():
    data = request.json
    model_img = data.get("model_img")
    dress_img = data.get("dress_img")
    if not model_img or not dress_img:
        return jsonify({"error": "model_img and dress_img are required"}), 400
    job_id = str(uuid.uuid4())
    TASKS[job_id] = {"status": "pending"}
    thread = threading.Thread(target=lambda: asyncio.run(process_try_on(job_id, model_img, dress_img)))
    thread.start()
    return jsonify({"jobId": job_id}), 202

@app.route('/status/<job_id>', methods=['GET'])
def get_status_api(job_id):
    task = TASKS.get(job_id)
    if not task:
        return jsonify({"error": "Job ID not found"}), 404
    return jsonify(task)

@app.route('/static/<filename>')
def serve_static(filename):
    return send_from_directory(STATIC_DIR, filename)

# --- Main Execution ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"Server is running on port {port}")
    app.run(host="0.0.0.0", port=port)

