"""
Lipana Dev STK Push Payment Backend
Flask server that handles M-Pesa STK Push via the Lipana API.

Webhook payload (from real Lipana docs):
    POST /webhook
    X-Lipana-Signature: <hmac-sha256>
    {
      "event": "payment.success" | "payment.failed" | "payment.pending",
      "data": {
        "transactionId": "txn_123456",
        "amount": 5000,
        "currency": "KES",
        "status": "success" | "failed" | "pending",
        "phone": "+254712345678",
        "timestamp": "2024-01-15T10:30:00Z"
      }
    }
"""

import os
import hmac
import hashlib
import json
import logging
import requests

from flask import Flask, request, jsonify, send_from_directory
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────
LIPANA_SECRET_KEY     = os.getenv("LIPANA_SECRET_KEY")
LIPANA_WEBHOOK_SECRET = os.getenv("LIPANA_WEBHOOK_SECRET")
PORT                  = int(os.getenv("PORT", 3000))
LIPANA_API_BASE       = "https://api.lipana.dev/v1"
# Public URL where Lipana will POST webhook events.
# Set in .env as WEBHOOK_PUBLIC_URL=https://xxxx.ngrok-free.app
# If not set, the server will try to auto-detect from a running ngrok tunnel.
WEBHOOK_PUBLIC_URL    = os.getenv("WEBHOOK_PUBLIC_URL", "").rstrip("/")

# In-memory store: transactionId → { status, phone, amount }
# status values: "pending" | "success" | "failed"
payment_store: dict[str, dict] = {}

# ─────────────────────────────────────────────
# App Setup
# ─────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

app = Flask(__name__, static_folder="public", static_url_path="")


def get_webhook_url() -> str:
    """Return the full public webhook URL, auto-detecting ngrok if needed."""
    if WEBHOOK_PUBLIC_URL:
        return f"{WEBHOOK_PUBLIC_URL}/webhook"
    # Try to read from a running ngrok agent API
    try:
        resp = requests.get("http://localhost:4040/api/tunnels", timeout=3)
        tunnels = resp.json().get("tunnels", [])
        for t in tunnels:
            if t.get("proto") == "https":
                return f"{t['public_url']}/webhook"
    except Exception:
        pass
    return f"http://localhost:{PORT}/webhook  ⚠️  (not publicly reachable — set WEBHOOK_PUBLIC_URL)"


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def lipana_headers() -> dict:
    """Return common Lipana API auth headers."""
    return {
        "x-api-key": LIPANA_SECRET_KEY,
        "Content-Type": "application/json",
    }


