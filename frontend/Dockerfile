# Contexte de build: la RACINE du projet (voir docker-compose.yml), pour pouvoir
# embarquer deploy/nginx.conf -- un COPY ne peut pas remonter hors du contexte.
FROM node:22-alpine AS build
WORKDIR /app
# The lockfile, and `npm ci`: `npm install` resolves fresh versions at build
# time, so two builds of the same commit can ship different code. On a product
# whose whole claim is that it behaves as tested, that is not acceptable.
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
