# Lipana STK Push Checkout - Skitech Solutions

A modern, responsive M-Pesa STK Push payment integration built with Python (Flask) and Vanilla JS. This project provides a premium checkout experience for Kenyan businesses using the [Lipana.dev](https://lipana.dev) API.

## 🚀 Features
- **Premium UI**: Sleek, glassmorphism-inspired design with dark mode.
- **Dynamic Feedback**: Real-time STK push status tracking (Pending → Success/Failed).
- **Locked Phone Prefix**: User-friendly input with a fixed `+254` badge.
- **Webhook Integration**: Secure transaction verification using HMAC-SHA256 signatures.
- **API Fallback**: Active polling logic to ensure payment status resolves even if webhooks are delayed.

## 🛠️ Stack
- **Backend**: Python 3.12 + Flask
- **Frontend**: HTML5, CSS3 (Vanilla), JavaScript (ES6)
- **Payment Gateway**: [Lipana API](https://docs.lipana.dev)
- **Tunneling**: ngrok (for local webhook testing)

## 📦 Installation

1. **Clone the repository**:
   ```bash
   git clone https://github.com/Dev-Skylarker/lipana-stk-push.git
   cd lipana-stk-push
   ```

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure Environment**:
   Create a `.env` file in the root directory:
   ```env
   LIPANA_SECRET_KEY=your_sk_here
   LIPANA_WEBHOOK_SECRET=your_webhook_secret_here
   PORT=3000
   ```

## 🚀 Running Locally

1. **Start the Flask server**:
   ```bash
   python app.py
   ```

2. **Expose to the internet (for webhooks)**:
   ```bash
   ngrok http 3000
   ```

3. **Update Webhook URL**:
   Get the ngrok URL (e.g., `https://xxxx.ngrok-free.app`) and register `https://xxxx.ngrok-free.app/webhook` in your Lipana Dashboard.

## 🚢 Deployment
This project is ready for deployment on **Render**, **Railway**, or **DigitalOcean**. 
The `requirements.txt` includes `gunicorn` for production-grade serving.

- **Start Command**: `gunicorn app:app`

---
Built by [Skitech Solutions](https://skitech-website.vercel.app/)
