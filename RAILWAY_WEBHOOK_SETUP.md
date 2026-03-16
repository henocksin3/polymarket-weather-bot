# 🚀 Railway Webhook Setup Guide

## Overview

Boten består nå av **to separate services** på Railway:

1. **Web Service (Webhook)** - Mottar Telegram-kommandoer via webhook
2. **Cron Service (Trading Bot)** - Kjører trading-logikk hver time

---

## 📋 Setup Instructions

### Step 1: Deploy Web Service (Webhook)

**1.1. Create New Service**
```bash
# På Railway dashboard:
# 1. Gå til ditt prosjekt
# 2. Klikk "New" → "GitHub Repo"
# 3. Velg polymarket-weather-bot repository
# 4. Gi servicen navn: "webhook-server"
```

**1.2. Configure Service**
```bash
# I webhook-server service settings:

# Build & Deploy:
Start Command: python src/webhook_server.py
# (Railway vil automatisk bruke Procfile hvis den finnes)

# Environment Variables (legg til):
TELEGRAM_BOT_TOKEN=<din_bot_token>
TELEGRAM_CHAT_ID=<din_chat_id>
DB_PATH=/data/trades.db
DATA_DIR=/data
WEBHOOK_URL=https://[auto-generated-domain]/webhook
# (erstatt [auto-generated-domain] med Railway-domenet etter deploy)
```

**1.3. Generate Domain**
```bash
# I webhook-server service settings:
# 1. Gå til "Settings" → "Networking"
# 2. Klikk "Generate Domain"
# 3. Kopier domenet (f.eks. polymarket-bot-production.up.railway.app)
# 4. Oppdater WEBHOOK_URL til: https://<domain>/webhook
```

**1.4. Deploy**
```bash
# Railway vil automatisk deploye når du pusher til main branch
git add .
git commit -m "Add webhook server"
git push origin main
```

---

### Step 2: Deploy Cron Service (Trading Bot)

**2.1. Create Another Service**
```bash
# På Railway dashboard:
# 1. I samme prosjekt, klikk "New" → "GitHub Repo"
# 2. Velg samme polymarket-weather-bot repository
# 3. Gi servicen navn: "trading-bot"
```

**2.2. Configure Service**
```bash
# I trading-bot service settings:

# Build & Deploy:
Start Command: python main.py --once
# (Railway vil bruke railway.json hvis den finnes)

# Cron Schedule:
# Gå til "Settings" → "Cron"
# Sett schedule: 0 * * * * (kjører hver time)

# Environment Variables (legg til):
POLYMARKET_API_KEY=<din_api_key>
POLYMARKET_API_SECRET=<din_api_secret>
POLYMARKET_API_PASSPHRASE=<din_passphrase>
POLYMARKET_PRIVATE_KEY=<din_private_key>
TELEGRAM_BOT_TOKEN=<din_bot_token>
TELEGRAM_CHAT_ID=<din_chat_id>
DB_PATH=/data/trades.db
DATA_DIR=/data
DRY_RUN=true  # Sett til false for live trading
```

**2.3. Add Volume (Shared Database)**
```bash
# For BEGGE services:
# 1. Gå til "Settings" → "Volumes"
# 2. Klikk "Add Volume"
# 3. Mount Path: /data
# 4. Dette deler databasen mellom web og cron services
```

---

## 🔧 Configuration Files

### Procfile (for Web Service)
```
web: python src/webhook_server.py
```

### railway.json (for Cron Service)
```json
{
  "build": {
    "builder": "NIXPACKS"
  },
  "deploy": {
    "startCommand": "python main.py --once",
    "cronSchedule": "0 * * * *"
  }
}
```

---

## 🔍 Verification

### Test Webhook Server

**1. Health Check**
```bash
curl https://<your-domain>.up.railway.app/
# Should return:
# {
#   "status": "ok",
#   "service": "polymarket-weather-bot-webhook",
#   "telegram_configured": true
# }
```

**2. Test Telegram Command**
```
# I Telegram, send:
/status

# Bot skal svare umiddelbart (< 1 sekund)
```

**3. Check Logs**
```bash
railway logs --service webhook-server

# Should see:
# "Webhook registered successfully"
# "Starting Flask webhook server on port 8080"
```

### Test Trading Bot

**1. Trigger Cron Job**
```bash
# På Railway dashboard:
# 1. Gå til trading-bot service
# 2. Klikk "Deployments"
# 3. Klikk "Trigger Deployment" for å kjøre manuelt
```

**2. Check Logs**
```bash
railway logs --service trading-bot

# Should see:
# "Bot starting — dry_run=True once=True"
# "=== Scan cycle starting ==="
# "=== Scan cycle complete ==="
```

---

## 📊 Architecture