def verify_webhook_signature(payload_bytes: bytes, signature: str) -> bool:
    """
    Verify HMAC-SHA256 webhook signature from Lipana.
    Uses the raw request body (before JSON parsing) per Lipana docs.
    """
    expected = hmac.new(
        LIPANA_WEBHOOK_SECRET.encode("utf-8"),
        payload_bytes,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def map_lipana_status(raw: str) -> str:
    """
    Map Lipana's status strings to our internal values.
    Lipana webhook data.status: "success" | "failed" | "pending"
    Lipana API transaction list status: "completed" | "failed" | "pending" etc.
    """
    raw = (raw or "").lower().strip()
    if raw in ("success", "completed"):
        return "success"
    elif raw in ("failed", "failure", "cancelled", "canceled"):
        return "failed"
    else:
        return "pending"


def fetch_lipana_status(transaction_id: str) -> dict | None:
    """
    Query the Lipana API for the current status of a transaction.
    Used as a fallback when a webhook hasn't arrived yet.

    NOTE: GET /v1/transactions/{id} returns HTTP 500 on Lipana's end.
          The filter param ?transactionId= is also unreliable.
          We therefore fetch the list and scan for a matching transactionId.

    Returns { status: 'success'|'failed'|'pending' } or None on error.
    """
    try:
        # Fetch latest transactions (most recent first) and scan for our ID.
        # Paginate up to 3 pages (≤300 records) to handle busier accounts.
        page = 1
        while page <= 3:
            resp = requests.get(
                f"{LIPANA_API_BASE}/transactions",
                params={"limit": 100, "page": page},
                headers=lipana_headers(),
                timeout=15,
            )

            if not resp.ok:
                log.debug("Lipana list endpoint returned HTTP %s (page %s)",
                          resp.status_code, page)
                return None

            body  = resp.json()
            items = body.get("data", [])

            # Normalise: data may be nested dict with inner data array
            if isinstance(items, dict):
                items = items.get("data", [])

            if not isinstance(items, list) or not items:
                break  # no more records

            # Scan this page for our transaction
            for tx in items:
                if tx.get("transactionId") == transaction_id:
                    raw_status = tx.get("status", "")
                    log.info("Lipana list scan found  id=%s  raw_status=%s",
                             transaction_id, raw_status)
                    return {"status": map_lipana_status(raw_status)}

            # Check if there are more pages
            pagination = body.get("pagination", {})
            total_pages = pagination.get("pages", 1)
            if page >= total_pages:
                break
            page += 1

        log.debug("Transaction %s not found in Lipana list", transaction_id)
        return None

    except requests.exceptions.RequestException as exc:
        log.debug("fetch_lipana_status error: %s", exc)
        return None


# ─────────────────────────────────────────────
# Routes – Static Pages
# ─────────────────────────────────────────────
@app.route("/")
def index():
    """Serve payment method selection page."""
    return send_from_directory("public", "index.html")


@app.route("/checkout")
def checkout_page():
    """Serve M-Pesa STK checkout page."""
    return send_from_directory("public", "checkout.html")


@app.route("/thankyou")
def thankyou_page():
    """Serve the thank-you / success page."""
    return send_from_directory("public", "thankyou.html")


# ─────────────────────────────────────────────
# Routes – Payment Initiation
# ─────────────────────────────────────────────
@app.route("/pay", methods=["POST"])
def initiate_payment():
    """
    Initiate an STK Push via Lipana.

    Expects JSON body:
        { "phone": "254XXXXXXXXX", "amount": <int> }

    Returns:
        { "trackingId": "<transactionId>" }  on success
        { "error": "<message>" }             on failure
    """
    body   = request.get_json(silent=True) or {}
    phone  = str(body.get("phone", "")).strip()
    amount = body.get("amount")

    # ── Validate inputs ──────────────────────
    if not phone:
        return jsonify({"error": "Phone number is required"}), 400
    if not amount:
        return jsonify({"error": "Amount is required"}), 400

    try:
        amount = int(float(amount))
        if amount < 10:
            return jsonify({"error": "Minimum payment amount is KES 10"}), 400
    except (ValueError, TypeError):
        return jsonify({"error": "Amount must be a valid number"}), 400

    # Normalise phone: ensure +254XXXXXXXXX format
    phone = phone.lstrip("+").lstrip("0")
    if not phone.startswith("254"):
        phone = "254" + phone

    log.info("Initiating STK push → phone=+%s  amount=%s", phone, amount)

    # ── Call Lipana API ──────────────────────
    try:
        resp = requests.post(
            f"{LIPANA_API_BASE}/transactions/push-stk",
            json={"phone": f"+{phone}", "amount": amount},
            headers=lipana_headers(),
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()
    except requests.exceptions.Timeout:
        log.error("Lipana API timed out")
        return jsonify({"error": "Payment gateway timed out. Please try again."}), 504
    except requests.exceptions.HTTPError as exc:
        error_body = {}
        try:
            error_body = exc.response.json()
        except Exception:
            pass
        log.error("Lipana API error %s: %s", exc.response.status_code, error_body)
        return jsonify({"error": error_body.get("message", "Payment initiation failed")}), 502
    except requests.exceptions.RequestException as exc:
        log.error("Network error calling Lipana: %s", exc)
        return jsonify({"error": "Could not reach payment gateway"}), 502

    # ── Extract transactionId (primary tracking key) ──
    data           = result.get("data", result)
    transaction_id = (
        data.get("transactionId")
        or data.get("transaction_id")
        or data.get("checkoutRequestID")   # legacy fallback
        or data.get("checkout_request_id")
        or ""
    )

    if not transaction_id:
        log.error("Lipana response missing transactionId. Full response: %s", result)
        return jsonify({"error": "Unexpected response from payment gateway"}), 502

    # ── Store initial pending state ──────────
    payment_store[transaction_id] = {
        "status":  "pending",
        "phone":   phone,
        "amount":  amount,
    }
    log.info("STK push queued  trackingId=%s", transaction_id)

    return jsonify({"trackingId": transaction_id}), 200


# ─────────────────────────────────────────────
# Routes – Status Polling
# ─────────────────────────────────────────────
@app.route("/status/<tracking_id>", methods=["GET"])
def check_status(tracking_id: str):
    """
    Poll payment status.

    If the local record is missing (due to server restart or multiple workers),
    we try to recover it by asking the Lipana API directly.
    """
    record = payment_store.get(tracking_id)

    # ── Attempt Recovery if not found in memory ──
    if not record:
        log.info("Recovery poll for missing id=%s", tracking_id)
        live = fetch_lipana_status(tracking_id)
        if live:
            # Reconstruct the record from API data
            payment_store[tracking_id] = {
                "status": live["status"],
                "phone":  "Recovered",
                "amount": 0 # Amount isn't critical for the status screen
            }
            record = payment_store[tracking_id]
        else:
            return jsonify({"status": "not_found"}), 404

    # ── Active API poll while still pending ──
    if record["status"] == "pending":
        live = fetch_lipana_status(tracking_id)
        if live and live["status"] != "pending":
            record["status"] = live["status"]
            log.info(
                "API poll resolved  id=%s  → status=%s",
                tracking_id, record["status"],
            )

    return jsonify({
        "status": record["status"],
        "phone":  record.get("phone"),
        "amount": record.get("amount"),
    }), 200


# ─────────────────────────────────────────────
# Routes – Webhook
# ─────────────────────────────────────────────
@app.route("/webhook-info", methods=["GET"])
def webhook_info():
    """
    Diagnostic endpoint — returns the public webhook URL to register
    in the Lipana dashboard, plus pending/tracked transaction count.
    """
    return jsonify({
        "webhook_url":          get_webhook_url(),
        "instruction":         "Register the webhook_url above in your Lipana dashboard → Webhooks settings",
        "tracked_transactions": len(payment_store),
        "transactions":        {
            tid: {"status": r["status"], "amount": r.get("amount")}
            for tid, r in payment_store.items()
        },
    }), 200


@app.route("/webhook", methods=["POST"])
def webhook():
    """
    Receive Lipana payment webhook notifications.
    Verifies HMAC-SHA256 signature before processing.

    Supported events (from Lipana docs):
        payment.success  →  data.status == "success"
        payment.failed   →  data.status == "failed"
        payment.pending  →  data.status == "pending"
    """
    raw_payload = request.get_data()

    signature = request.headers.get("X-Lipana-Signature", "")
    if not signature:
        log.warning("Webhook received without X-Lipana-Signature header")
        return jsonify({"error": "Missing signature"}), 401

    if not verify_webhook_signature(raw_payload, signature):
        log.warning("Webhook signature mismatch — possible spoofed request")
        return jsonify({"error": "Invalid signature"}), 401

    try:
        data = json.loads(raw_payload)
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid JSON"}), 400

    event      = data.get("event", "")          # e.g. "payment.success"
    event_data = data.get("data", {})

    # Per Lipana docs: data.transactionId is the primary identifier
    transaction_id = event_data.get("transactionId") or event_data.get("transaction_id", "")

    # data.status is the ground-truth status field in Lipana's payload
    raw_status = event_data.get("status", "")
    resolved   = map_lipana_status(raw_status)

    log.info("Webhook  event=%s  id=%s  status=%s → %s",
             event, transaction_id, raw_status, resolved)

    # ── Update payment store ──────────────────
    if transaction_id in payment_store:
        payment_store[transaction_id]["status"] = resolved
        log.info("Payment store updated  id=%s  status=%s", transaction_id, resolved)
    else:
        # Payment arrived via webhook before a poll — register it now
        payment_store[transaction_id] = {
            "status": resolved,
            "phone":  event_data.get("phone", "").lstrip("+"),
            "amount": event_data.get("amount"),
        }
        log.info("Webhook registered new payment  id=%s", transaction_id)

    return jsonify({"received": True}), 200


# ─────────────────────────────────────────────
# Entry Point
# ─────────────────────────────────────────────
if __name__ == "__main__":
    if not LIPANA_SECRET_KEY:
        raise RuntimeError("LIPANA_SECRET_KEY is not set in .env")
    if not LIPANA_WEBHOOK_SECRET:
        raise RuntimeError("LIPANA_WEBHOOK_SECRET is not set in .env")

    log.info("Starting Lipana payment server on port %s", PORT)
    log.info("Checkout page  →  http://localhost:%s", PORT)
    log.info("Webhook URL    →  %s  (register this in your Lipana dashboard)", get_webhook_url())
    log.info("Diagnostic     →  http://localhost:%s/webhook-info", PORT)
    app.run(host="0.0.0.0", port=PORT, debug=False)
