"""Minimal Kalshi client: RSA-PSS signed requests, demo env by default, dry-run OMS.

Live order placement requires:  pip install cryptography requests
Dry-run mode needs nothing.
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import time
import uuid

DEMO_BASE = "https://demo-api.kalshi.co/trade-api/v2"
PROD_BASE = "https://api.elections.kalshi.com/trade-api/v2"  # verify current prod host in docs


class KalshiClient:
    def __init__(self, key_id: str = "", private_key_path: str = "",
                 base_url: str = DEMO_BASE, dry_run: bool = True):
        self.key_id, self.base_url, self.dry_run = key_id, base_url, dry_run
        self._pk = None
        if not dry_run:
            from cryptography.hazmat.primitives import serialization
            with open(private_key_path, "rb") as f:
                self._pk = serialization.load_pem_private_key(f.read(), password=None)

    def _headers(self, method: str, path: str) -> dict:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding
        ts = str(int(time.time() * 1000))
        msg = (ts + method + path).encode()
        sig = self._pk.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                        salt_length=padding.PSS.DIGEST_LENGTH),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "Content-Type": "application/json",
        }

    def _request(self, method: str, path: str, body: dict | None = None):
        import requests
        url = self.base_url + path
        headers = self._headers(method, "/trade-api/v2" + path)
        r = requests.request(method, url, headers=headers,
                             json=body, timeout=10)
        r.raise_for_status()
        return r.json()

    # ---- public ----
    def get_markets(self, series_ticker: str | None = None, status: str = "open"):
        if self.dry_run:
            return {"markets": [], "note": "dry-run: no live call made"}
        q = f"?status={status}" + (f"&series_ticker={series_ticker}" if series_ticker else "")
        return self._request("GET", f"/markets{q}")

    def place_limit_order(self, ticker: str, side: str, price_cents: int, count: int):
        """side: 'yes' or 'no'; resting limit buy on that side."""
        order = {
            "ticker": ticker,
            "client_order_id": str(uuid.uuid4()),
            "action": "buy",
            "side": side,
            "type": "limit",
            "count": count,
            f"{side}_price": price_cents,
        }
        if self.dry_run:
            stamp = dt.datetime.now().strftime("%H:%M:%S")
            print(f"  [DRY-RUN {stamp}] POST /portfolio/orders  {json.dumps(order)}")
            return {"order": order, "status": "dry_run"}
        return self._request("POST", "/portfolio/orders", order)

    def cancel_order(self, order_id: str):
        if self.dry_run:
            print(f"  [DRY-RUN] DELETE /portfolio/orders/{order_id}")
            return {"status": "dry_run"}
        return self._request("DELETE", f"/portfolio/orders/{order_id}")