```
┌─────────────────────────────────────────────────────┐
│                  Railway Project                    │
├─────────────────────────────────────────────────────┤
│                                                     │
│  ┌─────────────────┐      ┌───────────────────┐   │
│  │  Web Service    │      │  Cron Service     │   │
│  │  (webhook)      │      │  (trading-bot)    │   │
│  ├─────────────────┤      ├───────────────────┤   │
│  │ webhook_server  │      │ main.py --once    │   │
│  │ PORT: 8080      │      │ Schedule: hourly  │   │
│  │ Receives:       │      │ Executes:         │   │
│  │ - /status       │      │ - Resolve trades  │   │
│  │ - /rapport      │      │ - Run learning    │   │
│  │ - /eksperimenter│      │ - Experiments     │   │
│  │ - /kalibrering  │      │ - Trade signals   │   │
│  │ - /stopp        │      │                   │   │
│  │ - /start        │      │                   │   │
│  └────────┬────────┘      └─────────┬─────────┘   │
│           │                         │             │
│           └──────────┬──────────────┘             │
│                      │                            │
│              ┌───────▼────────┐                   │
│              │  Shared Volume │                   │
│              │  /data         │                   │
│              │  - trades.db   │                   │
│              └────────────────┘                   │
│                                                     │
└─────────────────────────────────────────────────────┘

External:
  Telegram API ──webhook──> Web Service
  Polymarket API <────────> Cron Service
```

---

## 🔐 Environment Variables

### Web Service (webhook-server)
```bash
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat_id>
DB_PATH=/data/trades.db
DATA_DIR=/data
WEBHOOK_URL=https://<domain>/webhook
PORT=8080  # Auto-set by Railway
```

### Cron Service (trading-bot)
```bash
POLYMARKET_API_KEY=<key>
POLYMARKET_API_SECRET=<secret>
POLYMARKET_API_PASSPHRASE=<passphrase>
POLYMARKET_PRIVATE_KEY=<private_key>
TELEGRAM_BOT_TOKEN=<token>
TELEGRAM_CHAT_ID=<chat_id>
DB_PATH=/data/trades.db
DATA_DIR=/data
DRY_RUN=true
```

---

## 🐛 Troubleshooting

### Webhook Not Receiving Messages

**Check webhook registration:**
```bash
# I Python:
python3 << EOF
import httpx
token = "YOUR_BOT_TOKEN"
response = httpx.get(f"https://api.telegram.org/bot{token}/getWebhookInfo")
print(response.json())
EOF

# Should show:
# {
#   "url": "https://your-domain/webhook",
#   "has_custom_certificate": false,
#   "pending_update_count": 0
# }
```

**Re-register webhook:**
```bash
# Restart webhook-server service på Railway
# Den vil automatisk registrere webhook på nytt
```

### Cron Job Not Running

**Check cron schedule:**
```bash
# Railway dashboard:
# Settings → Cron → Verify schedule is "0 * * * *"
```

**Manual trigger:**
```bash
# Railway dashboard:
# Deployments → Trigger Deployment
```

### Database Not Shared

**Verify volumes:**
```bash
# For BEGGE services:
# Settings → Volumes → Should see "/data" mount
```

---

## 📈 Monitoring

### Webhook Server Logs
```bash
railway logs --service webhook-server --follow

# Watch for:
# - "Webhook registered successfully"
# - "Received webhook update"
# - "Processing command: /status"
# - "Command processed: status"
```

### Trading Bot Logs
```bash
railway logs --service trading-bot --follow

# Watch for:
# - "Bot starting"
# - "Scan cycle starting"
# - "Resolved X trade(s)"
# - "Learning complete"
# - "Scan cycle complete"
```

---

## 🚀 Deployment Checklist

- [ ] Create webhook-server service på Railway
- [ ] Generate domain for webhook-server
- [ ] Set WEBHOOK_URL environment variable
- [ ] Create trading-bot service på Railway
- [ ] Configure cron schedule (hourly)
- [ ] Add /data volume to BOTH services
- [ ] Set all environment variables
- [ ] Deploy both services
- [ ] Test webhook with /status command
- [ ] Trigger cron job manually to test
- [ ] Verify database sharing between services
- [ ] Monitor logs for errors

---

## 💡 Tips

1. **Use same repository for both services** - Railway will build from same code
2. **Different start commands** - webhook uses Procfile, cron uses railway.json
3. **Shared volume is crucial** - Both need access to /data/trades.db
4. **Test webhook first** - Easier to debug than cron
5. **Monitor both services** - Use `railway logs --service <name>`

---

## 📝 Summary

**Before:**
- Single service with polling thread
- Inefficient (polls every 10s)
- Can't scale independently

**After:**
- Two services: web + cron
- Efficient webhooks (instant response)
- Can scale independently
- Better separation of concerns

**Result:** Production-ready architecture! 🚀

---

*Last updated: 2026-03-16*
*Railway: Production*
*Architecture: Web Service + Cron Job*
