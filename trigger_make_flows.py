import time
import requests

# Replace with your Make webhooks
WEBHOOK_URLS = [
    "https://hook.make.com/scenario1",
    "https://hook.make.com/scenario2",
    "https://hook.make.com/scenario3",
    # ... up to scenario 10
]

def trigger_all():
    for i, url in enumerate(WEBHOOK_URLS):
        try:
            response = requests.post(url)
            if response.status_code == 200:
                print(f"✅ Scenario {i+1} triggered.")
            else:
                print(f"❌ Scenario {i+1} failed: {response.text}")
        except Exception as e:
            print(f"⚠️ Error triggering scenario {i+1}: {e}")

if __name__ == "__main__":
    while True:
        print("🚀 Triggering all scenarios...")
        trigger_all()
        print("⏳ Waiting 90 seconds...\\n")
        time.sleep(90)
