import subprocess
import atexit
import requests
from flask import Flask, render_template, request, jsonify

app = Flask(__name__)

# -----------------------------------------------------------------
# 1. AUTOMATIC BACKGROUND STARTUP FOR FASTAPI (api.py)
# -----------------------------------------------------------------
print("🚀 Starting FastAPI backend server in the background...")
backend_process = subprocess.Popen(
    ["uvicorn", "api:app", "--host", "127.0.0.1", "--port", "8000", "--reload"]
)

# 2. CLEANUP: Jab aap Flask band karenge (Ctrl+C), toh FastAPI bhi band ho jayega
@atexit.register
def stop_backend():
    if backend_process:
        backend_process.terminate()
        print("🛑 FastAPI backend server stopped successfully.")

# -----------------------------------------------------------------
# ROUTES
# -----------------------------------------------------------------

@app.route("/")
def home():
    return render_template("index.html")

@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json()
    user_message = data.get("message", "")
    
    if not user_message:
        return jsonify({"response": "Kripya koi message likhiye."})
    
    try:
        # Flask se request ko FastAPI backend (`http://127.0.0.1:8000/chat`) par forward kar rahe hain
        fastapi_url = "http://127.0.0.1:8000/chat"
        
        # FastAPI ko request bhej kar response fetch kar rahe hain
        api_response = requests.post(
            fastapi_url, 
            json={"message": user_message}, 
            timeout=30
        )
        
        if api_response.status_code == 200:
            bot_response = api_response.json().get("response", "Agent ne response nahi diya.")
        else:
            bot_response = f"Backend Error: Status code {api_response.status_code}"
            
        return jsonify({"response": bot_response})
        
    except requests.exceptions.ConnectionError:
        return jsonify({"response": "Connection Error: FastAPI backend abhi start ho raha hai, kripya 2 sekund baad dobara try karein."})
    except Exception as e:
        return jsonify({"response": f"Error aa gaya: {str(e)}"})

if __name__ == "__main__":
    # Flask server port 5000 par start hoga
    app.run(debug=True, port=5000)