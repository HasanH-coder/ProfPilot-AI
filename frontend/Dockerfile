# The ProfPilot web app, as a container (for AWS Lightsail or any container host).
# NEXT_PUBLIC_* values are built into the app: pass them with --build-arg, and
# rebuild the image whenever one changes.
FROM node:22-slim

WORKDIR /app

COPY package.json package-lock.json ./
# Retry downloads, so a dropped connection doesn't fail the whole build.
RUN npm config set fetch-retries 6 \
    && npm config set fetch-retry-mintimeout 20000 \
    && npm config set fetch-timeout 300000 \
    && npm ci

COPY . .

ARG NEXT_PUBLIC_DEMO_MODE=true
ARG NEXT_PUBLIC_SUPABASE_URL
ARG NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
ARG NEXT_PUBLIC_API_URL
ENV NEXT_PUBLIC_DEMO_MODE=$NEXT_PUBLIC_DEMO_MODE \
    NEXT_PUBLIC_SUPABASE_URL=$NEXT_PUBLIC_SUPABASE_URL \
    NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY=$NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY \
    NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

RUN npm run build

ENV NODE_ENV=production PORT=3000
EXPOSE 3000
CMD ["npm", "start"]
