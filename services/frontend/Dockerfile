FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

# Lightweight image that copies built files to the shared volume on startup.
# The build output is baked in at /build/dist (not on the volume mount point),
# then copied to /app/dist (the volume) every time the container starts —
# so rebuilds always produce fresh files.
FROM alpine:3.20
COPY --from=build /app/dist /build/dist
CMD ["sh", "-c", "cp -r /build/dist/. /app/dist/ && echo 'Frontend files ready' && sleep infinity"]
