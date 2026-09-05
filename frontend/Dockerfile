# Investigation Agent Client — frontend
# Stage 1: build the existing Vite production bundle into dist/
# Stage 2: serve it with nginx; /api is reverse-proxied to the backend
# service (same-origin routing, mirroring the Vite dev proxy).
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npx vite build

FROM nginx:1.27-alpine

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
