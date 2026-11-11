"""Run inside the built image to check the real HTTP process and order flow."""

import json
import os
import time
import urllib.request

base = f"http://127.0.0.1:{os.environ.get('PORT', '8080')}"
for _attempt in range(30):
    try:
        with urllib.request.urlopen(base + "/health/live", timeout=2) as response:
            assert json.load(response)["status"] == "ok"
        break
    except OSError:
        time.sleep(1)
else:
    raise RuntimeError("API did not become live")

payload = json.dumps({"customer_id": "smoke", "items": [{"sku": "DEMO-001", "quantity": 1}]})
headers = {"Content-Type": "application/json", "Idempotency-Key": "smoke-test-1"}


def submit():
    request = urllib.request.Request(base + "/v1/orders", payload.encode(), headers=headers)
    with urllib.request.urlopen(request, timeout=5) as response:
        return response.status, json.load(response)


status, order = submit()
assert status == 201 and order["status"] == "confirmed"
replay_status, replay = submit()
assert replay_status == 200 and replay == order
with urllib.request.urlopen(base + "/v1/products", timeout=5) as response:
    assert json.load(response)[0]["available"] == 9
assert os.getuid() == 10001
print("Container HTTP, order, idempotency, stock and non-root checks passed")
