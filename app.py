from flask import Flask, request, jsonify, send_from_directory
import asyncio
import aiohttp
import itertools
import os
import requests

from dotenv import load_dotenv
load_dotenv()  # loads .env file


app = Flask(__name__)

# Make sure static folder exists
STATIC_DIR = "static"
os.makedirs(STATIC_DIR, exist_ok=True)

API_URL = "https://api.piapi.ai/api/v1/task"

# ----------------------------
# Use environment variable for API keys
# ----------------------------
api_keys_env = os.environ.get("KLING_KEYS", "")
if not api_keys_env:
    raise ValueError("KLING_KEYS environment variable not set! Add your keys as comma-separated string.")

API_KEYS = api_keys_env.split(",")
key_cycle = itertools.cycle(API_KEYS)

def get_next_api_key():
    return next(key_cycle)

# ----------------------------
# Try-on logic
# ----------------------------
async def try_on(session, model_img, dress_img):
    api_key = get_next_api_key()
    headers = {"Content-Type": "application/json", "X-API-KEY": api_key}

    data = {
        "model": "kling",
        "task_type": "ai_try_on",
        "input": {"model_input": model_img, "dress_input": dress_img, "batch_size": 1}
    }

    async with session.post(API_URL, json=data, headers=headers) as res:
        res_json = await res.json()
        task_id = res_json['data']['task_id']

    check_url = f"{API_URL}/{task_id}"
    status = "pending"
    while status in ["pending", "running", "processing"]:
        await asyncio.sleep(4)
        async with session.get(check_url, headers=headers) as check_res:
            check_json = await check_res.json()
            status = check_json['data']['status']

    try:
        img_url = check_json['data']['output']['works'][0]['image']['resource']
        local_filename = os.path.join(STATIC_DIR, f"{task_id}.png")
        download_image(img_url, local_filename)
        return {
            "task_id": task_id,
            "status": "completed",
            "image_url": img_url,
            "local_path": f"/static/{task_id}.png"
        }
    except Exception as e:
        return {"task_id": task_id, "status": "failed", "error": str(e)}

async def process_request(model_img, dress_img):
    async with aiohttp.ClientSession() as session:
        return await try_on(session, model_img, dress_img)

# ----------------------------
# Download image helper
# ----------------------------
def download_image(url, save_path):
    r = requests.get(url)
    if r.status_code == 200:
        with open(save_path, "wb") as f:
            f.write(r.content)
        return True
    return False

# ----------------------------
# Routes
# ----------------------------
@app.route('/tryon', methods=['POST'])
def tryon_api():
    data = request.json
    model_img = data.get("model_img")
    dress_img = data.get("dress_img")
    if not model_img or not dress_img:
        return jsonify({"error": "model_img and dress_img are required"}), 400
    result = asyncio.run(process_request(model_img, dress_img))
    return jsonify(result)

@app.route('/static/<filename>')
def serve_static(filename):
    return send_from_directory(STATIC_DIR, filename)

@app.route('/', methods=['GET'])
def home():
    return "Flask Virtual Try-On Server Running!"

# ----------------------------
# Main
# ----------------------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))  # Render uses PORT env
    app.run(host="0.0.0.0", port=port)
