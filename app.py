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

# --- Global In-Memory Store for Task Status ---
# This dictionary will hold the status and result of each job.
# In a larger application, this would be a database like Redis.
TASKS = {}

# --- API Configuration ---
API_URL = "https://api.piapi.ai/api/v1/task"
api_keys_env = os.environ.get("KLING_KEYS", "")
if not api_keys_env:
    raise ValueError("KLING_KEYS environment variable not set!")

API_KEYS = api_keys_env.split(",")
key_cycle = itertools.cycle(API_KEYS)

def get_next_api_key():
    return next(key_cycle)

# --- Image Download Helper (Synchronous) ---
def download_image(url, save_path):
    """Downloads an image from a URL and saves it locally."""
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()  # Raises an exception for bad status codes
        with open(save_path, "wb") as f:
            f.write(r.content)
        return True
    except requests.exceptions.RequestException as e:
        print(f"Error downloading image: {e}")
        return False

# --- Core Try-On Logic (Asynchronous) ---
async def process_try_on(job_id, model_img, dress_img):
    """
    This is the main worker function that runs in the background.
    It communicates with the external AI service and updates the global TASKS dictionary.
    """
    async with aiohttp.ClientSession() as session:
        try:
            # 1. Get an API key and prepare the request
            api_key = get_next_api_key()
            headers = {"Content-Type": "application/json", "X-API-KEY": api_key}
            data = {
                "model": "kling",
                "task_type": "ai_try_on",
                "input": {"model_input": model_img, "dress_input": dress_img, "batch_size": 1}
            }

            # 2. Submit the initial task
            TASKS[job_id]["status"] = "processing"
            async with session.post(API_URL, json=data, headers=headers) as res:
                res.raise_for_status()
                res_json = await res.json()
                external_task_id = res_json['data']['task_id']
            
            # 3. Poll for the result
            check_url = f"{API_URL}/{external_task_id}"
            status = "pending"
            while status in ["pending", "running", "processing"]:
                await asyncio.sleep(5) # Poll every 5 seconds
                async with session.get(check_url, headers=headers) as check_res:
                    check_res.raise_for_status()
                    check_json = await check_res.json()
                    status = check_json['data']['status']
            
            # 4. Process the final result
            if status == "completed":
                img_url = check_json['data']['output']['works'][0]['image']['resource']
                local_filename = os.path.join(STATIC_DIR, f"{job_id}.png")
                
                if download_image(img_url, local_filename):
                    TASKS[job_id].update({
                        "status": "completed",
                        "resultText": f"Image successfully generated and saved.",
                        "image_url": f"/static/{job_id}.png" # Provide the local URL
                    })
                else:
                    raise Exception("Failed to download the final image.")
            else:
                 raise Exception(f"Task failed with status: {status}")

        except Exception as e:
            print(f"Error in job {job_id}: {e}")
            TASKS[job_id].update({"status": "failed", "error": str(e)})

# --- Flask Routes ---

@app.route('/', methods=['GET'])
def home():
    """Home route to confirm the server is running."""
    return "Flask Virtual Try-On Server is running with changes made!"

@app.route('/start-tryon', methods=['POST'])
def start_tryon_api():
    """
    NEW: This endpoint starts the try-on process and immediately returns a job ID.
    This call is fast and will not time out in Velo.
    """
    data = request.json
    model_img = data.get("model_img")
    dress_img = data.get("dress_img")

    if not model_img or not dress_img:
        return jsonify({"error": "model_img and dress_img are required"}), 400

    # 1. Create a unique job ID.
    job_id = str(uuid.uuid4())

    # 2. Initialize the task status in our global dictionary.
    TASKS[job_id] = {"status": "pending"}

    # 3. Start the long-running task in a separate background thread.
    # We use asyncio.run because the target function is async.
    thread = threading.Thread(
        target=lambda: asyncio.run(process_try_on(job_id, model_img, dress_img))
    )
    thread.start()

    # 4. Immediately return the job ID to the client.
    # 202 Accepted is the standard response for starting an async task.
    return jsonify({"jobId": job_id}), 202

@app.route('/status/<job_id>', methods=['GET'])
def get_status_api(job_id):
    """
    NEW: This endpoint is polled by Velo to check the status of the job.
    """
    task = TASKS.get(job_id)
    if not task:
        return jsonify({"error": "Job ID not found"}), 404
    
    return jsonify(task)

@app.route('/static/<filename>')
def serve_static(filename):
    """Serves the generated images from the static directory."""
    return send_from_directory(STATIC_DIR, filename)

# --- Main Execution ---
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    print(f"Server is running on port {port}")
    app.run(host="0.0.0.0", port=port)
