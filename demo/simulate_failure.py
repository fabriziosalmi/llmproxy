import requests

def simulate_failure():
    url = "https://duck.ai/duckchat/v1/chat"
    # Generic OpenAI payload
    payload = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "Hello"}]
    }
    print(f"--- Attempting Generic Request to {url} ---")
    try:
        response = requests.post(url, json=payload, timeout=5)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text[:100]}")
        if response.status_code != 200:
            print("[ADAPTER FAILURE] Missing dynamic headers (x-vqd-hash-1) and incorrect payload structure.")
    except Exception as e:
        print(f"Connection Failed: {e}")

if __name__ == "__main__":
    simulate_failure()
