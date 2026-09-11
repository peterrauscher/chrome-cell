IMAGE ?= ghcr.io/peterrauscher/chrome-cell
TAG ?= local
PLATFORM ?= linux/amd64

.PHONY: build run push k8s-apply k8s-delete

build:
	docker build --platform=$(PLATFORM) -t $(IMAGE):$(TAG) .

run:
	docker run --rm -it \
	  --name chrome-cell \
	  --shm-size=1g \
	  --security-opt seccomp=unconfined \
	  -p 3001:3001 \
	  -p 127.0.0.1:9223:9223 \
	  -e PUID=1000 \
	  -e PGID=1000 \
	  -e TZ=America/Los_Angeles \
	  -v $$(pwd)/config:/config \
	  $(IMAGE):$(TAG)

push:
	docker push $(IMAGE):$(TAG)

k8s-apply:
	kubectl apply -k k8s/overlays/homelab

k8s-delete:
	kubectl delete -k k8s/overlays/homelab
