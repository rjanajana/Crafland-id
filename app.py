import WorkshopSubscribe_pb2
import requests
import urllib3
import re
import json
import os
from concurrent.futures import ThreadPoolExecutor
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad, pad
from flask import Flask, request, jsonify
from waitress import serve

# Disable SSL Warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- Configuration (Byte List Format) ---
KEY = bytes([89, 103, 38, 116, 99, 37, 68, 69, 117, 104, 54, 37, 90, 99, 94, 56])
IV = bytes([54, 111, 121, 90, 68, 114, 50, 50, 69, 51, 121, 99, 104, 106, 77, 37])
TOKEN_FILE_PATH = os.path.join(os.path.dirname(__file__), "token_ind.json")

app = Flask(__name__)

# Global HTTP Session for connection pooling & performance
session = requests.Session()
adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=100)
session.mount('https://', adapter)
session.mount('http://', adapter)

# Token caching variables
_cached_tokens = []
_last_mtime = 0

def get_tokens():
    global _cached_tokens, _last_mtime
    try:
        if os.path.exists(TOKEN_FILE_PATH):
            current_mtime = os.path.getmtime(TOKEN_FILE_PATH)
            if current_mtime != _last_mtime or not _cached_tokens:
                with open(TOKEN_FILE_PATH, "r") as f:
                    tokens_data = json.load(f)
                    _cached_tokens = [item["token"] for item in tokens_data if "token" in item]
                _last_mtime = current_mtime
    except Exception as e:
        print(f"[!] Error loading tokens file: {e}")
    return _cached_tokens

def encrypt_CSSubscribeWorkshopCodeReq(map_code):
    msg = WorkshopSubscribe_pb2.CSSubscribeWorkshopCodeReq()
    msg.slot_id = 1
    msg.subscription_source = 26
    msg.language = 'en'
    msg.workshop_code = map_code
    raw_data = msg.SerializeToString()
    cipher = AES.new(KEY, AES.MODE_CBC, IV)
    return cipher.encrypt(pad(raw_data, 16))

def send_single_token_request(args):
    idx, jwt_token, payload = args
    url = "https://client.ind.freefiremobile.com/SubscribeWorkshopCode"
    headers = {
        'Host': 'client.ind.freefiremobile.com',
        'User-Agent': 'UnityPlayer/2022.3.47f1 (UnityWebRequest/1.0, libcurl/8.5.0-DEV)',
        'Accept': '*/*',
        'Accept-Encoding': 'deflate, gzip',
        'Authorization': f'Bearer {jwt_token}',
        'X-GA': 'v1 1',
        'ReleaseVersion': 'OB54',
        'Content-Type': 'application/x-www-form-urlencoded',
        'X-Unity-Version': '2022.3.47f1',
        'Content-Length': str(len(payload))
    }
    
    try:
        response = session.post(url, headers=headers, data=payload, verify=False, timeout=8)
        raw_bytes = response.content
        
        # --- AES Decryption ---
        decrypted = None
        if len(raw_bytes) % 16 == 0 and len(raw_bytes) > 0:
            try:
                cipher = AES.new(KEY, AES.MODE_CBC, IV)
                decrypted = unpad(cipher.decrypt(raw_bytes), AES.block_size)
            except Exception:
                pass

        final_data = decrypted if decrypted else raw_bytes
        
        strings = re.findall(rb'[ -~]{4,}', final_data)
        response_msg = ", ".join([s.decode('ascii', errors='ignore') for s in strings]) if strings else final_data.hex()
            
        return {
            "token_index": idx + 1,
            "status_code": response.status_code,
            "response": response_msg
        }
    except Exception as e:
        return {
            "token_index": idx + 1,
            "status_code": 500,
            "error": str(e)
        }

@app.route('/subscribe', methods=['GET', 'POST'])
def subscribe():
    map_code = request.args.get('map_code') or request.args.get('code')
    if not map_code and request.is_json:
        map_code = request.json.get('map_code') or request.json.get('code')
    if not map_code and request.form:
        map_code = request.form.get('map_code') or request.form.get('code')
        
    if not map_code:
        return jsonify({
            "status": "error",
            "message": "Missing 'map_code' parameter. Example usage: /subscribe?map_code=51C3834CE45F403AAFF03F4D85F86625Q387"
        }), 400

    tokens = get_tokens()
    if not tokens:
        return jsonify({
            "status": "error",
            "message": f"No tokens found in {TOKEN_FILE_PATH}"
        }), 500

    payload = encrypt_CSSubscribeWorkshopCodeReq(map_code)
    
    # Execute requests in parallel using multithreading for maximum speed & load handling
    task_args = [(idx, token, payload) for idx, token in enumerate(tokens)]
    with ThreadPoolExecutor(max_workers=25) as executor:
        results = list(executor.map(send_single_token_request, task_args))

    # Sort by token index
    results.sort(key=lambda x: x["token_index"])

    return jsonify({
        "status": "success",
        "map_code": map_code,
        "tokens_processed": len(tokens),
        "results": results
    })

if __name__ == "__main__":
    print("[*] Starting High-Performance API Server (Waitress) on http://0.0.0.0:5000 ...")
    serve(app, host="0.0.0.0", port=5000, threads=100)
