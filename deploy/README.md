# Appelsin Space

Deployment URL: `https://mhs.dmitriy.space/zerotrace/`.
Checkout: `/home/hamza/apps/zerotrace` on SSH host `apelsin-space`.

After updating the checkout, run on the server:

```sh
sudo bash /home/hamza/apps/zerotrace/deploy/install-apelsin.sh
```

The installer builds with the `/zerotrace/` asset/API prefix, serves only static demo files, waits for health checks, and adds an
Nginx snippet to the existing HTTPS host. It backs up the host configuration
and checks Nginx before reloading. Existing applications retain their routes.
The static server listens only on `127.0.0.1:8000`. No backend, database,
control endpoints or load generator are exposed. Existing full-stack containers
in the ZeroTrace Compose project are removed; their data volumes are preserved.
Browser settings cannot switch this build to a live connection.

The root `compose.yaml` still runs the full application locally.

This server uses Snap Docker and requires sudo. The installer does not
change Docker socket permissions or grant users permanent Docker access.

Verification:

```sh
curl --fail https://mhs.dmitriy.space/zerotrace/healthz
sudo docker compose -f deploy/apelsin.compose.yaml ps
```
