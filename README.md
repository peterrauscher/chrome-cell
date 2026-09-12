# chrome-cell

A headed Chrome cell you can keep. Built for agent harnesses that want the Muse-shaped split:

- real Chrome on a real display
- live view + take-control in a browser (KasmVNC)
- persistent profile
- CDP on loopback, proxied to the pod network for a broker you own
- a viewport gateway for embedding the live page in your own web app

This is **not** a Secure VM. It is a durable Docker/K8s unit for a home server pool of ~5.

## Viewport

```bash
docker compose up --build
# open http://localhost:8080
```

On the cluster, after `kubectl apply -k k8s/overlays/homelab`:

```
http://<node-ip>:30080          dashboard
ws://<node-ip>:30080/cast/0     page screencast + input
http://<node-ip>:30080/cells/0/ desktop (KasmVNC proxy)
GET    /api/cells               lock + viewer counts
GET    /api/cells/0             one cell
POST   /api/cells/0/lock        take human control
DELETE /api/cells/0/lock        give back to the agent
```

Embed `/` in an iframe, or open `/cast/{id}` from your own canvas. The Chrome profile is what persists. The stream reconnects.

Your harness should stop CDP actions while `lock == human`.

Gateway is one replica. Lock state is in memory.

## Ports

| Port | Bind | What |
| --- | --- | --- |
| 3000 | 0.0.0.0 | KasmVNC HTTP |
| 3001 | 0.0.0.0 | KasmVNC HTTPS |
| 9222 | 127.0.0.1 | Chrome DevTools Protocol |
| 9223 | 0.0.0.0 | socat proxy of 9222 |
| 8080 | gateway | dashboard + /cast + /cells |

## Docker

```bash
docker compose up --build
```

Or the cell alone:

```bash
docker build -t ghcr.io/peterrauscher/chrome-cell:local .
docker run --rm -it --name chrome-cell --shm-size=1g --security-opt seccomp=unconfined \
  -p 3001:3001 -p 127.0.0.1:9223:9223 \
  -e PUID=1000 -e PGID=1000 -e TZ=America/Los_Angeles -e PASSWORD=change-me \
  -v $PWD/config:/config \
  ghcr.io/peterrauscher/chrome-cell:local
```

## Kubernetes

```bash
kubectl apply -k k8s/overlays/homelab
```

Five cells on NodePorts 30010-30014. Gateway on 30080. CDP stays ClusterIP.

Label harness pods `chrome-cell.access/cdp=true`.

Images: `ghcr.io/peterrauscher/chrome-cell` and `ghcr.io/peterrauscher/chrome-cell-gateway`.

For k3s local builds:

```bash
docker build -t ghcr.io/peterrauscher/chrome-cell:local .
docker build -t ghcr.io/peterrauscher/chrome-cell-gateway:local gateway
docker save ghcr.io/peterrauscher/chrome-cell:local ghcr.io/peterrauscher/chrome-cell-gateway:local | sudo k3s ctr images import -
```

## Harness contract

1. Connect to `ws://chrome-cell-N:9223` (CDP) or watch lock via the gateway.
2. Snapshot `Accessibility.getFullAXTree`, not the raw DOM.
3. Do not give the model `Runtime.evaluate`.
4. Keep passwords out of the model.
5. Pause CDP traffic while the gateway reports `lock: human`.
