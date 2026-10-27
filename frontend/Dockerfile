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

# 1.30 is the current STABLE line (even minor; 1.31 is mainline). 1.27 reached
# end of life on 2025-06-24 and has received no security fixes since -- on the
# one process that faces the internet. Lesson 23 applies to any change here:
# check what the server actually SENDS (curl -I), not that the config parses.
FROM nginx:1.30-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
