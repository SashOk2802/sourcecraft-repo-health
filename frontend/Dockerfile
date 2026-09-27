FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402

WORKDIR /app

RUN addgroup -S app && adduser -S app -G app

# A named node_modules volume may have been created by an older root-run
# development container. Keep Vite's mutable cache outside that volume.
ENV VITE_CACHE_DIR=/tmp/vite-cache

COPY package.json package-lock.json ./
RUN npm ci && chown -R app:app /app

COPY . .
RUN chown -R app:app /app

EXPOSE 5173

USER app

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
