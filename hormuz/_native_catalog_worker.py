"""Isolated native catalog transport, run with a parent-owned process deadline."""
import base64
import http.client
import json
import sys

CATALOG_PATH = "/backend-api/codex/models"
NATIVE_ORIGIN = "https://chatgpt.com/backend-api/codex"
MAX_CATALOG_BYTES = 1024 * 1024


def main():
    value = json.load(sys.stdin)
    connection = http.client.HTTPSConnection("chatgpt.com", timeout=3)
    try:
        headers = {"Authorization": value["authorization"], "Accept": "application/json"}
        if value["account"] is not None:
            headers["ChatGPT-Account-ID"] = value["account"]
        connection.request("GET", CATALOG_PATH + ("?" + value["query"] if value["query"] else ""), headers=headers)
        response = connection.getresponse()
        data = response.read(MAX_CATALOG_BYTES + 1) if response.status == 200 else b""
        retry_after = response.headers.get_all("Retry-After", [])
        # Bound the private pipe independently of the upstream's header/body sizes.
        if len(retry_after) != 1 or len(retry_after[0]) > 64:
            retry_after = []
        print(json.dumps({"status": response.status, "retry_after": retry_after,
                          "body": base64.b64encode(data).decode("ascii")}))
    finally:
        connection.close()


if __name__ == "__main__":
    main()
