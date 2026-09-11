# chrome-cell

A headed Chrome cell you can keep. Built for agent harnesses that want the Muse-shaped split:

- real Chrome on a real display
- live view + take-control in a browser (KasmVNC)
- persistent profile
- CDP on loopback, proxied to the pod network for a broker you own

This is **not** a Secure VM. It is a durable Docker/K8s unit for a home server pool of ~5.

Image base: [`ghcr.io/linuxserver/baseimage-kasmvnc:ubuntujammy`](https://docs.linuxserver.io/images/docker-baseimage-kasmvnc/). Official `google-chrome-stable` on `amd64`, distro Chromium on `arm64`.

## Ports

| Port | Bind | What |
| --- | --- | --- |
| 3000 | 0.0.0.0 | KasmVNC HTTP |
| 3001 | 0.0.0.0 | KasmVNC HTTPS |
| 9222 | 127.0.0.1 | Chrome DevTools Protocol |
| 9223 | 0.0.0.0 | `socat` proxy of 9222. Point the harness here. |

Chrome never listens on `0.0.0.0:9222`. The broker talks to 9223.

## Docker

```bash
docker build -t ghcr.io/peterrauscher/chrome-cell:local .

docker run --rm -it \
  --name chrome-cell \
  --shm-size=1g \
  --security-opt seccomp=unconfined \
  -p 3001:3001 \
  -p 127.0.0.1:9223:9223 \
  -e PUID=1000 \
  -e PGID=1000 \
  -e TZ=America/Los_Angeles \
  -e PASSWORD=change-me \
  -e LAUNCH_URL=about:blank \
  -v $PWD/config:/config \
  ghcr.io/peterrauscher/chrome-cell:local
```

Open `https://localhost:3001` (self-signed). Default UI user is `abc`. Password is whatever you set in `PASSWORD`. If `PASSWORD` is unset there is **no** UI auth.

Or: `docker compose up --build`

### Environment

| Var | Default | Meaning |
| --- | --- | --- |
| `PUID` / `PGID` | 911 in the base | File ownership on `/config` |
| `TZ` | UTC | Clock / Chrome timezone |
| `PASSWORD` | unset (no auth) | HTTP basic auth for the live view |
| `CUSTOM_USER` | `abc` | HTTP basic auth user |
| `LAUNCH_URL` | `about:blank` | First tab |
| `CHROME_PROFILE_DIR` | `/config/chrome` | `--user-data-dir` |
| `CHROME_FLAGS` | empty | Extra Chrome flags, space-separated |
| `CHROME_SANDBOX` | `0` | Set `1` only if the container can actually run the sandbox |
| `CDP_PORT` | `9222` | Chrome loopback CDP |
| `CDP_PROXY_PORT` | `9223` | Published proxy |
| `TITLE` | `chrome-cell` | VNC page title |

Pass `/dev/dri` if the host has an Intel/AMD iGPU.

## Kubernetes (single host)

Designed for k3s / k3d / MicroK8s on one machine. Uses the default storage class (`local-path` on k3s).

```bash
# edit k8s/overlays/homelab/secret.example.yaml first
kubectl apply -k k8s/overlays/homelab
```

That starts **5** cells:

| Cell | Live view | CDP (in-cluster) |
| --- | --- | --- |
| `chrome-cell-0` | `https://<node-ip>:30010` | `chrome-cell-0.chrome-cell.chrome-cell.svc.cluster.local:9223` |
| `chrome-cell-1` | `:30011` | `chrome-cell-1.chrome-cell.chrome-cell.svc.cluster.local:9223` |
| `chrome-cell-2` | `:30012` | `chrome-cell-2.chrome-cell...:9223` |
| `chrome-cell-3` | `:30013` | `chrome-cell-3.chrome-cell...:9223` |
| `chrome-cell-4` | `:30014` | `chrome-cell-4.chrome-cell...:9223` |

Each pod gets a 5Gi PVC at `/config` and a 1Gi memory-backed `/dev/shm`.

CDP is **ClusterIP / headless only**. Do not NodePort 9223. Label your harness pods with `chrome-cell.access/cdp=true` so the NetworkPolicy lets them through.

```yaml
metadata:
  labels:
    chrome-cell.access/cdp: "true"
```

Scale:

```bash
kubectl -n chrome-cell scale statefulset chrome-cell --replicas=3
```

If you scale above 5, add more NodePort services in `k8s/overlays/homelab/nodeports.yaml`.

### Build the image on the host

k3s will not see images you only built with Docker unless you import them:

```bash
docker build -t ghcr.io/peterrauscher/chrome-cell:local .
docker save ghcr.io/peterrauscher/chrome-cell:local | sudo k3s ctr images import -
```

Then set the tag in `k8s/overlays/homelab/kustomization.yaml`.

After the GHCR workflow on `main` runs, you can just pull `ghcr.io/peterrauscher/chrome-cell:latest`.

## Harness contract

The cell is the browser. Your broker stays outside Chrome:

1. Connect to `ws://chrome-cell-N:9223` (CDP).
2. Snapshot `Accessibility.getFullAXTree`, not the raw DOM.
3. Do not give the model `Runtime.evaluate`.
4. Keep passwords out of the model. Inject them from your vault into the form.
5. Pause CDP traffic when a human has the VNC session.

## What this will not do

- Isolate like Muse Secure VM (`nspawn` + host-side Sentinel). These pods share the host kernel.
- Hide datacenter/home-IP reputation. Egress is whatever IP the node has.
- Defeat bot detection. It is headed Chrome with a real profile, not a stealth browser.

## Layout

```
Dockerfile
root/defaults/autostart          # starts socat + chrome-cell
root/usr/local/bin/chrome-cell   # Chrome wrapper
k8s/base/                        # StatefulSet + services + NetworkPolicy
k8s/overlays/homelab/            # 5 NodePorts + UI password secret
.github/workflows/image.yml      # GHCR amd64+arm64
```
