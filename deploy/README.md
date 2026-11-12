# Appelsin Space

Deployment URL: `https://mhs.dmitriy.space/zerotrace/`.
Checkout: `/home/hamza/apps/zerotrace` on SSH host `apelsin-space`.

After updating the checkout, run on the server:

```sh
sudo bash /home/hamza/apps/zerotrace/deploy/install-apelsin.sh
```

The installer builds with the `/zerotrace/` asset/API prefix, starts the
demo services and load generator, waits for health checks, and adds an
Nginx snippet to the existing HTTPS host. It backs up the host configuration
and checks Nginx before reloading. Existing applications retain their routes.
The app listens only on `127.0.0.1:8000`; Docker volumes retain its data.

This server uses Snap Docker and requires sudo. The installer does not
change Docker socket permissions or grant users permanent Docker access.

Verification:

```sh
curl --fail https://mhs.dmitriy.space/zerotrace/healthz
sudo docker compose -f compose.yaml -f deploy/apelsin.compose.yaml --profile demo ps
```
