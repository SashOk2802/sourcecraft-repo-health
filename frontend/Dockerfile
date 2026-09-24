FROM node:22-alpine

WORKDIR /app

RUN addgroup -S app && adduser -S app -G app

COPY package.json package-lock.json ./
RUN npm ci && chown -R app:app /app

COPY . .
RUN chown -R app:app /app

EXPOSE 5173

USER app

CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0"]
