#!/bin/bash
# Deploy TitanChess Poster Bot ke Google Cloud Run

set -e

PROJECT_ID=$(grep GCP_PROJECT_ID .env | cut -d= -f2)
SERVICE_NAME="titanchess-poster"
REGION="asia-southeast1"  # Singapore — paling dekat Indonesia
IMAGE="gcr.io/$PROJECT_ID/$SERVICE_NAME"

echo "🔨 Building Docker image..."
gcloud builds submit --tag $IMAGE

echo "🚀 Deploying to Cloud Run..."
gcloud run deploy $SERVICE_NAME \
  --image $IMAGE \
  --platform managed \
  --region $REGION \
  --allow-unauthenticated \
  --memory 512Mi \
  --timeout 3600 \
  --set-env-vars "TELEGRAM_BOT_TOKEN=$(grep TELEGRAM_BOT_TOKEN .env | cut -d= -f2)" \
  --set-env-vars "TELEGRAM_OWNER_ID=$(grep TELEGRAM_OWNER_ID .env | cut -d= -f2)" \
  --set-env-vars "GCP_PROJECT_ID=$PROJECT_ID"

# Ambil URL Cloud Run
SERVICE_URL=$(gcloud run services describe $SERVICE_NAME --region $REGION --format "value(status.url)")
echo ""
echo "✅ Deployed! URL: $SERVICE_URL"
echo ""
echo "⚙️  Set webhook URL di .env:"
echo "WEBHOOK_URL=$SERVICE_URL"
echo ""
echo "📅 Setup Cloud Scheduler (jalankan setiap menit):"
echo "gcloud scheduler jobs create http titanchess-poster-scheduler \\"
echo "  --schedule='* * * * *' \\"
echo "  --uri='$SERVICE_URL/execute_scheduled' \\"
echo "  --http-method=POST \\"
echo "  --location=$REGION"
