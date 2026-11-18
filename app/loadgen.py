"""Steady traffic generator: ~CHECKOUT_SHARE of requests hit /checkout, the rest /products."""
import argparse
import json
import os
import random
import threading
import time
import urllib.request


def worker(url, interval, share, stats, stop):
    rng = random.Random()
    while not stop.is_set():
        start = time.time()
        try:
            if rng.random() < share:
                req = urllib.request.Request(url + "/checkout", data=b"{}", method="POST",
                                             headers={"Content-Type": "application/json"})
            else:
                req = urllib.request.Request(url + "/products")
            with urllib.request.urlopen(req, timeout=10) as r:
                r.read()
                stats["ok"] += 1
        except Exception:
            stats["err"] += 1
        stop.wait(max(0.0, interval - (time.time() - start)))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--url", default=os.environ.get("TARGET_URL", "http://shop-api.shop.svc:8080"))
    p.add_argument("--rps", type=float, default=float(os.environ.get("RPS", "20")))
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--checkout-share", type=float, default=float(os.environ.get("CHECKOUT_SHARE", "0.5")))
    a = p.parse_args()
    stats, stop = {"ok": 0, "err": 0}, threading.Event()
    interval = a.workers / a.rps
    for _ in range(a.workers):
        threading.Thread(target=worker, args=(a.url, interval, a.checkout_share, stats, stop), daemon=True).start()
    try:
        while True:
            time.sleep(15)
            print(json.dumps(stats), flush=True)
    except KeyboardInterrupt:
        stop.set()


if __name__ == "__main__":
    main()
