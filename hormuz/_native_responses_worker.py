"""Isolated fixed-authority native Responses transport; credentials use stdin."""
import http.client
import json
import sys

RESPONSES_PATH = "/backend-api/codex/responses"
MAX_RESPONSE_BYTES = 64 * 1024 * 1024


def main():
    value = json.load(sys.stdin)
    connection = http.client.HTTPSConnection("chatgpt.com", timeout=3)
    try:
        connection.connect()
        print(json.dumps({"connected": True}), flush=True)
        connection.sock.settimeout(30)
        headers = {"Authorization": value["authorization"], "Content-Type": "application/json",
                   "Accept": "text/event-stream"}
        if value["account"] is not None:
            headers["ChatGPT-Account-ID"] = value["account"]
        connection.request("POST", RESPONSES_PATH, body=value["body"].encode("utf-8"), headers=headers)
        response = connection.getresponse()
        retry_after = response.headers.get_all("Retry-After", [])
        if len(retry_after) != 1 or len(retry_after[0]) > 64:
            retry_after = []
        print(json.dumps({"status": response.status, "retry_after": retry_after,
                          "event_stream": response.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() == "text/event-stream"}), flush=True)
        if response.status == 200:
            count = 0
            while data := response.read1(65536):
                count += len(data)
                if count > MAX_RESPONSE_BYTES:
                    raise ValueError("native_response_oversized")
                sys.stdout.buffer.write(data)
                sys.stdout.buffer.flush()
    finally:
        connection.close()


if __name__ == "__main__":
    main()
